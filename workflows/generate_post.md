# Workflow: Generate LinkedIn Post

## Objective
Generate a LinkedIn post tailored to the target audience using Claude. Save the result to Supabase.

## Tool
`tools/generate_post.py`

## Inputs
| Argument       | Required | Description |
|----------------|----------|-------------|
| `--audience`   | Yes      | `technical`, `business`, or `story` |
| `--story_id`   | No       | UUID from `stories` table (required when `--audience story`) |

## Output
A new row in the `posts` table with `status = 'image_pending'`.
Prints the `post_id` to stdout so the next step can capture it.

## Audience-Specific Prompts

### Technical (Mon / Wed / Fri)
**Target:** AI developers, engineers, tech-savvy builders
**Content:** New AI tools, model releases, implementation walkthroughs, industry news
**Tone:** Peer-to-peer, specific, shows hands-on knowledge
**Format:**
- Strong hook (a surprising fact, a contrarian take, or a tool name drop)
- 3–4 concise bullet points OR short paragraphs
- One practical takeaway or tip
- CTA: follow / comment / share
- 3–5 hashtags: #AIEngineering #LLMs #BuildInPublic #AITools

### Business (Tue / Thu)
**Target:** SME founders, ops managers, non-technical business owners
**Content:** ROI of AI/automation — hours saved, costs cut, revenue gained, staff freed up
**Tone:** Results-first, plain English, no jargon
**Format:**
- Hook: a specific result ("We saved 40 hours/week doing X")
- Brief problem → solution → outcome structure
- Social proof angle where possible
- CTA: DM for help / comment your use case
- 3–5 hashtags: #AIForBusiness #Automation #SmallBusiness #Productivity

### Story / Portfolio (Saturday)
**Target:** Everyone — broad, humanising content
**Content:** Based on the raw story from Discord #stories channel
**Tone:** First-person, genuine, reflective
**Format:**
- Personal opening
- The challenge / the work / the outcome
- Lesson or insight extracted
- CTA: connection / conversation
- 3–5 hashtags: #AIAutomation #BuildInPublic #PakistanTech

## Post Constraints
- Max 1,300 characters (LinkedIn shows "see more" after ~210 chars — hook must land in those first 210)
- No more than 5 hashtags
- No markdown bold/italic (LinkedIn doesn't render it)
- One blank line between paragraphs

## Error Handling
- If Claude returns malformed output (not valid JSON), retry once with a stricter prompt
- If retry fails, raise an exception — do not save a broken post
- Never publish a post without an `image_url`

## Known Constraints
- Claude `claude-opus-4-6` gives the best voice consistency; use it for this step
- System prompt must establish Muhammad Usman's voice before the user message
