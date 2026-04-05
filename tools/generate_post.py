"""
generate_post.py — Generate a LinkedIn post using Claude and save it to Supabase.

Usage:
    python tools/generate_post.py --audience technical
    python tools/generate_post.py --audience business
    python tools/generate_post.py --audience story --story_id <uuid>

Outputs the post_id to stdout on success.
"""

import argparse
import json
import os
import sys

import anthropic
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from tools.db_client import create_post, get_pending_story, mark_story_processed

load_dotenv()

MODEL = "claude-opus-4-6"

VOICE_SYSTEM_PROMPT = """You are writing LinkedIn posts for Muhammad Usman, a software engineer from Charsadda, Pakistan, and a COMSATS Abbottabad graduate who builds AI automation systems.

Usman uses two distinct personas on LinkedIn:

THE ARCHITECT (technical audience): Senior AI Automation Engineer who speaks in "Efficiency per Line of Code." Technical terms like 'Latency', 'API Rate Limits', 'State Management', and 'Data Normalization' are natural — never defined, just used. Mention authentic Pakistani context when it adds credibility — building lean, limited infrastructure, no enterprise budget.

THE ROI STRATEGIST (business audience): Fractional CTO / Automation Strategist who speaks in "Hours Saved" and "Revenue Retained." ZERO technical jargon. No 'nodes', 'JSON', 'Python', 'API', or any code-level concepts. Focus strictly on business outcomes: hours saved, cost reduced, errors eliminated, staff freed for higher-value work. Use words like Workflow, Systems, Staff Capacity, Accuracy, Oversight, Process.

SENTENCE SUBJECTS — the single most important rule:
Most sentences should have the situation, the tool, the problem, the decision, or the outcome as the subject — NOT "I."
Bad (diary mode): "I tried using n8n. I hit a rate limit. I switched to Python. I learned that..."
Good (vivid): "The n8n rate limit hit mid-run." or "Three weeks of manual exports. One script ended it."
"I" can appear but should not dominate — no more than 2-3 sentences in the entire post where "I" is the subject.
Count your "I" subjects before finishing. If more than 3, rewrite those sentences to make the situation, tool, or outcome the subject.

TONE AND REGISTER:
Write the way a software engineer explains something to another engineer — not the way someone writes a LinkedIn post.
Use contractions: "it's", "didn't", "wasn't", "couldn't", "I've". Never use formal constructions like "it was not" or "I did not."
Short sentences are usually better. Fragments are fine: "Seven workflows. All down." or "500 lines. 24 hours."
Vary the rhythm — one very short sentence after a longer one creates punch.
Avoid corporate filler: "end result", "running smoothly", "streamlined", "has been worth it", "proved to be", "final straw", "at the end of the day", "handle X and Y", "the system performed well."
The specific beats the vague every time. "Three clients couldn't open their Monday reports" beats "client data was impacted."
Include one detail that only someone who actually lived through this would know — not the lesson, the texture.

Post rules (both personas):
- Maximum 1,300 characters total
- The hook must compel a click — LinkedIn shows roughly 210 characters before "see more"
- The Founder opens with a business pain or result. The Engineer opens with the situation, not the narrator.
- No markdown formatting (LinkedIn ignores bold/italic)
- One blank line between paragraphs
- No more than 5 hashtags, placed at the end
- CTA must be specific: ask a question, invite a DM, or prompt a comment
- No em dashes, no emoji, no bullet points in the post body
- No file names ever (sheets_helper.py, generate_posts.py, etc.) — describe what the code does, not what it's called"""

