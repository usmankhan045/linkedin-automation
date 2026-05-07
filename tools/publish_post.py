"""
publish_post.py — Daily LinkedIn publisher with Discord approval gate.

Human-in-the-loop flow:
  1. Script finds today's Sheets-approved post, writes it to vault/Pending_Approval/,
     pings Discord #approval, updates Sheets status to 'pending_approval', and exits.
  2. Human types !approve in Discord → discord_watcher bot moves the file to vault/Approved/.
  3. Next script run calls publish_approved_from_vault() first, which picks up the file,
     publishes to LinkedIn, updates Sheets to 'published', and moves the vault file to Done/.

Scheduling: runs every weekday at 12:00 PKT via .github/workflows/daily_publishing.yml
Depends on: tools/sheets_helper.py, tools/db_client.py, vault_sync.py,
            watchers/discord_watcher.py
Output: Post live on LinkedIn; Sheets row updated; Supabase record inserted; Discord alert sent

Environment variables required:
  LINKEDIN_ACCESS_TOKEN, LINKEDIN_PERSON_URN, GOOGLE_SHEETS_SPREADSHEET_ID,
  GOOGLE_SERVICE_ACCOUNT_JSON (or _PATH), SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
  VAULT_PATH
Optional (for token refresh):
  LINKEDIN_REFRESH_TOKEN, LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET
Optional Discord:
  DISCORD_WEBHOOK_APPROVAL, DISCORD_WEBHOOK_LINKEDIN, DISCORD_WEBHOOK_ERRORS,
  DISCORD_WEBHOOK_CONFIRMATIONS
"""

import os
import sys
import time
from datetime import datetime, timezone

import requests
import pytz
from dotenv import load_dotenv

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import vault_sync  # noqa: E402
import tools.sheets_helper as sheets
import tools.db_client as db
from watchers.discord_watcher import notify  # noqa: E402

load_dotenv()

PKT = pytz.timezone('Asia/Karachi')
LINKEDIN_API_BASE = 'https://api.linkedin.com/v2'
SPREADSHEET_ID = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID', '')
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK_CONFIRMATIONS')



# ─── Discord ──────────────────────────────────────────────────────────────────

