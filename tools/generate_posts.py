"""
generate_posts.py — Sunday batch post generator (v2).

Reads the next 5 pending topics from the Google Sheets 'Topic Bank', generates a
LinkedIn post for each using a single Groq call (llama-3.3-70b-versatile), then
writes back: post_text, bullet_points, headline, audience, status='pending',
and scheduled_date (next Mon-Fri).

v2 changes vs v1:
  - Single Groq call per post (was 3 calls — post + bullets + headline).
    Estimated ~3,400 tokens per post vs ~4,500 previously.
  - Richer prompt system: USMAN_CONTEXT, POST_STRUCTURE, RULES, SELF_CHECK.
  - Model runs its own self-check before outputting; rejects posts that fail.
  - 'what_makes_this_real' quality signal logged to stdout for monitoring.
  - Stricter banned-word / banned-phrase enforcement baked into the system prompt.

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
from datetime import datetime, timedelta

import pytz
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.sheets_helper as sheets

load_dotenv()

PKT = pytz.timezone('Asia/Karachi')
GROQ_MODEL = os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')
SPREADSHEET_ID = os.environ['GOOGLE_SHEETS_SPREADSHEET_ID']
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK_CONFIRMATIONS')


# ─── Full Context Block ───────────────────────────────────────────────────────

USMAN_CONTEXT = """
IDENTITY:
Muhammad Usman. 23 years old. COMSATS Abbottabad CS graduate.
Based in Charsadda, Khyber Pakhtunkhwa, Pakistan.
Freelance AI Automation Engineer on Upwork ($18/hr) and Fiverr.
Building internet businesses and personal brand in parallel.

EXPERTISE — only claim what is real:
- n8n workflow automation
- Python automation scripts
- RAG pipelines with Supabase pgvector + Groq/OpenAI embeddings
- AI chatbots using Groq (llama-3.3-70b), OpenAI, Anthropic APIs
- Playwright for headless browser automation and HTML-to-image rendering
- Agentic systems (learning phase — LangGraph, OpenAI Agents SDK)
- generative ai langChain
- Flutter + Firebase mobile development (prior background)
- Supabase (PostgreSQL, Storage, Edge Functions)
- Discord bots with discord.py

REAL PROJECTS (use as grounding material):
- This LinkedIn automation engine: Python + GitHub Actions + Groq +
  Playwright + Supabase + Google Sheets + Discord. Zero cost. Self-built.
- Upwork AI automation profile — first clients, building case studies
- Fiverr n8n specialist gig (low-competition keyword positioning)
- Previously:
- Blood Donation App: final year project, multi-role, Google Maps, FCM

REAL CONSTRAINTS (what makes posts authentic):
- Building on free tiers: Groq free, Supabase free, GitHub Actions free
- Pakistan infrastructure: power cuts happen, 50ms+ latency to Western servers
- PKT is UTC+5 — 5 hours ahead of London, 10 ahead of New York
  This means building at night for clients who are sleeping
- No enterprise budget, no team — solo builder
- Self-taught most of agentic AI knowledge post-graduation
- Charsadda is not a tech hub — building world-class systems from a tier-3 city

TARGET AUDIENCES:
Engineer (Mon/Wed/Fri): AI developers, n8n users, automation engineers,
  solo builders, technical freelancers learning to build systems
Founder (Tue/Thu): SME owners, solopreneurs, startup founders who want
  automation but cannot code. ZERO technical jargon for this audience.

VOICE FINGERPRINT:
- Mentions specific numbers: tokens, hours, dollars, lines of code, days
- Names exact tools and models: llama-3.3-70b not "an AI model"
- Acknowledges Pakistani constraints without complaining about them
- Never positions as guru — positions as fellow builder who figured something out
- Dry humour occasionally: "It worked. I don't know why. Moving on."
- Sentences are short. Often fragments. Like this.
- The situation is the subject, not the narrator.
  BAD: "I tried n8n and hit a rate limit"
  GOOD: "The n8n rate limit hit at 2am, mid-run."
"""


# ─── 9.5 Post Structure ───────────────────────────────────────────────────────

POST_STRUCTURE = """
MANDATORY POST STRUCTURE — follow this exactly, in order:

Line 1:    HOOK — tension without explanation. Under 12 words.
           The reader must think "wait, what happened?" not understand the situation.
           The situation is the subject, not Usman.
           BAD: "I learned something important about n8n this week."
           GOOD: "The n8n rate limit hit at 2am, mid-run."

