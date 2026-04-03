"""
generate_posts.py — Sunday batch post generator.

Reads the next 5 pending topics from the Google Sheets 'Topic Bank', generates a
LinkedIn post for each using Groq (llama-3.3-70b-versatile), then extracts bullet
points (for infographic images) and a headline, and writes everything back to Sheets.

Scheduling: runs every Sunday via .github/workflows/weekly_generation.yml
Depends on: tools/sheets_helper.py
Output: up to 5 Topic Bank rows updated with post_text, bullet_points, headline,
        audience, status='pending', scheduled_date (next Mon-Fri)

Environment variables required:
  GROQ_API_KEY, GOOGLE_SHEETS_SPREADSHEET_ID, GOOGLE_SERVICE_ACCOUNT_JSON (or _PATH)
Optional:
  GROQ_MODEL, DISCORD_WEBHOOK_CONFIRMATIONS
"""

import os
import sys
import json
import time
from datetime import datetime, date, timedelta

import pytz
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.sheets_helper as sheets

load_dotenv()

PKT = pytz.timezone('Asia/Karachi')
GROQ_MODEL = os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')
SPREADSHEET_ID = os.environ['GOOGLE_SHEETS_SPREADSHEET_ID']
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK_CONFIRMATIONS')


# ─── Prompts ──────────────────────────────────────────────────────────────────

MASTER_SYSTEM_PROMPT = """You are ghostwriting a LinkedIn post for Usman, a software engineer from Pakistan who builds AI automation systems. Write in first person as Usman.
STRICT RULES:

No em dashes (use commas or periods instead)
No 'Here is the thing:' or 'Let me be honest' or 'Here is what I learned' transitions
No emoji bullet points or emoji anywhere
No more than one question mark per post
No 'rapidly evolving landscape' or any variation
No 'game-changer', 'groundbreaking', 'revolutionary', 'seamless', 'cutting-edge'
No rule-of-three patterns (never list exactly 3 adjectives in a row)
No 'It is not just X, it is Y' construction
No 'At its core' or 'In today's world' or 'The reality is'
No starting sentences with 'And' or 'But' more than once per post
Short paragraphs only: 1-2 sentences maximum per paragraph
Use line breaks between every paragraph (LinkedIn format)
Open with a bold or surprising first line (hook). People see only 210 characters before 'see more'
Include at least one specific technical detail, tool name, or real number
Close with a genuine question OR a mild contrarian opinion (not both)
Tone: confident but not arrogant, casual but technically credible
Write like a smart engineer explaining something to a friend over chai
NOT like a motivational speaker
NOT like a LinkedIn influencer
NOT like a press release
"""

CATEGORY_PROMPTS = {
    'build-log': (
        "Write about a specific thing Usman built or is building. "
        "Structure: "
        "1. What the problem was (1-2 sentences) "
        "2. What he tried first (1 sentence) "
        "3. What actually worked and why (2-3 sentences with specific tool names) "
        "4. One honest admission of what was hard or what broke "
        "5. Close with what comes next, or a question asking others how they handle the same problem. "
        "Do not make it sound like a tutorial. "
        "Make it sound like a work log entry from someone in the middle of building."
    ),
    'transformation': (
        "Write a before/after story about a process that was slow or manual and is now automated. "
        "Structure: "
        "1. The old way and how painful it was (specific time/effort numbers) "
        "2. The new way: what tools were used, how they connect (be specific) "
        "3. The exact result: hours saved, steps eliminated, errors reduced (use real numbers) "
        "4. Close with: 'What manual process in your work would you want to kill?' "
        "Keep it grounded. No exaggeration. Real numbers only."
    ),
    'hot-take': (
        "Write a mildly contrarian opinion about AI, automation, or software engineering. "
        "Structure: "
        "1. State the popular belief that most people hold (1 sentence) "
        "2. Why it is incomplete or wrong, based on real experience (2-3 sentences) "
        "3. The alternative perspective with a specific example "
        "4. Close with 'What do you think?' or a restatement of the contrarian view. "
        "Not clickbait-contrarian. Genuinely challenging a common assumption. Respectful but firm."
    ),
    'behind-scenes': (
        "Write about the internals of something Usman is working on. "
        "Show the messy middle, not the polished result. "
        "Structure: "
        "1. Here is what I am working on right now (1 sentence) "
        "2. Here is what the current state looks like "
        "3. Here is what I have learned so far (1-2 specific takeaways) "
        "4. Close with an invitation for suggestions or feedback. "
        "Make it feel like opening the hood of a car while it is still running."
    ),
    'founder-roi': (
        "Write a post targeting non-technical business owners about how AI automation saves time and money. "
        "Structure: "
        "1. Open with a relatable business pain (something they experience daily, "
        "manual reporting, chasing approvals, repetitive data entry) "
        "2. Show the automation solution in plain language (no jargon, no node names, just the outcome) "
        "3. Give concrete numbers: hours saved per week, cost reduced, errors eliminated "
        "4. Close with a question: 'What part of your operations eats the most time?' "
        "Make the founder feel like Usman understands their world, not just the technology."
    ),
}

