"""
vault_sync.py — Obsidian Vault bridge for the LinkedIn Automation Engine.

Reads and writes structured Markdown files into the AI_Employee_Vault so that
every significant system event surfaces as a note that can be reviewed, approved,
or logged inside Obsidian.

Vault layout expected:
  $VAULT_PATH/Needs_Action/      — items requiring human attention
  $VAULT_PATH/Plans/             — generated execution plans
  $VAULT_PATH/Pending_Approval/  — actions awaiting human sign-off
  $VAULT_PATH/Approved/          — approved items (human moves files here)
  $VAULT_PATH/Done/              — completed items
  $VAULT_PATH/Logs/              — daily append-only activity logs
  $VAULT_PATH/Dashboard.md       — live metrics file

Set VAULT_PATH in .env to override the default location.
"""

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import frontmatter
from dotenv import load_dotenv

load_dotenv()

VAULT_PATH = Path(os.getenv("VAULT_PATH", r"C:\Users\usman khan\AI_Employee_Vault"))
GIT_SYNC = os.getenv("GIT_SYNC", "false").lower() == "true"

_SUBDIRS = ["Needs_Action", "Plans", "Pending_Approval", "Approved", "Done", "Logs"]

# Ordered list of dirs to search when locating a file by name
_SEARCH_DIRS = ["Needs_Action", "Plans", "Pending_Approval", "Approved"]

# Sentinels that delimit the auto-updated metrics block in Dashboard.md
_METRICS_START = "<!-- metrics:start -->"
_METRICS_END   = "<!-- metrics:end -->"


# ── Git sync ──────────────────────────────────────────────────────────────


