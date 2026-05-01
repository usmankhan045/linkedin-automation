# Run with: pm2 start orchestrator.py --interpreter python3
# NOT for GitHub Actions — requires persistent vault access on local filesystem.
# See pm2_ecosystem.config.js for the full process manager config.
"""
orchestrator.py — Master brain of the AI Employee.

Polls vault/Needs_Action/ every 5 minutes, asks Gemini 2.0 Flash to decide
what to do with each item, then executes the decision: move to Pending_Approval
for human review, auto-complete, log only, or escalate to Discord.

Token budget: every Gemini call is capped at ~500 tokens in + ~100 out.
Raw file contents are never sent — only a 100-char content snippet.

Run modes:
  python orchestrator.py             # poll every 5 minutes (production)
  python orchestrator.py --once      # single cycle then exit (debugging)
  python orchestrator.py --dry-run   # print decisions, no file ops, no Discord pings
  python orchestrator.py --once --dry-run

Environment variables required:
  GEMINI_API_KEY  — Google AI Studio key for Gemini 2.0 Flash
  VAULT_PATH      — path to Obsidian vault (default: C:\\Users\\usman khan\\AI_Employee_Vault)

Environment variables used indirectly (via discord_watcher):
  DISCORD_WEBHOOK_APPROVAL, DISCORD_WEBHOOK_ERRORS, DISCORD_WEBHOOK_COMMANDS
"""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import frontmatter
from google import genai
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(_REPO_ROOT))

import vault_sync  # noqa: E402
from watchers.discord_watcher import notify  # noqa: E402

load_dotenv()


# ── Config ──────────────────────────────────────────────────────────────────

VAULT_PATH     = vault_sync.VAULT_PATH
PAUSE_FLAG     = VAULT_PATH / "PAUSED"
POLL_SECONDS   = 300
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

VALID_ACTIONS = {"request_approval", "auto_process", "log_only", "escalate"}


# ── Gemini singleton ─────────────────────────────────────────────────────────

_client: Optional[object] = None


def _get_client() -> Optional[object]:
    global _client
    if _client is not None:
        return _client
    if not GEMINI_API_KEY:
        return None
    _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


# ── Fallback rule engine ─────────────────────────────────────────────────────

_FALLBACK_RULES: dict[str, str] = {
    "generate_posts": "request_approval",
    "publish_post":   "request_approval",
    "error":          "escalate",
}


def _fallback_decision(source: str) -> dict:
    action = _FALLBACK_RULES.get(source, "log_only")
    return {
        "action":    action,
        "reason":    f"Gemini unavailable — rule: source={source!r} → {action}",
        "next_step": "handled by fallback rule engine",
    }


# ── Gemini call ──────────────────────────────────────────────────────────────

_DECISION_PROMPT = """\
You are an AI employee assistant. Given this task, decide what to do.

Task: {title}
Source: {source}
Priority: {priority}
Content summary: {snippet}

Company rules:
- LinkedIn posts need approval before publishing
- Errors always notify Discord
- Content generation uses Groq not Gemini

Respond with JSON only:
{{"action": "request_approval" | "auto_process" | "log_only" | "escalate", "reason": "one sentence", "next_step": "one sentence instruction"}}"""


