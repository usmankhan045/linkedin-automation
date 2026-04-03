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

VOICE_SYSTEM_PROMPT = """You are writing LinkedIn posts for Muhammad Usman, an AI Automation Engineer based in Pakistan.

His voice is:
- Direct and confident, but not arrogant
- Practical — always ties ideas to real-world results
- Enthusiastic about AI without being hype-y
- Writes in plain English; avoids buzzword soup
- Occasionally uses Pakistani context when it adds authenticity

Post rules:
- Maximum 1,300 characters total
- First 210 characters MUST hook the reader (this is what shows before "see more")
- No markdown formatting (LinkedIn ignores bold/italic)
- One blank line between paragraphs
- No more than 5 hashtags, placed at the end
- Never start with "I" — find a more engaging opening
- CTA must be specific: ask a question, invite a DM, or prompt a comment"""

AUDIENCE_PROMPTS = {
    "technical": """Write a LinkedIn post for an audience of AI developers, engineers, and tech builders.

Topic focus: Pick ONE of the following angles (choose what feels most current and useful):
- A new AI tool or model release and what it actually means
- A specific implementation pattern or architecture decision
- A counterintuitive finding from building AI systems
- A quick breakdown of how a specific AI capability works

Format:
- Hook: surprising stat, bold claim, or specific tool name in the first sentence
- 3–4 bullet points OR short punchy paragraphs
- One concrete takeaway the reader can apply today
- CTA: "Follow for more" or "Drop your take in the comments"
- Hashtags: #AIEngineering #LLMs #BuildInPublic #AITools (pick 3–4 relevant ones)

Also return an image_prompt: a DALL-E prompt for a clean, dark-background tech visual (no text, no faces).""",

    "business": """Write a LinkedIn post for an audience of SME founders and business owners who are NOT technical.

Topic focus: Pick ONE of the following angles:
- A specific AI/automation result (hours saved, cost reduced, errors eliminated)
- A process that most businesses do manually but shouldn't
- A misconception about AI that's costing businesses money
- How a simple automation changed someone's work week

Format:
- Hook: lead with a specific result or relatable pain ("If your team is still doing X manually...")
- Problem → Solution → Outcome structure
- Plain English — zero jargon
- CTA: "DM me to explore this for your business" or "Comment if you're doing this manually"
- Hashtags: #AIForBusiness #Automation #SmallBusiness (pick 3 relevant ones)

Also return an image_prompt: a DALL-E prompt for a clean, bright, professional visual (graphs, clean office, upward trend — no text, no faces).""",

    "story": """Write a LinkedIn post based on the personal story provided below.

Format:
- First-person voice (Muhammad Usman speaking)
- Open with the human moment, not the result
- Middle: what was hard, what was learned, what changed
- End: the insight or lesson, made universal so others can relate
- CTA: invite connection or conversation
- Hashtags: #AIAutomation #BuildInPublic #PakistanTech (3–4 relevant)

Also return an image_prompt: a DALL-E prompt for a warm, human visual — can reference Pakistan, technology, or achievement. No text, no faces.""",
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
  "image_prompt": "<DALL-E prompt for the image>"
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
        content=result["content"],
        image_prompt=result["image_prompt"],
        audience_type=args.audience,
    )

    if args.audience == "story" and story_id:
        mark_story_processed(story_id, post["id"])

    print(post["id"])  # stdout — captured by GitHub Actions / next step


if __name__ == "__main__":
    main()
