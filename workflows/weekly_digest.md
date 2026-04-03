# Workflow: Weekly Digest

## Objective
Every Sunday at 10am PKT, query Supabase for the past week's posts and analytics, calculate engagement metrics, compare to the previous week, and send a clean summary to Discord `#digest`.

## Schedule
**GitHub Actions cron:** `0 5 * * 0` (10 AM PKT = 05:00 UTC, Sunday)

Note: This runs on the same Sunday trigger as `weekly_generation.md`. Order matters:
1. `weekly_digest.py` runs first (summarise the week that just ended)
2. `weekly_generation.py` runs second (prepare the week ahead)

Implement in GitHub Actions as sequential steps in the same job, or two separate jobs with `needs: [digest]`.

## Tool
`tools/weekly_digest.py`

## Inputs
None — reads from Supabase.

## Supabase Tables Read
| Table | Columns used |
|-------|-------------|
| `posts` | `id`, `content`, `audience_type`, `status`, `published_at`, `linkedin_post_id`, `impressions`, `likes`, `comments`, `shares` |

### Analytics Columns (add to schema)
These columns must be added to the `posts` table. They are populated by a separate analytics-sync job (future scope):
```sql
ALTER TABLE posts ADD COLUMN IF NOT EXISTS impressions  INTEGER DEFAULT 0;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS likes        INTEGER DEFAULT 0;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS comments     INTEGER DEFAULT 0;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS shares       INTEGER DEFAULT 0;
```

Until analytics sync is implemented: these columns will be 0. The digest will still run — it will just show 0s and note that analytics aren't connected yet.

## Step-by-Step Execution

### Step 1 — Query This Week's Posts
```sql
SELECT * FROM posts
WHERE status = 'published'
AND published_at >= NOW() - INTERVAL '7 days'
ORDER BY published_at ASC
```

### Step 2 — Query Previous Week's Posts (for delta)
```sql
SELECT * FROM posts
WHERE status = 'published'
AND published_at >= NOW() - INTERVAL '14 days'
AND published_at < NOW() - INTERVAL '7 days'
ORDER BY published_at ASC
```

### Step 3 — Calculate Metrics

**This week:**
- `total_posts` = count of rows
- `total_impressions` = SUM(impressions)
- `total_likes` = SUM(likes)
- `total_comments` = SUM(comments)
- `total_shares` = SUM(shares)
- `avg_engagement_score` = (likes + comments*2 + shares*3) / total_impressions × 100 (if impressions > 0)
- `best_post` = row with highest `(likes + comments*2 + shares*3)` — tie-break by impressions
- `worst_post` = row with lowest same score

**Week-over-week delta:**
- `impressions_delta` = this_week.total_impressions - last_week.total_impressions
- `engagement_delta` = this_week.avg_engagement_score - last_week.avg_engagement_score
- Format as: `+12%` or `-5%` with appropriate sign

### Step 4 — Format Discord Message

```
📊 **Weekly LinkedIn Digest — {week_start} to {week_end}**

**Posts Published:** {total_posts} / 6 expected
**Total Impressions:** {total_impressions} ({impressions_delta} WoW)
**Likes:** {total_likes} | **Comments:** {total_comments} | **Shares:** {total_shares}
**Avg Engagement Score:** {avg_engagement_score:.2f}% ({engagement_delta} WoW)

🏆 **Best Post**
{best_post_topic_or_first_line}
Impressions: {n} | Score: {n}
https://www.linkedin.com/feed/update/{linkedin_post_id}/

📉 **Weakest Post**
{worst_post_topic_or_first_line}
Impressions: {n} | Score: {n}
https://www.linkedin.com/feed/update/{linkedin_post_id}/

📅 **Next week:** {next_mon} – {next_fri}
Check Sheets to approve pending posts before Monday 12pm PKT.
```

### Step 5 — Send to Discord
Post the formatted message to `DISCORD_DIGEST_CHANNEL_ID`.

## Expected Outputs
- Discord `#digest`: weekly summary message
- No database writes (read-only workflow)

## Edge Cases

### No Posts Published This Week
- Query returns empty set
- Send simplified Discord message:
  ```
  📊 **Weekly Digest — {date range}**
  No posts published this week.
  Check Sheets — were posts approved before publish time?
  ```
- Still calculate and show last week's numbers if available (for continuity)

### Analytics Columns Are All Zero
- This is expected until analytics sync is implemented
- Add a footer note to the digest:
  ```
  ℹ️ Analytics sync not yet configured. Impressions/engagement showing 0.
  ```
- Do not fail the run

### No Previous Week Data (First-Ever Run)
- `impressions_delta` and `engagement_delta` → show "—" instead of a number
- `best_post` / `worst_post` from last week → skip the WoW comparison

### Supabase Query Failure
- Retry once after 10 seconds
- If still failing: send Discord `#digest` alert: "⚠️ Could not fetch analytics from Supabase. Digest skipped this week."
- Exit with error code 1

### `content` Too Long for Discord Embed
- Discord messages max: 2,000 characters
- Truncate `best_post` / `worst_post` preview to first 100 characters with `...`

### Digest and Generation Running Simultaneously
- Both are GitHub Actions jobs on the same Sunday cron
- Use `needs: [digest]` in the generation job to enforce order
- Or add explicit 3-minute delay at the start of `weekly_generation.yml`

## Rate Limits & Timing Notes
- Supabase: 2 queries, each < 100ms — no concern
- Discord: 1 message — no concern
- Total runtime: ~5 seconds
- Must complete before `weekly_generation.py` starts (otherwise next week's dates could conflict)
