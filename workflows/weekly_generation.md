# Workflow: Weekly Post Generation

## Objective
Every Sunday, read 5 unscheduled topics from the Google Sheets Topic Bank, generate a LinkedIn post for each using Groq, write all drafts back to Sheets with `status="pending"`, then trigger image generation for each post.

## Schedule
**GitHub Actions cron:** `0 5 * * 0` (10 AM PKT every Sunday)

## Tool
`tools/weekly_generation.py`

## Inputs
None — fully automated. All inputs come from Google Sheets.

## Google Sheets Topic Bank Schema
Spreadsheet: `GOOGLE_SHEETS_SPREADSHEET_ID` (set in `.env`)
Sheet tab: `Topic Bank`

| Column | Type | Description |
|--------|------|-------------|
| `topic` | string | The subject to write about |
| `category` | string | `technical` or `business` |
| `key_details` | string | Bullet points or notes to include |
| `audience` | string | Who this post is for (mirrors category) |
| `status` | string | `new` → `pending` → `approved` → `published` |
| `date` | date | Scheduled publish date (written by this script) |
| `post_id` | string | LinkedIn URN (written after publishing) |
| `image_path` | string | Supabase Storage URL for the post image |

## Step-by-Step Execution

### Step 1 — Read the Topic Bank
- Fetch all rows from the `Topic Bank` sheet
- Filter to rows where `status = "new"`
- Select the next 5 rows (oldest first — preserve order)
- If fewer than 5 rows exist with `status = "new"`, process however many are available (minimum 1)
- Assign publishing dates: Mon → Fri of the coming week

### Step 2 — Generate Post Content via Groq
For each selected topic, call `tools/generate_post.py` with the row data.

**Model:** `llama-3.3-70b-versatile` via Groq API
**Max tokens:** 1024
**Temperature:** 0.8

**System prompt template:**
```
You are writing LinkedIn posts for Muhammad Usman, an AI Automation Engineer based in Pakistan.

Voice: direct, practical, no buzzword soup. Results-first. Writes like a builder, not a marketer.

Post rules:
- Max 1,300 characters
- First 210 characters must hook the reader (shown before "see more")
- No markdown bold/italic
- One blank line between paragraphs
- 3–5 hashtags at the end only
- Never start with "I"
- End with a specific CTA
```

**User prompt by category:**

*Technical:*
```
Topic: {topic}
Key details to include: {key_details}
Audience: AI developers, engineers, builders

Write a LinkedIn post that:
- Opens with a specific tool name, surprising stat, or counterintuitive claim
- Uses 3–4 punchy bullet points or short paragraphs
- Closes with one concrete takeaway
- CTA: "Follow for more" or "Drop your take in comments"
- Hashtags: #AIEngineering #LLMs #BuildInPublic #AITools (use 3–4 relevant)

Also return: image_prompt — a DALL-E style description of a dark-background abstract tech visual (no text, no faces, no logos)

Respond in JSON only:
{"content": "...", "image_prompt": "..."}
```

*Business:*
```
Topic: {topic}
Key details to include: {key_details}
Audience: SME founders, ops managers, non-technical business owners

Write a LinkedIn post that:
- Opens with a specific result ("We saved 40 hours/week doing X")
- Uses Problem → Solution → Outcome structure
- Zero jargon — plain English throughout
- CTA: "DM me to explore this" or "Comment your situation below"
- Hashtags: #AIForBusiness #Automation #SmallBusiness (use 3 relevant)

Also return: image_prompt — DALL-E style description of a clean bright professional visual (upward trend, clean office metaphor — no text, no faces)

Respond in JSON only:
{"content": "...", "image_prompt": "..."}
```

### Step 3 — Write Drafts Back to Sheets
For each generated post:
- Update `status` → `"pending"`
- Write `date` → assigned publish date (YYYY-MM-DD)
- Supabase: insert row into `posts` table with `status="draft"`, save `post_id` UUID

### Step 4 — Trigger Image Generation
After writing all 5 rows to Sheets, call `tools/generate_image.py --post_id <uuid>` for each post.
On success: write the Supabase Storage image URL to the `image_path` column in Sheets.

## Expected Outputs
- 5 rows in Google Sheets updated: `status="pending"`, `date` set, `image_path` set
- 5 rows in Supabase `posts` table with `status="queued"`
- Discord `#confirmations`: summary message "✅ 5 posts generated for Mon–Fri. Review in Sheets."

## Edge Cases