AUDIENCE_PROMPTS = {
    "technical": """PERSONA: THE ARCHITECT (Senior AI Automation Engineer — "Efficiency per Line of Code")

Write a LinkedIn post for AI developers, engineers, CTOs, and technical builders.

THE HOOK: Open with a technical hot take or a specific failure that makes a fellow engineer stop scrolling. Not "I've been building X." The failure, the surprise, the thing nobody talks about. Examples: "The library everyone recommends is a memory hog at 50k tokens." or "Rate limiting killed the demo. Three days before launch."

TECHNICAL DEPTH: Use terms like Latency, API Rate Limits, State Management, Data Normalization, Token Budget, Context Window, Throughput naturally — do not define them. Your audience already knows. Name exact tools, libraries, frameworks.

THE BETTER WAY — WINNER/LOSER FRAMEWORK: Compare two tools or approaches head-to-head. Be direct about which one wins and why. n8n vs. LangGraph. LangChain vs. raw SDK calls. Managed vector DB vs. self-hosted. One wins. Be specific about why.

Structure (3 bullets minimum):
- Bullet 1: Performance/Speed — a concrete benchmark, latency number, or throughput difference
- Bullet 2: Ease of Deployment — real friction point, not vague complexity
- Bullet 3: Cost at Scale — actual cost difference at volume (requests/day, tokens/month)

Write from inside the situation. The script hung. The rate limit hit. The client data was malformed. Make the tool, the error, the outcome the subject — not "I." Limit "I" as subject to 2-3 sentences in the entire post.

CTA: Ask a high-level architectural question a senior engineer would want to debate — not "what do you think?" Something like: "How are you handling persistent memory in multi-agent loops?" or "What's your state management strategy when the orchestrator crashes mid-run?"

Banned phrases — never use: "Here's what I learned:", "What actually worked was:", "One surprising thing was", "I'm not saying X is bad. I'm saying Y is better when Z.", "end result", "streamlined", "running smoothly."

Hashtags: #AIEngineering #BuildInPublic #Python #Automation (pick 3-4 relevant ones)

Also return an image_prompt: describe a DALL-E prompt for a clean system architecture diagram or benchmark comparison table. Example: "Clean dark-background benchmark table comparing n8n vs LangGraph on latency, deployment complexity, and cost at scale. Minimal design, white text, amber accent color, no people, no logos." This is passed directly to DALL-E 3.""",

    "business": """PERSONA: THE ROI STRATEGIST (Fractional CTO / Automation Strategist — "Hours Saved" and "Revenue Retained")

Write a LinkedIn post for CEOs, founders, and operations managers who are NOT technical.

HARD CONSTRAINT: zero technical vocabulary. Do not write: Python, API, JSON, n8n, script, code, GitHub, LLM, model, function, endpoint, node, or any engineering term. If you catch yourself about to write one, describe the business outcome instead. Use: Workflow, Systems, Staff Capacity, Accuracy, Oversight, Process, Systemize.

THE HOOK — THE SILENT KILLER: Open with the specific operational cost every founder silently pays — a concrete scenario, not an abstract claim. "Your most expensive employee is spending 10 hours a week copy-pasting data between two spreadsheets." or "Three hours. Every Monday. Just to reconcile last week's numbers." Make the status quo feel expensive without making the founder feel foolish.

MAKE THE COST VISIBLE: Convert hours per week into hours per year. Add a dollar figure if it's defensible. "That's 150 hours a year — one full month of your ops manager's time."

THE BRIDGE — SYSTEMIZING, NOT CODING: Describe the better version in business language only. "The system pulls the data, checks for errors, and sends the summary — without anyone touching it." What gets handled automatically, what the team no longer touches, what used to slip through.

BEFORE vs. AFTER: Use this framework explicitly or implicitly. The contrast must be concrete enough that the founder pictures their own team.

ONE HUMAN DETAIL: What does the freed-up person actually do with that time? Not "higher-value work" — be specific. "That person now runs client onboarding calls instead."

Structure (3 steps minimum):
- Step 1: Audit the repetitive — find the task eating hours every week
- Step 2: Remove the human bottleneck — the system handles it, the team reviews outcomes
- Step 3: Scale without hiring — the same capacity now handles 3x the volume

CTA: Ask a business logic question that makes the founder think about their own most expensive manual process. "If you could systemize one task today to free up your Sales team, what would it be?"

Hashtags: #AIForBusiness #Automation #OperationalEfficiency (pick 3 relevant ones)

Also return an image_prompt: describe a DALL-E prompt for a clean consultant's slide visual. Example: "Clean white background consultant slide showing 'Buy Back 20 Hours/Week' as the dominant bold headline in dark navy text, with three numbered steps below in clean sans-serif font, minimal design, no people, no stock photos, no gradients." This is passed directly to DALL-E 3.""",

    "story": """PERSONA: THE ENGINEER (personal story)

Write a LinkedIn post based on the personal story provided below.

Format:
- First-person voice (Muhammad Usman speaking)
- Open with the human moment, not the result — something that happened, not something you learned
- Middle: what was hard, what was learned, what changed — include real specifics (tool names, numbers, decisions)
- End: the insight or lesson, made universal so others can relate
- CTA: invite connection or a genuine response to the lesson
- Hashtags: #BuildInPublic #PakistanTech #AIAutomation (3-4 relevant)

Also return an image_prompt: describe a visual concept for the CSS background theme (e.g., "warm amber and dark charcoal gradient, spotlight from upper right") — this is used to select a static template, not to generate an image.""",
}


def build_story_prompt(story_content: str) -> str:
    return AUDIENCE_PROMPTS["story"] + f"\n\nRaw story:\n{story_content}"


def call_claude(audience: str, story_content: str | None = None) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    if audience == "story":
        if not story_content:
            raise ValueError("story_content is required for audience=story")
        user_message = build_story_prompt(story_content)
    else:
        user_message = AUDIENCE_PROMPTS[audience]

    user_message += """

Respond with valid JSON only — no markdown fences, no extra text:
{
  "content": "<the full LinkedIn post text>",
  "image_prompt": "<visual concept description for CSS background theme>"
}"""

    message = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=VOICE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = message.content[0].text.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # One retry with stricter instruction
        retry_message = (
            "Your previous response was not valid JSON. "
            "Reply with ONLY the JSON object, nothing else:\n"
            '{"content": "...", "image_prompt": "..."}'
        )
        message2 = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=VOICE_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": raw},
                {"role": "user", "content": retry_message},
            ],
        )
        parsed = json.loads(message2.content[0].text.strip())

    if "content" not in parsed or "image_prompt" not in parsed:
        raise ValueError(f"Claude returned incomplete JSON: {parsed}")

    return parsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audience", required=True, choices=["technical", "business", "story"])
    parser.add_argument("--story_id", default=None, help="UUID from stories table (required for --audience story)")
    args = parser.parse_args()

    story_content = None
    story_id = args.story_id

    if args.audience == "story":
        if not story_id:
            story = get_pending_story()
            if not story:
                print("No pending stories found. Skipping Saturday post.", file=sys.stderr)
                sys.exit(0)
            story_id = story["id"]
            story_content = story["raw_content"]
        else:
            from tools.db_client import get_client
            result = get_client().table("stories").select("*").eq("id", story_id).single().execute()
            story_content = result.data["raw_content"]

    print(f"Generating {args.audience} post via Claude ({MODEL})...", file=sys.stderr)
    result = call_claude(args.audience, story_content)

    post = create_post(
        post_text=result["content"],
        audience=args.audience,
    )

    if args.audience == "story" and story_id:
        mark_story_processed(story_id, post["id"])

    print(post["id"])  # stdout — captured by GitHub Actions / next step


if __name__ == "__main__":
    main()
