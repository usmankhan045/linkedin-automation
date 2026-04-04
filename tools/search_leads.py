"""
search_leads.py — Lead hunter for AI automation opportunities on LinkedIn.

Runs 16 targeted Google searches (8 per category) to find LinkedIn posts from
business owners either asking for automation/AI help or describing problems that
automation could solve.

For each new lead found (not yet in leads_seen):
  1. Calls Groq once to classify quality (HIGH/MEDIUM/LOW) and generate a
     ready-to-paste comment reply + DM.
  2. Records the lead in Supabase leads_seen for deduplication.
  3. Sends a Discord alert to DISCORD_LEADS_WEBHOOK_URL for HIGH/MEDIUM only.
  LOW quality leads are recorded but no alert is sent.

Runs twice daily Mon–Fri via lead_hunter.yml.
Error philosophy: best-effort, never crash the GitHub Actions run.

Called by: .github/workflows/lead_hunter.yml
Env vars: GROQ_API_KEY, GROQ_MODEL, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
          DISCORD_LEADS_WEBHOOK_URL
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup
from groq import Groq
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

GROQ_MODEL = os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')
DISCORD_LEADS_WEBHOOK = os.getenv('DISCORD_LEAD_HUNTER_WEBHOOK_URL', '')

DISCORD_MAX_LEN = 2000


# ── Supabase ───────────────────────────────────────────────────────────────────

def get_supabase_client():
    url = os.environ['SUPABASE_URL']
    key = os.environ['SUPABASE_SERVICE_ROLE_KEY']
    return create_client(url, key)


def is_already_seen(supabase_client, url: str) -> bool:
    try:
        result = supabase_client.table('leads_seen').select('id').eq('post_url', url).execute()
        return len(result.data) > 0
    except Exception:
        return False


def mark_as_seen(supabase_client, url: str, poster_name: str, snippet: str, query: str, category: str) -> None:
    try:
        supabase_client.table('leads_seen').insert({
            'post_url': url,
            'poster_name': poster_name,
            'post_snippet': snippet[:500],
            'search_query': query,
            'category': category,
            'discord_alerted': False,
        }).execute()
    except Exception as e:
        print(f'[WARN] Failed to mark lead as seen: {e}')


# ── Google Search ──────────────────────────────────────────────────────────────

def get_yesterday_date() -> str:
    """Return yesterday's date in YYYY-MM-DD format for Google date filter."""
    return (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')


def google_search(query: str, num_results: int = 5) -> list[dict]:
    """
    Search Google using a scraping approach.
    Returns list of dicts with: url, title, snippet, search_query.
    Returns [] on failure — never raises.
    """
    params = {
        'q': query + ' after:' + get_yesterday_date(),
        'num': num_results,
        'hl': 'en',
        'gl': 'us',
    }

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
    }

    url = 'https://www.google.com/search?' + urlencode(params)

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 429:
            print(f'[WARN] Google rate limited. Waiting 60s...')
            time.sleep(60)
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 429:
                print(f'[WARN] Google still rate limited after retry. Skipping remaining queries for this run.')
                return []
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print(f'[WARN] Google HTTP error for query "{query[:60]}": {e}')
        return []
    except Exception as e:
        print(f'[WARN] Google search failed for query "{query[:60]}": {e}')
        return []

    results = []
    try:
        soup = BeautifulSoup(resp.text, 'html.parser')

        for g in soup.find_all('div', class_='g')[:num_results]:
            anchor = g.find('a')
            if not anchor:
                continue
            link = anchor.get('href', '')
            if 'linkedin.com' not in link:
                continue
            if 'linkedin.com/posts' not in link and 'linkedin.com/feed' not in link:
                continue

            title_tag = g.find('h3')
            title = title_tag.get_text() if title_tag else ''

            snippet = ''
            snippet_div = g.find('div', {'data-sncf': True}) or g.find('span', class_='aCOpRe')
            if snippet_div:
                snippet = snippet_div.get_text()
            else:
                for sel in ['.VwiC3b', '.s3v9rd', '.st']:
                    s = g.select_one(sel)
                    if s:
                        snippet = s.get_text()
                        break

            results.append({
                'url': link,
                'title': title,
                'snippet': snippet,
                'search_query': query,
            })
    except Exception as e:
        print(f'[WARN] Failed to parse Google results for query "{query[:60]}": {e}')
        return []

    return results


