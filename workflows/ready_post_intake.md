# Workflow: Ready Post Intake

## Objective
Accept a fully-written LinkedIn post from Discord and queue it for publishing — without any AI rewriting. User also provides image tags; the system generates the image and schedules the post on the next available weekday slot.

## Trigger
Message posted in the **#ready-posts** Discord channel (configured via `DISCORD_READY_POSTS_CHANNEL_ID`).

## Message Format
Just paste the complete LinkedIn post text — nothing else. No special format or separators.

- The post content is used **verbatim** for LinkedIn publishing — no AI rewriting.
- Groq automatically extracts hook, headline, bullet points, category, and audience for the image.
- Bot ignores messages that start with `/`.

## Required Inputs
| Input | Source |
|-------|--------|
| Post text | Above `---TAGS---` in the Discord message |
| Image tags | Below `---TAGS---` in the Discord message |
| Next available slot | Google Sheets Topic Bank |

## Tools Used
- `tools/ready_post_intake.py` — main handler
- `tools/db_client.py` — Supabase CRUD
- `tools/sheets_helper.py` — Google Sheets read/write

## Process

1. **Parse message** — split on `---TAGS---`. Reject if separator missing, either section empty, or post < 50 chars.
2. **Deduplicate** — check Supabase `posts` table for `topic = 'ready:{discord_message_id}'`. Skip silently if already processed (handles bot restarts).
3. **Build image prompt** — wrap tags in a professional DALL-E prompt template.
4. **Generate image** — call DALL-E 3 (1024×1024, vivid) if `OPENAI_API_KEY` is set. Skip image generation if key is absent; store prompt for manual generation later.
5. **Save to Supabase** — insert into `posts` table:
   - `post_text` = verbatim post
   - `topic` = `ready:{discord_message_id}` (for dedup)
   - `category` = `ready_post`
   - `audience` = `general`
   - `image_prompt` = built from tags
   - `image_url` = Supabase Storage URL (if generated)
6. **Find slot** — scan Topic Bank for the next Mon-Fri with no pending/queued post. Looks up to 30 days ahead.
7. **Write to Google Sheets** — append row to Topic Bank:
   | Column | Value |
   |--------|-------|
   | topic | First 100 chars of post |
   | category | `ready_post` |
   | key_details | Image tags string |
   | audience | `general` |
   | status | `pending` |
   | scheduled_date | Next available weekday |
   | post_id | Supabase post UUID |
   | image_path | Image URL (or blank) |
8. **Reply in Discord** — confirm with slot date, post preview, and image status.

## Expected Outputs
- Row in Supabase `posts` table
- Row in Google Sheets Topic Bank with `status=pending`
- Image in Supabase Storage `post-images/posts/{post_id}.png` (if generated)
- Discord reply with confirmation

## Edge Cases

| Situation | Handling |
|-----------|----------|
| Missing `---TAGS---` | Reply with format instructions, stop |
| Post < 50 chars | Reply with error, stop |
| Duplicate message ID | Silently skip (idempotent) |
| `OPENAI_API_KEY` not set | Skip image, reply with manual generation command |
| DALL-E fails | Skip image, save prompt, reply with manual generation command |
| Supabase upload fails | Same as above |
| Sheets write fails | Logged as warning; post still saved to Supabase |
| All 30 weekdays booked | Schedule on day 31+, include overbooked warning in reply |

## After Queuing

1. Review the post in **Google Sheets Topic Bank**
2. Check the image URL in the `image_path` column
3. If no image: run `python tools/generate_image.py --post_id <uuid>`
4. Set `status = approved` in Sheets when ready
5. The daily publishing cron (12pm PKT weekdays) picks it up, stages it for Discord approval, then publishes to LinkedIn

## Differences vs Story Intake

| Feature | Story Intake (`#stories`) | Ready Post Intake (`#ready-posts`) |
|---------|--------------------------|-------------------------------------|
| AI rewriting | Yes (Groq/Llama) | No — verbatim |
| Image prompt | AI-generated | Built from user-provided tags |
| Image timing | Generated later (separate step) | Generated inline during intake |
| Schedule slot | Next available Saturday | Next available weekday (Mon-Fri) |
| Dedup key | `stories.discord_message_id` | `posts.topic = ready:{id}` |
