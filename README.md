# LinkedIn Automation System

An automation system for planning, generating, approving, publishing, and monitoring LinkedIn content using Python scripts, GitHub Actions, Discord bots/webhooks, Google Sheets, and Supabase.

This repository uses a **WAT-style split**:
- **Workflows** (`workflows/*.md`) for SOP/instructions
- **Agents/orchestration** (`orchestrator.py`, Discord bots)
- **Tools** (`tools/*.py`) for deterministic execution

---

## Features

### 1) Content Planning & Weekly Batch Generation
- Reads pending topics from Google Sheets **Topic Bank** (`tools/generate_posts.py` via `tools/sheets_helper.py`)
- Generates up to 5 posts for next Mon–Fri with Groq (`GROQ_MODEL`, default `llama-3.3-70b-versatile`)
- Auto-assigns audience/category logic by weekday (engineer vs founder)
- Writes back to Sheets columns including:
  - `post_text`, `hook`, `bullet_points`, `headline`, `audience`, `status`, `scheduled_date`
- Duplicate-week guard: skips generation if the upcoming week already has scheduled content
- Includes `--dry-run` mode for integration checks without writing production content

### 2) Image Generation (HTML Template Rendering)
- `tools/generate_images.py` renders branded 1080x1350 PNGs using **Playwright + Chromium** from HTML templates:
  - `templates/technical_post.html`
  - `templates/business_post.html`
  - `templates/story_post.html`
- Uses extracted `hook/headline/bullet_points` for infographic layout
- Uploads PNGs to Supabase Storage bucket (`SUPABASE_IMAGE_BUCKET`, default `post-images`)
- Writes public image URL back to Sheets `image_path`
- Falls back gracefully if render/upload fails (post can still proceed text-only path)

### 3) Ready-Post Intake from Discord (Verbatim Content)
- In `#ready-posts`, `tools/ready_post_intake.py`:
  - Uses pasted post text **as-is** (no rewriting)
  - Extracts structure (hook/headline/bullets/category/audience) with Groq
  - Renders image with Playwright
  - Stores post in Supabase
  - Queues next available weekday in Sheets
  - Sends Discord preview with **Approve & Schedule** button
- Includes idempotent dedup by Discord message ID (`topic=ready:{message_id}`)

### 4) Story Intake from Discord
- In `#stories`, `tools/story_intake.py`:
  - Validates story length and deduplicates submissions
  - Uses Groq to transform raw story into publish-ready post + extracted points
  - Saves story + post in Supabase
  - Schedules next available Saturday slot in Sheets
  - Responds with preview and scheduling status in Discord

### 5) Publishing Pipeline (LinkedIn API)
- `tools/publish_post.py` publishes approved content to LinkedIn UGC API
- Supports:
  - Registering LinkedIn image upload
  - Uploading image bytes (from URL or local path)
  - Posting text-only fallback if image upload fails
  - Sheets status updates to `published`
  - Supabase record insertion/update for published posts
  - Discord publish confirmations and error alerts
- Includes token handling:
  - 401 handling with refresh-token attempt
  - 429 rate-limit wait-and-retry

### 6) Comment Triage & Routing
- `tools/triage_comments.py`:
  - Pulls comments for recently published posts (lookback window)
  - Deduplicates via `processed_comments`
  - Classifies comments in Groq batches into A/B/C:
    - A = lead (Discord alert + drafted DM)
    - B = content idea (append to Sheets backlog)
    - C = noise (logged only)
- Operational safeguard:
  - Handles LinkedIn comments API 403 (**Marketing Developer Platform (MDP)** restriction) with one-per-week warning gate using `config` table

### 7) Lead Hunting
- `tools/search_leads.py`:
  - Uses Apify LinkedIn post search actor + multiple query patterns
  - Filters/deduplicates against `leads_seen`
  - Uses Groq to score lead quality and draft outreach comment + DM
  - Sends alerts for qualified leads to Discord webhook
  - Includes retry/delay logic for 429 and transient failures

### 8) Weekly Digest / Analytics Summary
- `tools/weekly_digest.py`:
  - Queries published posts from Supabase
  - Computes weekly totals and weighted engagement score
  - Compares against previous week (WoW deltas)
  - Sends digest to Discord
  - Updates `config.top_performers` (best-effort)

### 9) Human-in-the-Loop Control & Ops
- `watchers/discord_watcher.py` command bot for:
  - `!approve`, `!reject`, `!queue`, `!status`, `!pause`, `!resume`
- `tools/dm_ghostwriter.py` slash-command bot for DM drafting:
  - `/draft`, `/biz`, `/tech`, `/follow`, `/reply`, `/comment`, `/retry`