# ── Groq Lead Analysis ─────────────────────────────────────────────────────────

LEAD_ANALYSIS_PROMPT = """You are helping Muhammad Usman, an AI Automation Engineer from Pakistan, identify and respond to potential leads on LinkedIn.

Here is a LinkedIn post snippet found via search:
URL: {url}
Title: {title}
Snippet: {snippet}
Search query that found it: {query}

TASK 1 — QUALIFY:
Is this a genuine potential lead for AI automation services? Score it:
- HIGH: Person is clearly asking for automation help, has a business problem automation could solve, or is decision-maker expressing frustration with manual processes
- MEDIUM: Person mentions automation/AI challenges but not clearly seeking services, or context is unclear
- LOW: Not relevant, just using keywords casually, student/researcher, or clearly not a business buyer

TASK 2 — EXTRACT:
From the snippet, extract:
- poster_name: The person's name if visible, otherwise "LinkedIn User"
- business_context: What their business/role appears to be (1 sentence, "Unknown" if not clear)
- pain_point: The specific problem or need they mentioned (1 sentence)

TASK 3 — GENERATE COMMENT REPLY:
Write a comment reply Muhammad could post on their LinkedIn post.
Rules:
- 2-3 sentences maximum
- Acknowledge their specific situation (reference what they actually said)
- Position Muhammad as someone who has solved this exact type of problem
- End with a soft question or invitation to connect, NOT a hard sell
- Warm, peer-to-peer tone — not salesy
- Do NOT mention prices, packages, or services explicitly
- Sound like a human who genuinely wants to help, not a bot

TASK 4 — GENERATE DM:
Write a LinkedIn DM Muhammad could send if he connects with this person.
Rules:
- 4-5 sentences maximum
- Reference their specific post (show you actually read it)
- Share one specific relevant experience or result Muhammad has achieved
- Ask ONE qualifying question about their situation
- Warm, conversational, zero pressure
- End with a clear but soft next step

Return ONLY valid JSON, no markdown, no explanation:
{{
  "quality": "HIGH" | "MEDIUM" | "LOW",
  "quality_reason": "one sentence explaining why",
  "poster_name": "extracted name or LinkedIn User",
  "business_context": "one sentence",
  "pain_point": "one sentence",
  "comment_reply": "ready to paste comment",
  "dm": "ready to paste DM"
}}"""


def analyze_lead(groq_client: Groq, result: dict, query: str) -> dict | None:
    """
    Call Groq to classify lead quality and generate reply + DM in one shot.
    Returns parsed dict on success, None on failure.
    """
    prompt = LEAD_ANALYSIS_PROMPT.format(
        url=result.get('url', ''),
        title=result.get('title', ''),
        snippet=result.get('snippet', ''),
        query=query,
    )

    for attempt in range(2):
        try:
            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.4,
                max_tokens=1000,
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

    print(f'[WARN] Groq analysis failed for {result.get("url", "unknown")}. Skipping alert.')
    return None


# ── Discord ────────────────────────────────────────────────────────────────────

def format_discord_alert(result: dict, analysis: dict, category: str) -> str:
    quality_emoji = '🔥' if analysis['quality'] == 'HIGH' else '👀'
    category_label = 'ASKING FOR HELP' if category == 'asking_for_help' else 'HAS A PROBLEM TO SOLVE'

    message = (
        f"{quality_emoji} **NEW LEAD — {analysis['quality']} QUALITY**\n"
        f"**Type:** {category_label}\n"
        f"**Who:** {analysis['poster_name']}\n"
        f"**Context:** {analysis['business_context']}\n"
        f"**Their pain:** {analysis['pain_point']}\n"
        f"**Post:** {result['url']}\n"
        f"\n---\n"
        f"**COMMENT REPLY (paste on their post):**\n"
        f"{analysis['comment_reply']}\n"
        f"\n---\n"
        f"**DM (send after connecting):**\n"
        f"{analysis['dm']}\n"
        f"\n---\n"
        f'*Found via: "{result["search_query"]}"*'
    )

    # Discord 2000 char limit — truncate DM field if needed
    if len(message) > DISCORD_MAX_LEN:
        overflow = len(message) - DISCORD_MAX_LEN + len('... [truncated]')
        truncated_dm = analysis['dm'][:-overflow] + '... [truncated]'
        message = (
            f"{quality_emoji} **NEW LEAD — {analysis['quality']} QUALITY**\n"
            f"**Type:** {category_label}\n"
            f"**Who:** {analysis['poster_name']}\n"
            f"**Context:** {analysis['business_context']}\n"
            f"**Their pain:** {analysis['pain_point']}\n"
            f"**Post:** {result['url']}\n"
            f"\n---\n"
            f"**COMMENT REPLY (paste on their post):**\n"
            f"{analysis['comment_reply']}\n"
            f"\n---\n"
            f"**DM (send after connecting):**\n"
            f"{truncated_dm}\n"
            f"\n---\n"
            f'*Found via: "{result["search_query"]}"*'
        )

    return message


