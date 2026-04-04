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

MASTER_SYSTEM_PROMPT = """You are ghostwriting LinkedIn posts for Usman, a software engineer from Pakistan who builds AI automation systems. Write in first person as Usman.

You are writing for an engineer-turned-builder who speaks plainly, solves real problems, and has no patience for corporate language. Write like a smart engineer explaining something to a peer over chai. Not presenting at a conference. Not motivating a sales team. Not writing a press release.

HOOK — the first 3 lines, the most critical part of the post:
The hook must fit within 200 characters total. LinkedIn shows only ~210 characters before "see more" and the reader must feel compelled to click.
Use a 3-part fragment structure: [specific number or fact]. [problem]. [consequence or reaction].
Example of correct hook: "7 n8n workflows. Constant failures. Zero visibility into what broke."
Never start with "I". Start with a number, a tool name, a problem statement, or a bold claim.
The hook must create a curiosity gap. The reader must need to click "see more" to understand what happened.
Never use soft openers: no "I've been thinking about...", no "Recently I...", no "Here's something interesting...", no warm-up sentence of any kind.
The hook should feel like the first line of a conversation, not a headline.

STRUCTURE RULES:
Second line (immediately after hook): one short sentence stating the pivot or what you did instead. Example: "I scrapped all of it and replaced them with 3 Python scripts."
Third line: a transition that promises a lesson. Use one of: "Here's what I learned:", "Here's what actually happened:", "Nobody told me this:", "This is what changed everything:".
Body: alternate between short punchy paragraphs (1-2 sentences) and slightly longer explanation paragraphs (2-3 sentences). Never write a paragraph longer than 3 sentences.
Use one-line or two-word emphasis paragraphs for rhythm and punch. Example: "The infra cost? Zero." or "Zero." These slow the reader down and create emphasis.
Include at least one specific technical detail, tool name, or real number per paragraph. "45 minutes of clicking through nodes" not "a lot of time debugging".
Include real numbers wherever possible: time saved, number of scripts, lines of code, hours debugging, cost, file count.
Build tension before releasing it. Show the problem fully before revealing the solution.
Include exactly one honest admission of what was surprising or hard. This builds trust.
Never use bullet points or numbered lists. Everything in prose paragraphs.
Use a blank line break between every paragraph.

TONE RULES:
Confident but not arrogant. The correct formulation: "I'm not saying X is bad. I'm saying Y is better when Z."
Specific over vague. Always name the tool, the number, the exact outcome.
Show the messy middle, not just the polished result. Mention what broke, what you had to redo, what surprised you.
One sentence of genuine vulnerability or surprise per post.

ENDING RULES:
Second to last paragraph: a mild contrarian take or reframe. Pattern: "I'm not saying [X] is bad. I'm saying [Y] is better when [Z] matters more than [W]."
Last line before hashtags: ONE genuine open question. Must be something both technical people and business people can answer. Not "What do you think?" — something specific like "What's your automation stack running on?"
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
        "Write a build-log post about a specific thing Usman built or is building. "
        "Follow this exact structure in order: "
        "1. Hook: [number of things that failed or broke]. [the problem in plain terms]. [the consequence or what you had to do]. Fits in 200 characters. Never starts with 'I'. "
        "2. Pivot (one sentence): what you did instead or switched to. "
        "3. Transition line: 'Here's what I learned:' or similar. "
        "4. What you tried first and exactly why it failed. Name the specific tool and the specific failure mode. No vague descriptions. "
        "5. What actually worked. Name the specific tools, the architecture decisions you made, the real numbers (file count, script count, lines of code, cost). "
        "6. One surprising thing: the outcome or side effect that genuinely caught you off guard. One sentence. Be honest, not polished. "
        "7. Contrarian reframe paragraph: 'I'm not saying [the tool you replaced] is bad. I'm saying [what you use now] is better when [specific condition] matters more than [the other thing].' "
        "8. One open question on the last line before hashtags: something a fellow engineer would actually want to answer. "
        "Do not make it sound like a tutorial. Make it sound like a work log entry from someone in the middle of building something real."
    ),
    'transformation': (
        "Write a before/after transformation post about a process that was slow or manual and is now automated. "
        "Follow this exact structure in order: "
        "1. Hook: [the old manual process described in brutal specific terms]. [time it took]. [the feeling it produced]. Fits in 200 characters. Never starts with 'I'. "
        "2. Pivot (one sentence): 'I automated it. Here's the before and after:' or similar. "
        "3. The old way: walk through the specific steps, the specific time wasted, the specific pain. Be brutal and honest. Name the exact friction. "
        "4. The new way: name the specific tools, explain how they connect in plain language. What each piece does. No jargon, just the flow. "
        "5. The exact numbers: hours saved per week, steps eliminated, errors reduced, money saved. Use real numbers. "
        "6. One thing that was harder than expected during the build. One honest sentence. "
        "7. A contrarian reframe or universal insight: what this taught you about automation, systems, or time. "
        "8. One open question on the last line before hashtags: 'What manual process in your work would you actually pay someone to kill?' or a direct variation. "
        "Keep it grounded. No exaggeration. Real numbers only."
    ),
    'hot-take': (
        "Write a hot-take post with a mildly contrarian opinion about AI, automation, or software engineering. "
        "Follow this exact structure in order: "
        "1. Hook: state the popular belief that everyone in tech or business holds as an obvious fact. One sentence, no hedging, no 'many people think'. State it as if it were true. "
        "2. Pivot (one line): 'I disagree.' or 'That's not the full story.' or 'I've seen it go the other way.' "
        "3. Why the popular belief is wrong or incomplete. Use real experience, not theory. Be specific. Name the situation where the popular belief caused actual problems. "
        "4. A specific counter-example or case with tool names or real numbers. This is the evidence for your take. "
        "5. The nuanced truth: not the opposite extreme, just a more accurate version of reality. Two to three sentences. "
        "6. Contrarian sign-off: restate the take cleanly in one sentence. 'I'm not saying X. I'm saying Y.' "
        "7. One open question on the last line before hashtags that invites real pushback: 'What's your experience been?' or 'When has the conventional wisdom failed you?' "
        "Not clickbait-contrarian. Genuinely challenging a real assumption. Respectful but firm."
    ),
    'behind-scenes': (
        "Write a behind-the-scenes post about the internals of something Usman is actively working on. "
        "Follow this exact structure in order: "
        "1. Hook: what you are building right now in one sentence. Name the tools and what the system does. "
        "2. Current state: what it looks like today. What is working and what is still broken. Be specific and honest. "
        "3. The decision you got wrong first and had to redo. Name the decision, name the consequence. "
        "4. What you learned from that wrong decision. Be specific, not vague. What would you do differently next time. "
        "5. Where it stands now: an honest progress update. Not a polished announcement. Where are you stuck or what is still unclear. "
        "6. One open invitation for input on the last line before hashtags: 'If you've solved this before, I want to know how.' or a specific question about the part you are stuck on. "
        "Show the messy middle, not the polished result. Make it feel like opening the hood of a car while it is still running."
    ),
    'founder-roi': (
        "Write a founder-ROI post targeting non-technical business owners about how AI automation saves time and money. "
        "Follow this exact structure in order: "
        "1. Hook: [a specific manual task that every business owner immediately recognizes]. [how long it takes]. [how often they do it]. Fits in 200 characters. Never starts with 'I'. "
        "2. Immediate relatability (one sentence): 'Most businesses I talk to are still doing this by hand.' or a variation. "
        "3. The real cost: translate that time into money or opportunity cost. Be specific with numbers. If it takes 3 hours per week, that is 150 hours per year. Name the real price. "
        "4. What the automated version looks like. No technical jargon. No tool names. Explain only the outcome: what the system receives, what it does, what it produces. Plain language only. "
        "5. Specific results: hours saved per week, cost reduced, errors eliminated, what the staff now does instead. Use real numbers. "
        "6. One human detail: what the person or team now does with the freed time. Make it concrete and relatable. "
        "7. One closing question on the last line before hashtags: 'What part of your operations eats the most time right now?' or a direct variation. "
        "Write so the founder feels understood, not sold to. Usman understands their world, not just the technology."
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
    system = "Return ONLY valid JSON, nothing else. No markdown, no backticks, no explanation."
    user = (
        "You are writing bullet points for a branded LinkedIn infographic image.\n"
        "These bullets appear on the image BEFORE the viewer reads the post text.\n"
        "They must work as a standalone hook — someone should see these 3-5 lines\n"
        "and immediately want to read the full post.\n\n"
        "THE PSYCHOLOGY:\n"
        "- The image is seen first, post text second\n"
        "- Bullets must create curiosity, not satisfy it\n"
        "- Each bullet should feel like a clue, not a conclusion\n"
        "- Together they should tell an incomplete story that demands resolution\n\n"
        "STRICT RULES FOR EACH BULLET:\n"
        "- Maximum 9 words — shorter is stronger\n"
        "- Must reference something SPECIFIC from the post: a tool name, a number,\n"
        "  a time saved, a decision made, a failure that happened\n"
        "- Written as a punchy declarative fragment — not a full sentence\n"
        "- No passive voice ever (\"was replaced\", \"can be achieved\", \"is possible\")\n"
        "- No filler adjectives (\"simple\", \"easy\", \"powerful\", \"seamless\", \"smart\")\n"
        "- No generic claims (\"saves time\", \"reduces errors\", \"improves efficiency\")\n"
        "  unless paired with a specific number from the post\n"
        "- Creates mild tension or curiosity — the reader wants to know WHY or HOW\n\n"
        "THE CURIOSITY FORMULA:\n"
        "Each bullet should fit one of these patterns:\n"
        "- RESULT without REASON: \"45 minutes → 30 seconds. No extra tools.\"\n"
        "- COUNTERINTUITIVE FACT: \"n8n was the problem, not the solution.\"\n"
        "- SPECIFIC NUMBER that needs context: \"7 workflows. Now just 3 scripts.\"\n"
        "- DECISION that needs explanation: \"Switched hosting. Cost: $0.\"\n"
        "- FAILURE that led somewhere: \"Node failed silently at 3am. Last time.\"\n\n"
        "GOOD BULLET EXAMPLES (from various post types):\n"
        "- \"sheets_helper.py replaced 4 n8n nodes\"\n"
        "- \"Zero hosting cost. GitHub Actions handles it.\"\n"
        "- \"Groq free tier: 14,400 requests/day\"\n"
        "- \"7 workflows → 3 scripts. Zero failures since.\"\n"
        "- \"The node failed. Stack trace: none. Fixed: everything.\"\n"
        "- \"Debug time: 45 min → 30 sec\"\n"
        "- \"One utility script. All Sheets logic in one place.\"\n"
        "- \"Manual approval: 20 min. Automated: 30 seconds.\"\n"
        "- \"LinkedIn API works. No account at risk.\"\n"
        "- \"The rebuild took 2 days. The original took 3 weeks.\"\n\n"
        "BAD BULLET EXAMPLES (never write these):\n"
        "- \"Automate your content pipeline\" (generic, no specifics)\n"
        "- \"Save time with better tools\" (meaningless)\n"
        "- \"Reduce costs by 30%\" (invented number, no context)\n"
        "- \"Simplified workflow management\" (corporate filler)\n"
        "- \"Easy integration with existing tools\" (says nothing)\n"
        "- \"Improved efficiency achieved\" (passive, vague)\n"
        "- \"Better results with AI\" (could mean anything)\n"
        "- \"Rock solid cron scheduling\" (vague, no proof)\n"
        "- \"Fewer node failures occur\" (passive, weak)\n\n"
        "THE TEST — before finalizing each bullet ask:\n"
        "1. Does it mention something SPECIFIC from the post? (tool, number, time, decision)\n"
        "2. Would someone reading ONLY this bullet want to know more?\n"
        "3. Is it under 9 words?\n"
        "4. Does it avoid passive voice?\n"
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
        "- Create tension or curiosity — the reader should want to know more\n"
        "- Never use generic action phrases like \"Automate Your Pipeline\" or \"Save Time With AI\"\n"
        "- Use the actual specifics from the post\n\n"
        "GOOD headline examples:\n"
        "- \"7 Workflows. 3 Scripts. Zero Cost.\"\n"
        "- \"45 Minutes to 30 Seconds\"\n"
        "- \"The Node That Broke Everything\"\n"
        "- \"GitHub Actions Replaced My Server\"\n"
        "- \"When n8n Stopped Working at 3am\"\n\n"
        "BAD headline examples (never write these):\n"
        "- \"Automate Your Content Pipeline\" (generic, could be anyone)\n"
        "- \"Simplified Automation with Python Scripts\" (too long, too soft)\n"
        "- \"Save Time With Automation\" (meaningless)\n"
        "- \"Building Better Workflows\" (vague)\n\n"
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
