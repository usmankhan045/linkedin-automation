"""
discord_watcher.py — Discord command center and notification hub.

Two responsibilities that can be used independently:

  1. notify() — importable one-way function; sends formatted Discord embeds via
     webhook URLs. Any script in the repo can call:
         from watchers.discord_watcher import notify
         notify("errors", "Groq rate-limit", "Retry in 60 s", color="red")

  2. run_bot() — persistent bot that listens in #commands for human control
     commands. Run alongside dm_ghostwriter.py on Railway / Fly.io / VPS.

New env vars required (add to .env and GitHub Secrets):
  DISCORD_COMMANDS_CHANNEL_ID     — numeric ID of your #commands channel
  DISCORD_WEBHOOK_NEEDS_ACTION    — webhook for items needing attention
  DISCORD_WEBHOOK_APPROVAL        — webhook for pending-approval pings
  DISCORD_WEBHOOK_LINKEDIN        — webhook for LinkedIn publish confirmations
  DISCORD_WEBHOOK_ERRORS          — webhook for system errors/alerts
  DISCORD_WEBHOOK_BRIEFINGS       — webhook for daily/weekly summaries
  DISCORD_WEBHOOK_COMMANDS        — webhook for command echo / audit trail
  DISCORD_WEBHOOK_READY_POSTS     — webhook for #ready-posts channel (external scripts / GitHub Actions)

Existing env vars re-used:
  DISCORD_BOT_TOKEN — shared with dm_ghostwriter.py
  VAULT_PATH        — resolved via vault_sync module
"""

import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
import frontmatter
import requests
from dotenv import load_dotenv

# Resolve repo root so we can import vault_sync (root) and tools.db_client
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import vault_sync  # noqa: E402  (must come after sys.path insert)

load_dotenv()


# ── Config ─────────────────────────────────────────────────────────────────

VAULT_PATH          = vault_sync.VAULT_PATH
PAUSE_FLAG          = VAULT_PATH / "PAUSED"
REJECTED_DIR        = VAULT_PATH / "Rejected"
COMMANDS_CHANNEL_ID = int(os.getenv("DISCORD_COMMANDS_CHANNEL_ID", "0"))

WEBHOOK_MAP: dict[str, Optional[str]] = {
    "needs_action": os.getenv("DISCORD_WEBHOOK_NEEDS_ACTION"),
    "approval":     os.getenv("DISCORD_WEBHOOK_APPROVAL"),
    "linkedin":     os.getenv("DISCORD_WEBHOOK_LINKEDIN") or os.getenv("DISCORD_WEBHOOK_CONFIRMATIONS"),
    "errors":       os.getenv("DISCORD_WEBHOOK_ERRORS"),
    "briefings":    os.getenv("DISCORD_WEBHOOK_BRIEFINGS"),
    "commands":     os.getenv("DISCORD_WEBHOOK_COMMANDS"),
    "ready_posts":  os.getenv("DISCORD_WEBHOOK_READY_POSTS"),
    "confirmations": os.getenv("DISCORD_WEBHOOK_CONFIRMATIONS"),
}

WEBHOOK_ENV_NAMES: dict[str, str] = {
    "needs_action": "DISCORD_WEBHOOK_NEEDS_ACTION",
    "approval": "DISCORD_WEBHOOK_APPROVAL",
    "linkedin": "DISCORD_WEBHOOK_LINKEDIN or DISCORD_WEBHOOK_CONFIRMATIONS",
    "errors": "DISCORD_WEBHOOK_ERRORS",
    "briefings": "DISCORD_WEBHOOK_BRIEFINGS",
    "commands": "DISCORD_WEBHOOK_COMMANDS",
    "ready_posts": "DISCORD_WEBHOOK_READY_POSTS",
    "confirmations": "DISCORD_WEBHOOK_CONFIRMATIONS",
}

COLORS: dict[str, int] = {
    "blue":   4886754,
    "orange": 16737075,
    "green":  5763719,
    "red":    15548997,
    "yellow": 16776960,
}

_DISCORD_LIMIT = 1900  # hard cap with headroom for Discord's 2000-char limit


# ── Part 1: notify() ───────────────────────────────────────────────────────


def notify(
    channel: str,
    title: str,
    body: str,
    color: str = "blue",
    urgent: bool = False,
) -> bool:
    """
    Send a formatted Discord embed to the named channel via webhook.

    Args:
        channel: logical name → looks up the matching DISCORD_WEBHOOK_* env var.
                 Values: needs_action / approval / linkedin / errors / briefings / commands / ready_posts
        title:   embed title (truncated at 256 chars by Discord).
        body:    embed description / message body.
        color:   one of blue / orange / green / red / yellow.
        urgent:  if True, prepends @everyone so the ping breaks through DND.

    Returns True on success, False on any failure. Never raises.
    """
    url = WEBHOOK_MAP.get(channel)
    if not url:
        env_name = WEBHOOK_ENV_NAMES.get(channel, f"DISCORD_WEBHOOK_{channel.upper()}")
        print(f"[discord_watcher] No webhook URL for channel '{channel}' — set {env_name}")
        return False

    payload: dict = {
        "embeds": [{
            "title":       title[:256],
            "description": body[:4096],
            "color":       COLORS.get(color, COLORS["blue"]),
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }]
    }
    if urgent:
        payload["content"] = "@everyone"

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"[discord_watcher] Webhook POST failed (channel={channel}): {exc}")
        return False


