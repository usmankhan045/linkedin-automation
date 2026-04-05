"""
search_leads.py — Lead hunter for AI automation opportunities on LinkedIn.

Runs 4 targeted Boolean searches via the Apify LinkedIn Post Search Scraper
to find LinkedIn posts from business owners either asking for automation/AI
help or describing problems that automation could solve.

For each new lead found (not yet in leads_seen):
  1. Calls Groq once to classify quality (HIGH/MEDIUM/LOW) and generate a
     ready-to-paste comment reply + DM.
  2. Records the lead in Supabase leads_seen for deduplication.
  3. Sends a Discord alert to DISCORD_LEADS_WEBHOOK_URL for HIGH/MEDIUM only.
  LOW quality leads are recorded but no alert is sent.

Runs twice daily Mon–Fri via lead_hunter.yml.
Error philosophy: best-effort, never crash the GitHub Actions run.

Called by: .github/workflows/lead_hunter.yml
Env vars: APIFY_API_TOKEN, GROQ_API_KEY, GROQ_MODEL, SUPABASE_URL,
          SUPABASE_SERVICE_ROLE_KEY, DISCORD_LEADS_WEBHOOK_URL
"""

import os
import sys
import json
import time
from datetime import datetime, timezone

import requests
from groq import Groq
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

APIFY_API_TOKEN = os.environ['APIFY_API_TOKEN']
APIFY_ACTOR_URL = 'https://api.apify.com/v2/acts/harvestapi~linkedin-post-search/run-sync-get-dataset-items'
GROQ_MODEL = os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')
DISCORD_LEADS_WEBHOOK = os.getenv('DISCORD_LEAD_HUNTER_WEBHOOK_URL', '')
MAX_POSTS_PER_QUERY = 5
POSTED_LIMIT = '24h'

SEARCH_QUERIES = [
    # ── First-person pain point: manual / repetitive work ──────────────────────
    {
        "query": "I spend hours every week manually copying data",
        "category": "describing_problem",
        "label": "Manual data entry pain (first-person)"
    },
    {
        "query": "our team is still doing everything manually in spreadsheets",
        "category": "describing_problem",
        "label": "Still on spreadsheets (first-person)"
    },
    {
        "query": "I am drowning in repetitive tasks every day",
        "category": "describing_problem",
        "label": "Repetitive task overload"
    },
    {
        "query": "we keep copy pasting between systems all day",
        "category": "describing_problem",
        "label": "Copy-paste between systems"
    },
    # ── First-person asks for recommendation / help ────────────────────────────
    {
        "query": "can anyone recommend how to automate my workflow",
        "category": "asking_for_help",
        "label": "Asking community for automation advice"
    },
    {
        "query": "does anyone know how to automate this business process",
        "category": "asking_for_help",
        "label": "Asking how to automate process"
    },
    {
        "query": "I need someone to help me automate my business",
        "category": "asking_for_help",
        "label": "Directly seeking automation help"
    },
    # ── Frustration with current tools / processes ─────────────────────────────
    {
        "query": "I hate how much time I waste on admin tasks",
        "category": "describing_problem",
        "label": "Admin task frustration"
    },
    {
        "query": "our operations are a mess nothing is automated",
        "category": "describing_problem",
        "label": "Operational chaos"
    },
]


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


# ── Apify Search ───────────────────────────────────────────────────────────────

def run_apify_search(query_obj: dict) -> list[dict]:
    """
    Run one Apify LinkedIn Post Search actor call.
    Returns list of normalized post dicts with keys:
    url, content, poster_name, poster_headline, poster_profile_url, category, label
    """
    payload = {
        "searchQueries": [query_obj["query"]],
        "maxPosts": 5,
    }

    params = {"token": APIFY_API_TOKEN}

    try:
        print(f'[{datetime.now()}] Running Apify query: {query_obj["label"]}')
        print(f'[{datetime.now()}] Sending payload: {json.dumps(payload)}')
        resp = requests.post(
            APIFY_ACTOR_URL,
            json=payload,
            params=params,
            timeout=180,  # Apify runs can take up to 2 minutes
        )

        if resp.status_code == 402:
            print(f'[{datetime.now()}] [WARN] Apify credit limit reached. Stopping all queries.')
            return []

        if resp.status_code == 429:
            print(f'[{datetime.now()}] [WARN] Apify rate limited. Waiting 60s...')
            time.sleep(60)
            resp = requests.post(APIFY_ACTOR_URL, json=payload, params=params, timeout=180)

        resp.raise_for_status()
        raw_posts = resp.json()

        if not isinstance(raw_posts, list):
            print(f'[{datetime.now()}] [WARN] Apify returned unexpected format: {type(raw_posts)}')
            return []

        # Normalize the response
        normalized = []
        for post in raw_posts:
            author = post.get('author', {})
            url = post.get('linkedinUrl', '')
            content = post.get('content', '').strip()

            if not url or not content:
                continue

            normalized.append({
                'url': url,
                'content': content,
                'poster_name': author.get('name', 'LinkedIn User'),
                'poster_headline': author.get('info', ''),
                'poster_profile_url': author.get('linkedinUrl', ''),
                'category': query_obj['category'],
                'label': query_obj['label'],
            })

        print(f'[{datetime.now()}] Found {len(normalized)} posts for: {query_obj["label"]}')
        return normalized

    except requests.exceptions.Timeout:
        print(f'[{datetime.now()}] [WARN] Apify request timed out for query: {query_obj["label"]}')
        return []
    except requests.exceptions.HTTPError as e:
        try:
            error_body = e.response.text
        except Exception:
            error_body = 'could not read response body'
        print(f'[{datetime.now()}] [WARN] Apify 400 error body: {error_body}')
        print(f'[{datetime.now()}] [WARN] Apify search failed for "{query_obj["label"]}": {e}')
        return []
    except Exception as e:
        print(f'[{datetime.now()}] [WARN] Apify search failed for "{query_obj["label"]}": {e}')
        return []


