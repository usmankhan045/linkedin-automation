"""
db_client.py — Supabase CRUD wrapper for the LinkedIn Automation Engine.
All other tools import from here; never call supabase directly from tool scripts.
"""

import os
from datetime import datetime, timezone
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        _client = create_client(url, key)
    return _client


# ── Posts ──────────────────────────────────────────────────────────────────

def create_post(post_text: str, audience: str, topic: str = '', category: str = '', scheduled_at: str | None = None) -> dict:
    """Insert a new post row and return it."""
    row = {
        "post_text": post_text,
        "audience": audience,
        "topic": topic,
        "category": category,
    }
    if scheduled_at:
        row["scheduled_at"] = scheduled_at

    result = get_client().table("posts").insert(row).execute()
    return result.data[0]


def get_post(post_id: str) -> dict:
    result = get_client().table("posts").select("*").eq("id", post_id).single().execute()
    return result.data


def update_post(post_id: str, **fields) -> dict:
    result = get_client().table("posts").update(fields).eq("id", post_id).execute()
    return result.data[0]



def mark_post_published(post_id: str, linkedin_urn: str) -> dict:
    return update_post(
        post_id,
        linkedin_urn=linkedin_urn,
        published_at=datetime.now(timezone.utc).isoformat(),
    )



# ── Stories ────────────────────────────────────────────────────────────────

def insert_story(discord_message_id: str, discord_author: str, raw_content: str) -> dict:
    row = {
        "discord_message_id": discord_message_id,
        "discord_author": discord_author,
        "raw_content": raw_content,
        "status": "pending",
    }
    result = get_client().table("stories").insert(row).execute()
    return result.data[0]


def get_pending_story() -> dict | None:
    """Return the oldest unprocessed story, or None."""
    result = (
        get_client()
        .table("stories")
        .select("*")
        .eq("status", "pending")
        .order("submitted_at", desc=False)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def story_exists(discord_message_id: str) -> bool:
    result = (
        get_client()
        .table("stories")
        .select("id")
        .eq("discord_message_id", discord_message_id)
        .execute()
    )
    return len(result.data) > 0


def mark_story_processed(story_id: str, post_id: str) -> dict:
    result = (
        get_client()
        .table("stories")
        .update({"status": "processed", "processed_post_id": post_id})
        .eq("id", story_id)
        .execute()
    )
    return result.data[0]


# ── Storage ────────────────────────────────────────────────────────────────

def upload_image(post_id: str, image_bytes: bytes) -> str:
    """Upload image to Supabase Storage and return the public URL."""
    bucket = "post-images"
    path = f"posts/{post_id}.png"
    get_client().storage.from_(bucket).upload(
        path, image_bytes, {"content-type": "image/png", "upsert": "true"}
    )
    url_response = get_client().storage.from_(bucket).get_public_url(path)
    return url_response