VALID_ENGINEER_CATEGORIES = {'build-log', 'transformation', 'hot-take', 'behind-scenes'}


# ─── Date / scheduling helpers ────────────────────────────────────────────────

def get_next_weekdays() -> list:
    """
    Return the 5 weekday dates (Mon-Fri) of the coming week in PKT timezone.

    If called on Sunday, returns tomorrow (Monday) through the following Friday.
    If called on any other day, returns the Monday of the following week.
    """
    today = datetime.now(PKT).date()
    days_ahead = (7 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # Today is Monday — target next week's Monday
    next_monday = today + timedelta(days=days_ahead)
    return [next_monday + timedelta(days=i) for i in range(5)]


def resolve_audience_and_category(weekday_num: int, sheet_category: str) -> tuple:
    """
    Determine (audience, category_prompt_key) from the scheduled weekday.

    weekday_num: Python date.weekday() value — 0=Mon, 1=Tue, ..., 4=Fri

    Rules:
      Tuesday (1) / Thursday (3) → founder audience → 'founder-roi' category prompt
      Monday (0) / Wednesday (2) / Friday (4) → engineer audience → use sheet category
      Sheet category defaults to 'build-log' if unrecognised.
    """
    if weekday_num in (1, 3):
        return 'founder', 'founder-roi'
    category = sheet_category if sheet_category in VALID_ENGINEER_CATEGORIES else 'build-log'
    return 'engineer', category


# ─── Groq helpers ─────────────────────────────────────────────────────────────

def _call_groq(client, system_prompt: str, user_message: str, max_tokens: int = 600) -> str:
    """
    Call Groq chat completions with one automatic retry on rate limit (429).

    Raises the underlying exception on the second failure or on any non-429 error.
    """
    import groq as groq_lib

    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_message},
    ]
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                temperature=0.8,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content.strip()
        except groq_lib.RateLimitError:
            if attempt == 0:
                print(f"[{datetime.now()}] Groq rate limit hit. Waiting 60s before retry...")
                time.sleep(60)
            else:
                raise  # Second attempt also rate-limited — let caller handle


def generate_post_text(client, topic: str, key_details: str, category: str) -> str:
    """Call 1 of 3: generate the main LinkedIn post body."""
    category_prompt = CATEGORY_PROMPTS.get(category, CATEGORY_PROMPTS['build-log'])
    system = MASTER_SYSTEM_PROMPT.strip() + '\n\n' + category_prompt
    user = (
        f"Topic: {topic}\n"
        f"Key details: {key_details}\n\n"
        "Write the LinkedIn post now."
    )
    return _call_groq(client, system, user, max_tokens=600)


def extract_bullet_points(client, post_text: str) -> str:
    """
    Call 2 of 3: extract 3-5 key takeaway bullet points for the infographic image.

    Returns a pipe-separated string, e.g. 'Point one|Point two|Point three'.
    Raises json.JSONDecodeError if the model returns malformed JSON (caller should catch).
    """
    system = (
        "You extract key takeaway points from LinkedIn posts. "
        "Return ONLY valid JSON, nothing else. No markdown, no backticks, no explanation."
    )
    user = (
        "Extract 3-5 key takeaway points from this LinkedIn post. "
        "Each point must be under 12 words. "
        "These will be displayed as bullet points on a branded infographic image. "
        "Make them punchy, specific, and scannable. "
        "Return ONLY a JSON array of strings, nothing else.\n\n"
        f"Post: {post_text}"
    )
    raw = _call_groq(client, system, user, max_tokens=200)

    # Strip accidental markdown code fences
    raw = raw.strip()
    if raw.startswith('```'):
        lines = raw.splitlines()
        # Remove first line (```json or ```) and last line (```)
        inner = lines[1:-1] if lines[-1].strip() == '```' else lines[1:]
        raw = '\n'.join(inner).strip()

    bullets = json.loads(raw)  # raises json.JSONDecodeError on bad output
    return '|'.join(str(b).strip() for b in bullets[:5])


def extract_headline(client, post_text: str) -> str:
    """
    Call 3 of 3: extract a punchy max-6-word headline for the infographic title.

    Returns the headline string directly.
    """
    system = (
        "You extract punchy headlines from LinkedIn posts. "
        "Return ONLY the headline text. No punctuation at the end, no quotes, no explanation."
    )
    user = (
        "Extract a punchy headline (maximum 6 words) from this LinkedIn post. "
        "This will be displayed as the large title on a branded infographic. "
        "Return ONLY the headline text, nothing else.\n\n"
        f"Post: {post_text}"
    )
    return _call_groq(client, system, user, max_tokens=30)


