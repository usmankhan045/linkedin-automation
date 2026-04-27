# LinkedIn Automation System

An intelligent, fully automated end-to-end LinkedIn management system. This project is built using the **WAT (Workflows, Agents, Tools)** framework, combining probabilistic AI decision-making with deterministic execution scripts.

## 🧠 Architecture: The WAT Framework

This system runs on a separation of concerns that ensures reliability, observability, and self-improvement:

1. **Workflows (Layer 1 - Instructions)**: Markdown SOPs (Standard Operating Procedures) in the `workflows/` directory. These define objectives, required inputs, edge cases, and which tools to use. They act as the overarching instructions for the system.
2. **Agents (Layer 2 - The Brain)**: The AI coordinator (powered by OpenAI, Anthropic, or Groq) reads the workflows, makes intelligent decisions, orchestrates execution sequences, handles failures gracefully, and asks clarifying questions.
3. **Tools (Layer 3 - Execution)**: Deterministic Python scripts in the `tools/` directory. These handle API calls, data scraping, database interactions, and file manipulations. 

*Why this matters:* By offloading exact execution to deterministic Python scripts (Tools), the AI Agent avoids compounding hallucination errors and focuses strictly on orchestration and reasoning.

## ✨ Core Features & Workflows

The repository contains several key workflows orchestrating a complete LinkedIn growth engine. Below is a detailed breakdown of how each feature operates within the WAT framework:

### 1. Story Intake & Ingestion (`story_intake.md`, `ingest_story.md`)
* **How it works:** The system ingests raw inputs such as random notes, URLs, shower thoughts, or voice transcriptions. The AI Agent parses these unstructured inputs, extracts the core narrative or value proposition, and structures them into workable "story concepts." 
* **The execution:** It offloads data processing and saving to Python tools, which push these structured records to Supabase or Google Sheets for future use.

### 2. Content Generation (`generate_post.md`, `generate_image.md`)
* **How it works:** Taking the structured story concepts from the database, the AI ghostwrites full LinkedIn posts. It acts as an expert copywriter, applying specific tone guidelines, formatting rules (like hooks, line breaks, and strong call-to-actions), and constraints defined in the workflow. 
* **The execution:** If the post requires media, the image generation workflow triggers AI image-generation tools to create relevant visuals, ensuring rich media posts are ready for the feed.

### 3. Scheduling (`weekly_generation.md`, `weekly_schedule.md`)
* **How it works:** Instead of generating one-off posts randomly, the system batches content creation. It aggregates the generated posts and logically slots them into a weekly calendar to ensure topic variety and optimal posting times.
* **The execution:** Calendar and spreadsheet manipulation tools (via Google Sheets API) are executed to assign publication dates, update statuses, and manage the live content queue.

### 4. Publishing (`publish_post.md`, `daily_publishing.md`)
* **How it works:** A cron-like agent checks the scheduling queue daily. When a post is due, the agent triggers the publishing tools to log into LinkedIn natively and post the content, including uploading any generated images. This runs completely hands-off.
* **The execution:** Deterministic browser automation scripts (`playwright`) navigate LinkedIn's DOM, handle the exact click paths, and submit posts safely, avoiding API limitations.

### 5. Engagement & Triage (`comment_triage.md`, `dm_ghostwriter.md`)
* **How it works:** Publishing is only half the battle. This workflow periodically scrapes incoming comments on recent posts and new Direct Messages. The AI filters out spam or noise, categorizes the engagement, and auto-drafts contextual, on-brand replies.
* **The execution:** `playwright` is used to scrape the feed and DMs safely, LLMs analyze the sentiment and draft responses, and `discord.py` can be used to ping the human operator with drafted replies for approval before sending.

### 6. Analytics (`weekly_digest.md`)
* **How it works:** At the end of the week, the system pulls performance data (impressions, likes, comments, profile views) for the published content. It compiles this into a digest to analyze what topics resonated best.
* **The execution:** Scraping tools gather the metrics, data processing scripts format them, and the agent outputs a digest to Google Sheets or Discord, feeding insights back into the Story Intake loop.

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