Lines 2-3: THE SCENE — specific, grounded, sensory.
           One real detail that only someone who lived this would know.
           Name the exact time, the exact tool, the exact failure mode.
           No generalizing. No "often" or "sometimes" — this specific instance.

Line 4:    THE TURN — what Usman did not expect.
           Subvert the reader's assumption about where this is going.
           This is the line people screenshot.
           BAD: "So I fixed it."
           GOOD: "The fix was one line. Finding it took 4 hours."

Lines 5-7: THE SUBSTANCE — tool name, exact decision made, real result with number.
           This is where the value lives. Be specific enough that a developer
           could replicate the decision. Include: what was tried, what worked,
           what the outcome was in measurable terms.

Line 8:    THE DEEPER LESSON — one layer below the obvious takeaway.
           Not "always add error handling." The insight underneath that.
           BAD: "Lesson: test your code properly."
           GOOD: "Visual builders abstract failure modes.
                  Raw code shows you exactly where and why."

Line 9:    THE QUESTION — specific enough that answering it costs the commenter something.
           Not "what do you think?" — a question that reveals something about them.
           BAD: "Have you ever had this happen?"
           GOOD: "What's the most expensive silent failure you've shipped?"

Hashtags:  3 maximum. No more. Place after the question on a new line.
           Engineer posts: #BuildInPublic + 2 technical ones
           Founder posts: #Automation + 2 business-outcome ones
"""


# ─── Rules Block ─────────────────────────────────────────────────────────────

RULES = """
ABSOLUTE RULES — violating any of these fails the post:

LENGTH: 150-280 words. Not characters — words.
        LinkedIn shows ~210 chars before "see more" — hook must land before that.

BANNED WORDS — never use these under any circumstance:
game-changer, revolutionary, seamless, cutting-edge, groundbreaking,
transformative, innovative, leverage, ecosystem, landscape, delve,
empower, cornerstone, utilize, streamline, synergy, circle back,
moving forward, at the end of the day, in today's world, it's no secret

BANNED PHRASES — these make every post sound identical:
"Here's what I learned:"
"Here's what actually happened:"
"Nobody told me this:"
"This is what changed everything:"
"I'm not saying X. I'm saying Y."
"Follow this structure:"
"In today's fast-paced..."
"Hope this helps!"

BANNED PUNCTUATION:
- Em dashes (—) anywhere. Use periods instead.
- More than one question mark in the entire post.
- Emoji anywhere in the post body.
- Bullet points or numbered lists in the post body.
  (bullets go in the IMAGE, not the post text)

"I" AS SUBJECT: Maximum 3 sentences in the entire post where "I" is the subject.
Count before finishing. If more than 3, rewrite those sentences so the
tool, situation, or outcome is the subject instead.

FOUNDER POSTS ONLY — HARD CONSTRAINT:
Zero technical vocabulary. Do not write:
Python, API, JSON, n8n, script, code, GitHub, LLM, model,
function, endpoint, node, webhook, cron, or any engineering term.
Describe the business outcome only. "The system pulls the data" not
"the Python script calls the API."

FILE NAMES: Never mention file names in post body.
sheets_helper.py, generate_posts.py — these mean nothing to most readers.
Describe what the code does, not what it is called.
"""


# ─── Self-Check Rubric ────────────────────────────────────────────────────────

SELF_CHECK = """
INTERNAL SELF-CHECK — do this before producing output. Do not output the check itself.

After drafting the post, verify each item:

[ ] Hook is under 12 words
[ ] Hook creates a question in the reader's mind — does NOT explain the situation
[ ] The situation/tool/outcome is the subject of most sentences, not "I"
[ ] "I" appears as subject 3 times or fewer in the entire post
[ ] One specific number exists (tokens, hours, $, lines, days, percentage)
[ ] At least one exact tool name is mentioned (not "an AI tool" — the actual name)
[ ] No banned words from the list appear anywhere
[ ] No em dashes anywhere
[ ] No emoji anywhere
[ ] No bullet points in the post body
[ ] The turn on line 4 subverts an expectation — it is not just the next event
[ ] The deeper lesson goes one layer below the obvious takeaway
[ ] The closing question would cost the commenter something to answer honestly
[ ] Post is between 150-280 words
[ ] Founder posts contain zero technical vocabulary (if applicable)
[ ] Contains one detail that only someone who lived this situation would know

