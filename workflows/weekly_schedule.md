# Workflow: Weekly Schedule

## Objective
Orchestrate the full weekly LinkedIn posting schedule for Muhammad Usman.

## Schedule (All times PKT / UTC+5)

| Day       | Time   | Audience   | GitHub Actions trigger |
|-----------|--------|------------|------------------------|
| Monday    | 9 AM   | Technical  | `cron: '0 4 * * 1'`   |
| Tuesday   | 9 AM   | Business   | `cron: '0 4 * * 2'`   |
| Wednesday | 9 AM   | Technical  | `cron: '0 4 * * 3'`   |
| Thursday  | 9 AM   | Business   | `cron: '0 4 * * 4'`   |
| Friday    | 9 AM   | Technical  | `cron: '0 4 * * 5'`   |
| Saturday  | 10 AM  | Story      | `cron: '0 5 * * 6'`   |
| Sunday    | —      | Off        | —                      |

## Execution Flow per Run

For **Mon / Wed / Fri** (Technical audience):
1. `tools/generate_post.py --audience technical`
2. `tools/generate_image.py --post_id <id>`
3. `tools/publish_linkedin.py --post_id <id>`

For **Tue / Thu** (Business audience):
1. `tools/generate_post.py --audience business`
2. `tools/generate_image.py --post_id <id>`
3. `tools/publish_linkedin.py --post_id <id>`

For **Saturday** (Story):
1. `tools/discord_ingest.py` — pulls the latest unprocessed story from #stories channel
2. `tools/generate_post.py --audience story --story_id <id>`
3. `tools/generate_image.py --post_id <id>`
4. `tools/publish_linkedin.py --post_id <id>`

## GitHub Actions File
`.github/workflows/run_daily.yml`

## Required Secrets (set in GitHub repo Settings → Secrets)
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `LINKEDIN_ACCESS_TOKEN`
- `LINKEDIN_REFRESH_TOKEN`
- `LINKEDIN_CLIENT_ID`
- `LINKEDIN_CLIENT_SECRET`
- `LINKEDIN_PERSON_URN`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `DISCORD_BOT_TOKEN`
- `DISCORD_STORIES_CHANNEL_ID`

## Error Handling
- If any step fails, the post status in Supabase is set to `failed` with `error_message`.
- GitHub Actions marks the run as failed — visible in the repo Actions tab.
- Re-run the failed job manually after investigating the error.
- Do not auto-retry publishing steps — LinkedIn may have already received the request.