# ── Groq Lead Analysis ─────────────────────────────────────────────────────────

LEAD_ANALYSIS_PROMPT = """You are helping Muhammad Usman, an AI Automation Engineer from Pakistan, identify and respond to potential leads on LinkedIn.

Here is a LinkedIn post found via keyword search:
URL: {url}
Poster name: {poster_name}
Poster headline: {poster_headline}
Post content: {content}
Search type that found it: {label}

TASK 1 — QUALIFY:
Is this a genuine potential lead for AI automation services? Score it:

IMMEDIATELY score LOW (do not proceed to HIGH/MEDIUM) if ANY of these are true:
- The post is a job posting or recruitment ad (e.g. "We're hiring", "We're looking for a Senior X", "Apply now", "Read the full JD", "DM me to apply")
- The post is from an AI company, software vendor, or automation agency promoting their own product or services
- The post appears to be AI-generated promotional content or a thought-leadership piece with no real personal pain
- The poster is clearly a recruiter, headhunter, or HR professional
- The post is about hiring/building a team rather than solving the poster's own operational problem
- The post is a general opinion piece about AI trends with no concrete personal business problem

Score HIGH if:
- A business owner, founder, or operator is expressing a specific, personal operational pain point that automation could solve
- Someone is directly asking for help automating their own workflow or process (first-person)
- A decision-maker is frustrated with manual work and appears open to solutions

Score MEDIUM if:
- A business professional is discussing pain points that suggest automation could help, but isn't directly asking for it
- Someone is asking for tool recommendations for their own use case

Score LOW if:
- None of the above apply, it's a student, researcher, or casual mention

TASK 2 — EXTRACT:
- business_context: What their business/role appears to be (1 sentence max, use poster_headline as context)
- pain_point: The specific problem or frustration they mentioned in the post (1 sentence, be specific)

TASK 3 — GENERATE COMMENT REPLY:
Write a comment reply Muhammad could post directly on their LinkedIn post.
Rules:
- 2-3 sentences maximum
- Reference something SPECIFIC from their post (show you read it)
- Position Muhammad naturally as someone who has solved this exact type of problem
- End with a soft open question or invitation, NOT a hard sell
- Sound like a genuine human response, not a sales pitch
- Do NOT mention prices, packages, or "services"
- Warm, peer-to-peer tone

TASK 4 — GENERATE DM:
Write a LinkedIn DM Muhammad could send after connecting with this person.
Rules:
- 4-5 sentences maximum
- Open by referencing their specific post (show you actually read it, quote a phrase if possible)
- Share one relevant result Muhammad has achieved (specific, not vague)
- Ask ONE qualifying question about their specific situation
- Zero pressure — end with a soft next step like "happy to share how I did it" or "would love to hear more about your situation"

Return ONLY valid JSON, no markdown, no preamble:
{{
  "quality": "HIGH" | "MEDIUM" | "LOW",
  "quality_reason": "one sentence",
  "business_context": "one sentence",
  "pain_point": "one sentence",
  "comment_reply": "ready to paste comment",
  "dm": "ready to paste DM"
}}"""


JOB_POST_SIGNALS = [
    "we're looking for a",
    "we are looking for a",
    "we're hiring",
    "we are hiring",
    "now hiring",
    "read the full jd",
    "full job description",
    "apply now",
    "dm me to apply",
    "send your cv",
    "send your resume",
    "job opening",
    "job opportunity",
    "open position",
    "join our team",
    "we need a senior",
    "we need an experienced",
    "looking for a senior",
    "looking for an experienced",
    "looking for someone who can",
    "hiring manager",
    "in the comments below",  # often recruitment CTA pattern
]


def is_job_post(content: str) -> bool:
    """Quick pre-filter: reject obvious job postings before spending a Groq call."""
    lower = content.lower()
    matched = [sig for sig in JOB_POST_SIGNALS if sig in lower]
    if matched:
        print(f'[{datetime.now()}] [FILTER] Rejected job post — matched: {matched[0]}')
        return True
    return False


