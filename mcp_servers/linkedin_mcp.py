"""
linkedin_mcp.py — LinkedIn action layer for the AI Employee.

Wraps LinkedIn API operations as a clean class so orchestrator.py and other
callers can publish, query analytics, and queue drafts without dealing with
auth headers, retry logic, or vault bookkeeping.

Auth logic (li_request, token refresh, image upload, ugcPosts) is imported
from tools/publish_post.py — not duplicated here.

Env vars required:
  LINKEDIN_ACCESS_TOKEN — current Bearer token
  LINKEDIN_PERSON_URN   — urn:li:person:{id}

Env vars optional (for automatic token refresh on 401):
  LINKEDIN_REFRESH_TOKEN, LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET

Vault:
  Drafts are saved to $VAULT_PATH/LinkedIn/Queue/{filename}.md
"""

import os
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import frontmatter
import requests
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from tools.publish_post import (  # noqa: E402
    li_request,
    register_image_upload,
    upload_image_binary,
    create_ugc_post,
)
import vault_sync  # noqa: E402
from watchers.discord_watcher import notify  # noqa: E402

load_dotenv()


# ── Config ───────────────────────────────────────────────────────────────────

LINKEDIN_API_BASE = "https://api.linkedin.com/v2"
_DRAFT_DIR        = vault_sync.VAULT_PATH / "LinkedIn" / "Queue"

_EMPTY_ANALYTICS  = {"likes": 0, "comments": 0, "shares": 0, "error": ""}


# ── Class ────────────────────────────────────────────────────────────────────

