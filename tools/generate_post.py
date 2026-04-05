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

THE ENGINEER (technical audience): First-person, transparent, in the trenches. Technical terms like 'State Management', 'Latency', and 'Refactoring' are natural. Mention authentic Pakistani context when it adds credibility — power outages, building lean, limited local tech community.

THE FOUNDER (business audience): Strategic and ROI-focused. ZERO technical jargon. No 'nodes', 'JSON', 'Python', 'API', or any code-level concepts. Focus strictly on business outcomes: hours saved, cost reduced, errors eliminated, staff freed for higher-value work.

Post rules (both personas):
- Maximum 1,300 characters total
- First 210 characters MUST hook the reader (this is what shows before "see more")
- Never start with "I" — start with a number, a result, or a bold claim
- No markdown formatting (LinkedIn ignores bold/italic)
- One blank line between paragraphs
- No more than 5 hashtags, placed at the end
- CTA must be specific: ask a question, invite a DM, or prompt a comment
- No em dashes, no emoji, no bullet points in the post body"""

AUDIENCE_PROMPTS = {
    "technical": """PERSONA: THE ENGINEER

Write a LinkedIn post for an audience of AI developers, engineers, and tech builders.

Topic focus: Pick ONE of the following angles:
- A specific implementation pattern or architecture decision
- A counterintuitive finding from building AI systems in the real world
- A tool that failed and what you replaced it with (name the tools, name the failure)
- A build-log entry: something real that broke, how you fixed it, what you learned

Format:
- Hook: specific tool name, real number, or bold technical claim in the first sentence
- Short punchy prose paragraphs (no bullet points in the post body)
- One concrete technical takeaway the reader can apply today
- CTA: a genuine technical question inviting engineers to share their approach
- Hashtags: #AIEngineering #BuildInPublic #Python #Automation (pick 3-4 relevant ones)

Also return an image_prompt: describe a visual concept for the CSS background theme (e.g., "deep midnight blue with faint circuit-board geometry") — this is used to select a static template, not to generate an image.""",

    "business": """PERSONA: THE FOUNDER

Write a LinkedIn post for an audience of SME founders and business owners who are NOT technical.

HARD CONSTRAINT: Zero technical jargon. No mention of tools, code, APIs, or any engineering concepts. If you find yourself writing a technical term, stop and rephrase as a business outcome.

Topic focus: Pick ONE of the following angles:
- A specific automation result framed purely in business terms (hours saved, cost reduced, errors eliminated)
- A process that most businesses do manually and the exact cost of doing so
- A misconception about AI that is costing business owners money or time
- A before/after story about how a team's week changed after automating one task

Format:
- Hook: lead with a specific relatable pain or result ("Your team spends 3 hours every Monday doing X...")
- Problem to Cost to Solution (in plain English) to Outcome structure
- Real numbers only: hours per week, hours per year, percentage reduction, money saved
- CTA: "DM me to explore this for your business" or a specific open question
- Hashtags: #AIForBusiness #Automation #OperationalEfficiency (pick 3 relevant ones)

Also return an image_prompt: describe a visual concept for the CSS background theme (e.g., "airy white and soft blue gradient with gentle light from the upper right") — this is used to select a static template, not to generate an image.""",

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
