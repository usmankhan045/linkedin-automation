"""
discord_ingest.py — Pull the latest unprocessed story from Discord #stories channel.

Usage:
    python tools/discord_ingest.py

Prints the story_id to stdout if a new story was found.
Exits with code 0 and prints a skip message if no new stories exist.
"""

import asyncio
import os
import sys

import discord
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from tools.db_client import insert_story, story_exists, get_pending_story

load_dotenv()

MIN_STORY_LENGTH = 20
MAX_MESSAGES_TO_FETCH = 20


async def fetch_latest_story() -> dict | None:
    """Connect to Discord, fetch recent messages, save new ones, return first new story."""
    token = os.environ["DISCORD_BOT_TOKEN"]
    channel_id = int(os.environ["DISCORD_STORIES_CHANNEL_ID"])

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    found_story = None

    @client.event
    async def on_ready():
        nonlocal found_story
        try:
            channel = client.get_channel(channel_id)
            if channel is None:
                channel = await client.fetch_channel(channel_id)

            messages = [msg async for msg in channel.history(limit=MAX_MESSAGES_TO_FETCH)]

            for message in messages:
                # Skip bots
                if message.author.bot:
                    continue

                content = message.content.strip()

                # Skip too-short or link-only messages
                if len(content) < MIN_STORY_LENGTH:
                    continue
                if content.startswith("http") and " " not in content:
                    continue

                # Skip already-ingested messages
                if story_exists(str(message.id)):
                    continue

                # Save to Supabase
                story = insert_story(
                    discord_message_id=str(message.id),
                    discord_author=str(message.author),
                    raw_content=content,
                )
                print(f"New story saved from {message.author}: {content[:60]}...", file=sys.stderr)
                found_story = story
                break  # One story per run

            if found_story is None:
                print("No new stories found in #stories channel.", file=sys.stderr)

        finally:
            await client.close()

    await client.start(token)
    return found_story


def main():
    story = asyncio.run(fetch_latest_story())

    if story is None:
        # Check if there's already a pending story waiting to be processed
        pending = get_pending_story()
        if pending:
            print(f"Using existing pending story: {pending['id']}", file=sys.stderr)
            print(pending["id"])  # stdout
        else:
            print("No new stories found. Skipping Saturday post.")
            sys.exit(0)
    else:
        print(story["id"])  # stdout — captured by GitHub Actions


if __name__ == "__main__":
    main()