class LinkedInMCP:
    """
    LinkedIn action layer. Instantiate once per run — token_ref carries any
    refreshed token across method calls within the same session.
    """

    def __init__(self) -> None:
        self._token_ref  = [os.environ["LINKEDIN_ACCESS_TOKEN"]]
        self._person_urn = os.environ["LINKEDIN_PERSON_URN"]

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _load_image(self, image_url: str) -> Optional[bytes]:
        """Fetch image bytes from a URL or local path. Returns None on any failure."""
        if not image_url:
            return None
        try:
            if image_url.startswith(("http://", "https://")):
                r = requests.get(image_url, timeout=30)
                r.raise_for_status()
                return r.content
            if os.path.exists(image_url):
                with open(image_url, "rb") as fh:
                    return fh.read()
            print(f"[linkedin_mcp] image_url not found: {image_url!r}")
        except Exception as exc:
            print(f"[linkedin_mcp] Image load failed: {exc}")
        return None

    def _upload_image(self, image_bytes: bytes) -> Optional[str]:
        """Register + PUT image bytes to LinkedIn. Returns asset_urn or None."""
        try:
            upload_url, asset_urn = register_image_upload(self._token_ref, self._person_urn)
            upload_image_binary(upload_url, image_bytes, self._token_ref[0])
            time.sleep(3)  # LinkedIn needs a moment to process the asset
            return asset_urn
        except Exception as exc:
            print(f"[linkedin_mcp] Image upload failed: {exc} — falling back to text-only")
            return None

    # ── Public methods ───────────────────────────────────────────────────────

    def publish_post(self, post_text: str, image_url: str = "", topic: str = "") -> dict:
        """
        Publish a post to LinkedIn immediately.

        Returns:
            {"success": True,  "post_url": "https://...", "error": ""}
            {"success": False, "post_url": "",            "error": "reason"}

        Never raises. Token expiry (which normally calls sys.exit) is caught
        and returned as an error so the orchestrator process stays alive.
        """
        try:
            image_bytes = self._load_image(image_url)
            asset_urn   = self._upload_image(image_bytes) if image_bytes else None

            linkedin_urn = create_ugc_post(
                self._token_ref, self._person_urn, post_text, asset_urn
            )
            linkedin_url = f"https://www.linkedin.com/feed/update/{linkedin_urn}"

            vault_sync.log_action(
                "linkedin_mcp:publish", "success",
                f"topic={topic!r} urn={linkedin_urn}",
            )
            notify("linkedin", f"Published: {topic or 'post'}", linkedin_url, color="green")

            return {"success": True, "post_url": linkedin_url, "error": ""}

        except SystemExit:
            msg = "LinkedIn token expired — refresh LINKEDIN_ACCESS_TOKEN in .env / GitHub Secrets"
            notify("errors", "LinkedIn token expired", msg, color="red", urgent=True)
            vault_sync.log_action("linkedin_mcp:publish", "error", msg)
            return {"success": False, "post_url": "", "error": msg}

        except Exception as exc:
            msg = str(exc)
            notify(
                "errors",
                f"Publish failed: {topic or 'post'}",
                msg,
                color="red",
                urgent=True,
            )
            vault_sync.log_action(
                "linkedin_mcp:publish", "error",
                f"topic={topic!r} error={msg}",
            )
            return {"success": False, "post_url": "", "error": msg}

    def get_post_analytics(self, post_id: str) -> dict:
        """
        Fetch aggregate social metrics for a LinkedIn post via socialActions API.

        Returns:
            {"likes": int, "comments": int, "shares": int, "error": ""}
            {"likes": 0,   "comments": 0,   "shares": 0,   "error": "reason"}

        NOTE: LinkedIn's socialActions endpoint commonly returns 403 for personal
        posts without Marketing Developer Platform access — this is a known
        platform limitation, not a bug. The error is returned, not raised.
        """
        encoded_id = urllib.parse.quote(post_id, safe="")
        url        = f"{LINKEDIN_API_BASE}/socialActions/{encoded_id}"

        try:
            resp = li_request("GET", url, self._token_ref)

            if resp.status_code == 403:
                msg = (
                    "LinkedIn API 403 — Marketing Developer Platform access required "
                    "for personal post analytics"
                )
                print(f"[linkedin_mcp] {msg}")
                return {**_EMPTY_ANALYTICS, "error": msg}

            if resp.status_code == 404:
                msg = f"Post not found: {post_id}"
                print(f"[linkedin_mcp] {msg}")
                return {**_EMPTY_ANALYTICS, "error": msg}

            resp.raise_for_status()
            data = resp.json()

            return {
                "likes":    data.get("likesSummary",    {}).get("totalLikes",                0),
                "comments": data.get("commentsSummary", {}).get("totalFirstLevelComments",   0),
                "shares":   data.get("sharesSummary",   {}).get("totalShareStatistics",      0),
                "error":    "",
            }

        except SystemExit:
            return {**_EMPTY_ANALYTICS, "error": "LinkedIn token expired"}

        except Exception as exc:
            return {**_EMPTY_ANALYTICS, "error": str(exc)}

    def draft_post(self, post_text: str, image_url: str = "") -> str:
        """
        Save a post to vault/LinkedIn/Queue/ without publishing.

        The file can later be promoted to Pending_Approval by the orchestrator
        or published directly via publish_post().

        Returns the draft filename (e.g. 'draft_20260501_143022.md').
        Never raises.
        """
        _DRAFT_DIR.mkdir(parents=True, exist_ok=True)

        ts       = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"draft_{ts}.md"
        path     = _DRAFT_DIR / filename

        post = frontmatter.Post(
            post_text,
            type="linkedin_draft",
            status="queued",
            image_url=image_url,
            created=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        path.write_text(frontmatter.dumps(post), encoding="utf-8")
        print(f"[linkedin_mcp] Draft saved → {filename}")
        vault_sync.log_action("linkedin_mcp:draft", "saved", f"file={filename}")

        return filename


# ── Smoke test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== LinkedInMCP smoke test (draft only — no LinkedIn API calls) ===\n")

    # Provide dummy credentials so __init__ doesn't fail in dev without .env
    if not os.getenv("LINKEDIN_ACCESS_TOKEN"):
        os.environ["LINKEDIN_ACCESS_TOKEN"] = "dummy_token_for_smoke_test"
    if not os.getenv("LINKEDIN_PERSON_URN"):
        os.environ["LINKEDIN_PERSON_URN"] = "urn:li:person:smoke_test"

    mcp = LinkedInMCP()

    filename = mcp.draft_post(
        post_text=(
            "This is a smoke-test draft.\n\n"
            "It was created by linkedin_mcp.py and never published to LinkedIn."
        ),
        image_url="",
    )

    draft_path = _DRAFT_DIR / filename
    assert draft_path.exists(), f"[FAIL] Draft file not created at {draft_path}"

    loaded = frontmatter.load(str(draft_path))
    assert loaded["status"] == "queued",          f"[FAIL] status={loaded['status']}"
    assert loaded["type"]   == "linkedin_draft",  f"[FAIL] type={loaded['type']}"
    assert "smoke-test draft" in loaded.content,  "[FAIL] content not preserved"

    print(f"  [PASS] draft_post → {filename}")
    print(f"  Path: {draft_path}")
    print(f"\n--- File contents ---\n{draft_path.read_text(encoding='utf-8')}")