### Groq 429 (Rate Limit)
- Groq free tier: 30 requests/minute, 14,400 requests/day for `llama-3.3-70b-versatile`
- If 429 received: wait `retry_after` seconds (from response header), then retry
- If no `retry_after` header: wait 10 seconds and retry
- Max 3 retries per post. If all fail: mark that topic as `status="error"` in Sheets, continue with remaining topics
- Log: `[WARN] Rate limited on topic "{topic}". Retried 3x, skipping.`

### Empty Topic Bank
- If zero rows with `status="new"`: send Discord `#confirmations` alert: "⚠️ Topic Bank is empty. No posts generated for next week."
- Exit with code 0 (not an error — it's a signal for Muhammad Usman to add topics)

### Fewer Than 5 Topics
- Process however many exist (1–4). Assign Mon → available days only.
- Log: `[INFO] Only {n} topics available. Generating {n} posts.`

### Google Sheets API Failure (403/429/500)
- 403: likely expired credentials — exit with error, alert Discord `#confirmations`
- 429: wait 60s, retry once
- 500/503: wait 30s, retry twice
- If Sheets write fails after post is generated: still insert to Supabase (don't lose the content), log error, alert Discord

### Groq Returns Malformed JSON
- Strip any markdown code fences and retry JSON parse
- If still invalid: retry the Groq call once with stricter prompt ("Reply with ONLY the JSON object, no other text")
- If second attempt fails: skip this topic, mark `status="error"`, log full response

### Image Generation Failure
- If DALL-E fails for a post: set `image_path=""` in Sheets, post stays `status="pending"`
- Daily publishing workflow checks for missing image and skips gracefully
- Do NOT block the remaining posts

## Rate Limits & Timing Notes
- Google Sheets API: 100 requests/100 seconds per project. We make ~20 requests max — well within limits
- Groq `llama-3.3-70b-versatile`: 6,000 tokens/minute on free tier. Add 2s delay between calls to stay safe
- Total runtime estimate: ~3–4 minutes for 5 posts including image generation
- Run Sunday so Muhammad Usman has all week to review and approve in Sheets before posts go live

---

## Google Sheets Structure

**Spreadsheet ID:** stored in `GOOGLE_SHEETS_SPREADSHEET_ID` env var
**Auth:** Google Service Account (JSON stored in `GOOGLE_SERVICE_ACCOUNT_JSON` GitHub Secret)
**Share the sheet with:** the service account email address (Editor access)

### Tab 1: Topic Bank

This is the editorial calendar and post pipeline. One row per post.

| Column | Type | Values / Notes |
|--------|------|----------------|
| `topic` | string | What the post is about — written by Muhammad Usman or extracted from a Discord story |
| `category` | string | `build-log` / `transformation` / `hot-take` / `behind-scenes` / `story` |
| `key_details` | string | Bullet points, stats, or notes to include in the post |
| `audience` | string | `engineer` / `founder` / `story` |
| `status` | string | `pending` → `approved` → `published` / `skipped` |
| `scheduled_date` | date | YYYY-MM-DD — set by `generate_posts.py` each Sunday |
| `post_id` | string | Supabase UUID — set by `generate_posts.py` after inserting to DB |
| `image_path` | string | Supabase Storage public URL — set by `generate_images.py` |

**Status flow:**
```
new (Muhammad adds topic)
  → pending (generate_posts.py writes content + scheduled_date)
  → approved (Muhammad reviews draft in Sheets and changes status manually)
  → published (publish_post.py sets this after LinkedIn confirms)
  → skipped (Muhammad marks manually if he wants to skip a day)
```

**How Muhammad reviews:** Open the Sheet any day Mon–Fri. Read the `post_text` draft (stored in Supabase, linked via `post_id`). If happy → change `status` to `approved`. If not → edit `topic`/`key_details` and trigger a regeneration manually.

**Note:** The actual post text is NOT stored in Sheets — only the `post_id` UUID is. Full text lives in Supabase `posts.post_text`. This keeps Sheets readable and avoids 50,000-character cells.

### Tab 2: Content Backlog

LinkedIn comments classified as Category B by `triage_comments.py` land here. Muhammad reviews these to find new topic ideas for the Topic Bank.

| Column | Type | Notes |
|--------|------|-------|
| `comment_text` | string | The original LinkedIn comment |
| `commenter_name` | string | LinkedIn display name |
| `original_post_topic` | string | Topic of the post the comment was on |
| `suggested_angle` | string | Groq's suggested post angle ("This could become a post about...") |
| `date` | date | Date the comment was captured |
| `used` | boolean | `FALSE` by default; Muhammad marks `TRUE` when he adds it to Topic Bank |

**Workflow:** Muhammad reviews this tab weekly (Sunday or Monday). For any promising idea, copy the `comment_text` / `suggested_angle` into a new row in the Topic Bank tab. Mark the Backlog row `used=TRUE`.
