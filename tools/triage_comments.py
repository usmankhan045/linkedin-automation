"""
triage_comments.py — LinkedIn comment classifier and router.

Fetches comments from all LinkedIn posts published in the last 14 days,
deduplicates against Supabase processed_comments table,
classifies each comment via Groq in batches of 10 (A=lead, B=content idea, C=noise),
and routes:
  Category A → Discord #leads webhook with drafted DM reply
  Category B → Google Sheets Content Backlog tab
  Category C → logged in processed_comments, no further action

If any A or B found: sends summary to Discord #confirmations.

Runs every 2 hours Mon–Fri 9 AM–7 PM PKT via comment_triage.yml.

Called by: .github/workflows/comment_triage.yml
Depends on: tools/db_client.py, tools/sheets_helper.py
Env vars: LINKEDIN_ACCESS_TOKEN, GROQ_API_KEY, GOOGLE_SHEETS_SPREADSHEET_ID,
          DISCORD_LEADS_WEBHOOK_URL, DISCORD_WEBHOOK_CONFIRMATIONS
"""

import os
import sys
import json
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests
from groq import Groq
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.db_client as db
import tools.sheets_helper as sheets

load_dotenv()

LINKEDIN_ACCESS_TOKEN = os.getenv('LINKEDIN_ACCESS_TOKEN', '')
GROQ_MODEL = os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')
SPREADSHEET_ID = os.environ['GOOGLE_SHEETS_SPREADSHEET_ID']
DISCORD_LEADS_WEBHOOK = os.getenv('DISCORD_LEADS_WEBHOOK_URL', '')
DISCORD_CONFIRMATIONS_WEBHOOK = os.getenv('DISCORD_WEBHOOK_CONFIRMATIONS', '')

BATCH_SIZE = 10
LOOKBACK_DAYS = 14


# ── LinkedIn API ───────────────────────────────────────────────────────────────

