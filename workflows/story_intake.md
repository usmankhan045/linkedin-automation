# Workflow: Story Intake

## Objective
When Muhammad Usman posts a story in the Discord `#stories` channel, automatically extract the key points, format it as a LinkedIn post using Groq, generate a branded story image, add it to Google Sheets scheduled for next Saturday, and send a preview back to Discord for review.

## Trigger
Discord bot event: `on_message` in `#stories` channel (`DISCORD_STORIES_CHANNEL_ID`)
This runs as a persistent bot (`tools/discord_bot.py`) rather than a scheduled GitHub Actions job.

## Tool
`tools/story_intake.py` (called from `tools/discord_bot.py`)

## Inputs
| Source | Field | Description |
|--------|-------|-------------|
| Discord message | `message.content` | Raw story text (any length, any format) |
| Discord message | `message.author` | Username for logging |
| Discord message | `message.id` | Used for deduplication |

## Step-by-Step Execution

### Step 1 — Validate Message
- Skip messages from bots (`message.author.bot == True`)
- Skip if `len(message.content.strip()) < 30` — too short to be a story
- Check Supabase `stories` table: if `discord_message_id` already exists, skip (idempotent)
- If all checks pass: save raw message to `stories` table with `status="pending"`

### Step 2 — Extract & Format via Groq
**Model:** `llama-3.3-70b-versatile`
**Temperature:** 0.75

**System prompt:**
```
You are a LinkedIn ghostwriter for Muhammad Usman, an AI Automation Engineer from Pakistan.
Your job is to transform raw personal stories into polished LinkedIn posts.
Voice: first-person, honest, reflective, never cringe-worthy. Reads like a real person, not a marketer.
```

**User prompt:**
```
Raw story from Muhammad Usman:
---
{raw_story_content}
---

Transform this into a LinkedIn post that:
1. Opens with the human moment, not the achievement (don't lead with "I just...")
2. Middle: what was hard, what changed, what was learned
3. End: a universal insight others can apply — make it worth reading for strangers
4. CTA: invite a genuine conversation or connection
5. 3–5 hashtags: #AIAutomation #BuildInPublic #PakistanTech (+ 1–2 relevant to the story)
6. Max 1,300 characters total
7. First 210 characters must hook — this is what shows before "see more"
8. No markdown formatting

Also return:
- image_prompt: A warm, human DALL-E image description. Can reference Pakistan, tech, achievement. No text, no faces.
- extracted_points: Array of 3–5 bullet points summarising the story (for Sheets key_details column)

Respond in JSON only:
{
  "content": "...",
  "image_prompt": "...",
  "extracted_points": ["...", "...", "..."]
}
```

### Step 3 — Determine Next Saturday Slot
- Find the next upcoming Saturday date
- Check Google Sheets `Topic Bank` for any row with `date = next_saturday` and `status != "published"`
- If slot is already taken: find the Saturday after that (look ahead up to 4 weeks)
- If no Saturday slot is open within 4 weeks: use the next available one and note it in the Discord reply

### Step 4 — Generate Story Image
Call `tools/generate_image.py` with the `image_prompt`.

Story images use a **green accent** visual style (add to the DALL-E prompt):
> "...soft green accent lighting, warm tones, professional atmosphere"

On success: get `image_url` from Supabase Storage.

### Step 5 — Write to Google Sheets
Insert a new row in `Topic Bank`:
| Column | Value |
|--------|-------|
| `topic` | First sentence of the story (truncated to 100 chars) |
| `category` | `story` |
| `key_details` | `extracted_points` joined as bullet list |
| `audience` | `general` |
| `status` | `pending` |
| `date` | next available Saturday (YYYY-MM-DD) |
| `post_id` | Supabase UUID |
| `image_path` | Supabase Storage URL |

### Step 6 — Update Supabase
- `INSERT INTO stories (discord_message_id, discord_author, raw_content, processed_post_id, status)`
  with `status="processed"`
- `INSERT INTO posts (content, image_prompt, image_url, audience_type, status, scheduled_at)`

### Step 7 — Send Discord Preview
Reply in `#stories` with a formatted preview:

```
📝 **Story formatted!** Scheduled for {Saturday date}

---
{generated_post_content}
---

🖼️ Image: {image_url}

✅ Looks good? It's marked **pending** in Sheets — approve it there when ready.
❌ Not right? Reply with feedback and I'll regenerate.
```

## Expected Outputs
- Supabase `stories` row: `status="processed"`
- Supabase `posts` row: `status="queued"`, `image_url` set
- Google Sheets: new row with `status="pending"`, `date=next_saturday`, `image_path` set
- Discord `#stories`: preview message sent

## Edge Cases

### No Saturday Slot Available Within 4 Weeks
- Still create the Sheets row with the best available date
- Add note to Discord preview: "⚠️ Next 4 Saturdays are booked. Scheduled for {date} — you may want to reschedule something."

### Story Too Short (< 30 characters)
- Don't process
- React to the Discord message with ❓ emoji
- Reply: "That's a bit short for a story — can you share more detail?"

### Story Too Long (> 4,000 characters)
- Still process — Groq will summarise it
- Log a note: `[INFO] Long story ({n} chars) — Groq will condense`
- No special handling needed; the model handles it

### Groq Returns Malformed JSON
- Strip markdown fences, retry JSON parse
- If still invalid: retry the Groq call once with stricter prompt
- If second attempt fails:
  - Save raw story to Supabase with `status="pending"` (unprocessed)
  - Reply on Discord: "⚠️ Couldn't auto-format this story. It's saved — try `/story retry {message_id}` later."

### Image Generation Fails
- Continue without image
- Set `image_path=""` in Sheets
- Discord preview note: "⚠️ Image generation failed. Add image manually before approving."

### Duplicate Discord Message (Bot Restart / Re-delivery)
- Dedup via `discord_message_id` in Supabase `stories` table
- If duplicate: silently skip (do not re-reply on Discord)

### Discord Bot Offline
- Stories posted while bot is offline are NOT automatically processed
- `tools/discord_ingest.py` (scheduled, Sunday) catches missed messages
- See `ingest_story.md` for the fallback flow

## Rate Limits & Timing Notes
- Groq: 1 call per story — no rate limit concern
- Google Sheets write: 1 request — no concern
- Discord message events are real-time; bot must be running continuously (hosted on a VPS or Railway)
- GitHub Actions is NOT suitable for persistent Discord bot; run the bot separately