If any item fails: rewrite that specific part before outputting.
Do not output a post that fails the self-check.
"""


# ─── Category Prompts ────────────────────────────────────────────────────────

CATEGORY_PROMPTS = {
    'build-log': """
CATEGORY: BUILD LOG (Engineer audience — Mon/Wed/Fri)
Write from inside the situation. The bug happened. The deploy broke.
The rate limit hit. Make the tool or system the subject.
Open with the specific moment things got interesting — not the setup,
the moment itself.
Name the exact tools, the exact numbers as they appear in the story.
Be honest about what took longer than expected. That honesty is what
separates a real post from a case study.
""",

    'transformation': """
CATEGORY: TRANSFORMATION (Engineer audience — Mon/Wed/Fri)
Write about a manual process that is now automated.
Open with the specific pain of the old process — not the solution.
Make the reader feel the weight of the repetition before the exit appears.
The solution should emerge from the story, not be announced.
Name specific tools, hours saved, exact steps eliminated.
Include one honest detail about what was harder than expected during the build.
""",

    'hot-take': """
CATEGORY: HOT TAKE (Engineer audience — Mon/Wed/Fri)
Open with the popular belief stated plainly. No hedging.
Let the reader agree for one second.
Then challenge it with a real experience where the belief caused a problem.
Give the evidence: a specific tool, a number, a decision that backfired.
State the correction — not the opposite extreme, the more accurate version.
Do not label sections. Just write the argument.
""",

    'behind-scenes': """
CATEGORY: BEHIND THE SCENES (Engineer audience — Mon/Wed/Fri)
Open with exactly what is being built right now. Name the tools. Name the problem.
Describe the actual current state: what works, what is still broken.
Name the specific decision that had to be redone and what it caused.
Show where things are stuck right now or what is unresolved.
End with a specific request for input on the exact thing being wrestled with.
No tidy conclusion. No lesson. Show the mess.
""",

    'founder-roi': """
CATEGORY: FOUNDER ROI (Founder audience — Tue/Thu)
REMINDER: Zero technical vocabulary. Business outcomes only.

Open with the specific operational moment every founder recognizes.
A concrete scenario, not an abstract claim.
Make the real cost visible. Hours per week into hours per year. Dollar figure.
Describe the better version in business language only.
What gets handled automatically. What the team no longer touches.
Include specific results: hours saved, errors eliminated, what the team does instead.
One human detail: what the freed-up person actually does with that time. Concrete.
End with a question that makes the founder think about their own most expensive manual process.
""",
}

VALID_ENGINEER_CATEGORIES = {'build-log', 'transformation', 'hot-take', 'behind-scenes'}


# ─── Master System Prompt ────────────────────────────────────────────────────

MASTER_SYSTEM_PROMPT = f"""
You are ghostwriting LinkedIn posts for Muhammad Usman.
Write in first person as Usman. This is his voice, his story, his audience.

{USMAN_CONTEXT}

{POST_STRUCTURE}

{RULES}

{SELF_CHECK}

Your output must be a single JSON object. No markdown. No explanation.
No preamble. Just the JSON.
"""


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
      Tuesday (1) / Thursday (3) -> founder audience -> 'founder-roi' category prompt
      Monday (0) / Wednesday (2) / Friday (4) -> engineer audience -> use sheet category
      Sheet category defaults to 'build-log' if unrecognised.
    """
    if weekday_num in (1, 3):
        return 'founder', 'founder-roi'
    category = sheet_category if sheet_category in VALID_ENGINEER_CATEGORIES else 'build-log'
    return 'engineer', category


# ─── Single-call generator ────────────────────────────────────────────────────

