"""
dm_ghostwriter.py — Persistent Discord bot for DM ghostwriting and story intake.

HOSTING NOTE: This is NOT a GitHub Actions script. It must run as a long-lived
process. Recommended hosting: Railway (free tier), Fly.io, or any VPS.
- Railway: connect GitHub repo, set env vars, deploy (free $5/month credit)
- Fly.io: `fly launch` then `fly deploy`
- Local dev: `python tools/dm_ghostwriter.py` (bot stays online while terminal is open)

Handles two Discord channels:
  #ghostwriter (DISCORD_GHOSTWRITER_CHANNEL_ID)
    Slash Commands: /draft, /biz, /tech, /follow, /reply, /comment, /retry
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
from discord import app_commands
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

His Expertise:
- Builds AI agents and automation workflows using LangChain, LangGraph, n8n, RAG, and LLMs.

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
    'comment': (
        'Muhammad wants to leave a comment on this LinkedIn post.\n\n'
        'Original post:\n"{original_message}"\n\n'
        'Write a LinkedIn comment that:\n'
        '- Is perfectly human looking, absolutely no bullshit or fluff\n'
        '- Specifically addresses the main point of their post\n'
        '- Tone: "casual plus professional" (not too formal, not too casual)\n'
        '- Is not too long (2-3 sentences max)\n\n'
        'Reply only with the comment text ready to paste — no explanation, no labels.'
    ),
    'draft': (
        'You are an intelligent, context-aware ghostwriter drafting a LinkedIn DM for Muhammad.\n'
        'The user has provided an input that contains Context (relationship, technical level, background) '
        'and the Original Message from the other person.\n\n'
        'Instructions:\n'
        '1. Automatically analyze the tone of their message and match it. '
        'If they are in a good mood/enthusiastic, add a touch of professional humor. If formal, stay formal.\n'
        '2. Adapt to their technical level based on the context provided. '
        'If non-technical, keep it simple. If technical, use appropriate terminology.\n'
        '3. Be concise and straight to the point. Do not drag things out. 2-4 sentences max.\n'
        '4. Provide exactly ONE clear next step based on the context.\n'
        '5. MUST LOOK PERFECTLY HUMAN. Absolutely no hint of AI. No bullet points, no dashes, no rigid or corporate words (like "delve", "furthermore"). Write exactly like a real human typing a quick DM on their phone.\n\n'
        'User Input (Context + Message):\n'
        '"{original_message}"\n\n'
        'Reply only with the DM text — no explanation, no labels.'
    ),
}

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
        f'🔄 `/retry` to regenerate | Use the `/` menu for a new draft'
    )


# ── Bot ────────────────────────────────────────────────────────────────────────

def main():
    groq_client = Groq(api_key=os.environ['GROQ_API_KEY'])

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    async def handle_slash_command(interaction: discord.Interaction, command: str, their_message: str):
        if interaction.channel_id != GHOSTWRITER_CHANNEL_ID:
            await interaction.response.send_message(f"Please use this command in <#{GHOSTWRITER_CHANNEL_ID}>.", ephemeral=True)
            return

        await interaction.response.defer()
        
        user_id = str(interaction.user.id)
        was_truncated = len(their_message) > 2000
        
        try:
            draft = generate_dm_reply(groq_client, command, their_message)
            last_commands[user_id] = (command, their_message)
            await interaction.followup.send(format_draft_reply(command, draft, was_truncated))
        except Exception as e:
            print(f"[ERROR] Groq API error: {e}")
            await interaction.followup.send('⚠️ Groq API is temporarily unavailable. Try again in a moment.')

    @tree.command(name="draft", description="Smart reply with context and auto tone matching")
    @app_commands.describe(context="Optional context (e.g. they are non-technical)", message="Their message to you")
    async def slash_draft(interaction: discord.Interaction, message: str, context: str = ""):
        input_text = f"Context: {context} Message: {message}" if context else message
        await handle_slash_command(interaction, "draft", input_text)

    @tree.command(name="biz", description="Draft a DM response to a business inquiry")
    async def slash_biz(interaction: discord.Interaction, message: str):
        await handle_slash_command(interaction, "biz", message)

    @tree.command(name="tech", description="Draft a DM response to a technical question")
    async def slash_tech(interaction: discord.Interaction, message: str):
        await handle_slash_command(interaction, "tech", message)

    @tree.command(name="follow", description="Draft a DM to send to a new follower")
    async def slash_follow(interaction: discord.Interaction, message: str):
        await handle_slash_command(interaction, "follow", message)

    @tree.command(name="reply", description="Draft a generic reply to a message")
    async def slash_reply(interaction: discord.Interaction, message: str):
        await handle_slash_command(interaction, "reply", message)

    @tree.command(name="comment", description="Draft a human-like comment for a LinkedIn post")
    async def slash_comment(interaction: discord.Interaction, post: str):
        await handle_slash_command(interaction, "comment", post)

    @tree.command(name="retry", description="Regenerate your last generated draft")
    async def slash_retry(interaction: discord.Interaction):
        if interaction.channel_id != GHOSTWRITER_CHANNEL_ID:
            await interaction.response.send_message(f"Please use this command in <#{GHOSTWRITER_CHANNEL_ID}>.", ephemeral=True)
            return

        user_id = str(interaction.user.id)
        prev = last_commands.get(user_id)
        if not prev:
            await interaction.response.send_message('No previous command found in this session. Send a new command to start.', ephemeral=True)
            return
            
        await interaction.response.defer()
        command, their_message = prev
        try:
            draft = generate_dm_reply(groq_client, command, their_message)
            formatted = format_draft_reply(command, draft).replace('**DM Draft', '**DM Draft (retry)').replace('**DM Draft (retry)(', '**DM Draft (retry) (')
            await interaction.followup.send(formatted)
        except Exception:
            await interaction.followup.send('⚠️ Groq API is temporarily unavailable. Try again in a moment.')

    @client.event
    async def on_ready():
        from datetime import datetime
        print(f'[{datetime.now()}] Bot online: {client.user}')
        print(f'  Ghostwriter channel ID : {GHOSTWRITER_CHANNEL_ID}')
        print(f'  Stories channel ID     : {STORIES_CHANNEL_ID}')
        
        # Try to sync slash commands to the specific guild instantly using the channel ID from .env
        ghost_channel = client.get_channel(GHOSTWRITER_CHANNEL_ID)
        if ghost_channel and hasattr(ghost_channel, 'guild'):
            guild = ghost_channel.guild
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
            print(f'[{datetime.now()}] Successfully synced slash commands instantly to server: {guild.name}')
        else:
            await tree.sync()
            print(f'[{datetime.now()}] Synced slash commands globally (might take some time to appear)')

    @client.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return

        # ── #ghostwriter channel (fallback for people using old text commands) ──
        if message.channel.id == GHOSTWRITER_CHANNEL_ID:
            content = message.content.strip()
            if content.startswith('/'):
                await message.channel.send("⚠️ I've upgraded! Please click the command from the Discord `/` popup menu instead of typing it as text.")
            return

        # ── #stories channel ───────────────────────────────────────────────────
        elif message.channel.id == STORIES_CHANNEL_ID:
            # Ignore command-style messages
            if not message.content.startswith('/'):
                await story_intake.handle_story_submission(message, groq_client, SPREADSHEET_ID)

    client.run(os.environ['DISCORD_BOT_TOKEN'])


if __name__ == '__main__':
    main()