def send_discord(message: str) -> None:
    """POST a message to the Discord #confirmations channel via webhook. Non-fatal."""
    if not DISCORD_WEBHOOK:
        print(f"[{datetime.now()}] Discord webhook not configured (DISCORD_WEBHOOK_CONFIRMATIONS). Skipping.")
        return
    try:
        resp = requests.post(DISCORD_WEBHOOK, json={'content': message}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[{datetime.now()}] Discord notification failed (non-fatal): {e}")


# ─── LinkedIn token helpers ───────────────────────────────────────────────────

def get_li_headers(access_token: str) -> dict:
    """Return the standard LinkedIn API request headers."""
    return {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'X-Restli-Protocol-Version': '2.0.0',
    }


def refresh_linkedin_token() -> str:
    """
    Attempt to get a new access token using the refresh token OAuth flow.
    Returns the new access_token string.
    Raises RuntimeError on failure.
    """
    print(f"[{datetime.now()}] Attempting LinkedIn token refresh via refresh_token grant...")
    resp = requests.post(
        'https://www.linkedin.com/oauth/v2/accessToken',
        data={
            'grant_type': 'refresh_token',
            'refresh_token': os.environ['LINKEDIN_REFRESH_TOKEN'],
            'client_id': os.environ['LINKEDIN_CLIENT_ID'],
            'client_secret': os.environ['LINKEDIN_CLIENT_SECRET'],
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Token refresh failed: {resp.status_code} {resp.text}")
    new_token = resp.json().get('access_token', '')
    if not new_token:
        raise RuntimeError("Token refresh response contained no access_token")
    print(f"[{datetime.now()}] Token refreshed successfully.")
    return new_token


# ─── LinkedIn API calls ───────────────────────────────────────────────────────

def li_request(method: str, url: str, token_ref: list, **kwargs) -> requests.Response:
    """
    Make a LinkedIn API call with automatic handling of:
      - 401 Unauthorized: attempts one token refresh, then retries
      - 429 Too Many Requests: waits 30s, then retries

    token_ref is a single-element list [access_token] so the refreshed token
    persists across subsequent calls in the same main() run.

    Exits with code 1 on unrecoverable auth failure.
    """
    for attempt in range(2):
        resp = requests.request(
            method, url,
            headers=get_li_headers(token_ref[0]),
            timeout=30,
            **kwargs,
        )

        if resp.status_code == 401 and attempt == 0:
            print(f"[{datetime.now()}] LinkedIn returned 401. Attempting token refresh...")
            try:
                token_ref[0] = refresh_linkedin_token()
            except Exception as e:
                print(f"[{datetime.now()}] Token refresh failed: {e}")
                print(f"[{datetime.now()}] Action required: refresh LINKEDIN_ACCESS_TOKEN in GitHub Secrets.")
                sys.exit(1)
            continue  # retry with new token

        if resp.status_code == 429 and attempt == 0:
            print(f"[{datetime.now()}] LinkedIn rate limit (429). Waiting 30s before retry...")
            time.sleep(30)
            continue

        if resp.status_code == 401:
            # Second attempt still 401 — token refresh didn't help
            print(f"[{datetime.now()}] Token expired after refresh attempt. Update LINKEDIN_ACCESS_TOKEN in GitHub Secrets.")
            sys.exit(1)

        return resp

    return resp  # fallback — return last response so caller can inspect


def register_image_upload(token_ref: list, person_urn: str) -> tuple:
    """
    Register an image with LinkedIn's media upload API.
    Returns (upload_url: str, asset_urn: str).
    Raises on HTTP error.
    """
    payload = {
        'registerUploadRequest': {
            'recipes': ['urn:li:digitalmediaRecipe:feedshare-image'],
            'owner': person_urn,
            'serviceRelationships': [{
                'relationshipType': 'OWNER',
                'identifier': 'urn:li:userGeneratedContent',
            }],
        }
    }
    resp = li_request('POST', f'{LINKEDIN_API_BASE}/assets?action=registerUpload', token_ref, json=payload)
    resp.raise_for_status()
    data = resp.json()
    upload_url = (
        data['value']
        ['uploadMechanism']
        ['com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest']
        ['uploadUrl']
    )
    asset_urn = data['value']['asset']
    return upload_url, asset_urn


def upload_image_binary(upload_url: str, image_bytes: bytes, access_token: str) -> None:
    """
    PUT raw image bytes to the pre-signed LinkedIn upload URL.
    The upload URL does not use standard LinkedIn auth headers.
    Raises on HTTP error.
    """
    resp = requests.put(
        upload_url,
        data=image_bytes,
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/octet-stream',
        },
        timeout=60,
    )
    resp.raise_for_status()


def create_ugc_post(token_ref: list, person_urn: str, post_text: str, asset_urn: str = None) -> str:
    """
    Create a LinkedIn UGC post.
    If asset_urn is provided, includes the image as media.
    Returns the post URN string (e.g. 'urn:li:share:1234567890').
    Raises on HTTP error.
    """
    if asset_urn:
        share_content = {
            'shareCommentary': {'text': post_text},
            'shareMediaCategory': 'IMAGE',
            'media': [{
                'status': 'READY',
                'description': {'text': ''},
                'media': asset_urn,
                'title': {'text': ''},
            }],
        }
    else:
        share_content = {
            'shareCommentary': {'text': post_text},
            'shareMediaCategory': 'NONE',
        }

    payload = {
        'author': person_urn,
        'lifecycleState': 'PUBLISHED',
        'specificContent': {
            'com.linkedin.ugc.ShareContent': share_content,
        },
        'visibility': {
            'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC',
        },
    }

    resp = li_request('POST', f'{LINKEDIN_API_BASE}/ugcPosts', token_ref, json=payload)
    if not resp.ok:
        raise RuntimeError(f"LinkedIn ugcPosts failed: {resp.status_code} {resp.text}")

    # LinkedIn returns the post URN in the JSON 'id' field or the X-RestLi-Id header
    post_urn = resp.json().get('id') or resp.headers.get('X-RestLi-Id', '')
    if not post_urn:
        raise RuntimeError(f"Could not determine post URN from LinkedIn response: {resp.text}")
    return post_urn


# ─── Supabase ─────────────────────────────────────────────────────────────────

def insert_to_supabase(post_row: dict, linkedin_urn: str) -> None:
    """
    Insert the published post into the Supabase posts table.
    Maps Topic Bank columns to the posts table schema.
    Logs errors but does NOT raise — the post is already live on LinkedIn.
    """
    try:
        row = {
            'post_text': post_row.get('post_text') or post_row.get('topic', ''),
            'topic': post_row.get('topic', ''),
            'category': post_row.get('category', ''),
            'audience': post_row.get('audience', 'engineer'),
            'linkedin_urn': linkedin_urn,
            'published_at': datetime.now(timezone.utc).isoformat(),
        }

        db.get_client().table('posts').insert(row).execute()
        print(f"[{datetime.now()}] Supabase record inserted.")
    except Exception as e:
        print(f"[{datetime.now()}] Supabase insert failed (non-fatal, post already published): {e}")


# ─── Core publish logic ───────────────────────────────────────────────────────

def _publish_post_row(post_row: dict, token_ref: list, person_urn: str) -> None:
    """
    Publish one Sheets row to LinkedIn, update Sheets to 'published', insert to
    Supabase, and send the Discord confirmation. Raises on any fatal error so the
    caller can handle it uniformly.
    """
    row_index  = post_row['_row_index']
    post_text  = post_row.get('post_text', '').strip()
    image_path = post_row.get('image_path', '').strip()
    topic      = post_row.get('topic', 'unknown')

    if not post_text:
        raise ValueError(f"post_text is empty in Sheets row {row_index}")

    # ── Load image bytes ────────────────────────────────────────────────────
    image_bytes = None
    if image_path:
        try:
            if image_path.startswith(('http://', 'https://')):
                r = requests.get(image_path, timeout=30)
                r.raise_for_status()
                image_bytes = r.content
                print(f"[{datetime.now()}] Image downloaded ({len(image_bytes):,} bytes)")
            elif os.path.exists(image_path):
                with open(image_path, 'rb') as f:
                    image_bytes = f.read()
                print(f"[{datetime.now()}] Image loaded from disk ({len(image_bytes):,} bytes)")
            else:
                print(f"[{datetime.now()}] image_path not found — posting text-only")
        except Exception as e:
            print(f"[{datetime.now()}] Image load failed ({e}) — posting text-only")
            image_bytes = None

    # ── Upload image to LinkedIn ────────────────────────────────────────────
    asset_urn = None
    if image_bytes:
        try:
            upload_url, asset_urn = register_image_upload(token_ref, person_urn)
            upload_image_binary(upload_url, image_bytes, token_ref[0])
            time.sleep(3)
        except Exception as e:
            print(f"[{datetime.now()}] Image upload failed ({e}) — falling back to text-only")
            asset_urn = None

    # ── Create the LinkedIn post ────────────────────────────────────────────
    post_type = "with image" if asset_urn else "text-only"
    print(f"[{datetime.now()}] Creating UGC post ({post_type})...")
    linkedin_urn = create_ugc_post(token_ref, person_urn, post_text, asset_urn)
    linkedin_url = f"https://www.linkedin.com/feed/update/{linkedin_urn}"
    print(f"[{datetime.now()}] Post live: {linkedin_url}")

    # ── Update Sheets ───────────────────────────────────────────────────────
    sheets.update_row_status(SPREADSHEET_ID, row_index, {
        'status':  'published',
        'post_id': linkedin_urn,
    })

    # ── Insert to Supabase (non-fatal) ──────────────────────────────────────
    insert_to_supabase(post_row, linkedin_urn)

    # ── Discord confirmation ────────────────────────────────────────────────
    notify("linkedin", f"Published: {topic}", linkedin_url, color="green")
    vault_sync.log_action("publish_post", "success", f"topic={topic} urn={linkedin_urn}")


# ─── Vault-driven publish (backward compat) ────────────────────────────────────

def publish_approved_from_vault() -> None:
    """
    Publish any items that were previously staged to vault/Approved/ via !approve.
    Kept for backward compatibility; new posts go through the direct Sheets path.
    """
    approved = vault_sync.check_approved()
    posts_to_publish = [
        item for item in approved
        if item["metadata"].get("action") == "linkedin_post"
    ]

    if not posts_to_publish:
        print(f"[{datetime.now()}] No approved LinkedIn posts in vault.")
        return

    token_ref = [os.environ['LINKEDIN_ACCESS_TOKEN']]
    person_urn = os.environ['LINKEDIN_PERSON_URN']

    for item in posts_to_publish:
        filename   = item["filename"]
        details    = item["metadata"].get("details", {})
        sheets_row = details.get("sheets_row")
        topic      = details.get("topic", "unknown")

        print(f"[{datetime.now()}] Publishing vault-approved post: '{topic}' ({filename})")
        try:
            if not sheets_row:
                raise ValueError("sheets_row missing from vault metadata")
            post_row = sheets.get_row_by_index(SPREADSHEET_ID, int(sheets_row))
            if not post_row:
                raise ValueError(f"Row {sheets_row} not found in Sheets")
            _publish_post_row(post_row, token_ref, person_urn)
            vault_sync.move_to_done(filename)
        except Exception as e:
            print(f"[{datetime.now()}] Failed to publish vault item '{topic}': {e}")
            notify("errors", f"Publish failed: {topic}", str(e), color="red", urgent=True)
            vault_sync.log_action("publish_post", "error", f"topic={topic} error={e}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"[{datetime.now()}] Starting LinkedIn publish job ({datetime.now(PKT).strftime('%Y-%m-%d %A PKT')})")

    # 1. Publish anything already sitting in vault/Approved/ (legacy path)
    publish_approved_from_vault()

    # 2. Find today's approved post in Sheets and publish it directly.
    #    Approval already happened when the user clicked the Discord Approve button.
    print(f"[{datetime.now()}] Checking Sheets for today's approved post...")
    post_row = sheets.get_todays_post(SPREADSHEET_ID)

    if post_row is None:
        today_str = datetime.now(PKT).date().strftime('%Y-%m-%d')
        print(f"[{datetime.now()}] No approved post in Sheets for {today_str}. Nothing to publish.")
        return

    row_index = post_row['_row_index']
    topic     = post_row.get('topic', 'unknown')
    post_text = post_row.get('post_text', '').strip()

    if not post_text:
        msg = f"Approved post has no post_text (row {row_index}, topic: '{topic}'). Skipping."
        print(f"[{datetime.now()}] {msg}")
        notify("errors", "Publish: missing post_text", msg, color="red")
        return

    print(f"[{datetime.now()}] Found approved post: '{topic[:80]}' (row {row_index}). Publishing now...")

    token_ref  = [os.environ['LINKEDIN_ACCESS_TOKEN']]
    person_urn = os.environ['LINKEDIN_PERSON_URN']

    try:
        _publish_post_row(post_row, token_ref, person_urn)
    except Exception as e:
        print(f"[{datetime.now()}] Failed to publish '{topic}': {e}")
        notify("errors", f"Publish failed: {topic}", str(e), color="red", urgent=True)
        vault_sync.log_action("publish_post", "error", f"topic={topic} error={e}")


if __name__ == '__main__':
    main()
