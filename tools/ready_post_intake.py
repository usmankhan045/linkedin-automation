"""
ready_post_intake.py — Handle ready-made LinkedIn posts submitted to Discord.

Called from dm_ghostwriter.py when a message is received in the #ready-posts channel.

Message format expected:
    <Full LinkedIn post text — multiple lines OK>

    ---TAGS---
    tag1, tag2, tag3

Core function: handle_ready_post(message, spreadsheet_id)
  - Parses message: post content + image tags
  - Generates image via DALL-E 3 using the tags (if OPENAI_API_KEY is set)
  - Saves post to Supabase
  - Writes to Google Sheets Topic Bank on the next available weekday slot
  - Replies with a preview + image URL

Differs from story_intake.py:
  - No AI rewriting — post content is used verbatim
  - Image is generated inline from user-provided tags (not an AI-generated prompt)
  - Slots are next available weekday (Mon-Fri), not next Saturday

Env vars: OPENAI_API_KEY, GOOGLE_SHEETS_SPREADSHEET_ID, SHEETS_TOPIC_BANK_TAB,
          SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""

import os
import sys
import requests
from datetime import datetime, timedelta

import pytz
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.db_client as db
import tools.sheets_helper as sheets

load_dotenv()

PKT = pytz.timezone('Asia/Karachi')
TOPIC_BANK_TAB = os.getenv('SHEETS_TOPIC_BANK_TAB', 'Topic Bank')

TAG_SEPARATOR = '---TAGS---'
MIN_POST_LENGTH = 50


# ── Parsing ────────────────────────────────────────────────────────────────────

def parse_message(content: str) -> tuple[str, list[str]] | None:
    """
    Split message into (post_text, tags_list) on the TAG_SEPARATOR line.

    Returns None if the separator is missing, either section is empty,
    or no valid tags are found.
    """
    if TAG_SEPARATOR not in content:
        return None

    parts = content.split(TAG_SEPARATOR, 1)
    post_text = parts[0].strip()
    tags_raw = parts[1].strip()

    if not post_text or not tags_raw:
        return None

    tags = [t.strip() for t in tags_raw.split(',') if t.strip()]
    if not tags:
        return None

    return post_text, tags


def build_image_prompt(tags: list[str]) -> str:
    tags_str = ', '.join(tags)
    return (
        f'Professional LinkedIn post image featuring {tags_str}. '
        'Clean composition, modern aesthetic, photorealistic, high quality. '
        'No text overlays, no faces. Suitable for a professional social media post.'
    )


# ── Image generation ───────────────────────────────────────────────────────────

def generate_image_bytes(image_prompt: str) -> bytes | None:
    """
    Call DALL-E 3 and return raw PNG bytes.

    Returns None if OPENAI_API_KEY is not set.
    Raises on API or download failure.
    """
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        return None

    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    response = client.images.generate(
        model='dall-e-3',
        prompt=image_prompt,
        size='1024x1024',
        quality='standard',
        style='vivid',
        n=1,
    )

    img_url = response.data[0].url
    r = requests.get(img_url, timeout=30)
    r.raise_for_status()
    return r.content


# ── Scheduling ─────────────────────────────────────────────────────────────────

def next_available_weekday_slot(spreadsheet_id: str) -> tuple[str, bool]:
    """
    Find the next Mon-Fri that has no pending/queued row in the Topic Bank.

    Looks up to 30 calendar days ahead. Returns (date_str, overbooked).
    overbooked=True means every weekday in the next 30 days is already taken.
    """
    today = datetime.now(PKT).date()

    try:
        ws = sheets.get_sheet_client().open_by_key(spreadsheet_id).worksheet(TOPIC_BANK_TAB)
        all_rows = ws.get_all_values()
        headers = all_rows[0] if all_rows else []
        date_col = headers.index('scheduled_date') if 'scheduled_date' in headers else -1
        status_col = headers.index('status') if 'status' in headers else -1
        data_rows = all_rows[1:] if len(all_rows) > 1 else []
    except Exception as e:
        print(f'[WARN] Could not read Topic Bank for slot check: {e}')
        # Fallback: first weekday starting tomorrow
        candidate = today + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate.strftime('%Y-%m-%d'), False

    taken: set[str] = set()
    for row in data_rows:
        if date_col >= 0 and len(row) > date_col and row[date_col]:
            is_published = (
                status_col >= 0
                and len(row) > status_col
                and row[status_col] == 'published'
            )
            if not is_published:
                taken.add(row[date_col])

    for days_ahead in range(1, 31):
        candidate = today + timedelta(days=days_ahead)
        if candidate.weekday() < 5:  # Mon=0 … Fri=4
            candidate_str = candidate.strftime('%Y-%m-%d')
            if candidate_str not in taken:
                return candidate_str, False

    # All weekdays in the next 30 days are booked
    overbooked = today + timedelta(days=31)
    while overbooked.weekday() >= 5:
        overbooked += timedelta(days=1)
    return overbooked.strftime('%Y-%m-%d'), True


# ── Deduplication ──────────────────────────────────────────────────────────────

def ready_post_exists(discord_message_id: str) -> bool:
    """Return True if this Discord message was already processed."""
    result = (
        db.get_client()
        .table('posts')
        .select('id')
        .eq('topic', f'ready:{discord_message_id}')
        .execute()
    )
    return len(result.data) > 0


# ── Discord handler ────────────────────────────────────────────────────────────

async def handle_ready_post(message, spreadsheet_id: str) -> None:
    """
    Process a Discord #ready-posts message end-to-end.

    1. Validates format (requires ---TAGS--- separator)
    2. Deduplicates by Discord message ID
    3. Generates DALL-E 3 image from tags (skipped if no OPENAI_API_KEY)
    4. Saves post to Supabase
    5. Writes to Google Sheets Topic Bank (next available weekday)
    6. Replies with preview
    """
    content = message.content.strip()

    parsed = parse_message(content)
    if parsed is None:
        await message.channel.send(
            '⚠️ **Wrong format.** Use:\n\n'
            '```\nYour complete LinkedIn post goes here...\n\n'
            '---TAGS---\ntag1, tag2, tag3\n```\n\n'
            'The post is used **verbatim** — no AI rewriting.'
        )
        return

    post_text, tags = parsed

    if len(post_text) < MIN_POST_LENGTH:
        await message.channel.send(
            f'⚠️ Post is too short ({len(post_text)} chars). '
            f'Minimum is {MIN_POST_LENGTH} characters.'
        )
        return

    if ready_post_exists(str(message.id)):
        return  # already processed on a previous bot run

    await message.channel.send('⏳ Processing your post...')

    image_prompt = build_image_prompt(tags)
    tags_str = ', '.join(tags)

    # Generate image inline
    image_bytes = None
    image_url = None
    image_error = None

    if not os.getenv('OPENAI_API_KEY'):
        image_error = 'no_key'
    else:
        try:
            image_bytes = generate_image_bytes(image_prompt)
        except Exception as e:
            print(f'[WARN] Image generation failed for message {message.id}: {e}')
            image_error = str(e)

    # Save post to Supabase
    # topic stores the discord_message_id with prefix for deduplication
    post_row = db.create_post(
        post_text=post_text,
        audience='general',
        topic=f'ready:{message.id}',
        category='ready_post',
    )
    post_id = post_row['id']

    # Save image prompt regardless of whether generation succeeded
    db.update_post(post_id, image_prompt=image_prompt)

    # Upload image if generated
    if image_bytes:
        try:
            image_url = db.upload_image(post_id, image_bytes)
            db.update_post(post_id, image_url=image_url)
        except Exception as e:
            print(f'[WARN] Image upload failed for post {post_id}: {e}')
            image_error = f'upload failed: {e}'
            image_url = None

    # Find next available weekday slot
    slot_date, overbooked = next_available_weekday_slot(spreadsheet_id)

    # Write to Google Sheets Topic Bank
    try:
        ws = sheets.get_sheet_client().open_by_key(spreadsheet_id).worksheet(TOPIC_BANK_TAB)
        ws.append_row(
            [
                post_text[:100],   # topic (preview for sheet readability)
                'ready_post',      # category
                tags_str,          # key_details (the image tags)
                'general',         # audience
                'pending',         # status
                slot_date,         # scheduled_date
                post_id,           # post_id
                image_url or '',   # image_path
            ],
            value_input_option='USER_ENTERED',
        )
    except Exception as e:
        print(f'[WARN] Sheets Topic Bank write failed (non-fatal): {e}')

    # Build Discord reply
    char_count = len(post_text)
    preview = post_text[:400] + ('...' if len(post_text) > 400 else '')

    reply_lines = [
        f'✅ **Ready post queued!** Scheduled for **{slot_date}**',
        f'📊 {char_count} chars',
        '',
        '---',
        preview,
        '---',
        '',
        f'🏷️ **Image tags:** {tags_str}',
    ]

    if image_url:
        reply_lines.append(f'🖼️ **Image generated:** {image_url}')
    elif image_error == 'no_key':
        reply_lines.append(
            f'⚠️ Image pending — set `OPENAI_API_KEY` then run:\n'
            f'`python tools/generate_image.py --post_id {post_id}`'
        )
    else:
        reply_lines.append(
            f'⚠️ Image generation failed — retry with:\n'
            f'`python tools/generate_image.py --post_id {post_id}`'
        )

    reply_lines += [
        '',
        '✅ Set status to **approved** in Google Sheets when ready to publish.',
    ]

    if overbooked:
        reply_lines.append(
            f'\n⚠️ Calendar is packed — scheduled for {slot_date}. '
            'You may want to reschedule something.'
        )

    await message.channel.send('\n'.join(reply_lines))
    print(f'[{datetime.now(PKT)}] Ready post from {message.author} → queued for {slot_date} (post_id={post_id})')
