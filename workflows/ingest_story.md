# Workflow: Ingest Story from Discord

## Objective
Pull the latest unprocessed story from the Discord #stories channel and save it to the `stories` table in Supabase.

## Tool
`tools/discord_ingest.py`

## Inputs
None — runs on schedule (Saturday morning, before generate_post.py)

## Output
- New row in `stories` table with `status = 'pending'`
- Prints the `story_id` to stdout so `generate_post.py` can consume it

## How Stories Are Submitted
Muhammad Usman (or anyone he designates) posts in the Discord `#stories` channel.
Format is freeform — the agent handles structuring. Example submissions:
- "Just delivered an n8n + Claude pipeline that automated 6 hours of daily data entry for a client"
- "Got my first enterprise client this week. Here's what worked and what didn't..."
- "Shipped a WhatsApp AI assistant for a local restaurant. Took 3 days, cut their order errors by 80%"

## Logic
1. Connect to Discord using bot token
2. Fetch the last 10 messages from `DISCORD_STORIES_CHANNEL_ID`
3. For each message (newest first):
   - Check if `discord_message_id` already exists in `stories` table
   - If not → insert with `status = 'pending'`
   - Stop after finding the first new message (we process one story per Saturday)
4. If no new stories found → exit with a warning, skip the rest of Saturday's pipeline

## Story Filtering
- Skip messages from bots (`message.author.bot == True`)
- Skip messages shorter than 20 characters
- Skip messages that are just links with no context

## Discord Bot Setup
1. Create a bot at discord.com/developers
2. Enable "Message Content Intent" (required to read message text)
3. Add bot to server with `Read Messages` permission on #stories channel
4. Store `DISCORD_BOT_TOKEN` and `DISCORD_STORIES_CHANNEL_ID` in `.env` / GitHub Secrets

## Error Handling
- If Discord is unreachable → exit with error, mark GitHub Actions run as failed
- If no new stories → exit with code 0, print "No new stories found. Skipping Saturday post."
- GitHub Actions job should check this condition and skip subsequent steps gracefully
