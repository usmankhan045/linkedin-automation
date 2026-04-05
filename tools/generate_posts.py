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

MASTER_SYSTEM_PROMPT = """You are ghostwriting LinkedIn posts for Muhammad Usman, a software engineer from Charsadda, Pakistan, and a COMSATS Abbottabad graduate who builds AI automation systems. Write in first person as Usman.

Usman posts on LinkedIn five days a week using two distinct personas. The category prompt will tell you which persona to use. Read it carefully before writing.

HOOK APPROACH (persona-specific — see category prompt for the specific opening style):
The hook must feel like the first line of a real conversation, not a marketing headline.
LinkedIn shows roughly 210 characters before "see more" — write a hook strong enough to compel a click, but do not mechanically restrict yourself to a character count.
The Engineer can open with "I" when it serves authenticity. Example: "I spent 4 hours hunting a bug that turned out to be a missing semicolon."
The Founder opens with a relatable business pain or a specific operational result a business owner immediately recognizes.
Both personas avoid soft openers: no "I've been thinking about...", no "Here's something interesting...", no warm-up sentences of any kind.
The goal is curiosity and authenticity — not the formula "[number]. [problem]. [consequence]."

ENDING:
End the post with ONE specific open question the target audience would actually want to answer. Not "What do you think?" — something tied to the specific story.
Final line: 3-5 hashtags only.
Do NOT force a contrarian sign-off. If one fits naturally, use it. If not, skip it.

ABSOLUTE BANS — never use any of the following under any circumstances:
Banned words: game-changer, revolutionary, seamless, cutting-edge, groundbreaking, rapidly evolving, transformative, innovative
Banned template phrases — these make every post sound identical:
  "Here's what I learned:"
  "Here's what actually happened:"
  "Nobody told me this:"
  "This is what changed everything:"
  "One surprising thing was"
  "What actually worked was"
  "I'm not saying [X] is bad. I'm saying [Y] is better when [Z]"
  "I'm not saying X. I'm saying Y."
  "Follow this exact structure"
Banned phrases: "Here is the thing:", "Let me be honest:", "At its core:", "In today's world:", "The reality is:", "It's not just X, it's Y"
Banned punctuation: em dashes — use periods and commas instead
Question marks: no more than one in the entire post
Emoji: none anywhere in the post body or hashtags
Lists: no bullet points, no numbered lists, no dashes used as list markers
"""