def send_discord_alert(message: str) -> None:
    if not DISCORD_LEADS_WEBHOOK:
        print('[WARN] DISCORD_LEAD_HUNTER_WEBHOOK_URL not set. Skipping alert.')
        return
    try:
        requests.post(DISCORD_LEADS_WEBHOOK, json={'content': message}, timeout=10)
    except Exception as e:
        print(f'[WARN] Discord webhook failed: {e}')


# ── Main ───────────────────────────────────────────────────────────────────────

ALL_QUERIES = [
    # (query, category)
    ('site:linkedin.com "looking for someone to automate"', 'asking_for_help'),
    ('site:linkedin.com "need help automating"', 'asking_for_help'),
    ('site:linkedin.com "can anyone recommend automation"', 'asking_for_help'),
    ('site:linkedin.com "need a developer to build"', 'asking_for_help'),
    ('site:linkedin.com "looking for an AI solution for"', 'asking_for_help'),
    ('site:linkedin.com "hire someone to automate"', 'asking_for_help'),
    ('site:linkedin.com "anyone built an automation for"', 'asking_for_help'),
    ('site:linkedin.com "need an AI agent to"', 'asking_for_help'),
    ('site:linkedin.com "doing this manually is killing"', 'describing_problem'),
    ('site:linkedin.com "wasting hours every week on"', 'describing_problem'),
    ('site:linkedin.com "our team spends too much time"', 'describing_problem'),
    ('site:linkedin.com "still using spreadsheets for"', 'describing_problem'),
    ('site:linkedin.com "copy pasting data every"', 'describing_problem'),
    ('site:linkedin.com "wish there was a way to automate"', 'describing_problem'),
    ('site:linkedin.com "manually entering data"', 'describing_problem'),
    ('site:linkedin.com "takes us hours to"', 'describing_problem'),
]


def main():
    print(f'[{datetime.now()}] Starting lead hunter')

    supabase_client = get_supabase_client()
    groq_client = Groq(api_key=os.environ['GROQ_API_KEY'])

    total_found = 0
    total_new = 0
    total_alerted = 0
    google_blocked = False

    for query, category in ALL_QUERIES:
        if google_blocked:
            print(f'[{datetime.now()}] Google blocked — skipping remaining queries.')
            break

        print(f'[{datetime.now()}] Searching: {query[:70]}')

        results = google_search(query, num_results=5)

        # Detect persistent block (empty return after 429 retry inside google_search)
        # google_search already handles one 60s retry; if still empty we continue.
        total_found += len(results)

        if not results:
            print(f'[{datetime.now()}] No results returned for this query.')

        for result in results:
            url = result['url']

            if is_already_seen(supabase_client, url):
                continue

            total_new += 1
            print(f'[{datetime.now()}] New lead found: {url[:80]}')

            analysis = analyze_lead(groq_client, result, query)

            if not analysis:
                mark_as_seen(supabase_client, url, 'Unknown', result.get('snippet', ''), query, category)
                continue

            mark_as_seen(
                supabase_client,
                url,
                analysis.get('poster_name', 'Unknown'),
                result.get('snippet', ''),
                query,
                category,
            )

            if analysis['quality'] in ('HIGH', 'MEDIUM'):
                message = format_discord_alert(result, analysis, category)
                send_discord_alert(message)
                total_alerted += 1
                print(f'[{datetime.now()}] Discord alert sent — {analysis["quality"]} quality lead')
            else:
                print(f'[{datetime.now()}] LOW quality — skipped alert')

            time.sleep(1)  # Rate limit between Groq calls

        time.sleep(3)  # Rate limit between Google searches

    print(
        f'[{datetime.now()}] Lead hunt complete. '
        f'Found={total_found}, New={total_new}, Alerted={total_alerted}'
    )


if __name__ == '__main__':
    main()