def generate_post_single_call(client, topic: str, key_details: str,
                               category: str, weekday: str, audience: str) -> dict:
    """
    Single Groq call that produces post + hook + headline + bullets in one shot.

    Replaces the previous 3-call approach (post -> bullets -> headline).
    Estimated tokens: ~3,400 per post (was ~4,500 across 3 calls).

    Returns a dict with keys:
        post, hook, headline, bullets (list), what_makes_this_real,
        word_count (int), self_check_passed (bool)
    """
    import groq as groq_lib

    category_prompt = CATEGORY_PROMPTS.get(category, CATEGORY_PROMPTS['build-log'])

    user_prompt = f"""
{category_prompt}

RAW MATERIAL FOR THIS POST:
Topic: {topic}
Key details (what actually happened — do not invent beyond this):
{key_details}

Scheduled: {weekday} ({audience} audience)

BEFORE WRITING — think through these silently:
1. What specific moment from the key_details is the most surprising or unexpected?
   That moment is your hook.
2. What detail exists here that only someone who lived this would know?
   That detail goes in lines 2-3.
3. What did Usman NOT expect?
   That is line 4 — the turn.
4. What is the lesson one layer below the obvious one?
   That is line 8.
5. What question would cost the reader something honest to answer?
   That is line 9.

Now write the post following the mandatory structure.
Run the internal self-check before producing output.
Rewrite any part that fails the check.

Also produce:
- headline: 5-7 word infographic title.
  Same rules as the hook — tension without explanation.
  BAD: "How I Fixed My Automation Pipeline"
  GOOD: "Seven Workflows. Two AM. No Alerts."

- bullets: exactly 3-5 bullet points for the branded infographic image.
  Each bullet is a FRAGMENT of the story — reveals just enough to create
  a question in the reader's mind. Maximum 9 words each.
  These appear on the image BEFORE the reader sees the post text.
  They must create enough curiosity that the reader clicks to read the post.
  BAD: "Fixed n8n rate limit issue" (complete, no question)
  GOOD: "Seven workflows. Two AM. No alerts." (scene, raises: what happened?)
  BAD: "Saved 14 hours per week" (complete fact)
  GOOD: "14 hours. Gone. One line of code." (how? raises curiosity)

- what_makes_this_real: one sentence describing the specific detail in this
  post that only someone who actually lived it would know.
  This is a quality check — if you cannot name it, the post is not authentic enough.

Output ONLY this JSON, nothing else:
{{
  "post": "full post text here",
  "hook": "the literal first line of the post, under 12 words",
  "headline": "5-7 word infographic title",
  "bullets": ["bullet one", "bullet two", "bullet three"],
  "what_makes_this_real": "one sentence naming the unforgeable detail",
  "word_count": 0,
  "self_check_passed": true
}}
"""

    messages = [
        {'role': 'system', 'content': MASTER_SYSTEM_PROMPT},
        {'role': 'user', 'content': user_prompt},
    ]

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                temperature=0.75,
                max_tokens=1200,
            )
            break
        except groq_lib.RateLimitError:
            if attempt == 0:
                print(f"[{datetime.now()}] Groq rate limit hit. Waiting 60s before retry...")
                time.sleep(60)
            else:
                raise

    raw = response.choices[0].message.content.strip()

    # Strip accidental markdown fences
    if raw.startswith('```'):
        lines = raw.splitlines()
        raw = '\n'.join(
            lines[1:-1] if lines[-1].strip() == '```' else lines[1:]
        ).strip()

    result = json.loads(raw)

    # Validate required keys
    required = ['post', 'hook', 'headline', 'bullets', 'what_makes_this_real']
    for key in required:
        if key not in result:
            raise ValueError(f"Model output missing key: {key}")

    # Safety net: if model skipped hook field, extract from first post line
    if not result.get('hook'):
        result['hook'] = result['post'].split('\n')[0].strip()[:80]

    return result


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

    print(f"[{datetime.now()}] Starting weekly post generation v2 (model: {GROQ_MODEL})")

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
        weekday_name = scheduled_date.strftime('%A')
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
            # ── Single call: post + headline + bullets ────────────────────────
            result = generate_post_single_call(
                client=groq_client,
                topic=topic,
                key_details=key_details,
                category=category,
                weekday=weekday_name,
                audience=audience,
            )

            post_text = result['post']
            hook = result['hook']
            headline = result['headline']
            bullets = result['bullets']
            what_makes_this_real = result.get('what_makes_this_real', '')
            word_count = result.get('word_count', 0)
            self_check = result.get('self_check_passed', False)

            bullet_points = '|'.join(str(b).strip() for b in bullets[:5])

            print(f"[{datetime.now()}]   Post generated ({len(post_text)} chars, ~{word_count} words)")
            print(f"[{datetime.now()}]   Hook: '{hook}'")
            print(f"[{datetime.now()}]   Headline: '{headline}'")
            print(f"[{datetime.now()}]   Bullets: {bullet_points[:80]}...")
            print(f"[{datetime.now()}]   Self-check passed: {self_check}")
            print(f"[{datetime.now()}]   What makes this real: {what_makes_this_real}")

            # ── Write back to Sheets ──────────────────────────────────────────
            sheets.update_row_status(SPREADSHEET_ID, row_index, {
                'post_text': post_text,
                'hook': hook,
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
