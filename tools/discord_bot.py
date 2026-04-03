"""
discord_bot.py — Persistent Discord bot for real-time story intake and DM ghostwriting.

This is NOT a GitHub Actions script. It runs as a long-lived process on a server
(Railway, Fly.io, VPS, etc.) and listens for Discord events 24/7.

Handles two channels:
  #stories (#DISCORD_STORIES_CHANNEL_ID)
    → on_message: validates story, calls story_intake.py logic, replies with preview
  #ghostwriter (#DISCORD_GHOSTWRITER_CHANNEL_ID)
    → on_message: parses /biz /tech /follow /reply commands, calls dm_ghostwriter.py logic

Does NOT handle scheduled GitHub Actions workflows — those are separate yml files.

Depends on: tools/db_client.py, tools/sheets_client.py
Must run separately from GitHub Actions (Actions cannot host persistent processes).
"""

import os
import sys
import asyncio

import discord
from dotenv import load_dotenv

# Local modules
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tools.db_client as db
import tools.sheets_client as sheets

load_dotenv()

STORIES_CHANNEL_ID     = int(os.getenv('DISCORD_STORIES_CHANNEL_ID', '0'))
GHOSTWRITER_CHANNEL_ID = int(os.getenv('DISCORD_GHOSTWRITER_CHANNEL_ID', '0'))
MIN_STORY_LENGTH       = 30
GHOSTWRITER_COMMANDS   = ('/biz', '/tech', '/follow', '/reply', '/retry')


def main():
    # TODO: Configure discord.Client with Intents (message_content=True required)
    # TODO: Register on_ready event — log bot name + confirm channel IDs are valid
    # TODO: Register on_message event:
    #   - If message.channel.id == STORIES_CHANNEL_ID:
    #       → validate length, dedup via db, call story intake logic, reply with preview
    #   - If message.channel.id == GHOSTWRITER_CHANNEL_ID:
    #       → parse command prefix, route to persona prompt, reply with DM draft
    #   - Skip all bot messages (message.author.bot)
    # TODO: Store last ghostwriter command per user_id in memory dict (for /retry)
    # TODO: Handle Groq errors gracefully — reply with user-friendly error message
    # TODO: client.run(os.environ['DISCORD_BOT_TOKEN'])
    pass


if __name__ == '__main__':
    main()