- Vault-based orchestration (`orchestrator.py` + `vault_sync.py`) for task lifecycle in Obsidian-style folders (`Needs_Action`, `Pending_Approval`, `Done`, etc.)

### 10) Operational Behaviors (from code)
- **Retries / backoff:** present for Groq, Apify, LinkedIn, and Supabase query paths
- **Rate-limit handling:** explicit handling for 429 in multiple scripts
- **Error handling:** non-fatal handling where possible + Discord notifications + vault logs
- **Scheduling:** GitHub Actions cron + persistent PM2 processes for bots/orchestrator
- **Deduplication:** comments, stories, lead URLs, ready-post intake all have dedup logic
- **Not implemented in codebase:** proxy rotation, CAPTCHA solving, and interactive 2FA automation

### 11) Present in Repository but Not Fully Wired / Legacy
- `tools/linkedin_auth.py` exists but main flow is currently TODO/incomplete
- `tools/generate_post.py`, `tools/generate_image.py`, and `tools/publish_linkedin.py` provide an alternate/legacy DB-first pipeline (still usable scripts, but main scheduled path currently uses `generate_posts.py` + `generate_images.py` + `publish_post.py`)
- `mcp_servers/*` are stubs/partial integration surfaces

---

## Tech Stack

### Language & Runtime
- **Python 3.11** (GitHub Actions workflows pin this version)
- **Node.js/PM2** configs for long-running process management (`pm2_ecosystem*.config.js`)

### Core Python Libraries (`requirements.txt`)
- **LLM:** `groq`, `anthropic`, `openai`, `google-genai`
- **Data/API:** `requests`, `python-dotenv`
- **Database/Storage:** `supabase`
- **Google integration:** `gspread`, `google-auth`, `google-auth-httplib2`, `google-api-python-client`
- **Discord:** `discord.py`
- **Automation/rendering:** `playwright`
- **Images:** `Pillow`
- **Utilities:** `pytz`, `python-frontmatter`

### External Services
- **LinkedIn API v2** (UGC posting, social actions endpoints)
- **Supabase Postgres + Storage**
- **Google Sheets API**
- **Discord bot + webhooks**
- **Apify** (lead search source)
- **GitHub Actions** (scheduled/dispatch automation)

### Templates / UI Layer
- HTML/CSS templates for social image rendering (Playwright screenshot pipeline)
- Discord embeds/buttons for approval and operational control
- Local HTTP callback scaffold for LinkedIn OAuth exists (`http.server` usage in `linkedin_auth.py`)

### Deployment / Operations
- GitHub Actions workflow automation (`.github/workflows/*.yml`)
- PM2 process manager configs for persistent services
- Deploy scripts for remote host sync/restart (`deploy.sh`, deploy workflows)

---

## Data & Storage

### Supabase Tables in Schema Files
- `posts`
- `stories`
- `processed_comments`
- `config`
- `content_backlog`
- `leads_seen`
- `vault_items` (in `supabase/vault_items.sql`)

### PL/pgSQL / Trigger Routines Present
- `update_updated_at()` trigger for posts (legacy schema file)
- `update_config_updated_at()` trigger for config
- `set_vault_items_updated_at()` trigger for vault_items

> Note: The repo currently contains both `schema/supabase_schema.sql` (broader current schema) and `supabase/schema.sql` (legacy/alternate shape). In practice, treat `schema/supabase_schema.sql` as the primary source and `supabase/schema.sql` as deprecated legacy to avoid schema drift.

---

## Project Structure & Key Entrypoints

```text
.
├── .github/workflows/          # Scheduled/manual GitHub Actions jobs
├── tools/                      # Core automation scripts
├── watchers/                   # Discord command watcher + notifications
├── templates/                  # HTML image templates
├── workflows/                  # SOP docs / workflow specs
├── schema/                     # Main Supabase SQL schema
├── supabase/                   # Additional SQL (legacy + vault items)
├── orchestrator.py             # Vault/task orchestrator loop
├── vault_sync.py               # Vault bridge + logging/dashboard helpers
├── pm2_ecosystem*.config.js    # PM2 process configs
└── .env.example                # Environment variable template
```

Primary entrypoints used in practice:
- `tools/generate_posts.py`
- `tools/generate_images.py`
- `tools/publish_post.py`
- `tools/triage_comments.py`
- `tools/search_leads.py`
- `tools/weekly_digest.py`
- `tools/dm_ghostwriter.py`
- `watchers/discord_watcher.py`
- `orchestrator.py`

---

## Setup / Installation

