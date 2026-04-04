"""
publish_linkedin.py — Publish a queued post to LinkedIn via the UGC Posts API v2.

Usage:
    python tools/publish_linkedin.py --post_id <uuid>

On success, updates post status to 'published' and prints the LinkedIn post URN.
"""

import argparse
import os
import sys
import time

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from tools.db_client import get_post, mark_post_published, update_post

load_dotenv()

LINKEDIN_API_BASE = "https://api.linkedin.com/v2"


def get_auth_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": "202401",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def refresh_access_token() -> str:
    """Attempt to refresh the LinkedIn access token. Returns new access token."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": os.environ["LINKEDIN_REFRESH_TOKEN"],
        "client_id": os.environ["LINKEDIN_CLIENT_ID"],
        "client_secret": os.environ["LINKEDIN_CLIENT_SECRET"],
    }
    resp = requests.post("https://www.linkedin.com/oauth/v2/accessToken", data=data, timeout=15)
    resp.raise_for_status()
    tokens = resp.json()
    new_access_token = tokens["access_token"]
    new_refresh_token = tokens.get("refresh_token", os.environ["LINKEDIN_REFRESH_TOKEN"])

    # Surface new tokens to stdout so they can be rotated in GitHub Secrets
    print(f"::notice title=LinkedIn Token Refreshed::New LINKEDIN_ACCESS_TOKEN starts with {new_access_token[:8]}...")
    print(f"NEW_LINKEDIN_ACCESS_TOKEN={new_access_token}", file=sys.stderr)
    print(f"NEW_LINKEDIN_REFRESH_TOKEN={new_refresh_token}", file=sys.stderr)

    return new_access_token


def register_image_upload(access_token: str, person_urn: str) -> tuple[str, str]:
    """Register an image upload slot. Returns (upload_url, asset_urn)."""
    url = f"{LINKEDIN_API_BASE}/assets?action=registerUpload"
    body = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": person_urn,
            "serviceRelationships": [
                {
                    "relationshipType": "OWNER",
                    "identifier": "urn:li:userGeneratedContent",
                }
            ],
        }
    }
    resp = requests.post(url, json=body, headers=get_auth_headers(access_token), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    upload_url = data["value"]["uploadMechanism"][
        "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
    ]["uploadUrl"]
    asset_urn = data["value"]["asset"]
    return upload_url, asset_urn


def upload_image_to_linkedin(upload_url: str, access_token: str, image_bytes: bytes) -> None:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/octet-stream",
    }
    resp = requests.put(upload_url, data=image_bytes, headers=headers, timeout=30)
    resp.raise_for_status()


def create_ugc_post(access_token: str, person_urn: str, content: str, asset_urn: str) -> str:
    """Create the LinkedIn post. Returns the LinkedIn post URN."""
    url = f"{LINKEDIN_API_BASE}/ugcPosts"
    body = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": content},
                "shareMediaCategory": "IMAGE",
                "media": [
                    {
                        "status": "READY",
                        "description": {"text": ""},
                        "media": asset_urn,
                        "title": {"text": ""},
                    }
                ],
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }
    resp = requests.post(url, json=body, headers=get_auth_headers(access_token), timeout=15)
    if resp.status_code == 429:
        print("Rate limited. Waiting 60s and retrying...", file=sys.stderr)
        time.sleep(60)
        resp = requests.post(url, json=body, headers=get_auth_headers(access_token), timeout=15)
    resp.raise_for_status()
    return resp.headers.get("x-restli-id", resp.json().get("id", "unknown"))


def publish_post(post_id: str) -> None:
    post = get_post(post_id)

    if post.get("linkedin_urn"):
        print(f"Post already has linkedin_urn={post['linkedin_urn']} — may be duplicate. Aborting.", file=sys.stderr)
        sys.exit(1)

    access_token = os.environ["LINKEDIN_ACCESS_TOKEN"]
    person_urn = os.environ["LINKEDIN_PERSON_URN"]

    # Download image from Supabase public URL
    print(f"Downloading image from {post['image_url']}...", file=sys.stderr)
    img_resp = requests.get(post["image_url"], timeout=30)
    img_resp.raise_for_status()
    image_bytes = img_resp.content

    # Register image upload with LinkedIn
    print("Registering image upload with LinkedIn...", file=sys.stderr)
    try:
        upload_url, asset_urn = register_image_upload(access_token, person_urn)
    except requests.HTTPError as e:
        if e.response.status_code == 401:
            print("Access token expired. Refreshing...", file=sys.stderr)
            access_token = refresh_access_token()
            upload_url, asset_urn = register_image_upload(access_token, person_urn)
        else:
            raise

    # Upload image bytes to LinkedIn
    print("Uploading image to LinkedIn...", file=sys.stderr)
    upload_image_to_linkedin(upload_url, access_token, image_bytes)

    # Brief pause — LinkedIn needs a moment to process the image
    time.sleep(3)

    # Create the post
    print("Creating LinkedIn post...", file=sys.stderr)
    linkedin_urn = create_ugc_post(access_token, person_urn, post["post_text"], asset_urn)

    mark_post_published(post_id, linkedin_urn)
    print(f"Published! LinkedIn URN: {linkedin_urn}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--post_id", required=True)
    args = parser.parse_args()

    try:
        publish_post(args.post_id)
    except Exception as e:
        print(f"Failed to publish: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