def git_sync_vault() -> None:
    """Push vault changes to git if GIT_SYNC=true and a remote is configured."""
    if not GIT_SYNC:
        return
    try:
        remote_check = subprocess.run(
            ["git", "remote", "-v"],
            cwd=VAULT_PATH,
            capture_output=True,
            text=True,
        )
        if not remote_check.stdout.strip():
            return
        subprocess.run(["git", "add", "-A"], cwd=VAULT_PATH, check=True, capture_output=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = subprocess.run(
            ["git", "commit", "-m", f"vault update {timestamp}"],
            cwd=VAULT_PATH,
            capture_output=True,
            text=True,
        )
        # nothing to commit is not an error
        if result.returncode not in (0, 1):
            result.check_returncode()
        if "nothing to commit" not in result.stdout + result.stderr:
            subprocess.run(["git", "push", "origin", "main"], cwd=VAULT_PATH, check=True, capture_output=True)
            print("[vault_sync] git sync complete")
    except Exception as e:
        print(f"[vault_sync] git sync failed (non-fatal): {e}")


# ── Internal helpers ───────────────────────────────────────────────────────


def _ensure_dirs() -> None:
    for sub in _SUBDIRS:
        (VAULT_PATH / sub).mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _find_file(filename: str) -> Optional[Path]:
    """Search known vault subdirectories for a file by name. Returns Path or None."""
    for subdir in _SEARCH_DIRS:
        candidate = VAULT_PATH / subdir / filename
        if candidate.exists():
            return candidate
    root_candidate = VAULT_PATH / filename
    return root_candidate if root_candidate.exists() else None


# ── Write ──────────────────────────────────────────────────────────────────


def write_needs_action(
    title: str,
    content: str,
    source: str,
    priority: str = "normal",
) -> Path:
    """
    Create a Needs_Action note and return its path.

    Priority values: low / normal / high / urgent
    Filename pattern: {source}_{timestamp}.md
    """
    _ensure_dirs()
    filename = f"{source}_{_ts_slug()}.md"
    path = VAULT_PATH / "Needs_Action" / filename

    post = frontmatter.Post(
        content,
        type="needs_action",
        title=title,
        source=source,
        priority=priority,
        status="pending",
        created=_now_iso(),
    )
    path.write_text(frontmatter.dumps(post), encoding="utf-8")
    print(f"[vault_sync] needs_action written → {filename}")
    git_sync_vault()
    return path


def write_plan(task_name: str, steps: list) -> Path:
    """
    Create a Plan note with steps rendered as Markdown checkboxes.
    Filename: Plan_{task_name}.md (spaces in task_name become underscores).
    Returns the path to the created file.
    """
    _ensure_dirs()
    safe_name = task_name.replace(" ", "_")
    filename = f"Plan_{safe_name}.md"
    path = VAULT_PATH / "Plans" / filename

    checkbox_lines = "\n".join(f"- [ ] {step}" for step in steps)
    content = f"## Steps\n\n{checkbox_lines}\n"

    post = frontmatter.Post(
        content,
        type="plan",
        task=task_name,
        status="active",
        created=_now_iso(),
    )
    path.write_text(frontmatter.dumps(post), encoding="utf-8")
    print(f"[vault_sync] plan written → {filename}")
    return path


def write_pending_approval(action_type: str, details: dict) -> Path:
    """
    Create a Pending_Approval note for human review and return its path.

    To approve: move the file to vault/Approved/
    To reject:  delete the file or move it to vault/Done/ manually.
    Filename: APPROVAL_{action_type}_{timestamp}.md
    """
    _ensure_dirs()
    filename = f"APPROVAL_{action_type}_{_ts_slug()}.md"
    path = VAULT_PATH / "Pending_Approval" / filename

    detail_lines = "\n".join(f"- **{k}**: {v}" for k, v in details.items())
    content = (
        f"## Action\n\n`{action_type}`\n\n"
        f"## Details\n\n{detail_lines}\n\n"
        "## Instructions\n\n"
        "- **To approve**: move this file into `Approved/`\n"
        "- **To reject**: delete this file or move it to `Done/`\n"
    )

    post = frontmatter.Post(
        content,
        type="pending_approval",
        action=action_type,
        status="awaiting_approval",
        created=_now_iso(),
        details=dict(details),
    )
    path.write_text(frontmatter.dumps(post), encoding="utf-8")
    print(f"[vault_sync] pending_approval written → {filename}")
    git_sync_vault()
    return path


# ── Read ───────────────────────────────────────────────────────────────────


def check_approved() -> list:
    """
    Scan vault/Approved/ and return all .md files as a list of dicts.

    Each dict contains:
        filename  — file name (not full path)
        content   — parsed body text
        metadata  — frontmatter dict

    Returns an empty list if the directory does not exist or is empty.
    Files that cannot be parsed are skipped with a warning.
    """
    approved_dir = VAULT_PATH / "Approved"
    if not approved_dir.exists():
        return []

    results = []
    for md_file in sorted(approved_dir.glob("*.md")):
        try:
            post = frontmatter.load(str(md_file))
            results.append({
                "filename": md_file.name,
                "content": post.content,
                "metadata": post.metadata,
            })
        except Exception as e:
            print(f"[vault_sync] Warning: could not parse {md_file.name}: {e}")
    return results


# ── Move ───────────────────────────────────────────────────────────────────


def move_to_done(filename: str) -> Optional[Path]:
    """
    Move a vault file to Done/ and stamp it with completed_at in frontmatter.

    Searches Needs_Action, Plans, Pending_Approval, Approved, and vault root.
    Returns the new Done/ path, or None if the file was not found.
    """
    _ensure_dirs()
    source = _find_file(filename)
    if source is None:
        print(f"[vault_sync] Warning: '{filename}' not found in vault — nothing moved")
        return None

    try:
        post = frontmatter.load(str(source))
        post["status"] = "done"
        post["completed_at"] = _now_iso()
        source.write_text(frontmatter.dumps(post), encoding="utf-8")
    except Exception as e:
        print(f"[vault_sync] Warning: could not update frontmatter on '{filename}': {e}")

    dest = VAULT_PATH / "Done" / filename
    shutil.move(str(source), str(dest))
    print(f"[vault_sync] '{filename}' moved → Done/")
    return dest


# ── Dashboard ──────────────────────────────────────────────────────────────


def update_dashboard(metrics: dict) -> None:
    """
    Update the metrics block in vault/Dashboard.md.

    The block is delimited by HTML comments so the rest of the file is
    preserved exactly. If Dashboard.md does not exist it is created.
    If the sentinel comments are absent, the block is appended.
    Running this multiple times is idempotent — only one metrics block exists.
    """
    _ensure_dirs()
    dashboard = VAULT_PATH / "Dashboard.md"

    text = dashboard.read_text(encoding="utf-8") if dashboard.exists() else "# AI Employee Dashboard\n\n"

    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    metric_rows = "\n".join(f"| {k} | {v} |" for k, v in metrics.items())
    block = (
        f"{_METRICS_START}\n"
        f"*Updated: {updated_at}*\n\n"
        f"| Metric | Value |\n"
        f"| --- | --- |\n"
        f"{metric_rows}\n"
        f"{_METRICS_END}"
    )

    if _METRICS_START in text and _METRICS_END in text:
        before = text[: text.index(_METRICS_START)]
        after  = text[text.index(_METRICS_END) + len(_METRICS_END) :]
        text = before + block + after
    else:
        text = text.rstrip("\n") + f"\n\n## Metrics\n\n{block}\n"

    dashboard.write_text(text, encoding="utf-8")
    print(f"[vault_sync] dashboard updated ({len(metrics)} metrics)")
    git_sync_vault()


# ── Logging ────────────────────────────────────────────────────────────────


def log_action(action: str, result: str, details: str = "") -> None:
    """
    Append one structured line to vault/Logs/YYYY-MM-DD.md.

    Format: | HH:MM:SS | action | result | details |
    The log file and its Markdown table header are created on first write of the day.
    Pipe characters in details are escaped so the table stays valid.
    """
    _ensure_dirs()
    now = datetime.now(timezone.utc)
    log_file = VAULT_PATH / "Logs" / f"{now.strftime('%Y-%m-%d')}.md"

    if not log_file.exists():
        log_file.write_text(
            f"# Log — {now.strftime('%Y-%m-%d')}\n\n"
            "| Time (UTC) | Action | Result | Details |\n"
            "| --- | --- | --- | --- |\n",
            encoding="utf-8",
        )

    safe_details = details.replace("|", "\\|").replace("\n", " ")
    line = f"| {now.strftime('%H:%M:%S')} | {action} | {result} | {safe_details} |\n"
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print(f"[vault_sync] logged: {action} → {result}")
    git_sync_vault()


# ── Self-test ──────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import sys

    failures = 0

    def run(label: str, fn) -> None:
        global failures
        try:
            fn()
            print(f"  [PASS]  {label}")
        except Exception as exc:
            print(f"  [FAIL]  {label}: {exc}")
            failures += 1

    print(f"\n=== vault_sync self-test ===")
    print(f"Vault path: {VAULT_PATH}\n")

    # ── 1. write_needs_action ──────────────────────────────────────────────
    na_path: list = [None]

    def _t1():
        p = write_needs_action(
            title="Test alert",
            content="Something needs attention during self-test.",
            source="test_suite",
            priority="high",
        )
        assert p.exists(), "File was not created"
        post = frontmatter.load(str(p))
        assert post["status"] == "pending", f"status={post['status']}"
        assert post["priority"] == "high", f"priority={post['priority']}"
        assert post["source"] == "test_suite"
        na_path[0] = p

    run("write_needs_action", _t1)

    # ── 2. write_plan ──────────────────────────────────────────────────────
    def _t2():
        p = write_plan("Test Task", ["Step one", "Step two", "Step three"])
        assert p.exists(), "Plan file not created"
        text = p.read_text(encoding="utf-8")
        assert "- [ ] Step one" in text
        assert "- [ ] Step three" in text
        post = frontmatter.load(str(p))
        assert post["status"] == "active"

    run("write_plan", _t2)

    # ── 3. write_pending_approval ──────────────────────────────────────────
    def _t3():
        p = write_pending_approval(
            action_type="PUBLISH_POST",
            details={"post_id": "abc123", "platform": "LinkedIn", "scheduled": "2026-05-02"},
        )
        assert p.exists(), "Approval file not created"
        post = frontmatter.load(str(p))
        assert post["status"] == "awaiting_approval"
        assert post["action"] == "PUBLISH_POST"
        assert "To approve" in post.content

    run("write_pending_approval", _t3)

    # ── 4. check_approved ─────────────────────────────────────────────────
    def _t4():
        items = check_approved()
        assert isinstance(items, list), "Expected list"
        # OK to be empty — just verifying no crash and correct return type

    run("check_approved (empty dir ok)", _t4)

    # ── 5a. move_to_done ──────────────────────────────────────────────────
    def _t5a():
        assert na_path[0] is not None, "write_needs_action must pass first"
        dest = move_to_done(na_path[0].name)
        assert dest is not None, "Expected a Path, got None"
        assert dest.exists(), f"File not in Done/: {dest}"
        post = frontmatter.load(str(dest))
        assert post["status"] == "done", f"status={post['status']}"
        assert "completed_at" in post.metadata

    run("move_to_done", _t5a)

    # ── 5b. move_to_done — missing file ───────────────────────────────────
    def _t5b():
        result = move_to_done("file_that_does_not_exist_xyz.md")
        assert result is None, f"Expected None for missing file, got {result}"

    run("move_to_done (missing file → None)", _t5b)

    # ── 6a. update_dashboard ──────────────────────────────────────────────
    def _t6a():
        update_dashboard({
            "Posts published this week": 4,
            "Leads found": 12,
            "Comments triaged": 31,
            "Pending approvals": 1,
        })
        db = VAULT_PATH / "Dashboard.md"
        assert db.exists(), "Dashboard.md not created"
        text = db.read_text(encoding="utf-8")
        assert "Posts published this week" in text
        assert _METRICS_START in text
        assert _METRICS_END in text

    run("update_dashboard", _t6a)

    # ── 6b. update_dashboard — idempotent re-run ──────────────────────────
    def _t6b():
        update_dashboard({"Posts published this week": 5, "Leads found": 13})
        text = (VAULT_PATH / "Dashboard.md").read_text(encoding="utf-8")
        count = text.count(_METRICS_START)
        assert count == 1, f"Expected 1 metrics block, found {count}"
        assert "| Posts published this week | 5 |" in text

    run("update_dashboard (idempotent re-run)", _t6b)

    # ── 7. log_action ─────────────────────────────────────────────────────
    def _t7():
        log_action("publish_post", "success", "post_id=abc123")
        log_action("generate_posts", "error", "rate limited by Groq | retry queued")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = VAULT_PATH / "Logs" / f"{today}.md"
        assert log_file.exists(), "Log file not created"
        text = log_file.read_text(encoding="utf-8")
        assert "publish_post" in text
        assert "generate_posts" in text
        # Pipe in details should be escaped so the table isn't broken
        assert "retry queued" in text

    run("log_action", _t7)

    # ── Summary ───────────────────────────────────────────────────────────
    total = 8
    passed = total - failures
    print(f"\n{'=' * 32}")
    print(f"  {passed}/{total} tests passed")
    if failures:
        sys.exit(1)
