"""
dm_ghostwriter.py — Persistent Discord bot for DM ghostwriting and story intake.

HOSTING NOTE: This is NOT a GitHub Actions script. It must run as a long-lived
process. Recommended hosting: Railway (free tier), Fly.io, or any VPS.
- Railway: connect GitHub repo, set env vars, deploy (free $5/month credit)
- Fly.io: `fly launch` then `fly deploy`
- Local dev: `python tools/dm_ghostwriter.py` (bot stays online while terminal is open)

Handles two Discord channels:
  #ghostwriter (DISCORD_GHOSTWRITER_CHANNEL_ID)
    Commands: /biz /tech /follow /reply [message] — generates a DM reply draft
    Command:  /retry — regenerates last draft for the current user
  #stories (DISCORD_STORIES_CHANNEL_ID)
    Any non-command message → story_intake.handle_story_submission()

Depends on: tools/story_intake.py, tools/db_client.py, tools/sheets_helper.py
Env vars: DISCORD_BOT_TOKEN, DISCORD_GHOSTWRITER_CHANNEL_ID, DISCORD_STORIES_CHANNEL_ID,
          GROQ_API_KEY, GROQ_MODEL, GOOGLE_SHEETS_SPREADSHEET_ID
"""

import os
import sys
import time

import discord
from groq import Groq
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.story_intake as story_intake

load_dotenv()

GROQ_MODEL = os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')
GHOSTWRITER_CHANNEL_ID = int(os.getenv('DISCORD_GHOSTWRITER_CHANNEL_ID', '0'))
STORIES_CHANNEL_ID = int(os.getenv('DISCORD_STORIES_CHANNEL_ID', '0'))
SPREADSHEET_ID = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID', '')

# Per-user memory for /retry: user_id → (command, their_message)
last_commands: dict[str, tuple[str, str]] = {}


# ── Persona prompts ────────────────────────────────────────────────────────────

BASE_SYSTEM_PROMPT = """You are ghostwriting LinkedIn DMs for Muhammad Usman, an AI Automation Engineer from Pakistan.

His communication style:
- Warm but professional — reads like a real person, not a sales bot
- Confident without being pushy
- Specific — references what the other person actually said
- Short — LinkedIn DMs that are too long get ignored. Aim for 3-5 sentences max
- Never uses: "Hope this finds you well", "I wanted to reach out", "circle back", "synergy"
- Always ends with ONE clear next step (a question or a soft ask)"""

PERSONA_PROMPTS: dict[str, str] = {
    'biz': (
        'The person who sent this is likely a business owner or non-technical decision maker.\n'
        'They may have commented on a LinkedIn post or messaged directly.\n\n'
        'Original message:\n"{original_message}"\n\n'
        'Write a LinkedIn DM reply that:\n'
        '- Acknowledges what they specifically said\n'
        '- Briefly positions Muhammad as someone who solves their exact problem\n'
        '- Asks ONE qualifying question (e.g. what the business does, what process is painful)\n'
        '- Tone: helpful consultant, not a salesperson\n'
        '- 3-4 sentences max\n\n'
        'Reply only with the DM text — no explanation, no labels.'
    ),
    'tech': (
        'The person who sent this is a developer, engineer, or technical builder.\n'
        "They may be a peer, a potential collaborator, or someone referencing Muhammad's work.\n\n"
        'Original message:\n"{original_message}"\n\n'
        'Write a LinkedIn DM reply that:\n'
        "- Matches their technical level — don't dumb it down\n"
        '- Shows genuine interest in what they\'re building or thinking\n'
        '- Suggests a natural next step (share a repo, jump on a call, exchange notes)\n'
        '- Tone: peer-to-peer, curious, low-pressure\n'
        '- 3-4 sentences max\n\n'
        'Reply only with the DM text — no explanation, no labels.'
    ),
    'follow': (
        "This person commented on one of Muhammad's LinkedIn posts.\n"
        'He wants to follow up and deepen the connection.\n\n'
        'Their comment / original message:\n"{original_message}"\n\n'
        'Write a LinkedIn DM reply that:\n'
        '- References what they said in the comment specifically\n'
        "- Doesn't feel like a mass outreach message\n"
        '- Either: asks a follow-up question, shares a resource, or invites a conversation\n'
        '- Tone: genuine, low-pressure, like reaching out to someone interesting at a conference\n'
        '- 2-3 sentences max\n\n'
        'Reply only with the DM text — no explanation, no labels.'
    ),
    'reply': (
        'Muhammad wants to reply to this LinkedIn message. No specific context about who they are.\n\n'
        'Original message:\n"{original_message}"\n\n'
        'Write a LinkedIn DM reply that:\n'
        '- Is natural and specific to what was said\n'
        '- Moves the conversation forward with one clear next step\n'
        '- Tone: professional but warm\n'
        '- 3-4 sentences max\n\n'
        'Reply only with the DM text — no explanation, no labels.'
    ),
}