def analyze_lead(groq_client: Groq, post: dict) -> dict | None:
    """
    Call Groq to classify lead quality and generate reply + DM in one shot.
    Returns parsed dict on success, None on failure.
    """
    prompt = LEAD_ANALYSIS_PROMPT.format(
        url=post.get('url', ''),
        poster_name=post.get('poster_name', 'LinkedIn User'),
        poster_headline=post.get('poster_headline', ''),
        content=post.get('content', ''),
        label=post.get('label', ''),
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

    print(f'[WARN] Groq analysis failed for {post.get("url", "unknown")}. Skipping alert.')
    return None


# ── Discord ────────────────────────────────────────────────────────────────────

def format_discord_alert(post: dict, analysis: dict) -> str:
    quality_emoji = '🔥' if analysis['quality'] == 'HIGH' else '👀'
    category_label = 'ASKING FOR HELP' if post['category'] == 'asking_for_help' else 'HAS A PROBLEM TO SOLVE'

    profile_line = f"\n**Profile:** {post['poster_profile_url']}" if post.get('poster_profile_url') else ''
    headline_line = f"\n**Role:** {post['poster_headline']}" if post.get('poster_headline') else ''

    msg = (
        f"{quality_emoji} **NEW LEAD — {analysis['quality']} QUALITY**\n"
        f"**Type:** {category_label}\n"
        f"**Who:** {post['poster_name']}"
        f"{headline_line}"
        f"{profile_line}\n"
        f"**Pain point:** {analysis['pain_point']}\n"
        f"**Post:** {post['url']}\n\n"
        f"---\n"
        f"**COMMENT REPLY (paste on their post):**\n"
        f"{analysis['comment_reply']}\n\n"
        f"---\n"
        f"**DM (send after connecting):**\n"
        f"{analysis['dm']}\n\n"
        f"---\n"
        f"*Search: {post['label']}*"
    )

    # Discord 2000 char limit — truncate DM if needed
    if len(msg) > 1990:
        dm_truncated = analysis['dm'][:300] + '... [truncated — full DM in Supabase]'
        msg = msg.replace(analysis['dm'], dm_truncated)

    return msg[:1990]


def send_discord_alert(message: str) -> None:
    if not DISCORD_LEADS_WEBHOOK:
        print('[WARN] DISCORD_LEAD_HUNTER_WEBHOOK_URL not set. Skipping alert.')
        return
    try:
        requests.post(DISCORD_LEADS_WEBHOOK, json={'content': message}, timeout=10)
    except Exception as e:
        print(f'[WARN] Discord webhook failed: {e}')


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f'[{datetime.now()}] Starting Apify lead hunter')

    supabase_client = get_supabase_client()
    groq_client = Groq(api_key=os.environ['GROQ_API_KEY'])

    total_found = 0
    total_new = 0
    total_alerted = 0
    credit_limit_hit = False

    for query_obj in SEARCH_QUERIES:
        if credit_limit_hit:
            break

        posts = run_apify_search(query_obj)

        if not posts and query_obj == SEARCH_QUERIES[0]:
            # If first query returns nothing, might be a connectivity issue
            # Continue to try remaining queries
            pass

        # Check for credit limit signal (empty return after 402)
        if len(posts) == 0 and credit_limit_hit:
            break

        total_found += len(posts)

        for post in posts:
            url = post['url']

            # Deduplicate
            if is_already_seen(supabase_client, url):
                print(f'[{datetime.now()}] Already seen: {url[:70]}')
                continue

            # Pre-filter: reject job posts without spending a Groq call
            if is_job_post(post['content']):
                mark_as_seen(
                    supabase_client,
                    url=url,
                    poster_name=post['poster_name'],
                    snippet=post['content'][:500],
                    query=query_obj['query'],
                    category='filtered_job_post',
                )
                continue

            total_new += 1
            print(f'[{datetime.now()}] New post: {post["poster_name"]} — {url[:70]}')

            # Classify and generate replies
            analysis = analyze_lead(groq_client, post)

            # Always mark as seen to prevent reprocessing
            mark_as_seen(
                supabase_client,
                url=url,
                poster_name=post['poster_name'],
                snippet=post['content'][:500],
                query=query_obj['query'],
                category=post['category'],
            )

            if not analysis:
                print(f'[{datetime.now()}] Analysis failed for {url[:70]} — marked as seen, no alert')
                continue

            # Only alert HIGH and MEDIUM
            if analysis['quality'] in ('HIGH', 'MEDIUM'):
                message = format_discord_alert(post, analysis)
                send_discord_alert(message)
                total_alerted += 1
                print(f'[{datetime.now()}] Discord alert sent — {analysis["quality"]} quality')
            else:
                print(f'[{datetime.now()}] LOW quality — skipped')

            time.sleep(2)  # Rate limit between Groq calls

        # Wait between Apify calls to be respectful
        time.sleep(5)

    print(
        f'[{datetime.now()}] Lead hunt complete. '
        f'Found={total_found}, New={total_new}, Alerted={total_alerted}'
    )

    if total_alerted > 0:
        send_discord_alert(
            f'✅ Lead hunt complete — {datetime.now().strftime("%Y-%m-%d %H:%M PKT")}\n'
            f'Posts scanned: {total_found} | New: {total_new} | Alerts sent: {total_alerted}'
        )


if __name__ == '__main__':
    main()
