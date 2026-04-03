# Workflow: Generate Post Image

## Objective
Generate a visual for the LinkedIn post using DALL-E 3, then upload it to Supabase Storage and attach the URL to the post record.

## Tool
`tools/generate_image.py`

## Inputs
| Argument     | Required | Description |
|--------------|----------|-------------|
| `--post_id`  | Yes      | UUID of a post with `status = 'image_pending'` |

## Output
- Post record updated: `image_url` set, `status` changed to `'queued'`
- Image stored in Supabase Storage bucket `post-images`

## Image Specs
- **Model:** `dall-e-3`
- **Size:** `1024x1024` (square crops well for LinkedIn)
- **Quality:** `standard` (use `hd` only if budget allows)
- **Style:** `vivid`

## Prompt Guidelines
The `image_prompt` field in the post record is written by `generate_post.py`. It should:
- Describe a clean, professional visual (no text overlaid — LinkedIn handles that)
- Match the audience tone:
  - Technical → abstract tech visuals, circuit patterns, glowing data flows, dark backgrounds
  - Business → clean office metaphors, graphs going up, confident figures, bright backgrounds
  - Story → warm, human scenes, Pakistan landscape if relevant, authentic candid feel
- Avoid: faces (DALL-E restrictions), brand logos, misleading diagrams

## Supabase Storage Setup
Bucket name: `post-images`
Access: Public read (so the URL works in LinkedIn's image uploader)
Path format: `posts/{post_id}.png`

## Error Handling
- If DALL-E returns a content policy error, log the refused prompt and set post status to `failed` with `error_message = "Image generation refused: <reason>"`
- If Supabase upload fails, retry once. If still fails, set status to `failed`
- Never set status to `queued` unless `image_url` is confirmed non-null

## Cost Note
DALL-E 3 standard 1024x1024 costs ~$0.04/image. At 6 posts/week that's ~$1/week. Check OpenAI usage dashboard weekly.
