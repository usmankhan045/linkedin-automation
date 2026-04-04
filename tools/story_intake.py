"""
story_intake.py — Story processing module for the Discord bot.

Called from tools/dm_ghostwriter.py (or tools/discord_bot.py) when a message
is received in the Discord #stories channel.

Core function: handle_story_submission(message, groq_client, spreadsheet_id)
  - Validates the message length and deduplicates via Supabase
  - Calls Groq to transform raw story into a formatted LinkedIn post
  - Saves story + post to Supabase
  - Writes a new row to the Google Sheets Topic Bank for the next Saturday slot
  - Replies to Discord with a formatted preview

Depends on: tools/db_client.py, tools/sheets_helper.py
Env vars: GROQ_MODEL, GOOGLE_SHEETS_SPREADSHEET_ID, SHEETS_TOPIC_BANK_TAB,
          SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta

import pytz
from groq import Groq
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.db_client as db
import tools.sheets_helper as sheets

load_dotenv()

PKT = pytz.timezone('Asia/Karachi')
GROQ_MODEL = os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')
TOPIC_BANK_TAB = os.getenv('SHEETS_TOPIC_BANK_TAB', 'Topic Bank')
MIN_STORY_LENGTH = 30


# ── Prompts ────────────────────────────────────────────────────────────────────

STORY_SYSTEM_PROMPT = (
    'You are a LinkedIn ghostwriter for Muhammad Usman, an AI Automation Engineer from Pakistan.\n'
    'Your job is to transform raw personal stories into polished LinkedIn posts.\n'
    'Voice: first-person, honest, reflective, never cringe-worthy. '
    'Reads like a real person, not a marketer.'
)

STORY_USER_TEMPLATE = (
    'Raw story from Muhammad Usman:\n---\n{raw_story}\n---\n\n'
    'Transform this into a LinkedIn post that:\n'
    '1. Opens with the human moment, not the achievement (don\'t lead with "I just...")\n'
    '2. Middle: what was hard, what changed, what was learned\n'
    '3. End: a universal insight others can apply -- make it worth reading for strangers\n'
    '4. CTA: invite a genuine conversation or connection\n'
    '5. 3-5 hashtags: #AIAutomation #BuildInPublic #PakistanTech (+ 1-2 relevant to the story)\n'
    '6. Max 1,300 characters total\n'
    '7. First 210 characters must hook -- this is what shows before "see more"\n'
    '8. No markdown formatting\n\n'
    'Also return:\n'
    '- image_prompt: A warm, human DALL-E image description. '
    'Can reference Pakistan, tech, achievement. No text, no faces.\n'
    '- extracted_points: Array of 3-5 bullet points summarising the story '
    '(for Sheets key_details column)\n\n'
    'Respond in JSON only:\n'
    '{{"content": "...", "image_prompt": "...", "extracted_points": ["...", "...", "..."]}}'
)


# ── Scheduling ─────────────────────────────────────────────────────────────────

def next_saturday_slot(spreadsheet_id: str) -> tuple[str, bool]:
    """
    Find the next available Saturday slot in the Google Sheets Topic Bank.

    Tries up to 4 consecutive Saturdays. A slot is 'available' if no non-published
    row with that scheduled_date already exists in the Topic Bank.

    Returns (date_str, overbooked) where overbooked=True means all 4 are taken.
    """
    today = datetime.now(PKT).date()
    days_to_saturday = (5 - today.weekday()) % 7
    if days_to_saturday == 0:
        days_to_saturday = 7  # today is Saturday — target next one

    try:
        ws = sheets.get_sheet_client().open_by_key(spreadsheet_id).worksheet(TOPIC_BANK_TAB)
        all_rows = ws.get_all_values()
        headers = all_rows[0] if all_rows else []
        date_col = headers.index('scheduled_date') if 'scheduled_date' in headers else -1
        status_col = headers.index('status') if 'status' in headers else -1
        data_rows = all_rows[1:] if len(all_rows) > 1 else []
    except Exception as e:
        print(f'[WARN] Could not read Topic Bank for slot check: {e}')
        # Fall back to next Saturday without availability check
        candidate = today + timedelta(days=days_to_saturday)
        return candidate.strftime('%Y-%m-%d'), False

    for weeks_ahead in range(4):
        candidate = today + timedelta(days=days_to_saturday + weeks_ahead * 7)
        candidate_str = candidate.strftime('%Y-%m-%d')

        if date_col < 0:
            return candidate_str, False

        taken = any(
            len(row) > date_col
            and row[date_col] == candidate_str
            and (status_col < 0 or len(row) <= status_col or row[status_col] != 'published')
            for row in data_rows
        )
        if not taken:
            return candidate_str, False

    # All 4 Saturdays taken — return the 4th anyway
    overbooked_date = (today + timedelta(days=days_to_saturday + 3 * 7)).strftime('%Y-%m-%d')
    return overbooked_date, True


# ── Groq ───────────────────────────────────────────────────────────────────────

def format_story_via_groq(groq_client: Groq, raw_story: str) -> dict:
    """
    Call Groq to transform raw story text into a LinkedIn post.

    Returns a dict with keys: content, image_prompt, extracted_points.
    Retries once on JSON parse failure. Raises on second failure.
    """
    user_prompt = STORY_USER_TEMPLATE.format(raw_story=raw_story)

    for attempt in range(2):
        try:
            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {'role': 'system', 'content': STORY_SYSTEM_PROMPT},
                    {'role': 'user', 'content': user_prompt},
                ],
                temperature=0.75,
                max_tokens=800,
            )
            raw = response.choices[0].message.content.strip()

            # Strip accidental markdown fences
            if raw.startswith('```'):
                lines = raw.splitlines()
                raw = '\n'.join(lines[1:-1] if lines[-1].strip() == '```' else lines[1:]).strip()

            return json.loads(raw)

        except json.JSONDecodeError as e:
            if attempt == 0:
                print(f'[WARN] Story Groq JSON parse error, retrying: {e}')
                time.sleep(2)
            else:
                raise


# ── Discord handler ────────────────────────────────────────────────────────────

async def handle_story_submission(message, groq_client: Groq, spreadsheet_id: str) -> None:
    """
    Process a Discord #stories message end-to-end.

    1. Validates length + deduplicates
    2. Formats via Groq
    3. Saves to Supabase (stories + posts)
    4. Writes to Google Sheets Topic Bank
    5. Replies with a preview
    """
    content = message.content.strip()

    # Too short
    if len(content) < MIN_STORY_LENGTH:
        await message.add_reaction('❓')
        await message.channel.send(
            "That's a bit short for a story -- can you share more detail?"
        )
        return

    # Duplicate check (idempotent on bot restart)
    if db.story_exists(str(message.id)):
        return

    if len(content) > 4000:
        print(f'[INFO] Long story ({len(content)} chars) — Groq will condense')

    await message.channel.send('Processing your story...')

    # Format via Groq
    try:
        result = format_story_via_groq(groq_client, content)
    except Exception as e:
        print(f'[ERROR] Story formatting failed for message {message.id}: {e}')
        # Save raw for manual recovery
        db.insert_story(
            discord_message_id=str(message.id),
            discord_author=str(message.author),
            raw_content=content,
        )
        await message.channel.send(
            "⚠️ Couldn't auto-format this story. It's saved -- resubmit or try again later."
        )
        return

    post_content = result.get('content', content[:1300])
    image_prompt = result.get('image_prompt', '')
    extracted_points = result.get('extracted_points', [])
    bullets = ' | '.join(str(p) for p in extracted_points) if extracted_points else ''

    # Save story to Supabase
    story = db.insert_story(
        discord_message_id=str(message.id),
        discord_author=str(message.author),
        raw_content=content,
    )

    # Save post to Supabase
    post_row = db.create_post(
        post_text=post_content,
        audience='story',
        category='story',
    )

    # Find next Saturday slot
    slot_date, overbooked = next_saturday_slot(spreadsheet_id)

    # Write to Google Sheets Topic Bank
    topic_preview = content[:100]
    try:
        ws = sheets.get_sheet_client().open_by_key(spreadsheet_id).worksheet(TOPIC_BANK_TAB)
        ws.append_row(
            [
                topic_preview,    # topic
                'story',          # category
                bullets,          # key_details
                'general',        # audience
                'pending',        # status
                slot_date,        # scheduled_date
                post_row['id'],   # post_id
                '',               # image_path (populated by generate_image.py later)
            ],
            value_input_option='USER_ENTERED',
        )
    except Exception as e:
        print(f'[WARN] Sheets Topic Bank write failed (non-fatal): {e}')

    # Mark story as processed
    db.mark_story_processed(story['id'], post_row['id'])

    # Build Discord preview
    preview = post_content[:400] + ('...' if len(post_content) > 400 else '')
    reply_lines = [
        f'📝 **Story formatted!** Scheduled for {slot_date}',
        '',
        '---',
        preview,
        '---',
    ]
    if bullets:
        reply_lines += ['', f'**Key points:** {bullets}']
    reply_lines += [
        '',
        '✅ Marked **pending** in Sheets -- approve it there when ready.',
        '⚠️ Image will be generated when you approve.' if not image_prompt else
        '🖼️ Image prompt saved. Image generated on approval.',
    ]
    if overbooked:
        reply_lines.append(
            f'\n⚠️ Next 4 Saturdays are booked. Scheduled for {slot_date} -- '
            'you may want to reschedule something.'
        )

    await message.channel.send('\n'.join(reply_lines))
    print(f'[{datetime.now()}] Story from {message.author} processed → scheduled {slot_date}')
