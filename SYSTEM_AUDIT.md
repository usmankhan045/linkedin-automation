# LinkedIn Automation System Audit
*Generated: 2026-05-01 — Read-only audit, no files modified*

---

## 1. Repository Overview

This is a personal LinkedIn branding automation engine built on the **WAT framework** (Workflows, Agents, Tools). It runs entirely on free/low-cost tiers via GitHub Actions cron, with a persistent Discord bot for interactive commands. The system generates posts, creates branded images, publishes to LinkedIn, triages comments, hunts leads, and sends weekly analytics digests.

**Stack:** Python · Groq LLM · Google Sheets · Supabase · LinkedIn API v2 · Discord · Playwright · GitHub Actions

---

## 2. File Inventory & What Each Script Does

### Root Config Files

| File | Purpose |
|------|---------|
| `.env` | Live production credentials (see Security section) |
| `.env.example` | Template with 30+ env var definitions and setup instructions |
| `requirements.txt` | Python dependencies: supabase, gspread, groq, anthropic, openai, playwright, discord.py, requests, python-dotenv, pytz |
| `CLAUDE.md` | AI agent operating instructions (WAT framework rules) |
| `README.md` | System architecture documentation |
| `.gitignore` | Excludes `.env`, `credentials.json`, `.venv/`, `.tmp/` |

### `tools/` — 17 Python Scripts

