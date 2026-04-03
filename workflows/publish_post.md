# Workflow: Publish Post to LinkedIn

## Objective
Publish a queued post (with image) to Muhammad Usman's personal LinkedIn profile via the LinkedIn API v2.

## Tool
`tools/publish_linkedin.py`

## Inputs
| Argument     | Required | Description |
|--------------|----------|-------------|
| `--post_id`  | Yes      | UUID of a post with `status = 'queued'` |

## Output
- Post published on LinkedIn
- Post record updated: `status = 'published'`, `published_at = NOW()`, `linkedin_post_id` = LinkedIn's returned URN

## LinkedIn API Flow

### Step 1 — Register image upload
```
POST https://api.linkedin.com/v2/assets?action=registerUpload
```
Body:
```json
{
  "registerUploadRequest": {
    "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
    "owner": "urn:li:person:{LINKEDIN_PERSON_ID}",
    "serviceRelationships": [{"relationshipType": "OWNER", "identifier": "urn:li:userGeneratedContent"}]
  }
}
```
Returns: `uploadMechanism.com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest.uploadUrl` and `asset` URN.

### Step 2 — Upload image bytes
```
PUT {uploadUrl}
Content-Type: application/octet-stream
Body: raw image bytes (download from image_url)
```

### Step 3 — Create UGC post
```
POST https://api.linkedin.com/v2/ugcPosts
```
Body:
```json
{
  "author": "urn:li:person:{LINKEDIN_PERSON_ID}",
  "lifecycleState": "PUBLISHED",
  "specificContent": {
    "com.linkedin.ugc.ShareContent": {
      "shareCommentary": { "text": "{post_content}" },
      "shareMediaCategory": "IMAGE",
      "media": [{
        "status": "READY",
        "description": { "text": "" },
        "media": "{asset_urn}",
        "title": { "text": "" }
      }]
    }
  },
  "visibility": { "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC" }
}
```

## Auth Headers (all requests)
```
Authorization: Bearer {LINKEDIN_ACCESS_TOKEN}
LinkedIn-Version: 202401
X-Restli-Protocol-Version: 2.0.0
```

## Token Management
- Access tokens expire after 60 days. Store `LINKEDIN_ACCESS_TOKEN` and `LINKEDIN_REFRESH_TOKEN` in GitHub Secrets.
- `publish_linkedin.py` will attempt a token refresh using `LINKEDIN_REFRESH_TOKEN` if it gets a 401.
- After a successful refresh, print the new tokens to stdout so GitHub Actions can surface them — update the secrets manually or via `gh secret set`.
- Token refresh endpoint: `POST https://www.linkedin.com/oauth/v2/accessToken`

## Error Handling
- `401 Unauthorized` → attempt token refresh, retry once
- `422 Unprocessable Entity` → log full response body, set status to `failed`
- `429 Too Many Requests` → wait 60s, retry once
- Any other 4xx/5xx → set status to `failed` with `error_message`
- Never retry publishing without checking if the post was already created (check `linkedin_post_id` first)

## Rate Limits
LinkedIn allows ~100 UGC post creations per day per app. We post 1/day — well within limits.