CATEGORY_PROMPTS = {
    'build-log': (
        "PERSONA: THE ENGINEER (Monday / Wednesday / Friday)\n"
        "Voice: First-person, transparent, in the trenches. Technical terms like State Management, "
        "Latency, Refactoring, Cron Job, and API Rate Limit are natural here.\n"
        "Identity: Usman builds real systems in Pakistan — from Charsadda, studied at COMSATS Abbottabad. "
        "Mention local reality when it adds texture: power outages, building lean, no enterprise budget. "
        "These details make the post feel real, not performed.\n\n"
        "Write a build-log post about something Usman built or is currently building.\n\n"
        "Open with the specific moment things got interesting — something broke, a decision turned out wrong, "
        "you hit a wall you didn't expect. You can start with 'I' when it's natural. "
        "Do not open with a motivational setup or a broad claim.\n\n"
        "Tell the story in the order it happened. Do not announce sections. Do not write transition labels. "
        "Do not write 'I tried X first' as a setup for 'then I switched to Y'. "
        "Just describe what happened, then what happened next, then what you found out.\n\n"
        "Name the exact tools and numbers as they come up: lines of code, hours spent, scripts, cost. "
        "Not in a summary — woven into the story as evidence.\n\n"
        "Be honest about what was harder than expected. Not 'it was challenging' — "
        "the specific thing that surprised you, in one or two sentences.\n\n"
        "Keep every paragraph short: one or two sentences. Blank line between every paragraph. "
        "End with a specific question a fellow engineer would actually want to answer."
    ),
    'transformation': (
        "PERSONA: THE ENGINEER (Monday / Wednesday / Friday)\n"
        "Voice: First-person, transparent, in the trenches. Technical tool names and engineering concepts are natural.\n"
        "Identity: Usman builds in Pakistan with real constraints — mention local context when it shaped the work.\n\n"
        "Write a post about a manual process that is now automated.\n\n"
        "Open with the specific pain of the old process. A concrete moment, not an abstract description. "
        "You can start with 'I'. Make the reader feel the weight of the repetition before you show the exit.\n\n"
        "Do not structure this as [old way] then [new way] then [numbers]. "
        "Tell the story in the order it unfolded. Let the solution emerge from the narrative. "
        "Do not announce the pivot. Do not label what you switched to.\n\n"
        "Name the specific tools, decisions, and numbers as the story moves — "
        "hours per week, steps eliminated, errors gone. These belong in the story, not in a summary at the end.\n\n"
        "Include one honest detail about what was harder than expected during the build. "
        "This is what separates a real post from a marketing case study.\n\n"
        "No forced contrarian sign-off. End with a reflection or a question tied to the specific story. "
        "Keep every paragraph short. Blank line between every paragraph."
    ),
    'hot-take': (
        "PERSONA: THE ENGINEER (Monday / Wednesday / Friday)\n"
        "Voice: A practitioner who has seen things fail in the real world. "
        "Technical depth is expected — specific tool names, failure modes, engineering tradeoffs.\n"
        "Identity: Usman builds in Pakistan with real constraints. That perspective gives the take weight.\n\n"
        "Write a post with a mildly contrarian opinion about AI, automation, or software engineering.\n\n"
        "Open with the popular belief stated plainly. No hedging, no 'many people think'. "
        "Just the conventional wisdom as if it's obviously true. Let the reader agree for one second.\n\n"
        "Then challenge it with a real experience — a specific situation where the belief caused problems, "
        "produced the opposite result, or missed something important. Not theory. A real case.\n\n"
        "Give the evidence: a tool, a number, a decision that backfired in a specific way.\n\n"
        "State the correction. Not the opposite extreme — just the more accurate version of what's actually true.\n\n"
        "Do not label your sections. Do not write 'The nuanced truth is:' or 'Here's my actual take:'. "
        "Just write the argument. End with a question that invites real pushback. "
        "Keep every paragraph short. Blank line between every paragraph."
    ),
    'behind-scenes': (
        "PERSONA: THE ENGINEER (Monday / Wednesday / Friday)\n"
        "Voice: Open the hood while the car is still running. No polish, no announcements.\n"
        "Identity: Usman builds in Pakistan. Mention real constraints when they shaped the decisions.\n\n"
        "Write a post about something Usman is actively building right now.\n\n"
        "Open with what you're building — name the tools, name the problem it solves. Specific and short.\n\n"
        "Describe the actual current state honestly: what works, what is still broken. Don't clean it up.\n\n"
        "Name the specific decision you got wrong and had to redo. "
        "Not 'I made a mistake early on' — the actual decision and what it caused.\n\n"
        "Show where you're stuck right now or what's still unresolved. "
        "This is the part that makes other engineers want to help.\n\n"
        "End with a specific request for input about the exact thing you're wrestling with. "
        "No lesson. No tidy conclusion. Show the mess. "
        "Keep every paragraph short. Blank line between every paragraph."
    ),
    'founder-roi': (
        "PERSONA: THE FOUNDER (Tuesday / Thursday)\n"
        "Voice: Strategic and results-focused. Speaking directly to business owners who are NOT technical.\n"
        "HARD CONSTRAINT: zero technical vocabulary. Do not write: nodes, JSON, Python, API, n8n, script, "
        "code, GitHub, Groq, LLM, model, function, endpoint, workflow, or any engineering term. "
        "If you catch yourself about to use one, stop and describe the business outcome instead.\n\n"
        "Write a post for non-technical business owners about how intelligent systems save time and money.\n\n"
        "Open with the specific operational moment every founder recognizes. "
        "Not an abstract claim — a concrete scenario. "
        "'Your team is losing 3 hours every Monday to manual data entry.' "
        "Or: 'Three hours. Every Monday. Just to reconcile last week's numbers.'\n\n"
        "Make the real cost visible. Translate hours per week into hours per year. "
        "Name the dollar amount if you can. Make the status quo feel expensive without making the founder feel foolish.\n\n"
        "Describe the better version in business language only: what gets handled automatically, "
        "what the team no longer touches, what the operation looks like now. No technical explanation.\n\n"
        "Include specific results: hours saved, errors eliminated, what the team now does instead. Real numbers.\n\n"
        "Include one human detail — what the freed-up person actually does with that time. Keep it concrete.\n\n"
        "End with a question that makes the founder think about their own most expensive manual process. "
        "Write so the founder feels understood, not sold to. "
        "Keep every paragraph short. Blank line between every paragraph."
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
      Tuesday (1) / Thursday (3) -> founder audience -> 'founder-roi' category prompt
      Monday (0) / Wednesday (2) / Friday (4) -> engineer audience -> use sheet category
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
    Call 2 of 3: extract 3-5 curiosity-gap bullet points for the infographic image.

    Returns a pipe-separated string, e.g. 'Point one|Point two|Point three'.
    Raises json.JSONDecodeError if the model returns malformed JSON (caller should catch).
    """
    system = "Return ONLY valid JSON, nothing else. No markdown, no backticks, no explanation."
    user = (
        "You are writing bullet points for a branded LinkedIn infographic image.\n"
        "These bullets appear on the image BEFORE the viewer reads the post text.\n"
        "They must work as a standalone hook — someone should see these 3-5 lines\n"
        "and immediately want to read the full post.\n\n"
        "THE PSYCHOLOGY:\n"
        "- The image is seen first, post text second\n"
        "- Bullets must create curiosity, not satisfy it\n"
        "- Each bullet hints at a RESULT without revealing the HOW\n"
        "- The viewer should think 'wait, how did that happen?' after every bullet\n"
        "- Together they should tell an incomplete story that demands resolution\n\n"
        "STRICT RULES FOR EACH BULLET:\n"
        "- Maximum 9 words — shorter is stronger\n"
        "- Must reference something SPECIFIC from the post: a number, a tool name (engineer posts),\n"
        "  a business outcome (founder posts), a time saved, a decision made, a failure\n"
        "- Written as a punchy declarative fragment — not a full sentence\n"
        "- No passive voice ever ('was replaced', 'can be achieved', 'is possible')\n"
        "- No filler adjectives ('simple', 'easy', 'powerful', 'seamless', 'smart')\n"
        "- No generic claims ('saves time', 'reduces errors') unless paired with a specific number\n"
        "- HINT at the result. Do NOT explain how it was achieved.\n\n"
        "THE INCOMPLETE STORY RULE:\n"
        "Each bullet must be an incomplete story — it hints at a result but forces the reader into the post to find out how.\n"
        "Bad: 'Saved 40 hours with Python.' (complete — reader has the full fact, no reason to click)\n"
        "Good: 'The 50-line script that killed a 40-hour manual process.' (what process? how does 50 lines do that?)\n"
        "Bad: '7 workflows replaced with 3 scripts.' (complete — the transformation is fully stated)\n"
        "Good: 'Why 7 workflows became 3 scripts. One of them was the problem.' (reader needs the post)\n"
        "The reader should think 'wait, how?' or 'wait, what happened next?' after every bullet.\n\n"
        "GOOD EXAMPLES (engineer posts):\n"
        "- 'sheets_helper.py replaced 4 n8n nodes'\n"
        "- 'Zero hosting cost. GitHub Actions handles it.'\n"
        "- '7 workflows to 3 scripts. Zero failures since.'\n"
        "- 'Debug time: 45 min to 30 sec'\n"
        "- 'The rebuild took 2 days. The original took 3 weeks.'\n\n"
        "GOOD EXAMPLES (founder posts):\n"
        "- '40 hours of staff time. Recovered.'\n"
        "- 'The process that took 3 days now takes 4 minutes.'\n"
        "- '90% fewer invoice errors. Same team.'\n"
        "- 'One employee freed from the task entirely.'\n"
        "- 'Zero manual approvals. Same compliance.'\n\n"
        "BAD EXAMPLES (never write these):\n"
        "- 'Automate your content pipeline' (generic, no specifics)\n"
        "- 'Save time with better tools' (meaningless)\n"
        "- 'Simplified workflow management' (corporate filler)\n"
        "- 'Better results with AI' (says nothing)\n"
        "- 'Improved efficiency achieved' (passive, vague)\n\n"
        "THE TEST — before finalizing each bullet ask:\n"
        "1. Does it mention something SPECIFIC (number, tool, time, outcome)?\n"
        "2. Does it HIDE the how while showing the what?\n"
        "3. Would someone reading ONLY this bullet want to know more?\n"
        "4. Is it under 9 words?\n"
        "5. Does it avoid passive voice?\n"
        "If any answer is NO — rewrite it.\n\n"
        "POST TO EXTRACT FROM:\n"
        f"{post_text}\n\n"
        "Return ONLY a JSON array of 3-5 strings. No markdown, no backticks,\n"
        "no explanation, no preamble. Just the array.\n"
        "Example format: [\"bullet one here\", \"bullet two here\", \"bullet three here\"]"
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
    system = "Return ONLY the headline text. No punctuation at the end, no quotes, no explanation."
    user = (
        "Extract a punchy headline (maximum 6 words) from this LinkedIn post for use as the large title "
        "on a branded infographic.\n\n"
        "CRITICAL RULES:\n"
        "- Use fragment style, not a full sentence\n"
        "- Include numbers if they appear in the post — numbers make headlines stronger\n"
        "- Create tension or curiosity — the reader should want to know more, not feel the post has already been summarized\n"
        "- Prefer headlines that imply a problem, a failure, or a counterintuitive outcome\n"
        "- Never use generic action phrases like 'Automate Your Pipeline' or 'Save Time With AI'\n"
        "- Use the actual specifics from the post\n\n"
        "GOOD headline examples (tension-first):\n"
        "- 'Why Your n8n Workflow Will Fail'\n"
        "- 'The Refactor That Cost Three Days'\n"
        "- 'State Management Was the Bug'\n"
        "- 'The Node That Broke Everything'\n"
        "- '40 Hours of Staff Time. Gone.'\n"
        "- 'When Latency Hid in Plain Sight'\n\n"
        "BAD headline examples (never write these):\n"
        "- 'Automate Your Content Pipeline' (generic, could be anyone)\n"
        "- 'Simplified Automation with Python Scripts' (too long, too soft)\n"
        "- 'Save Time With Automation' (meaningless)\n"
        "- 'Building Better Workflows' (vague)\n\n"
        "Return ONLY the headline text. No punctuation at the end, no quotes, no explanation.\n\n"
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
