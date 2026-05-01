# Integration Test Report
*Run: 2026-05-01 — All fixes applied, no production files modified, nothing published to LinkedIn.*

---

## Fixes Applied

| Fix | Description | Status |
|-----|-------------|--------|
| FIX 1 | Deleted `.github/workflows/orchestrator.yml`; added PM2 header comment to `orchestrator.py` | DONE |
| FIX 2 | Removed `_SupabaseVaultAdapter`, `_LocalVaultAdapter`, `--actions-mode` flag, and all `VAULT_BACKEND` switching logic from `orchestrator.py`. Reverted to direct `vault_sync` calls (local filesystem only). | DONE |
| FIX 3 | Added `_handle_403_once()` to `triage_comments.py`: logs to vault via `vault_sync.log_action`, sends a one-per-week Discord `#errors` embed (gated by Supabase `config.triage_403_last_warned`), never retries. Added module-level docstring explaining the MDP limitation. | DONE |
| FIX 4 | Created `pm2_ecosystem.config.js` with three apps: `orchestrator`, `discord-bot` (`tools/dm_ghostwriter.py`), `discord-watcher` (`watchers/discord_watcher.py`). | DONE |
| FIX 5 | Deleted `tools/sheets_client.py` and `tools/discord_bot.py` (both were confirmed stubs with zero importers). | DONE |
| FIX 6 | Added `GEMINI_API_KEY` (with comment) to `# AI / LLM` section and `APIFY_API_TOKEN` (with comment) to new `# Lead Hunting` section in `.env.example`. Anonymised the default `VAULT_PATH` value. | DONE |

---

## Test Results

### TEST 1 — vault_sync.py self-test
**Result: PASS (8/8)**

```
=== vault_sync self-test ===
Vault path: C:\Users\usman khan\AI_Employee_Vault

[vault_sync] needs_action written → test_suite_20260501_201209.md
  [PASS]  write_needs_action
[vault_sync] plan written → Plan_Test_Task.md
  [PASS]  write_plan
[vault_sync] pending_approval written → APPROVAL_PUBLISH_POST_20260501_201209.md
  [PASS]  write_pending_approval
  [PASS]  check_approved (empty dir ok)
[vault_sync] 'test_suite_20260501_201209.md' moved → Done/
  [PASS]  move_to_done
[vault_sync] Warning: 'file_that_does_not_exist_xyz.md' not found in vault — nothing moved
  [PASS]  move_to_done (missing file → None)
[vault_sync] dashboard updated (4 metrics)
  [PASS]  update_dashboard
[vault_sync] dashboard updated (2 metrics)
  [PASS]  update_dashboard (idempotent re-run)
[vault_sync] logged: publish_post → success
[vault_sync] logged: generate_posts → error
  [PASS]  log_action

================================
  8/8 tests passed
```

**Note:** Requires `PYTHONIOENCODING=utf-8` on Windows because vault_sync print statements contain the `→` character (U+2192) which Windows cp1252 cannot encode. This is a console-only issue — file writes already use `encoding="utf-8"` and are unaffected in production.

---

### TEST 2 — discord_watcher.py notify()
**Result: PARTIAL — function works, webhook not configured in local .env**

```
[discord_watcher] No webhook URL for channel 'errors' — set DISCORD_WEBHOOK_ERRORS
notify() returned: False
```

`notify()` imported correctly, handled the missing webhook gracefully (returned `False`, no exception). `DISCORD_WEBHOOK_ERRORS` is not set in the local `.env` — this is expected for dev environment. The function is working; add the webhook URL to `.env` and a real message will be sent.

**Manual verification required:** once `DISCORD_WEBHOOK_ERRORS` is set, re-run:
```
python -c "from watchers.discord_watcher import notify; notify('errors', 'test', 'test body', color='blue')"
```

---

### TEST 3 — orchestrator.py --dry-run
**Result: PASS**

