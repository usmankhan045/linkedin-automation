# LinkedIn Automation System

An intelligent, fully automated end-to-end LinkedIn management system. This project is built using the **WAT (Workflows, Agents, Tools)** framework, combining probabilistic AI decision-making with deterministic Python execution for reliable content ingestion, creation, scheduling, and engagement.

## 🧠 Architecture: The WAT Framework

This system runs on a separation of concerns that ensures reliability, observability, and self-improvement:

1. **Workflows (Layer 1 - Instructions)**: Markdown SOPs (Standard Operating Procedures) in the `workflows/` directory. These define objectives, required inputs, edge cases, and which tools to use. They act as plain-language instructions for the AI.
2. **Agents (Layer 2 - The Brain)**: The AI coordinator (powered by OpenAI, Anthropic, or Groq) reads the workflows, makes intelligent decisions, orchestrates execution sequences, handles failures gracefully, and updates workflows as it learns.
3. **Tools (Layer 3 - Execution)**: Deterministic Python scripts in the `tools/` directory. These handle API calls, data scraping, database interactions, and file manipulations. 

*Why this matters:* By offloading exact execution to deterministic Python scripts (Tools), the AI Agent avoids compounding hallucination errors and focuses strictly on orchestration and reasoning.

## ✨ Core Features & Workflows

The repository contains several key workflows orchestrating a complete LinkedIn growth engine:

* **Story Intake & Ingestion (`story_intake.md`, `ingest_story.md`)**: Automatically processes raw notes, links, or ideas and structures them into workable story concepts.
* **Content Generation (`generate_post.md`, `generate_image.md`)**: AI ghostwrites LinkedIn posts matching specific tones and formats, alongside automated image generation for rich media posts.
* **Scheduling (`weekly_generation.md`, `weekly_schedule.md`)**: Aggregates generated content and logically schedules them out for the week.
* **Publishing (`publish_post.md`, `daily_publishing.md`)**: Executes the actual publishing to LinkedIn at scheduled intervals without manual intervention.
* **Engagement & Triage (`comment_triage.md`, `dm_ghostwriter.md`)**: Scrapes incoming comments and Direct Messages, filtering out noise, and auto-drafting contextual replies.
* **Analytics (`weekly_digest.md`)**: Compiles performance metrics into a digest to review what worked and what didn't.

## 🛠 Tech Stack

The tools and agents rely on a modern, robust tech stack:

* **Core Language:** Python 3 (82.8% of codebase)
* **LLM Orchestration:** `openai`, `anthropic`, `groq` 
* **Web Automation / Scraping:** `playwright` (to interact with LinkedIn's DOM safely)
* **Database & Storage:** `supabase` (PostgreSQL via `PLpgSQL` + HTML templates)
* **Data I/O & Integrations:** `gspread`, `google-api-python-client` (Google Sheets integration for CRM/content grids)
* **Notifications/Alerts:** `discord.py` (For runtime logs or manual approval pings)
* **Image Processing:** `Pillow`

## 📂 Directory Structure

```text
.
├── .tmp/                   # Temporary processing files (disposable)
├── tools/                  # Python scripts for deterministic execution (Layer 3)
├── workflows/              # Markdown SOPs defining workflows (Layer 1)
├── supabase/               # DB migrations and configurations
├── templates/              # HTML Templates and prompts
├── requirements.txt        # Core dependencies
└── .env.example            # Template for required API keys
```
*(Note: Cloud services like Google Sheets and Supabase serve as the final storage destinations; local files are strictly for temporary processing.)*

## 🚀 Setup & Installation

**1. Clone the repository:**
```bash
git clone https://github.com/usmankhan045/linkedin-automation.git
cd linkedin-automation
```

**2. Install dependencies:**
```bash
pip install -r requirements.txt
```

**3. Environment Variables:**
Copy `.env.example` to `.env` and fill in your credentials.
```bash
cp .env.example .env
```
*Required secrets generally include API keys for LLMs (OpenAI/Anthropic/Groq), Supabase credentials, Discord webhook URLs, and LinkedIn session cookies depending on the tools.*

**4. Setup OAuth (Google Sheets):**
Ensure your `credentials.json` and `token.json` (gitignored) are placed in the root directory if you are using Google Sheets as part of the intake/scheduling CRM.

**5. Initialize the Agent:**
Start the agent using your preferred runner. The Agent will begin by reading the target Markdown file in `workflows/` and executing the associated `tools/`.

## 🔄 The Self-Improvement Loop

When the agent encounters unexpected errors (e.g., rate limits, LinkedIn UI changes):
1. **Identify** the break point via error traces.
2. **Refactor** the Python tool script.
3. **Verify** the fix works.
4. **Update** the Workflow (`.md`) to document the new constraint.
This ensures the automation framework gets perpetually more resilient over time.