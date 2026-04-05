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

### Technical — "The Architect" (Mon / Wed / Fri)
**Persona:** Senior AI Automation Engineer — speaks in "Efficiency per Line of Code"
**Target:** AI developers, engineers, CTOs, technical builders
**Hook:** Technical hot take or specific failure moment — makes a fellow engineer stop scrolling
**Depth:** Uses Latency, API Rate Limits, State Management, Data Normalization naturally — never defined
**Structure:**
- Winner/Loser framework: compare two tools (e.g., n8n vs. LangGraph)
- Bullet 1: Performance/Speed — concrete benchmark or latency number
- Bullet 2: Ease of Deployment — real friction, not vague complexity
- Bullet 3: Cost at Scale — actual cost difference at volume
**CTA:** High-level architectural question (e.g., "How are you handling persistent memory in multi-agent loops?")
**Hashtags:** #AIEngineering #BuildInPublic #Python #Automation (3–4)
**Image:** System architecture diagram or benchmark comparison table

### Business — "The ROI Strategist" (Tue / Thu)
**Persona:** Fractional CTO / Automation Strategist — speaks in "Hours Saved" and "Revenue Retained"
**Target:** CEOs, founders, operations managers — non-technical
**HARD CONSTRAINT:** Zero technical vocabulary. No Python, API, JSON, code, script, LLM, node. Use: Workflow, Systems, Staff Capacity, Accuracy, Systemize.
**Hook:** The "Silent Killer" — specific operational cost every founder silently pays (e.g., "Your most expensive employee spends 10 hours/week copy-pasting data")
**Structure:**
- Make the cost visible: hours/week → hours/year → dollar figure
- Before vs. After framework — concrete enough to picture their own team
- Step 1: Audit the repetitive
- Step 2: Remove the human bottleneck
- Step 3: Scale without hiring
- One human detail: what the freed-up person actually does with that time (not "higher-value work")
**CTA:** Business logic question (e.g., "If you could systemize one task today to free up your Sales team, what would it be?")
**Hashtags:** #AIForBusiness #Automation #OperationalEfficiency (3)
**Image:** Consultant's Slide — outcome metric as dominant headline, clean white background, numbered steps

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