def _call_gemini(title: str, source: str, priority: str, snippet: str) -> dict:
    """
    Call Gemini 2.0 Flash with a compact prompt. Falls back to rule-based
    logic if Gemini is unconfigured, returns invalid JSON, or raises any error.
    """
    client = _get_client()
    if client is None:
        print("[orchestrator] GEMINI_API_KEY not set — using fallback rules")
        return _fallback_decision(source)

    prompt = _DECISION_PROMPT.format(
        title=title[:80],
        source=source[:40],
        priority=priority,
        snippet=snippet[:100],
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        raw = response.text.strip()

        # Strip accidental markdown fences
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()

        result = json.loads(raw)

        if result.get("action") not in VALID_ACTIONS:
            raise ValueError(f"Unknown action: {result.get('action')!r}")

        return result

    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[orchestrator] Gemini parse error: {exc} — using fallback")
        return _fallback_decision(source)
    except Exception as exc:
        print(f"[orchestrator] Gemini call failed: {exc} — using fallback")
        return _fallback_decision(source)


# ── File helpers ─────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp_and_move(path: Path, dest_dir: Path, extra_fields: dict) -> Path:
    """Update frontmatter on path, move to dest_dir, return new path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        post = frontmatter.load(str(path))
        for k, v in extra_fields.items():
            post[k] = v
        path.write_text(frontmatter.dumps(post), encoding="utf-8")
    except Exception as exc:
        print(f"[orchestrator] Frontmatter update failed for {path.name}: {exc}")
    dest = dest_dir / path.name
    shutil.move(str(path), str(dest))
    return dest


# ── Context summary ──────────────────────────────────────────────────────────

def _build_context_summary() -> str:
    """Return a <200-token status string — used for logging, not sent to Gemini."""
    na_count  = len(list((VAULT_PATH / "Needs_Action").glob("*.md")))     if (VAULT_PATH / "Needs_Action").exists()     else 0
    pa_count  = len(list((VAULT_PATH / "Pending_Approval").glob("*.md"))) if (VAULT_PATH / "Pending_Approval").exists() else 0
    done_dir  = VAULT_PATH / "Done"
    done_today = 0
    if done_dir.exists():
        today = datetime.now(timezone.utc).date()
        done_today = sum(
            1 for f in done_dir.glob("*.md")
            if datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).date() == today
        )
    return (
        f"{na_count} needs_action | {pa_count} pending_approval | "
        f"{done_today} done today | "
        f"{datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )


# ── Action dispatch ──────────────────────────────────────────────────────────

def _apply_action(
    item_path: Path,
    meta: dict,
    decision: dict,
    dry_run: bool,
) -> None:
    action = decision.get("action", "log_only")
    reason = decision.get("reason", "")
    title  = meta.get("title", item_path.stem)
    source = meta.get("source", "unknown")

    print(f"[orchestrator] {item_path.name} → {action} | {reason}")

    if dry_run:
        print(f"  [DRY RUN] Would execute: {action}")
        vault_sync.log_action(
            "orchestrator:dry_run", action,
            f"file={item_path.name} reason={reason!r}",
        )
        return

    if action == "request_approval":
        _stamp_and_move(item_path, VAULT_PATH / "Pending_Approval", {
            "status":      "pending_approval",
            "moved_at":    _now_iso(),
            "move_reason": reason,
        })
        notify(
            "approval",
            f"Needs approval: {title}",
            f"Source: `{source}`\nReason: {reason}",
            color="yellow",
        )
        vault_sync.log_action(
            "orchestrator:request_approval", "success",
            f"file={item_path.name}",
        )

    elif action == "auto_process":
        vault_sync.move_to_done(item_path.name)
        vault_sync.log_action(
            "orchestrator:auto_process", "done",
            f"file={item_path.name}",
        )

    elif action == "log_only":
        vault_sync.move_to_done(item_path.name)
        vault_sync.log_action(
            "orchestrator:log_only", "done",
            f"file={item_path.name}",
        )

    elif action == "escalate":
        # Move to Pending_Approval so it shows in !queue; mark escalated
        _stamp_and_move(item_path, VAULT_PATH / "Pending_Approval", {
            "status":       "escalated",
            "escalated_at": _now_iso(),
            "reason":       reason,
        })
        notify(
            "errors",
            f"Escalation required: {title}",
            f"Source: `{source}`\nReason: {reason}\n\nUse `!approve` or `!reject` after reviewing.",
            color="red",
            urgent=True,
        )
        vault_sync.log_action(
            "orchestrator:escalate", "notified",
            f"file={item_path.name}",
        )

    else:
        print(f"[orchestrator] Unknown action '{action}' for {item_path.name} — logging only")
        vault_sync.log_action(
            "orchestrator:unknown_action", "warning",
            f"action={action} file={item_path.name}",
        )


# ── One cycle ────────────────────────────────────────────────────────────────

def _run_cycle(dry_run: bool) -> None:
    if PAUSE_FLAG.exists():
        print("[orchestrator] System PAUSED — send !resume in Discord to continue.")
        vault_sync.log_action("orchestrator:cycle", "paused", "PAUSED flag present")
        return

    summary = _build_context_summary()
    print(f"[orchestrator] Cycle — {summary}")

    na_dir = VAULT_PATH / "Needs_Action"
    if not na_dir.exists():
        print("[orchestrator] Needs_Action directory missing — nothing to do.")
        return

    items = sorted(na_dir.glob("*.md"), key=lambda f: f.stat().st_ctime)
    if not items:
        print("[orchestrator] Needs_Action is empty.")
        if dry_run:
            print("Dry run complete — 0 items processed")
        return

    print(f"[orchestrator] Processing {len(items)} item(s)...")

    processed = 0
    for item_path in items:
        try:
            post    = frontmatter.load(str(item_path))
            meta    = post.metadata
            content = post.content

            title    = meta.get("title", item_path.stem)
            source   = meta.get("source", "unknown")
            priority = meta.get("priority", "normal")
            snippet  = content[:100].strip().replace("\n", " ")

            decision = _call_gemini(title, source, priority, snippet)
            _apply_action(item_path, meta, decision, dry_run)
            processed += 1

        except Exception as exc:
            print(f"[orchestrator] Error processing {item_path.name}: {exc}")
            vault_sync.log_action(
                "orchestrator:error", "error",
                f"file={item_path.name} error={exc}",
            )
            if not dry_run:
                notify(
                    "errors",
                    "Orchestrator processing error",
                    f"File: `{item_path.name}`\n{exc}",
                    color="red",
                    urgent=True,
                )

    if dry_run:
        print(f"Dry run complete — {processed} item(s) processed")

    vault_sync.log_action("orchestrator:cycle", "complete", summary)


# ── Entry point ──────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> None:
    """Poll vault/Needs_Action/ every POLL_SECONDS. Runs until KeyboardInterrupt."""
    print(f"[orchestrator] Starting. Poll interval: {POLL_SECONDS}s. Dry-run: {dry_run}")
    vault_sync.log_action("orchestrator:start", "running", f"dry_run={dry_run}")

    try:
        while True:
            try:
                _run_cycle(dry_run)
            except Exception as exc:
                print(f"[orchestrator] Unhandled cycle error: {exc}")
                vault_sync.log_action("orchestrator:cycle_error", "error", str(exc))

            print(f"[orchestrator] Sleeping {POLL_SECONDS}s...")
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\n[orchestrator] Keyboard interrupt — shutting down.")
        vault_sync.log_action("orchestrator:stop", "shutdown", "KeyboardInterrupt")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LinkedIn AI Employee orchestrator")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print decisions without moving files or notifying Discord",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle then exit (useful for debugging)",
    )
    args = parser.parse_args()

    if args.once:
        _run_cycle(dry_run=args.dry_run)
    else:
        run(dry_run=args.dry_run)