### 1) Clone and install
```bash
git clone https://github.com/usmankhan045/linkedin-automation.git
cd linkedin-automation
python -m pip install -r requirements.txt
```

### 2) Playwright browser install (required for image rendering)
```bash
playwright install chromium --with-deps
```

### 3) Configure environment
```bash
cp .env.example .env
```
Populate required values (see Configuration section below).

### 4) Google credentials
- Provide service account JSON via one of:
  - `GOOGLE_SERVICE_ACCOUNT_JSON` (raw JSON string; common in CI)
  - `GOOGLE_SERVICE_ACCOUNT_JSON_PATH` (path to local `credentials.json`)
- Share the target sheet with the service account email.

### 5) Supabase setup
- Run schema SQL (choose your target schema file and stay consistent)
- Create storage bucket (default: `post-images`)
- Set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`

### 6) Optional persistent services
- Start long-running bots/orchestrator with PM2 using provided config files.

---

## Configuration

The repository’s Python code reads the following env vars:

### AI / LLM
- `GROQ_API_KEY`, `GROQ_MODEL`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY`

### LinkedIn
- `LINKEDIN_ACCESS_TOKEN`
- `LINKEDIN_REFRESH_TOKEN`
- `LINKEDIN_CLIENT_ID`
- `LINKEDIN_CLIENT_SECRET`
- `LINKEDIN_PERSON_URN`

### Supabase
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_IMAGE_BUCKET`

### Google Sheets
- `GOOGLE_SHEETS_SPREADSHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GOOGLE_SERVICE_ACCOUNT_JSON_PATH`
- `SHEETS_TOPIC_BANK_TAB`
- `SHEETS_CONTENT_BACKLOG_TAB`

### Discord
- `DISCORD_BOT_TOKEN`
- `DISCORD_GHOSTWRITER_CHANNEL_ID`
- `DISCORD_STORIES_CHANNEL_ID`
- `DISCORD_READY_POSTS_CHANNEL_ID`
- `DISCORD_COMMANDS_CHANNEL_ID`
- `DISCORD_WEBHOOK_CONFIRMATIONS`
- `DISCORD_WEBHOOK_NEEDS_ACTION`
- `DISCORD_WEBHOOK_APPROVAL`
- `DISCORD_WEBHOOK_LINKEDIN`
- `DISCORD_WEBHOOK_ERRORS`
- `DISCORD_WEBHOOK_BRIEFINGS`
- `DISCORD_WEBHOOK_COMMANDS`
- `DISCORD_WEBHOOK_READY_POSTS`
- `DISCORD_LEADS_WEBHOOK_URL`
- `DISCORD_LEAD_HUNTER_WEBHOOK_URL`
- `DISCORD_DIGEST_WEBHOOK_URL`

### Lead hunting / ops
- `APIFY_API_TOKEN`
- `VAULT_PATH`
- `GIT_SYNC`

---

## Usage

### Local script examples

Generate weekly drafts:
```bash
python tools/generate_posts.py
```

Generate images for scheduled pending posts:
```bash
python tools/generate_images.py
```

Publish today’s approved post:
```bash
python tools/publish_post.py
```

Run comment triage manually:
```bash
python tools/triage_comments.py
```

Run lead hunter manually:
```bash
python tools/search_leads.py
```

Send weekly digest now:
```bash
python tools/weekly_digest.py
```

Run DM ghostwriter bot:
```bash
python tools/dm_ghostwriter.py
```

Run command watcher bot:
```bash
python watchers/discord_watcher.py
```

Orchestrator dry run:
```bash
python orchestrator.py --once --dry-run
```

### GitHub Actions workflows
- `daily_publish.yml` — scheduled weekdays publish
- `weekly_digest.yml` — scheduled weekly digest
- `lead_hunter.yml` — scheduled lead search
- `weekly_generation.yml` — currently manual (`workflow_dispatch`)
- `comment_triage.yml` — currently manual (`workflow_dispatch`)
- `deploy.yml` / `sync_secrets.yml` — deployment/ops

---

## Limitations, Safety, and Compliance

- LinkedIn API access has platform restrictions (notably comments/analytics 403 without elevated program access in some cases).
- Automating posting, scraping, or engagement can violate LinkedIn ToS depending on account/app permissions and usage patterns.
- Use conservative rate limits, avoid spam behavior, and keep human approval in the loop for outbound actions.
- You are responsible for compliance, account health, credential security, and regional/legal requirements.

---

## Validation Notes

Current repository includes integration/smoke style scripts/docs (e.g., `INTEGRATION_TEST.md`) but does not include a formal pytest test suite in this clone.
