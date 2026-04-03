"""
weekly_digest.py — Sunday analytics digest sender.

Queries Supabase for posts published in the last 7 days, calculates engagement
metrics, compares to the previous week (week-over-week delta), and sends a
formatted summary to Discord #digest via webhook.

Also attempts to update config.top_performers in Supabase for use by the
generation pipeline (non-fatal — skipped if config table doesn't exist yet).

Called by: .github/workflows/weekly_digest.yml
Runs at 10 AM PKT (05:00 UTC) every Sunday — before weekly_generation.yml.
Depends on: tools/db_client.py
Env vars: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, DISCORD_DIGEST_WEBHOOK_URL

NOTE on analytics columns: The posts table needs impressions, likes, comments,
shares columns (see weekly_digest.md). Until the analytics sync job is built,
these will be 0 and the digest will note that analytics are not yet configured.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytz
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.db_client as db

load_dotenv()

PKT = pytz.timezone('Asia/Karachi')
DISCORD_DIGEST_WEBHOOK = os.getenv('DISCORD_DIGEST_WEBHOOK_URL', '')


# ── Discord ────────────────────────────────────────────────────────────────────

def send_digest(content: str) -> None:
    """POST digest message to Discord #digest via webhook."""
    if not DISCORD_DIGEST_WEBHOOK:
        print('[WARN] DISCORD_DIGEST_WEBHOOK_URL not configured — printing to stdout only.')
        print(content)
        return
    try:
        resp = requests.post(DISCORD_DIGEST_WEBHOOK, json={'content': content}, timeout=10)
        resp.raise_for_status()
        print(f'[{datetime.now()}] Digest sent to Discord.')
    except Exception as e:
        print(f'[ERROR] Discord webhook failed: {e}')
        print(content)  # Still log to stdout


# ── Metrics helpers ────────────────────────────────────────────────────────────

def engagement_score(post: dict) -> int:
    """Weighted engagement: likes + comments*2 + shares*3."""
    likes = post.get('likes', 0) or 0
    comments = post.get('comments', 0) or 0
    shares = post.get('shares', 0) or 0
    return likes + comments * 2 + shares * 3


def post_first_line(post: dict) -> str:
    """Return first 100 characters of post content."""
    content = post.get('content', '') or ''
    first_line = content.split('\n')[0][:100]
    return first_line + ('...' if len(content.split('\n')[0]) > 100 else '')


def format_delta(this_val: float, prev_val: float) -> str:
    """Format a week-over-week percentage delta string."""
    if prev_val == 0:
        return '—'
    delta = ((this_val - prev_val) / prev_val) * 100
    return f'{delta:+.1f}%'


def format_impression_delta(this_val: int, prev_val: int) -> str:
    """Format a raw impressions delta string."""
    if prev_val == 0:
        return '—'
    delta = this_val - prev_val
    return f'{delta:+,}'


# ── Query helpers ──────────────────────────────────────────────────────────────