# ── Vault helpers (internal) ───────────────────────────────────────────────


def _ensure_rejected() -> None:
    REJECTED_DIR.mkdir(parents=True, exist_ok=True)


def _oldest_pending() -> Optional[Path]:
    """Return the oldest .md in Pending_Approval/ by creation time, or None."""
    d = VAULT_PATH / "Pending_Approval"
    if not d.exists():
        return None
    files = sorted(d.glob("*.md"), key=lambda f: f.stat().st_ctime)
    return files[0] if files else None


def _find_pending(filename: str) -> Optional[Path]:
    """Find a specific file in Pending_Approval/. Adds .md if omitted."""
    name = filename if filename.endswith(".md") else filename + ".md"
    target = VAULT_PATH / "Pending_Approval" / name
    return target if target.exists() else None


def _stamp_and_move(path: Path, dest_dir: Path, extra_fields: dict) -> str:
    """Write extra frontmatter fields to path, then move it to dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        post = frontmatter.load(str(path))
        for k, v in extra_fields.items():
            post[k] = v
        path.write_text(frontmatter.dumps(post), encoding="utf-8")
    except Exception as exc:
        print(f"[discord_watcher] Frontmatter update failed for {path.name}: {exc}")
    shutil.move(str(path), str(dest_dir / path.name))
    return path.name


def _move_to_approved(path: Path) -> str:
    return _stamp_and_move(
        path,
        VAULT_PATH / "Approved",
        {"status": "approved", "approved_at": _now_iso()},
    )


def _move_to_rejected(path: Path) -> str:
    _ensure_rejected()
    return _stamp_and_move(
        path,
        REJECTED_DIR,
        {"status": "rejected", "rejected_at": _now_iso()},
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _count_done_today() -> int:
    done_dir = VAULT_PATH / "Done"
    if not done_dir.exists():
        return 0
    today = datetime.now(timezone.utc).date()
    return sum(
        1 for f in done_dir.glob("*.md")
        if datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).date() == today
    )


def _last_linkedin_post() -> str:
    """
    Query Supabase for the most recently published post.
    Falls back to 'N/A' gracefully if Supabase is unreachable or unconfigured.
    """
    try:
        from tools.db_client import get_client
        result = (
            get_client()
            .table("posts")
            .select("topic, published_at")
            .not_.is_("published_at", "null")
            .order("published_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            row   = result.data[0]
            topic = row.get("topic") or "untitled"
            raw   = row.get("published_at", "")
            dt    = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return f"*{topic}* at {dt.strftime('%b %d, %H:%M UTC')}"
    except Exception:
        pass
    return "N/A"


def _truncate(text: str, limit: int = _DISCORD_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit - 3] + "..."


# ── Part 2: Command handlers ───────────────────────────────────────────────
# All handlers share the signature (message, arg) for uniform dispatch.
# Handlers that don't use arg simply ignore it.


async def handle_approve(message: discord.Message, arg: str) -> None:
    path = _find_pending(arg) if arg else _oldest_pending()

    if path is None:
        if arg:
            await message.channel.send(f"❌ Not found in Pending_Approval: `{arg}`")
        else:
            await message.channel.send("📭 Pending_Approval is empty — nothing to approve.")
        return

    action = _read_action_field(path)
    filename = _move_to_approved(path)

    vault_sync.log_action("discord:!approve", "success", f"file={filename}")
    notify("approval", "Approved ✅", f"Action: `{action}`\nFile: `{filename}`", color="green")
    await message.channel.send(
        f"✅ **Approved**\n"
        f"Action: `{action}`\n"
        f"File: `{filename}`\n"
        f"The orchestrator will pick this up on its next cycle."
    )


async def handle_reject(message: discord.Message, arg: str) -> None:
    path = _find_pending(arg) if arg else _oldest_pending()

    if path is None:
        if arg:
            await message.channel.send(f"❌ Not found in Pending_Approval: `{arg}`")
        else:
            await message.channel.send("📭 Pending_Approval is empty — nothing to reject.")
        return

    action = _read_action_field(path)
    filename = _move_to_rejected(path)

    vault_sync.log_action("discord:!reject", "success", f"file={filename}")
    await message.channel.send(
        f"🚫 **Rejected**\n"
        f"Action: `{action}`\n"
        f"File moved to `Rejected/`."
    )


async def handle_status(message: discord.Message, arg: str) -> None:
    na_dir      = VAULT_PATH / "Needs_Action"
    pending_dir = VAULT_PATH / "Pending_Approval"

    na_count      = len(list(na_dir.glob("*.md")))      if na_dir.exists()      else 0
    pending_count = len(list(pending_dir.glob("*.md"))) if pending_dir.exists() else 0
    done_today    = _count_done_today()
    last_post     = _last_linkedin_post()
    paused_line   = "⏸ **YES** — use `!resume` to restart" if PAUSE_FLAG.exists() else "No"

    await message.channel.send(
        f"**System Status**\n"
        f"- Needs Action: **{na_count}**\n"
        f"- Pending Approval: **{pending_count}**\n"
        f"- Done today: **{done_today}**\n"
        f"- Last LinkedIn post: {last_post}\n"
        f"- Paused: {paused_line}"
    )


async def handle_queue(message: discord.Message, arg: str) -> None:
    pending_dir = VAULT_PATH / "Pending_Approval"
    if not pending_dir.exists():
        await message.channel.send("❌ Pending_Approval directory not found in vault.")
        return

    files = sorted(pending_dir.glob("*.md"), key=lambda f: f.stat().st_ctime)
    if not files:
        await message.channel.send("📭 No files in Pending_Approval.")
        return

    count = len(files)
    lines = [f"**Pending Approval — {count} item{'s' if count != 1 else ''}**"]

    for i, f in enumerate(files[:10], start=1):
        action  = f.stem
        created = ""
        try:
            post    = frontmatter.load(str(f))
            action  = post.get("action", f.stem)
            raw_ts  = post.get("created", "")
            if raw_ts:
                dt      = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                created = dt.strftime("%b %d %H:%M")
        except Exception:
            pass
        age = f" _(created {created})_" if created else ""
        lines.append(f"`{i}.` **{action}**{age}\n   `{f.name}`")

    if count > 10:
        lines.append(f"\n_…and {count - 10} more. Use `!approve` to process FIFO._")

    await message.channel.send(_truncate("\n".join(lines)))


async def handle_pause(message: discord.Message, arg: str) -> None:
    if PAUSE_FLAG.exists():
        await message.channel.send("⏸ Already paused.")
        return

    PAUSE_FLAG.write_text(
        f"Paused by {message.author.name} at {_now_iso()}\n",
        encoding="utf-8",
    )
    vault_sync.log_action("discord:!pause", "success", f"by={message.author.name}")
    notify(
        "commands",
        "⏸ System Paused",
        f"Set by **{message.author.name}**. Send `!resume` to restart the orchestrator.",
        color="orange",
    )
    await message.channel.send(
        f"⏸ **System paused.** The orchestrator will not pick up new tasks.\n"
        f"Send `!resume` when you're ready to continue."
    )


async def handle_resume(message: discord.Message, arg: str) -> None:
    if not PAUSE_FLAG.exists():
        await message.channel.send("▶️ Not currently paused.")
        return

    PAUSE_FLAG.unlink()
    vault_sync.log_action("discord:!resume", "success", f"by={message.author.name}")
    notify(
        "commands",
        "▶️ System Resumed",
        f"Resumed by **{message.author.name}**. Orchestrator is live.",
        color="green",
    )
    await message.channel.send("▶️ **Resumed.** The orchestrator is running again.")


def _read_action_field(path: Path) -> str:
    """Parse frontmatter and return the action field, or fall back to the filename stem."""
    try:
        return frontmatter.load(str(path)).get("action", path.stem)
    except Exception:
        return path.stem


# ── Bot entry point ────────────────────────────────────────────────────────

_DISPATCH: dict[str, object] = {
    "!approve": handle_approve,
    "!reject":  handle_reject,
    "!status":  handle_status,
    "!queue":   handle_queue,
    "!pause":   handle_pause,
    "!resume":  handle_resume,
}

_HELP_TEXT = (
    "**Available commands**\n"
    "`!approve [filename]` — approve oldest (or named) pending action\n"
    "`!reject [filename]`  — reject oldest (or named) pending action\n"
    "`!status`             — system health snapshot\n"
    "`!queue`              — list everything awaiting approval\n"
    "`!pause`              — hold orchestrator until `!resume`\n"
    "`!resume`             — restart orchestrator\n"
    "`!help`               — show this message"
)


def run_bot() -> None:
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"[discord_watcher] Bot online: {client.user}")
        print(f"[discord_watcher] Commands channel ID: {COMMANDS_CHANNEL_ID}")
        if COMMANDS_CHANNEL_ID == 0:
            print("[discord_watcher] WARNING: DISCORD_COMMANDS_CHANNEL_ID is not set")

    @client.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return
        if message.channel.id != COMMANDS_CHANNEL_ID:
            return

        raw   = message.content.strip()
        parts = raw.split(None, 1)
        cmd   = parts[0].lower() if parts else ""
        arg   = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "!help":
            await message.channel.send(_HELP_TEXT)
            return

        handler = _DISPATCH.get(cmd)
        if handler is None:
            return  # ignore unrecognised messages silently

        vault_sync.log_action(
            f"discord:{cmd}",
            "received",
            f"arg={arg!r} user={message.author.name}",
        )
        await handler(message, arg)

    try:
        client.run(os.environ["DISCORD_BOT_TOKEN"])
    except KeyboardInterrupt:
        print("\n[discord_watcher] Keyboard interrupt — shutting down gracefully.")


if __name__ == "__main__":
    run_bot()