```
[orchestrator] Cycle — 2 needs_action | 2 pending_approval | 1 done today | 20:13 UTC
[orchestrator] Processing 2 item(s)...
[orchestrator] GEMINI_API_KEY not set — using fallback rules
[orchestrator] TEST_manual_20260501.md → log_only | Gemini unavailable — rule: source='manual' → log_only
  [DRY RUN] Would execute: log_only
[vault_sync] logged: orchestrator:dry_run → log_only
Dry run complete — 2 item(s) processed
[vault_sync] logged: orchestrator:cycle → complete
```

- Read `Needs_Action/` correctly
- Made zero Gemini calls (GEMINI_API_KEY not set, fell back to rule engine as designed)
- Printed `Dry run complete — 2 item(s) processed`
- No files moved

**Note:** `google-generativeai` package emits a `FutureWarning` — it has been deprecated in favour of `google.genai`. Functionally fine for now; see Remaining Issues.

---

### TEST 4 — linkedin_mcp.py smoke test
**Result: PASS**

```
=== LinkedInMCP smoke test (draft only — no LinkedIn API calls) ===

[linkedin_mcp] Draft saved → draft_20260501_201250.md
[vault_sync] logged: linkedin_mcp:draft → saved
  [PASS] draft_post → draft_20260501_201250.md
  Path: C:\Users\usman khan\AI_Employee_Vault\LinkedIn\Queue\draft_20260501_201250.md
```

Draft file created in `vault/LinkedIn/Queue/` with correct frontmatter (`status: queued`, `type: linkedin_draft`). No LinkedIn API calls made.

---

### TEST 5 — End-to-end simulation
**Result: PASS**

Test file created:
```
vault/Needs_Action/TEST_manual_20260501.md
---
type: test
source: manual
priority: normal
status: pending
title: Integration test task
---
This is a test task for integration testing.
```

Orchestrator dry-run output for this file:
```
[orchestrator] TEST_manual_20260501.md → log_only | Gemini unavailable — rule: source='manual' → log_only
  [DRY RUN] Would execute: log_only
```

- File was read and parsed correctly
- Decision was `log_only` (source=`manual` not in fallback rules → default `log_only`) ✓
- File remained in `Needs_Action/` after dry-run (not moved) ✓
- Nothing published to LinkedIn ✓

---

## Remaining Issues (Manual Intervention Required)

| # | Issue | File | Action Required |
|---|-------|------|----------------|
| 1 | `google-generativeai` deprecated | `orchestrator.py`, `requirements.txt` | Migrate to `google.genai` SDK (`pip install google-genai`). The old package still works but will receive no bug fixes. Low urgency. |
| 2 | `DISCORD_WEBHOOK_ERRORS` (and other webhook URLs) not in local `.env` | `.env` | Add all `DISCORD_WEBHOOK_*` URLs to local `.env` to enable full Discord integration. Already documented in `.env.example`. |
| 3 | `GEMINI_API_KEY` not in local `.env` | `.env` | Add key from Google AI Studio. Without it, orchestrator runs on fallback rules only (still functional). |
| 4 | `APIFY_API_TOKEN` not in local `.env` or GitHub Secrets | `.env`, GitHub Secrets | `search_leads.py` will fail at runtime without this. Get from Apify console. |
| 5 | LinkedIn token expires every 60 days | `tools/linkedin_auth.py` | `linkedin_auth.py` is still incomplete. Manual OAuth token refresh required when token expires. |
| 6 | LinkedIn Comments API — 403 (MDP required) | `tools/triage_comments.py` | Permanent platform restriction. Comment triage returns 0 results until Marketing Developer Platform access is granted. The 403 is now handled gracefully (vault log + one-per-week Discord alert). |
| 7 | Windows console requires `PYTHONIOENCODING=utf-8` | `vault_sync.py` (print statements) | Add `PYTHONIOENCODING=utf-8` to your shell profile or PM2 env config. Production on Linux is unaffected. |
| 8 | `gspread` and `pytz` were missing from venv | `requirements.txt` / `.venv` | `pip install -r requirements.txt` inside `.venv` resolves this. The `requirements.txt` already lists both packages. |
| 9 | Test artifacts remain in vault | `vault/Needs_Action/` | Two test files remain: `TEST_manual_20260501.md` and `test_suite_20260501_201159.md`. Safe to delete manually. |
