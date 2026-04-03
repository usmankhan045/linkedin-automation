# Workflow: DM Ghostwriter

## Objective
When Muhammad Usman posts a command in the Discord `#ghostwriter` channel, the bot routes it to a persona-specific Groq prompt and returns a ready-to-send LinkedIn DM draft within seconds.

## Trigger
Discord bot event: `on_message` in `#ghostwriter` channel (`DISCORD_GHOSTWRITER_CHANNEL_ID`)
Runs as part of the persistent Discord bot (`tools/discord_bot.py`).

## Tool
`tools/dm_ghostwriter.py` (called from `tools/discord_bot.py`)

## Commands

| Command | Target | Use case |
|---------|--------|----------|
| `/biz [message]` | Business owner / SME founder | Reply to a lead from a business owner asking about AI |
| `/tech [message]` | Developer / engineer | Engage a technical peer — collaboration, referral, peer conversation |
| `/follow [message]` | Anyone who commented | Warm follow-up to a commenter on a post |
| `/reply [message]` | Any context | Generic reply when none of the above fit |

`[message]` is the original LinkedIn message or comment Muhammad wants to reply to.

## Inputs
| Field | Source | Description |
|-------|--------|-------------|
| `command` | Discord message prefix | One of `/biz`, `/tech`, `/follow`, `/reply` |
| `original_message` | Everything after the command | The LinkedIn message or comment being replied to |

## Step-by-Step Execution

### Step 1 — Parse Command
- Strip the command prefix from `message.content`
- If no recognized prefix found: reply with help text (see edge cases)
- `original_message = message.content[len(command):].strip()`
- If `original_message` is empty: reply with "Please include the message after the command."

### Step 2 — Route to Persona Prompt

**Model:** `llama-3.3-70b-versatile`
**Temperature:** 0.85 (more conversational variation)
**Max tokens:** 512

**Base system prompt (all personas):**
```
You are ghostwriting LinkedIn DMs for Muhammad Usman, an AI Automation Engineer from Pakistan.

His communication style:
- Warm but professional — reads like a real person, not a sales bot
- Confident without being pushy
- Specific — references what the other person actually said
- Short — LinkedIn DMs that are too long get ignored. Aim for 3–5 sentences max
- Never uses: "Hope this finds you well", "I wanted to reach out", "circle back", "synergy"
- Always ends with ONE clear next step (a question or a soft ask)
```

**Per-persona user prompts:**

`/biz` — Business Owner:
```
The person who sent this is likely a business owner or non-technical decision maker.
They may have commented on a LinkedIn post or messaged directly.

Original message:
"{original_message}"

Write a LinkedIn DM reply that:
- Acknowledges what they specifically said
- Briefly positions Muhammad as someone who solves their exact problem
- Asks ONE qualifying question (e.g., what the business does, what process is painful)
- Tone: helpful consultant, not a salesperson
- 3–4 sentences max

Reply only with the DM text — no explanation, no labels.
```

`/tech` — Developer / Engineer:
```
The person who sent this is a developer, engineer, or technical builder.
They may be a peer, a potential collaborator, or someone referencing Muhammad's work.

Original message:
"{original_message}"

Write a LinkedIn DM reply that:
- Matches their technical level — don't dumb it down
- Shows genuine interest in what they're building or thinking
- Suggests a natural next step (share a repo, jump on a call, exchange notes)
- Tone: peer-to-peer, curious, low-pressure
- 3–4 sentences max

Reply only with the DM text — no explanation, no labels.
```

`/follow` — Post Commenter Follow-Up:
```
This person commented on one of Muhammad's LinkedIn posts.
He wants to follow up and deepen the connection.

Their comment / original message:
"{original_message}"

Write a LinkedIn DM reply that:
- References what they said in the comment specifically
- Doesn't feel like a mass outreach message
- Either: asks a follow-up question, shares a resource, or invites a conversation
- Tone: genuine, low-pressure, like reaching out to someone interesting at a conference
- 2–3 sentences max

Reply only with the DM text — no explanation, no labels.
```

`/reply` — Generic:
```
Muhammad wants to reply to this LinkedIn message. No specific context about who they are.

Original message:
"{original_message}"

Write a LinkedIn DM reply that:
- Is natural and specific to what was said
- Moves the conversation forward with one clear next step
- Tone: professional but warm
- 3–4 sentences max

Reply only with the DM text — no explanation, no labels.
```

### Step 3 — Return Draft to Discord
Reply in `#ghostwriter` with:
```
✍️ **DM Draft ({command})**

{generated_reply}

---
📋 Copy above ↑
🔄 Reply with `/retry` to regenerate | `/biz`, `/tech`, `/follow`, or `/reply [message]` for a new draft
```

## Expected Outputs
- Discord reply in `#ghostwriter` with a ready-to-send DM draft
- No database writes (this is ephemeral — drafts are not saved)

## Edge Cases

### Unrecognized Command
- Message doesn't start with `/biz`, `/tech`, `/follow`, or `/reply`
- Reply with:
  ```
  ❓ Command not recognized. Available commands:
  /biz [message]    — Reply to a business owner
  /tech [message]   — Reply to a developer/engineer
  /follow [message] — Follow up with a post commenter
  /reply [message]  — Generic reply (any context)
  ```

### Empty Message After Command
- Example: user types just `/biz` with nothing after
- Reply: "Please include the original LinkedIn message after the command. Example: `/biz Hey, I saw your post about automation...`"

### Message Too Long (> 2,000 characters)
- LinkedIn DM context is useful; long messages are fine for Groq
- Truncate to 2,000 characters if needed, add note: "[Note: message truncated for context]"
- Still process normally

### `/retry` Command
- Regenerate the last draft for the same command + message
- Implementation: store last command + message in Discord bot memory (in-process dict, keyed by `user_id`)
- If no previous command in session: reply "No previous command found in this session. Send a new command to start."

### Groq Unavailable / Timeout
- Retry once after 5 seconds
- If still failing: reply "⚠️ Groq API is temporarily unavailable. Try again in a moment."
- Do not crash the bot

### Bot Offline
- This is a persistent bot — if it's down, commands are missed (no queue)
- When bot comes back online: no backfill for missed `/ghostwriter` commands
- Solution: host bot on a reliable server (Railway, Fly.io, VPS)

## Rate Limits & Timing Notes
- Groq: 1 call per command — no rate limit concern at normal usage
- Response time: ~2–4 seconds from command to Discord reply
- No external API calls beyond Groq and Discord
- This tool is designed for interactive use only — do not automate commands into this channel