def query_posts_with_retry(cutoff_start: str, cutoff_end: str | None = None) -> list[dict]:
    """
    Query published posts between cutoff_start and optional cutoff_end.
    Retries once after 10 seconds on failure.
    Raises on second failure.
    """
    for attempt in range(2):
        try:
            q = (
                db.get_client()
                .table('posts')
                .select('id, content, audience_type, linkedin_post_id, published_at, '
                        'impressions, likes, comments, shares')
                .eq('status', 'published')
                .gte('published_at', cutoff_start)
            )
            if cutoff_end:
                q = q.lt('published_at', cutoff_end)
            return (q.execute()).data or []
        except Exception as e:
            if attempt == 0:
                print(f'[WARN] Supabase query failed, retrying in 10s: {e}')
                time.sleep(10)
            else:
                raise


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    now_utc = datetime.now(timezone.utc)
    now_pkt = datetime.now(PKT)

    # Date range labels (PKT)
    week_end = now_pkt.date()
    week_start = week_end - timedelta(days=6)
    week_label = f'{week_start.strftime("%b %d")} to {week_end.strftime("%b %d")}'
    next_mon = (week_end + timedelta(days=1)).strftime('%b %d')
    next_fri = (week_end + timedelta(days=5)).strftime('%b %d')

    seven_days_ago = (now_utc - timedelta(days=7)).isoformat()
    fourteen_days_ago = (now_utc - timedelta(days=14)).isoformat()

    print(f'[{datetime.now()}] Starting weekly digest ({week_label})')

    # Query this week + previous week
    try:
        this_week = query_posts_with_retry(seven_days_ago)
        prev_week = query_posts_with_retry(fourteen_days_ago, seven_days_ago)
    except Exception as e:
        send_digest(
            f'⚠️ Could not fetch analytics from Supabase. Digest skipped this week.\nError: {e}'
        )
        sys.exit(1)

    # ── No posts this week ─────────────────────────────────────────────────────
    if not this_week:
        msg_lines = [
            f'📊 **Weekly Digest — {week_label}**',
            '',
            'No posts published this week.',
            'Check Sheets — were posts approved before publish time?',
        ]
        if prev_week:
            prev_scores = [engagement_score(p) for p in prev_week]
            prev_avg = sum(prev_scores) / len(prev_scores)
            prev_impressions = sum(p.get('impressions', 0) or 0 for p in prev_week)
            msg_lines += [
                '',
                f'Last week: {len(prev_week)} posts | avg score {prev_avg:.1f} | '
                f'{prev_impressions:,} impressions',
            ]
        send_digest('\n'.join(msg_lines))
        print(f'[{datetime.now()}] No posts this week — minimal digest sent.')
        return

    # ── Calculate this week metrics ────────────────────────────────────────────
    total_posts = len(this_week)
    total_impressions = sum(p.get('impressions', 0) or 0 for p in this_week)
    total_likes = sum(p.get('likes', 0) or 0 for p in this_week)
    total_comments_count = sum(p.get('comments', 0) or 0 for p in this_week)
    total_shares = sum(p.get('shares', 0) or 0 for p in this_week)

    scores = [engagement_score(p) for p in this_week]
    avg_score = sum(scores) / total_posts

    avg_engagement_pct = (sum(scores) / total_impressions * 100) if total_impressions > 0 else 0.0

    sorted_posts = sorted(this_week, key=engagement_score, reverse=True)
    best_post = sorted_posts[0]
    worst_post = sorted_posts[-1]

    # ── Week-over-week deltas ──────────────────────────────────────────────────
    impressions_delta_str = '—'
    engagement_delta_str = '—'

    if prev_week:
        prev_impressions = sum(p.get('impressions', 0) or 0 for p in prev_week)
        prev_scores = [engagement_score(p) for p in prev_week]
        prev_avg_score = sum(prev_scores) / len(prev_week)

        impressions_delta_str = format_impression_delta(total_impressions, prev_impressions)
        engagement_delta_str = format_delta(avg_score, prev_avg_score)

    # ── LinkedIn post URLs ─────────────────────────────────────────────────────
    def li_url(post: dict) -> str:
        li_id = post.get('linkedin_post_id', '')
        return f'https://www.linkedin.com/feed/update/{li_id}/' if li_id else '(no URL)'

    # ── Analytics note ─────────────────────────────────────────────────────────
    analytics_note = (
        '\n\nℹ️ Analytics sync not yet configured. Impressions/engagement showing 0.'
        if total_impressions == 0 else ''
    )

    # ── Build message ──────────────────────────────────────────────────────────
    msg = (
        f'📊 **Weekly LinkedIn Digest — {week_label}**\n\n'
        f'**Posts Published:** {total_posts} / 5 expected\n'
        f'**Total Impressions:** {total_impressions:,} ({impressions_delta_str} WoW)\n'
        f'**Likes:** {total_likes} | **Comments:** {total_comments_count} | **Shares:** {total_shares}\n'
        f'**Avg Engagement Score:** {avg_score:.1f} ({engagement_delta_str} WoW)\n\n'
        f'🏆 **Best Post**\n'
        f'{post_first_line(best_post)}\n'
        f'Score: {engagement_score(best_post)} | '
        f'Impressions: {best_post.get("impressions", 0) or 0:,}\n'
        f'{li_url(best_post)}\n\n'
        f'📉 **Weakest Post**\n'
        f'{post_first_line(worst_post)}\n'
        f'Score: {engagement_score(worst_post)} | '
        f'Impressions: {worst_post.get("impressions", 0) or 0:,}\n'
        f'{li_url(worst_post)}\n\n'
        f'📅 **Next week:** {next_mon} – {next_fri}\n'
        f'Check Sheets to approve pending posts before Monday 12pm PKT.'
        f'{analytics_note}'
    )

    # Enforce Discord's 2,000 character limit
    if len(msg) > 2000:
        msg = msg[:1997] + '...'

    send_digest(msg)

    # ── Update top_performers in config table (non-fatal) ─────────────────────
    try:
        top3 = sorted_posts[:3]
        db.get_client().table('config').upsert(
            {
                'key': 'top_performers',
                'value': json.dumps([
                    {
                        'content_preview': (p.get('content', '') or '')[:200],
                        'audience_type': p.get('audience_type', ''),
                        'engagement_score': engagement_score(p),
                        'linkedin_post_id': p.get('linkedin_post_id', ''),
                    }
                    for p in top3
                ]),
                'updated_at': datetime.utcnow().isoformat(),
            },
            on_conflict='key',
        ).execute()
        print(f'[{datetime.now()}] config.top_performers updated.')
    except Exception as e:
        print(f'[WARN] config table update failed (non-fatal — table may not exist yet): {e}')

    print(f'[{datetime.now()}] Weekly digest complete.')


if __name__ == '__main__':
    main()
