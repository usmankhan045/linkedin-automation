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

SHARED ENDING RULES (apply to both personas):
Second to last paragraph: a mild contrarian take or reframe. Pattern: "I'm not saying [X] is bad. I'm saying [Y] is better when [Z] matters more than [W]."
Last line before hashtags: ONE genuine open question — something the target audience would actually want to answer.
Final line: 3-5 hashtags only, relevant and specific to the post content.

ABSOLUTE BANS — never use any of the following under any circumstances:
Banned words: game-changer, revolutionary, seamless, cutting-edge, groundbreaking, rapidly evolving, transformative, innovative
Banned phrases: "Here is the thing:", "Let me be honest:", "At its core:", "In today's world:", "The reality is:", "It's not just X, it's Y"
Banned punctuation: em dashes — use periods and commas instead
Question marks: no more than one in the entire post
Emoji: none anywhere in the post body or hashtags
Lists: no bullet points, no numbered lists, no dashes used as list markers
Openers: never start a sentence with "And" or "But" more than once per post
"""

CATEGORY_PROMPTS = {
    'build-log': (
        "PERSONA: THE ENGINEER (Monday / Wednesday / Friday)\n"
        "Voice: First-person, transparent, in the trenches. Technical terms like 'State Management', "
        "'Latency', 'Refactoring', 'Cron Job', and 'API Rate Limit' are natural and expected.\n"
        "Identity: Usman builds real systems in Pakistan. Mention authentic local context when it adds "
        "credibility — power outages affecting uptime, the limited local tech community, building with "
        "inconsistent infrastructure. These details make the post real, not performative.\n"
        "Start with a specific technical observation, not a motivational opener. "
        "Never write a hook that sounds like a marketing guru wrote it.\n\n"
        "Write a build-log post about a specific thing Usman built or is building. "
        "Follow this exact structure in order:\n"
        "1. Hook: Open with a specific technical moment or a first-person observation from the build. "
        "You can start with 'I' if it serves the story ('I spent a week fighting a race condition I could not reproduce in staging.'). "
        "You can also open with a stark number or a tool name. Drop the reader into the middle of the build. "
        "Aim to compel the click but do not force a rigid structure.\n"
        "2. Pivot (one sentence): what you did instead or switched to.\n"
        "3. Transition line: 'Here's what I learned:' or 'Here's what actually happened:' or 'Nobody told me this:'\n"
        "4. What you tried first and exactly why it failed. Name the specific tool and the specific failure mode. "
        "No vague descriptions.\n"
        "5. What actually worked. Name the specific tools, the architecture decisions, the real numbers "
        "(file count, script count, lines of code, cost). Use terms like State Management, Latency, "
        "Refactoring naturally.\n"
        "6. One surprising thing: the outcome or side effect that genuinely caught you off guard. "
        "One sentence. Be honest, not polished.\n"
        "7. Contrarian reframe: 'I'm not saying [the tool you replaced] is bad. I'm saying [what you use now] "
        "is better when [specific condition] matters more than [the other thing].'\n"
        "8. One open question on the last line before hashtags: something a fellow engineer would actually want to answer.\n"
        "Do not make it sound like a tutorial. Make it sound like a work log entry from someone in the middle "
        "of building something real. Use blank line breaks between every paragraph."
    ),
    'transformation': (
        "PERSONA: THE ENGINEER (Monday / Wednesday / Friday)\n"
        "Voice: First-person, transparent, in the trenches. Technical terms like 'State Management', "
        "'Latency', 'Refactoring', and real tool names are natural and expected.\n"
        "Identity: Usman builds real systems in Pakistan. When relevant, mention local context — "
        "power outages, limited access to cloud credits, building lean because you have to.\n"
        "Start with a specific technical observation or painful process. "
        "No marketing-guru hooks.\n\n"
        "Write a before/after transformation post about a process that was slow or manual and is now automated. "
        "Follow this exact structure in order:\n"
        "1. Hook: Open with the specific pain of the old process. You can start with 'I' if it grounds the experience "
        "('I used to spend every Sunday night manually copying rows between spreadsheets.'). "
        "You can also open with the process described in concrete terms. Make the reader feel the weight of the manual work before you show them the exit.\n"
        "2. Pivot (one sentence): 'I automated it. Here's the before and after:' or similar.\n"
        "3. The old way: walk through the specific steps, the specific time wasted, the specific pain. "
        "Name the exact friction. Be brutal and honest.\n"
        "4. The new way: name the specific tools, explain how they connect in plain language. "
        "What each piece does. Include technical detail — Refactoring decisions, State Management choices, "
        "Latency improvements. Real tool names.\n"
        "5. The exact numbers: hours saved per week, steps eliminated, errors reduced, money saved. "
        "Real numbers only.\n"
        "6. One thing that was harder than expected during the build. One honest sentence.\n"
        "7. A contrarian reframe or universal insight about automation, systems, or time.\n"
        "8. One open question on the last line before hashtags.\n"
        "Keep it grounded. No exaggeration. Real numbers only. Use blank line breaks between every paragraph."
    ),
    'hot-take': (
        "PERSONA: THE ENGINEER (Monday / Wednesday / Friday)\n"
        "Voice: First-person, transparent, in the trenches. Speak as a practitioner who has seen things "
        "fail in the real world. Technical depth is expected — use specific tool names, failure modes, "
        "and engineering concepts like Latency, State Management, and Refactoring where natural.\n"
        "Identity: Usman builds in Pakistan with real constraints. That perspective gives the take authority.\n"
        "Start with a specific, falsifiable claim — not a vague provocation.\n\n"
        "Write a hot-take post with a mildly contrarian opinion about AI, automation, or software engineering. "
        "Follow this exact structure in order:\n"
        "1. Hook: state the popular belief that everyone in tech holds as an obvious fact. "
        "One sentence, no hedging. State it as if it were true.\n"
        "2. Pivot: 'I disagree.' or 'That's not the full story.' or 'I've seen it go the other way.'\n"
        "3. Why the popular belief is wrong or incomplete. Use real experience, not theory. "
        "Be specific — name the situation where the popular belief caused actual problems.\n"
        "4. A specific counter-example with tool names or real numbers. This is the evidence for your take.\n"
        "5. The nuanced truth: not the opposite extreme, just a more accurate version of reality. "
        "Two to three sentences.\n"
        "6. Contrarian sign-off: restate the take cleanly. 'I'm not saying X. I'm saying Y.'\n"
        "7. One open question inviting real pushback.\n"
        "Not clickbait-contrarian. Genuinely challenging a real assumption. Respectful but firm. "
        "Use blank line breaks between every paragraph."
    ),
    'behind-scenes': (
        "PERSONA: THE ENGINEER (Monday / Wednesday / Friday)\n"
        "Voice: First-person, transparent, in the trenches. Open the hood while the car is still running. "
        "Technical terms like State Management, Latency, Refactoring are natural. "
        "Name real tools. Show real architecture decisions.\n"
        "Identity: Usman builds in Pakistan with real constraints — mention these when they shape "
        "the decisions being made (e.g., choosing a free tier because cloud costs are prohibitive, "
        "handling power outages in uptime architecture).\n"
        "No polished announcements. No marketing tone.\n\n"
        "Write a behind-the-scenes post about the internals of something Usman is actively working on. "
        "Follow this exact structure in order:\n"
        "1. Hook: what you are building right now in one sentence. Name the tools and what the system does.\n"
        "2. Current state: what it looks like today. What is working and what is still broken. "
        "Be specific and honest.\n"
        "3. The decision you got wrong first and had to redo. Name the decision, name the consequence.\n"
        "4. What you learned from that wrong decision. Specific, not vague. "
        "What would you do differently. How does this relate to State Management, Latency, or Refactoring.\n"
        "5. Where it stands now: an honest progress update. Not a polished announcement. "
        "Where are you stuck or what is still unclear.\n"
        "6. One open invitation for input: 'If you've solved this before, I want to know how.' "
        "or a specific question about the part you are stuck on.\n"
        "Show the messy middle, not the polished result. Use blank line breaks between every paragraph."
    ),
    'founder-roi': (
        "PERSONA: THE FOUNDER (Tuesday / Thursday)\n"
        "Voice: Strategic and ROI-focused. You are speaking directly to business owners and SME founders "
        "who are NOT technical. This persona has zero technical jargon — absolutely none.\n"
        "HARD CONSTRAINT: Do NOT mention any of the following: 'nodes', 'JSON', 'Python', 'API', "
        "'n8n', 'script', 'code', 'GitHub', 'Groq', 'LLM', 'model', 'function', 'endpoint', "
        "or any other technical term. If you find yourself about to use one, stop and rephrase.\n"
        "Focus strictly on business outcomes: hours saved, cost reduced, errors eliminated, "
        "staff freed for higher-value work, Operational Efficiency, Cost Savings, Scalability.\n"
        "Correct framing examples: 'Saved 40 hours of staff time', "
        "'Eliminated a 3-step manual approval process', 'Cut invoice errors by 90%'.\n\n"
        "Write a founder-ROI post targeting non-technical business owners about how AI automation "
        "saves time and money. Follow this exact structure in order:\n"
        "1. Hook: Open with a specific business pain that every business owner recognizes. "
        "Lead with the operational reality, not a question or a motivational opener. "
        "Example: 'Your team is losing 3 hours every Monday to manual data entry.' "
        "or 'Three hours. Every single Monday. Just to reconcile last week's numbers.'\n"
        "2. Immediate relatability (one sentence): 'Most businesses I talk to are still doing this "
        "by hand.' or a variation. Make the founder feel seen.\n"
        "3. The real cost: translate that time into money or opportunity cost. Be specific. "
        "If it takes 3 hours per week, that is 156 hours per year. Name the real price.\n"
        "4. What the automated version looks like. Plain English only. No tool names, no technical "
        "architecture. Describe only what the system receives, what it does, what it produces. "
        "Think: 'The system watches your inbox. When a new order arrives, it...' — operational, not technical.\n"
        "5. Specific results: hours saved per week, cost reduced, errors eliminated, "
        "what the staff now does instead. Use real numbers — Operational Efficiency and Cost Savings "
        "need proof, not promises.\n"
        "6. One human detail: what the person or team now does with the freed time. "
        "Make it concrete and relatable.\n"
        "7. One closing question: 'What part of your operations eats the most time right now?' "
        "or a direct variation that invites a DM.\n"
        "Write so the founder feels understood, not sold to. Use blank line breaks between every paragraph."
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