def get_recent_post_ids() -> list[str]:
    """Fetch linkedin_post_id for posts published in the last LOOKBACK_DAYS days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    result = (
        db.get_client()
        .table('posts')
        .select('linkedin_post_id')
        .eq('status', 'published')
        .gte('published_at', cutoff)
        .not_.is_('linkedin_post_id', 'null')
        .execute()
    )
    return [row['linkedin_post_id'] for row in result.data if row.get('linkedin_post_id')]


def fetch_linkedin_comments(post_id: str) -> list[dict]:
    """
    Fetch comments for a LinkedIn post via Social Actions API.

    Returns a list of dicts with keys: comment_id, author_name, comment_text.
    Returns [] on 403/404. Raises ValueError on 401 (token expired).
    """
    encoded_id = urllib.parse.quote(post_id, safe='')
    url = f'https://api.linkedin.com/v2/socialActions/{encoded_id}/comments'
    headers = {
        'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
        'LinkedIn-Version': '202401',
        'X-Restli-Protocol-Version': '2.0.0',
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as e:
        print(f'[WARN] Network error fetching comments for {post_id}: {e}')
        return []

    if resp.status_code == 401:
        raise ValueError('LinkedIn token expired or invalid (401)')
    if resp.status_code == 403:
        print(f'[WARN] LinkedIn API 403 for {post_id} — may need elevated API access.')
        return []
    if resp.status_code == 404:
        print(f'[WARN] LinkedIn API 404 for {post_id} — post not found or deleted.')
        return []

    resp.raise_for_status()

    comments = []
    for element in resp.json().get('elements', []):
        actor = element.get('actor~', {})
        first = actor.get('localizedFirstName', '')
        last = actor.get('localizedLastName', '')
        comment_text = element.get('message', {}).get('text', '').strip()
        comment_id = element.get('id', '')

        if comment_id and comment_text:
            comments.append({
                'comment_id': comment_id,
                'author_name': f'{first} {last}'.strip() or 'Unknown',
                'comment_text': comment_text,
            })

    return comments


# ── Deduplication ──────────────────────────────────────────────────────────────

def get_already_processed_ids(comment_ids: list[str]) -> set[str]:
    """Return the subset of comment_ids already in processed_comments."""
    if not comment_ids:
        return set()
    result = (
        db.get_client()
        .table('processed_comments')
        .select('linkedin_comment_id')
        .in_('linkedin_comment_id', comment_ids)
        .execute()
    )
    return {row['linkedin_comment_id'] for row in result.data}


# ── Groq Classification ────────────────────────────────────────────────────────

CLASSIFY_SYSTEM = (
    'You are classifying LinkedIn comments for Muhammad Usman, an AI Automation Engineer from Pakistan.\n\n'
    'Classify each comment into exactly one category:\n'
    '- A (Lead): Shows buying intent, asks for help with AI/automation, asks about services, '
    'wants to hire, or mentions a specific business problem they want solved.\n'
    '- B (Content Idea): Asks a question Muhammad could answer in a future post, shares an '
    'interesting counterpoint, or mentions a use case worth exploring.\n'
    '- C (Noise): Generic praise ("Great post!", "Insightful"), emoji-only, CFBR, spam, or off-topic.\n\n'
    'For category A: also write a drafted_reply — a 3-4 sentence LinkedIn DM opening '
    'Muhammad could send, conversational, references what they said specifically.\n\n'
    'Return ONLY valid JSON — a JSON array matching input order. No markdown, no explanation.'
)

CLASSIFY_USER_TEMPLATE = (
    'Comments to classify:\n{comments_json}\n\n'
    'Return ONLY a JSON array:\n'
    '[{{"id": "...", "category": "A|B|C", "reason": "one sentence", '
    '"drafted_reply": "3-4 sentence DM if A, else null"}}]'
)


def classify_batch(groq_client: Groq, comments: list[dict]) -> list[dict]:
    """
    Classify a batch of up to BATCH_SIZE comments via Groq.

    Returns a list of result dicts with keys: id, category, reason, drafted_reply.
    Falls back to category=C for all if classification fails both attempts.
    """
    comments_json = json.dumps([
        {'id': c['comment_id'], 'author': c['author_name'], 'text': c['comment_text']}
        for c in comments
    ], ensure_ascii=False)

    for attempt in range(2):
        try:
            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {'role': 'system', 'content': CLASSIFY_SYSTEM},
                    {'role': 'user', 'content': CLASSIFY_USER_TEMPLATE.format(comments_json=comments_json)},
                ],
                temperature=0.3,
                max_tokens=1500,
            )
            raw = response.choices[0].message.content.strip()

            # Strip accidental markdown fences
            if raw.startswith('```'):
                lines = raw.splitlines()
                raw = '\n'.join(lines[1:-1] if lines[-1].strip() == '```' else lines[1:]).strip()

            return json.loads(raw)

        except json.JSONDecodeError as e:
            print(f'[WARN] Groq JSON parse error (attempt {attempt + 1}): {e}')
            if attempt == 1:
                break
        except Exception as e:
            err_str = str(e).lower()
            if '429' in err_str or 'rate_limit' in err_str:
                wait = 10 if attempt == 0 else 30
                print(f'[WARN] Groq rate limit. Waiting {wait}s...')
                time.sleep(wait)
            else:
                print(f'[WARN] Groq error (attempt {attempt + 1}): {e}')
                if attempt == 1:
                    break

    # Fallback: mark all as C
    print(f'[WARN] Classification failed for batch of {len(comments)}. Marking all as C.')
    return [{'id': c['comment_id'], 'category': 'C', 'reason': 'classification failed', 'drafted_reply': None}
            for c in comments]


# ── Discord ────────────────────────────────────────────────────────────────────

def send_webhook(webhook_url: str, content: str) -> None:
    if not webhook_url:
        return
    try:
        requests.post(webhook_url, json={'content': content}, timeout=10)
    except Exception as e:
        print(f'[WARN] Discord webhook failed: {e}')


# ── Supabase Write ─────────────────────────────────────────────────────────────

def insert_processed(rows: list[dict]) -> None:
    """Upsert processed comment rows. ON CONFLICT (linkedin_comment_id) DO NOTHING."""
    if not rows:
        return
    try:
        db.get_client().table('processed_comments').upsert(
            rows, on_conflict='linkedin_comment_id'
        ).execute()
    except Exception as e:
        print(f'[WARN] processed_comments insert error: {e}')


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f'[{datetime.now()}] Starting comment triage')
    groq_client = Groq(api_key=os.environ['GROQ_API_KEY'])
    counts = {'A': 0, 'B': 0, 'C': 0}

    # Step 1: get recent post IDs
    post_ids = get_recent_post_ids()
    if not post_ids:
        print(f'[{datetime.now()}] No published posts in last {LOOKBACK_DAYS} days. Exiting.')
        return

    print(f'[{datetime.now()}] Checking comments on {len(post_ids)} posts.')

    for post_id in post_ids:
        print(f'[{datetime.now()}] Fetching comments: {post_id}')

        try:
            all_comments = fetch_linkedin_comments(post_id)
        except ValueError as e:
            # Token expired — halt entire run
            send_webhook(DISCORD_CONFIRMATIONS_WEBHOOK, f'🔴 {e} — comment triage paused.')
            sys.exit(1)

        if not all_comments:
            continue

        # Deduplicate
        existing = get_already_processed_ids([c['comment_id'] for c in all_comments])
        new_comments = [c for c in all_comments if c['comment_id'] not in existing]

        if not new_comments:
            print(f'[{datetime.now()}]   All {len(all_comments)} comments already processed.')
            continue

        print(f'[{datetime.now()}]   {len(new_comments)} new comments to classify.')

        # Classify in batches
        for i in range(0, len(new_comments), BATCH_SIZE):
            batch = new_comments[i:i + BATCH_SIZE]
            results = classify_batch(groq_client, batch)
            result_map = {r['id']: r for r in results}

            to_insert = []
            for comment in batch:
                result = result_map.get(comment['comment_id'], {
                    'category': 'C', 'reason': 'no result returned', 'drafted_reply': None
                })
                category = result.get('category', 'C')
                reason = result.get('reason', '')
                drafted_reply = result.get('drafted_reply')

                if category == 'A':
                    counts['A'] += 1
                    msg = (
                        f'🔥 **New Lead — {comment["author_name"]}**\n'
                        f'Comment: "{comment["comment_text"][:300]}"\n\n'
                        f'**Drafted DM reply:**\n{drafted_reply or "(no draft generated)"}\n\n'
                        f'_Copy, tweak, send on LinkedIn._'
                    )
                    send_webhook(DISCORD_LEADS_WEBHOOK, msg)

                elif category == 'B':
                    counts['B'] += 1
                    try:
                        sheets.append_content_backlog(SPREADSHEET_ID, {
                            'comment_text': comment['comment_text'],
                            'commenter_name': comment['author_name'],
                            'original_post_topic': post_id,
                            'suggested_angle': reason,
                        })
                    except Exception as e:
                        print(f'[WARN] Sheets backlog append failed: {e}')

                else:
                    counts['C'] += 1

                to_insert.append({
                    'linkedin_comment_id': comment['comment_id'],
                    'linkedin_post_id': post_id,
                    'author_name': comment['author_name'],
                    'comment_text': comment['comment_text'][:500],
                    'category': category,
                })

            insert_processed(to_insert)
            time.sleep(1)  # rate limit buffer between batches

    total = counts['A'] + counts['B'] + counts['C']
    summary = f'Processed {total} comments: A={counts["A"]}, B={counts["B"]}, C={counts["C"]}'
    print(f'[{datetime.now()}] {summary}')

    if counts['A'] > 0 or counts['B'] > 0:
        send_webhook(
            DISCORD_CONFIRMATIONS_WEBHOOK,
            f'🔍 Comment triage — {datetime.now().strftime("%Y-%m-%d %H:%M PKT")}\n{summary}'
        )


if __name__ == '__main__':
    main()
