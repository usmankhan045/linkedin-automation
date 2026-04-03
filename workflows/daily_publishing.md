# Workflow: Daily Publishing

## Objective
Monday through Friday at 12pm PKT, find today's approved post in Google Sheets, publish it to LinkedIn with the attached image, update Sheets and Supabase with the result, and send a Discord confirmation.

## Schedule
**GitHub Actions cron:** `0 7 * * 1-5` (12:00 PM PKT = 07:00 UTC, weekdays only)

## Tool
`tools/daily_publishing.py`

## Inputs
None — reads from Google Sheets. Today's date determines which row to publish.

## Google Sheets Columns Used
| Column | Read/Write | Value expected |
|--------|-----------|----------------|
| `status` | Read | Must be `"approved"` |
| `date` | Read | Must match today's date (YYYY-MM-DD) |
| `image_path` | Read | Supabase Storage public URL |
| `status` | Write | Updated to `"published"` on success |
| `post_id` | Write | LinkedIn post URN returned by API |

## Step-by-Step Execution

### Step 1 — Find Today's Approved Post
- Read all rows from `Topic Bank` sheet
- Filter: `status = "approved"` AND `date = today`
- If exactly one row matches: proceed
- If zero rows match: see edge cases below
- If multiple rows match: take the first (lowest row index) — log a warning

### Step 2 — Validate Post Data
Before touching LinkedIn, verify:
- `content` field is non-empty
- `image_path` is non-empty and is a valid URL (starts with `https://`)
- `content` length ≤ 3,000 characters (LinkedIn hard limit)

If validation fails: skip publishing, send Discord alert, exit with error.

### Step 3 — Download Image
- `GET {image_path}` with a 30s timeout
- Verify response Content-Type is `image/png` or `image/jpeg`
- Store bytes in memory (do not write to disk)

### Step 4 — Publish to LinkedIn
Run `tools/publish_linkedin.py --post_id <supabase_post_id>`.

Internally, publish_linkedin.py follows these API steps:
1. `POST /v2/assets?action=registerUpload` — get `uploadUrl` + `asset` URN
2. `PUT {uploadUrl}` — upload raw image bytes
3. Wait 3 seconds for LinkedIn to process the image
4. `POST /v2/ugcPosts` — create the post

On success: `linkedin_post_id` (the URN) is returned.

### Step 5 — Update Google Sheets
- Set `status` → `"published"`
- Set `post_id` → LinkedIn URN (e.g. `urn:li:ugcPost:7123456789`)

### Step 6 — Update Supabase
- `UPDATE posts SET status='published', linkedin_post_id='{urn}', published_at=NOW() WHERE id='{supabase_post_id}'`

### Step 7 — Send Discord Confirmation
Post to `#confirmations` channel:
```
✅ Published — {date}
Category: {category}
Topic: {topic}
LinkedIn: https://www.linkedin.com/feed/update/{linkedin_post_id}/
```

## Expected Outputs
- Post live on Muhammad Usman's LinkedIn profile
- Sheets row: `status="published"`, `post_id` filled
- Supabase `posts` row: `status="published"`, `linkedin_post_id` + `published_at` filled
- Discord `#confirmations`: confirmation message with LinkedIn link

## Edge Cases

### No Approved Post Found (Most Common)
- Cause: Muhammad Usman hasn't reviewed/approved this week's drafts in Sheets yet
- Action: Send Discord `#confirmations` alert:
  ```
  ⚠️ No approved post for today ({date}).
  Check Google Sheets — is today's post still "pending"?
  ```
- Exit with code 0 (this is expected behaviour, not a crash)
- Do NOT publish anything

### LinkedIn Access Token Expired (401)
- `publish_linkedin.py` auto-attempts token refresh using `LINKEDIN_REFRESH_TOKEN`
- If refresh succeeds: logs new tokens to stderr, retries publish
- If refresh fails (refresh token also expired — happens after 12 months):
  - Set post `status="failed"` in Supabase with `error_message="Token expired — manual refresh required"`
  - Send Discord `#confirmations` alert:
    ```
    🔴 LinkedIn token expired. Publish failed for {date}.
    Action required: re-authenticate and update LINKEDIN_ACCESS_TOKEN + LINKEDIN_REFRESH_TOKEN in GitHub Secrets.
    ```
  - Exit with error code 1

### Image File Missing or Unreachable
- If `image_path` is empty: skip post, send Discord alert "⚠️ No image for today's post. Add image_path in Sheets."
- If HTTP download returns 404/403/timeout: same alert, skip publish
- Do not publish a text-only post by default — LinkedIn posts perform significantly worse without images

### LinkedIn API 422 (Unprocessable Entity)
- Usually caused by content policy violation or malformed post body
- Log full response body to Supabase `error_message`
- Send Discord alert with the error detail so Muhammad Usman can edit the post manually
- Do not retry 422 errors

### LinkedIn API 429 (Rate Limited)
- Wait 60 seconds, retry once
- If second attempt also 429: mark `status="failed"`, send Discord alert

### Duplicate Publish Guard
- Before calling LinkedIn API, check: `SELECT linkedin_post_id FROM posts WHERE id='{supabase_post_id}'`
- If `linkedin_post_id` is already set (non-null): post was already published — log warning, skip, send Discord alert "⚠️ Post for {date} appears already published."

## Rate Limits & Timing Notes
- LinkedIn UGC Posts API: ~100 posts/day per app. We post 1/day — no concern
- LinkedIn image processing takes 2–5 seconds after upload before the post API accepts the asset URN — the 3s sleep in publish_linkedin.py handles this
- Google Sheets read on startup adds ~1–2s
- Total runtime: ~20–30 seconds
- Publish at 12pm PKT = 7am UTC — good engagement window