HELP_TEXT = (
    '❓ Command not recognized. Available commands:\n'
    '`/biz [message]`    — Reply to a business owner\n'
    '`/tech [message]`   — Reply to a developer/engineer\n'
    '`/follow [message]` — Follow up with a post commenter\n'
    '`/reply [message]`  — Generic reply (any context)\n'
    '`/retry`            — Regenerate the last draft'
)


# ── Groq ───────────────────────────────────────────────────────────────────────

def generate_dm_reply(groq_client: Groq, command: str, their_message: str) -> str:
    """
    Generate a LinkedIn DM reply draft via Groq.

    Retries once after 5 seconds on any error.
    Raises on second failure so the caller can send a user-facing error.
    """
    truncated = their_message[:2000]
    user_prompt = PERSONA_PROMPTS[command].format(original_message=truncated)

    for attempt in range(2):
        try:
            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {'role': 'system', 'content': BASE_SYSTEM_PROMPT},
                    {'role': 'user', 'content': user_prompt},
                ],
                temperature=0.85,
                max_tokens=512,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            if attempt == 0:
                print(f'[WARN] Groq error, retrying in 5s: {e}')
                time.sleep(5)
            else:
                raise


def format_draft_reply(command: str, draft: str, truncated: bool = False) -> str:
    suffix = '\n\n_[Note: original message was truncated to 2,000 characters for context]_' if truncated else ''
    return (
        f'✍️ **DM Draft ({command.upper()})**\n\n'
        f'{draft}{suffix}\n\n'
        f'---\n'
        f'📋 Copy above ↑\n'
        f'🔄 `/retry` to regenerate | `/biz`, `/tech`, `/follow`, `/reply [message]` for a new draft'
    )


# ── Bot ────────────────────────────────────────────────────────────────────────

def main():
    groq_client = Groq(api_key=os.environ['GROQ_API_KEY'])

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        from datetime import datetime
        print(f'[{datetime.now()}] Bot online: {client.user}')
        print(f'  Ghostwriter channel ID : {GHOSTWRITER_CHANNEL_ID}')
        print(f'  Stories channel ID     : {STORIES_CHANNEL_ID}')
        if GHOSTWRITER_CHANNEL_ID == 0 or STORIES_CHANNEL_ID == 0:
            print('[WARN] One or more channel IDs are 0 — check your .env')

    @client.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return

        # ── #ghostwriter channel ───────────────────────────────────────────────
        if message.channel.id == GHOSTWRITER_CHANNEL_ID:
            content = message.content.strip()
            user_id = str(message.author.id)

            # /retry — regenerate last command for this user
            if content.lower() == '/retry':
                prev = last_commands.get(user_id)
                if not prev:
                    await message.channel.send(
                        'No previous command found in this session. Send a new command to start.'
                    )
                    return
                command, their_message = prev
                await message.channel.send('Regenerating...')
                try:
                    draft = generate_dm_reply(groq_client, command, their_message)
                    await message.channel.send(
                        format_draft_reply(command, draft)
                        .replace('**DM Draft', '**DM Draft (retry)')
                        .replace('**DM Draft (retry)(', '**DM Draft (retry) (')
                    )
                except Exception:
                    await message.channel.send(
                        '⚠️ Groq API is temporarily unavailable. Try again in a moment.'
                    )
                return

            # Parse /biz /tech /follow /reply
            command = None
            their_message = ''
            for cmd in ('biz', 'tech', 'follow', 'reply'):
                prefix = f'/{cmd}'
                if content.lower().startswith(prefix):
                    their_message = content[len(prefix):].strip()
                    command = cmd
                    break

            if command is None:
                if content.startswith('/'):
                    await message.channel.send(HELP_TEXT)
                return

            if not their_message:
                await message.channel.send(
                    f'Please include the original LinkedIn message after the command.\n'
                    f'Example: `/{command} Hey, I saw your post about automation...`'
                )
                return

            was_truncated = len(their_message) > 2000
            await message.channel.send('Drafting reply...')

            try:
                draft = generate_dm_reply(groq_client, command, their_message)
                last_commands[user_id] = (command, their_message)
                await message.channel.send(format_draft_reply(command, draft, was_truncated))
            except Exception:
                await message.channel.send(
                    '⚠️ Groq API is temporarily unavailable. Try again in a moment.'
                )

        # ── #stories channel ───────────────────────────────────────────────────
        elif message.channel.id == STORIES_CHANNEL_ID:
            # Ignore command-style messages (e.g. /story retry ...)
            if not message.content.startswith('/'):
                await story_intake.handle_story_submission(message, groq_client, SPREADSHEET_ID)

    client.run(os.environ['DISCORD_BOT_TOKEN'])


if __name__ == '__main__':
    main()
