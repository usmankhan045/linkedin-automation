# Workflow: Comment Triage

## Objective
Every 2 hours on weekdays (9am–8pm PKT), fetch LinkedIn comments on recent posts, classify each one using Groq, and route accordingly: hot leads to Discord, content ideas to Sheets, noise logged and discarded.

## Schedule
**GitHub Actions cron:** `0 4,6,8,10,12,14 * * 1-5`
(Runs at: 04, 06, 08, 10, 12, 14 UTC = 09, 11, 13, 15, 17, 19 PKT Mon–Fri)

## Tool
`tools/comment_triage.py`

## Inputs
None — reads from Supabase `posts` table to get post URNs to fetch comments for.

## Supabase Tables Used
| Table | Purpose |
|-------|---------|
| `posts` | Read: get all `linkedin_post_id` values for posts published in last 14 days |
| `processed_comments` | Read: dedup check. Write: mark comment as processed |

### `processed_comments` Table Schema
```sql
CREATE TABLE processed_comments (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    linkedin_comment_id  TEXT UNIQUE NOT NULL,
    linkedin_post_id     TEXT NOT NULL,
    author_name          TEXT,
    comment_text         TEXT,
    category             TEXT CHECK (category IN ('A', 'B', 'C')),
    actioned_at          TIMESTAMPTZ DEFAULT NOW()
);
```

## Step-by-Step Execution

### Step 1 — Get Recent Post URNs
```sql
SELECT linkedin_post_id FROM posts
WHERE status = 'published'
AND published_at >= NOW() - INTERVAL '14 days'
AND linkedin_post_id IS NOT NULL
```

### Step 2 — Fetch Comments from LinkedIn API
For each `linkedin_post_id`:
```
GET https://api.linkedin.com/v2/socialActions/{linkedin_post_id}/comments
Headers:
  Authorization: Bearer {LINKEDIN_ACCESS_TOKEN}
  LinkedIn-Version: 202401
  X-Restli-Protocol-Version: 2.0.0
```

Response fields used:
- `id` → `linkedin_comment_id`
- `actor~.localizedFirstName` + `actor~.localizedLastName` → `author_name`
- `message.text` → `comment_text`
- `created.time` → timestamp for ordering

**Note on LinkedIn API Limitations:**
LinkedIn's Social Actions API returns comments only for content posted by apps with `r_organization_social` or via the same OAuth session. Personal UGC posts may have limited comment access depending on LinkedIn Developer Program tier. If the endpoint returns 403, see edge cases.

### Step 3 — Deduplicate
For each comment fetched:
- Query: `SELECT id FROM processed_comments WHERE linkedin_comment_id = '{id}'`
- If exists: skip
- If not exists: proceed to classification

### Step 4 — Classify with Groq
**Model:** `llama-3.3-70b-versatile`
**Temperature:** 0.3 (low — we want consistent classification)
**Batch:** Classify up to 10 comments in one Groq call to stay within rate limits.

**Prompt:**
```
You are classifying LinkedIn comments for Muhammad Usman, an AI Automation Engineer.

Classify each comment into one category:
- A (Lead): Shows buying intent, asks for help with AI/automation, asks about services, wants to hire, mentions a specific business problem
- B (Content Idea): Asks a question Muhammad could answer in a future post, shares an interesting counterpoint, mentions a use case worth exploring
- C (Noise): Generic praise ("Great post!"), emoji-only, spam, irrelevant, off-topic

Comments to classify (JSON array):
{comments_json}

Respond with a JSON array matching the input order:
[{"id": "...", "category": "A|B|C", "reason": "one sentence", "drafted_reply": "...if category A, write a natural DM reply opener Muhammad could send"}]
```

### Step 5 — Route by Category

**Category A — Lead:**
- Insert into `processed_comments` with `category='A'`
- Send to Discord `#leads` channel:
  ```
  🔥 **New Lead — {author_name}**
  Post: {topic from Sheets or post content preview}
  Comment: "{comment_text}"

  **Drafted DM reply:**
  {drafted_reply}

  Reply with `/approve` to send this DM, or edit it first.
  ```

**Category B — Content Idea:**
- Insert into `processed_comments` with `category='B'`
- Append to Google Sheets `Content Backlog` tab:
  | Column | Value |
  |--------|-------|
  | `source` | `linkedin_comment` |
  | `author` | `author_name` |
  | `idea` | `comment_text` |
  | `reason` | Classification reason |
  | `date_captured` | Today's date |
  | `status` | `new` |

**Category C — Noise:**
- Insert into `processed_comments` with `category='C'`
- No further action

### Step 6 — Summary Log (optional, low noise)
If any Category A or B comments were found in this run, post a single summary to Discord `#confirmations`:
```
🔍 Comment triage — {timestamp PKT}
Leads (A): {n} | Ideas (B): {n} | Noise (C): {n}
```
If only Category C: no Discord message (avoid notification fatigue).

## Expected Outputs
- All new comments from last 14 days' posts classified and logged in `processed_comments`
- Category A comments surfaced in Discord `#leads` with drafted replies
- Category B comments appended to Sheets `Content Backlog` tab
- Category C comments silently logged

## Edge Cases

### LinkedIn API 403 on Comments Endpoint
- Cause: LinkedIn restricts comment access on personal posts via API; this requires specific Developer Program access
- Fallback strategy:
  - Log: `[WARN] LinkedIn comments API returned 403 for post {id}. May require elevated API access.`
  - Send ONE-TIME Discord `#confirmations` alert (suppress on subsequent runs if issue persists)
  - Do not fail the entire run — skip the affected post and continue
- Resolution: Apply for `r_1st_connections_size` or contact LinkedIn developer support

### LinkedIn API 401 (Token Expired)
- Attempt token refresh using `LINKEDIN_REFRESH_TOKEN`
- If refresh succeeds: retry comments fetch
- If refresh fails: halt entire run, send Discord alert: "🔴 LinkedIn token expired — comment triage paused."

### No Posts Published in Last 14 Days
- Query returns empty set
- Exit cleanly with code 0
- No Discord notification needed

### Groq 429 (Rate Limited During Classification)
- Wait `retry_after` seconds (or 10s default), retry once
- If second attempt fails: skip classification for this batch, insert all comments with `category=NULL` and `actioned_at=NULL`
- Log: `[WARN] Groq rate limit hit. {n} comments left unclassified.`
- These unclassified comments will be skipped on next run (they'll exist in `processed_comments` without category) — fix: query with `category IS NOT NULL` for dedup, use separate `WHERE category IS NULL` query to re-process them

### Duplicate Comment Across Runs
- Handled by `processed_comments.linkedin_comment_id` unique constraint
- Any duplicate insert will silently fail (use `ON CONFLICT DO NOTHING`)

### Comment Text is Empty or Non-English
- Empty: classify as C automatically, no Groq call needed
- Non-English: send to Groq anyway — classify based on context. If Groq can't determine intent, classify as C

## Rate Limits & Timing Notes
- LinkedIn Social Actions API: rate limits not publicly documented for comments; monitor for 429s
- Groq: 10 comments per API call max (to stay within token limits). At 6 runs/day × max 30 posts × avg 10 comments = 1,800 comments/day worst case — split into batches of 10
- Google Sheets Append: 1 request per Category B comment. Max ~60 requests/day — no concern
- Total runtime per run: ~30–60 seconds depending on comment volume