# ─── Discord ──────────────────────────────────────────────────────────────────

def send_discord(message: str) -> None:
    """POST a message to the Discord #confirmations channel via webhook. Non-fatal."""
    if not DISCORD_WEBHOOK:
        print(f"[{datetime.now()}] Discord webhook not configured (DISCORD_WEBHOOK_CONFIRMATIONS). Skipping.")
        return
    try:
        import requests
        resp = requests.post(DISCORD_WEBHOOK, json={'content': message}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[{datetime.now()}] Discord notification failed (non-fatal): {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    from groq import Groq

    print(f"[{datetime.now()}] Starting weekly post generation (model: {GROQ_MODEL})")

    # ── Guard: skip if week already has 5 posts ──────────────────────────────
    existing = sheets.get_week_schedule(SPREADSHEET_ID)
    already_scheduled = [r for r in existing if r.get('status') in ('pending', 'approved', 'published')]
    if len(already_scheduled) >= 5:
        print(
            f"[{datetime.now()}] Week already has {len(already_scheduled)} scheduled posts. "
            "Skipping to prevent double generation."
        )
        return

    # ── Load pending topics ───────────────────────────────────────────────────
    pending = sheets.get_topic_bank(SPREADSHEET_ID)
    if not pending:
        msg = "Weekly generation skipped: no pending topics in Topic Bank. Add topics and re-run."
        print(f"[{datetime.now()}] {msg}")
        send_discord(msg)
        return

    weekdays = get_next_weekdays()
    topics_to_use = pending[:5]

    if len(topics_to_use) < 5:
        missing = 5 - len(topics_to_use)
        print(
            f"[{datetime.now()}] Warning: only {len(topics_to_use)} pending topics available. "
            f"{missing} weekday slot(s) will not be filled this week."
        )

    groq_client = Groq(api_key=os.environ['GROQ_API_KEY'])
    successes = 0
    failures = 0

    for i, topic_row in enumerate(topics_to_use):
        scheduled_date = weekdays[i]
        weekday_num = scheduled_date.weekday()  # 0=Mon, ..., 4=Fri
        audience, category = resolve_audience_and_category(weekday_num, topic_row.get('category', ''))

        topic = topic_row.get('topic', '')
        key_details = topic_row.get('key_details', '')
        row_index = topic_row['_row_index']

        print(
            f"[{datetime.now()}] Post {i + 1}/{len(topics_to_use)}: "
            f"'{topic}' | {scheduled_date.strftime('%A %Y-%m-%d')} | "
            f"category={category} | audience={audience}"
        )

        try:
            # ── Call 1: post text ─────────────────────────────────────────────
            post_text = generate_post_text(groq_client, topic, key_details, category)
            print(f"[{datetime.now()}]   Post text generated ({len(post_text)} chars)")

            # ── Call 2: bullet points ─────────────────────────────────────────
            try:
                bullet_points = extract_bullet_points(groq_client, post_text)
                print(f"[{datetime.now()}]   Bullet points: {bullet_points[:80]}...")
            except (json.JSONDecodeError, Exception) as e:
                print(f"[{datetime.now()}]   Bullet extraction failed (non-fatal): {e}")
                bullet_points = ''

            # ── Call 3: headline ──────────────────────────────────────────────
            try:
                headline = extract_headline(groq_client, post_text)
                print(f"[{datetime.now()}]   Headline: '{headline}'")
            except Exception as e:
                print(f"[{datetime.now()}]   Headline extraction failed (non-fatal): {e}")
                headline = ''

            # ── Write back to Sheets ──────────────────────────────────────────
            sheets.update_row_status(SPREADSHEET_ID, row_index, {
                'post_text': post_text,
                'bullet_points': bullet_points,
                'headline': headline,
                'audience': audience,
                'status': 'pending',
                'scheduled_date': scheduled_date.strftime('%Y-%m-%d'),
            })
            print(f"[{datetime.now()}]   Sheets row {row_index} updated — scheduled {scheduled_date}")
            successes += 1

        except Exception as e:
            print(f"[{datetime.now()}] FAILED for '{topic}': {e}")
            failures += 1

        # Brief pause between posts to respect Groq rate limits
        if i < len(topics_to_use) - 1:
            time.sleep(2)

    # ── Summary ───────────────────────────────────────────────────────────────
    summary_line = f"{successes}/{len(topics_to_use)} posts generated. {failures} failed."
    print(f"[{datetime.now()}] Generation complete. {summary_line}")

    week_label = weekdays[0].strftime('%b %d') if weekdays else '?'
    send_discord(
        f"Weekly content generation complete (week of {week_label}).\n"
        f"{summary_line}\n"
        "Posts are in Sheets with status=pending. Review and set to 'approved' before Monday."
    )


if __name__ == '__main__':
    main()