| Script | What It Does | Status |
|--------|-------------|--------|
| `db_client.py` | Singleton Supabase client wrapper — CRUD for posts, stories, processed_comments, leads_seen tables; image upload to Supabase Storage | Working |
| `sheets_helper.py` | Google Sheets wrapper — reads Topic Bank (pending topics, today's post), writes status/headline/image_path columns, appends Content Backlog | Working |
| `sheets_client.py` | Alternative Sheets wrapper | **STUB — all functions are TODOs; use `sheets_helper.py` instead** |
| `generate_posts.py` | Sunday batch generator — reads 5 topics from Sheets, calls Groq once per post (llama-3.3-70b-versatile, temp=0.75), assigns Mon-Fri slots, writes to Supabase + Sheets, sends Discord summary | Working (v2) |
| `generate_post.py` | On-demand single-post generator — uses Claude claude-opus-4-6, three persona modes (technical/business/story), outputs JSON with content + image_prompt | Working |
| `generate_images.py` | Sunday batch image renderer — Playwright headless Chromium renders HTML templates to 1080×1350px PNG, uploads to Supabase Storage, updates Sheets `image_path` | Working |
| `generate_image.py` | On-demand DALL-E 3 image generator — fetches post from Supabase, calls OpenAI, uploads image, updates post record | Working (requires OPENAI_API_KEY) |
| `publish_post.py` | Daily publisher — finds today's "approved" post in Sheets (PKT timezone), downloads image from Supabase, registers LinkedIn image upload, creates UGC post, updates Sheets to "published", sends Discord confirmation | Working |
| `publish_linkedin.py` | Lower-level LinkedIn publisher — takes post_id, publishes from Supabase directly (no Sheets interaction) | Working (secondary path) |
| `linkedin_auth.py` | OAuth 2.0 token refresh flow for LinkedIn | **TODO — not fully implemented; manual token refresh required every 60 days** |
| `triage_comments.py` | Every 2 hours — fetches LinkedIn comments on last 14 days of posts, deduplicates against Supabase, batch-classifies via Groq (A=Lead/B=Idea/C=Noise), routes A→Discord #leads + DM draft, B→Sheets backlog | Working (see Known Issues) |
| `search_leads.py` | Twice daily — runs 4 Boolean Apify LinkedIn scraper queries, deduplicates, pre-filters job seekers, qualifies HIGH/MEDIUM/LOW via Groq, alerts Discord #leads for HIGH leads with comment + DM ready to paste | Working (requires APIFY_API_TOKEN) |
| `story_intake.py` | Handles Discord #stories submissions — validates, deduplicates, formats via Groq, saves to Supabase, finds next Saturday slot, writes to Sheets Topic Bank, sends Discord preview | Working |
| `discord_ingest.py` | Saturday morning — fetches latest unprocessed story from Discord #stories channel via bot | Working |
| `dm_ghostwriter.py` | Persistent Discord bot — slash commands: /biz, /tech, /follow, /reply, /comment, /draft, /retry; each calls Groq with persona-specific prompts; per-user in-memory state for /retry | Working (requires persistent hosting) |
| `discord_bot.py` | Main Discord bot entry point | **STUB — not implemented; `dm_ghostwriter.py` is the real implementation** |
| `weekly_digest.py` | Sunday analytics — queries Supabase for last 7 days of published posts, calculates WoW delta on impressions/likes/comments/shares, finds best/worst post, sends Discord #digest summary | Working (see Known Issues) |

### `workflows/` — 11 Markdown SOPs

| Workflow | Trigger | Tool(s) Called |
|----------|---------|----------------|
| `weekly_schedule.md` | Reference doc | All tools (master orchestration) |
| `weekly_generation.md` | Sunday 10am PKT (GitHub Actions) | `generate_posts.py`, `generate_images.py` |
| `daily_publishing.md` | Mon-Fri 12pm PKT (GitHub Actions) | `publish_post.py` |
| `comment_triage.md` | Every 2h weekdays 9am–7pm PKT | `triage_comments.py` |
| `ingest_story.md` | Saturday 10am PKT (GitHub Actions) | `discord_ingest.py` |
| `story_intake.md` | Discord on_message in #stories | `story_intake.py` |
| `dm_ghostwriter.md` | Discord slash commands in #ghostwriter | `dm_ghostwriter.py` |
| `weekly_digest.md` | Sunday 10am PKT (GitHub Actions) | `weekly_digest.py` |
| `generate_post.md` | On-demand / API call | `generate_post.py` |
| `generate_image.md` | On-demand / called by generate_posts.py | `generate_image.py` |
| `publish_post.md` | On-demand or GitHub Actions | `publish_linkedin.py` |

### `templates/` — HTML/CSS + Prompt Templates

| File | Purpose |
|------|---------|
| `technical_post.html` | 1080×1350px dark navy + orange infographic (engineers audience) |
| `business_post.html` | 1080×1350px dark charcoal + blue slide (founders audience) |
| `story_post.html` | 1080×1350px dark + green portfolio card (story audience) |
| `technical.py` | Groq/Claude prompt strings for technical persona ("Architect") |
| `business.py` | Groq/Claude prompt strings for business persona ("ROI Strategist") |
| `story.py` | Groq/Claude prompt strings for story intake |

**HTML placeholders used by `generate_images.py`:**
`{{HOOK}}`, `{{HEADLINE}}`, `{{BULLET_1}}` through `{{BULLET_5}}`, `{{CATEGORY_LABEL}}`, `{{POST_NUMBER}}`, `{{NAME}}`, `{{TITLE}}`

### `.github/workflows/` — GitHub Actions

| YAML File | Schedule (UTC) | Local Time (PKT) | Job |
|-----------|---------------|-----------------|-----|
| `daily_publish.yml` | `0 7 * * 1-5` | Mon-Fri 12pm | Publish today's post |
| `weekly_generation.yml` | `0 5 * * 0` | Sunday 10am | Generate 5 posts + images |
| `weekly_digest.yml` | `0 5 * * 0` | Sunday 10am | Send analytics digest |
| `comment_triage.yml` | `0 4,6,8,10,12,14 * * 1-5` | Every 2h weekdays 9am-7pm | Classify comments |
| `lead_hunter.yml` | Twice daily Mon-Fri | Morning + afternoon | Hunt AI automation leads |

### `supabase/` + `schema/`

- `supabase_schema.sql` / `schema.sql` — Identical PostgreSQL schema defining: `posts`, `stories`, `processed_comments`, `leads_seen`, `config` tables

---

## 3. Environment Variables & API Keys

### Full Variable Map

| Variable | Used By | What For |
|----------|---------|---------|
| `GROQ_API_KEY` | generate_posts.py, triage_comments.py, search_leads.py, story_intake.py, dm_ghostwriter.py | LLM inference (primary) |
| `GROQ_MODEL` | dm_ghostwriter.py | Model name override (default: llama-3.3-70b-versatile) |
| `ANTHROPIC_API_KEY` | generate_post.py | Claude claude-opus-4-6 (on-demand single post) |
| `OPENAI_API_KEY` | generate_image.py | DALL-E 3 image generation |
| `APIFY_API_TOKEN` | search_leads.py | LinkedIn post scraping for lead hunting |
| `LINKEDIN_CLIENT_ID` | linkedin_auth.py | OAuth app credentials |
| `LINKEDIN_CLIENT_SECRET` | linkedin_auth.py | OAuth app credentials |
| `LINKEDIN_ACCESS_TOKEN` | publish_post.py, triage_comments.py | LinkedIn API v2 calls |
| `LINKEDIN_REFRESH_TOKEN` | publish_post.py | Auto-refresh on 401 (optional) |
| `LINKEDIN_PERSON_URN` | publish_post.py, publish_linkedin.py | `urn:li:person:XXXX` for post authorship |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | sheets_helper.py, generate_posts.py, generate_images.py | Sheets document ID |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | sheets_helper.py | Service account JSON as env var string (GitHub Actions) |
| `GOOGLE_SERVICE_ACCOUNT_PATH` | sheets_helper.py | Path to JSON file (local dev fallback) |
| `SUPABASE_URL` | db_client.py | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | db_client.py | Supabase admin key |
| `DISCORD_WEBHOOK_CONFIRMATIONS` | publish_post.py, generate_posts.py | Post publish notifications |
| `DISCORD_LEADS_WEBHOOK_URL` | triage_comments.py | Lead comment alerts |
| `DISCORD_DIGEST_WEBHOOK_URL` | weekly_digest.py | Weekly analytics |
| `DISCORD_LEAD_HUNTER_WEBHOOK_URL` | search_leads.py | Lead hunt alerts |
| `DISCORD_BOT_TOKEN` | discord_ingest.py, dm_ghostwriter.py | Bot authentication |
| `DISCORD_STORIES_CHANNEL_ID` | discord_ingest.py, story_intake.py | #stories channel |
| `DISCORD_CONFIRMATIONS_CHANNEL_ID` | (referenced in workflows) | #confirmations channel |
| `DISCORD_LEADS_CHANNEL_ID` | (referenced in workflows) | #leads channel |
| `DISCORD_DIGEST_CHANNEL_ID` | (referenced in workflows) | #digest channel |
| `DISCORD_GHOSTWRITER_CHANNEL_ID` | (referenced in workflows) | #ghostwriter channel |

### GitHub Secrets Required
All of the above must be set as GitHub repository secrets for GitHub Actions to work. The `.env.example` file lists all 30+ with setup instructions.

---

## 4. Data Flow: Trigger to LinkedIn Post

### Flow A — Weekly Batch (Primary, Automated)

```
SUNDAY 10am PKT
  │
  ├─ generate_posts.py
  │     1. Read Google Sheets "Topic Bank" → 5 rows with status="pending"
  │     2. For each topic: call Groq (llama-3.3-70b-versatile)
  │        Prompt → returns: post_text, hook, headline, bullet_1-5,
  │                          what_makes_this_real, image_prompt
  │     3. Assign Mon-Fri date slots (PKT timezone)
  │     4. Determine audience (Mon/Wed/Fri=technical, Tue/Thu=business)
  │     5. Write to Supabase `posts` table
  │     6. Update Sheets: status="draft", post_text, headline filled
  │     7. POST to Discord #confirmations: summary of all 5 posts
  │
  ├─ generate_images.py
  │     1. Read Sheets for next Mon-Fri posts
  │     2. Load matching HTML template (technical/business/story)
  │     3. Replace {{PLACEHOLDERS}} with post data
  │     4. Playwright renders page to 1080×1350px PNG
  │     5. Upload PNG to Supabase Storage (bucket: post-images)
  │     6. Update Sheets: image_path column
  │
  ▼
HUMAN REVIEW (Muhammad)
  │     Open Google Sheets
  │     Review post_text, images
  │     Change status: "draft" → "approved" (or "skip")
  │
MON–FRI 12pm PKT
  ├─ publish_post.py
  │     1. Read Sheets: find today's row (scheduled_date=today, status=approved)
  │     2. Download image from Supabase Storage URL
  │     3. LinkedIn API: register image upload → get upload URL
  │     4. PUT image bytes to LinkedIn CDN
  │     5. LinkedIn API: create UGC post (text + image URN + author URN)
  │     6. Update Sheets: status="published", post_id=LinkedIn URN
  │     7. POST to Discord #confirmations: published notification
  │
  ▼
POST IS LIVE ON LINKEDIN
```

### Flow B — Story Post (Discord → Saturday)

```
ANYTIME
  Discord user posts in #stories (min 30 chars)
    │
    ├─ story_intake.py (Discord on_message handler)
    │     1. Validate message length
    │     2. Deduplicate against Supabase (author_id + content hash)
    │     3. Groq: format raw story into LinkedIn post + image_prompt
    │     4. Save to Supabase `stories` + `posts` tables
    │     5. Find next available Saturday slot
    │     6. Write to Sheets Topic Bank
    │     7. POST to Discord: preview of formatted post
    │
SATURDAY 10am PKT
  ├─ discord_ingest.py (fallback)
  │     If story_intake.py missed it (bot was offline):
  │     Fetch latest unprocessed message from #stories channel
  │     Pass to generate_post.py for formatting
  │
  ▼ (same approval + publish flow as Flow A)
```

### Flow C — Engagement Loop (Ongoing)

```
EVERY 2 HOURS (weekdays 9am-7pm PKT)
  triage_comments.py
    1. Get post IDs from Supabase (published last 14 days)
    2. LinkedIn API: fetch comments for each post
    3. Filter already-seen (Supabase processed_comments table)
    4. Groq batch classify: A=Lead / B=Content Idea / C=Noise (max 10/call)
    5. Category A → Discord #leads (commenter name, excerpt, drafted DM)
       Category B → Append to Sheets Content Backlog
       Category C → Log only
    6. Mark all as processed in Supabase

TWICE DAILY (weekdays)
  search_leads.py
    1. Run 4 Apify Boolean queries (looking for "hiring AI", "needs automation" posts)
    2. Deduplicate against Supabase leads_seen
    3. Fast pre-filter: skip obvious job seekers
    4. Groq qualify each: HIGH / MEDIUM / LOW + pain point + comment text + DM
    5. HIGH only → Discord #leads webhook alert
    6. Save all to Supabase leads_seen

SUNDAY 10am PKT
  weekly_digest.py
    1. Query Supabase posts (published last 7 days + previous 7)
    2. Sum: impressions, likes, comments, shares
    3. Calculate WoW delta percentages
    4. Find best + worst post by engagement score
    5. POST to Discord #digest
```

### Flow D — DM Ghostwriting (Interactive)

```
ANYTIME (Discord #ghostwriter)
  User types: /biz [context], /tech [context], /follow [context], etc.
    │
    dm_ghostwriter.py (persistent Discord bot)
      1. Parse slash command + arguments
      2. Select persona prompt (business ROI / technical / followup / etc.)
      3. Call Groq with persona prompt + user context
      4. Reply to Discord with drafted DM
      5. Store in-memory for /retry (per user)
```

---

## 5. What Is Working vs. Known Issues

### Working

| Component | Evidence |
|-----------|---------|
| Post generation (Groq, v2) | `generate_posts.py` is complete with retry logic, rate limit handling, structured output |
| Image rendering (Playwright) | `generate_images.py` fully implemented with template system and Supabase upload |
| LinkedIn publishing | `publish_post.py` handles full UGC post flow including image upload and Sheets update |
| Comment triage (classify) | `triage_comments.py` batch classification with deduplication is complete |
| Lead hunting | `search_leads.py` has complete Apify + Groq + Discord pipeline |
| DM ghostwriter | `dm_ghostwriter.py` fully implemented with all slash commands |
| Google Sheets integration | `sheets_helper.py` fully functional (PKT timezone, full read/write) |
| Supabase integration | `db_client.py` complete with all CRUD operations |
| Weekly digest | `weekly_digest.py` implemented (see analytics caveat below) |
| Discord webhooks | All webhook URLs configured and used consistently |
| GitHub Actions cron | All 5 workflow YAML files have correct schedules and secrets references |

### Known Issues

#### Critical

| Issue | Location | Impact |
|-------|---------|--------|
| **`linkedin_auth.py` is incomplete** | `tools/linkedin_auth.py` | LinkedIn access token expires every 60 days. No automated refresh. Must manually run OAuth flow locally when it expires. System goes dark until token is refreshed. |
| **LinkedIn Comments API returns 403** | `triage_comments.py` | The LinkedIn API v2 `/socialActions/{post_id}/comments` endpoint returns 403 for personal profile posts without Marketing Developer Platform (MDP) access. Comment triage may silently return 0 comments. |
| **`APIFY_API_TOKEN` not in `.env`** | `search_leads.py` | Lead hunter will fail at runtime with missing env var. Token must be added to `.env` and GitHub Secrets. |

#### Non-Critical / Limitations

| Issue | Location | Impact |
|-------|---------|--------|
| **Analytics metrics are all 0** | `weekly_digest.py` | Supabase `posts` table has no impressions/likes/comments data — no LinkedIn analytics sync exists. Weekly digest sends accurate structure but zeroed metrics. Posts need to be enriched with analytics data post-publication. |
| **`sheets_client.py` is a stub** | `tools/sheets_client.py` | Exists but all functions return TODOs. Nothing imports it — `sheets_helper.py` is used everywhere. Safe to ignore or delete. |
| **`discord_bot.py` is a stub** | `tools/discord_bot.py` | Placeholder only. `dm_ghostwriter.py` is the real implementation. |
| **`generate_post.py` (Claude) vs `generate_posts.py` (Groq)** | Both files | Two separate post generation paths exist. `generate_posts.py` (Groq, v2) is the active batch workflow. `generate_post.py` (Claude) is the on-demand single-post path. They are independent; no conflict, but easy to confuse. |
| **No DALL-E in active batch flow** | `generate_image.py` | Single-image DALL-E script exists but is not called by GitHub Actions. Batch flow uses Playwright HTML rendering instead. |
| **Discord bot requires persistent hosting** | `dm_ghostwriter.py` | GitHub Actions cannot host a persistent bot. Needs Railway, Fly.io, or a VPS. If not running, `/biz`, `/tech` etc. commands are unavailable. |
| **Story intake depends on bot being online** | `story_intake.py` | Saturday `discord_ingest.py` cron is the fallback, but it only fetches the single latest story. Stories submitted while bot is offline may be missed. |
| **`LINKEDIN_REFRESH_TOKEN` optional** | `publish_post.py` | The code attempts a token refresh on 401, but only if LINKEDIN_REFRESH_TOKEN is set. It is not in `.env`. |
| **in-memory /retry state** | `dm_ghostwriter.py` | Per-user DM retry context is stored in a Python dict. Lost on bot restart. |

---

## 6. Security Issue

**The `.env` file contains live production credentials and is present in the repository working directory.**

While `.env` is listed in `.gitignore` (so it should not be committed), it is on disk and was readable during this audit. Verify that it has never been accidentally committed:

```
git log --all --full-history -- .env
```

If any commit shows `.env` was ever tracked, all credentials in it must be rotated immediately. Credentials that should be rotated if exposed: Groq API key, LinkedIn OAuth tokens, Supabase service role key, Discord bot token, Discord webhook URLs, any OpenAI/Anthropic keys.

---

## 7. Quick Reference — System Health Checklist

| Check | How to Verify |
|-------|-------------|
| LinkedIn token valid | Run `publish_post.py` against a draft post; look for 401 |
| Supabase connected | Run `python tools/db_client.py` — no error on client init |
| Sheets accessible | Run `python tools/sheets_helper.py` — check `get_todays_post()` |
| Groq working | Run `generate_posts.py` — check Discord #confirmations for output |
| Images rendering | Check `.tmp/` or Supabase Storage for PNG uploads after Sunday run |
| GitHub Actions live | Check Actions tab: last run status for all 5 workflows |
| Discord bot online | Check Discord server: bot status indicator |
| Apify token set | Check `.env` and GitHub Secrets for `APIFY_API_TOKEN` |
| Analytics data | Query Supabase `posts` table: `impressions` column — currently always 0 |
