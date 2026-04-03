---
name: linkedin-automation
description: Python and GitHub Actions automation for LinkedIn personal branding.
---

# LinkedIn Automation Engine — Project Skill

## What this project is
Python + GitHub Actions system that automates Muhammad Usman's LinkedIn personal branding.
Posts 5x/week + Saturday stories. Zero cost. No n8n.

## Tech stack
- LLM: Groq API, model: llama-3.3-70b-versatile
- Sheets: gspread + Google Service Account
- LinkedIn: Official REST API v2 (w_member_social scope)
- Images: Playwright (headless Chromium) rendering HTML templates
- Storage: Supabase (posts, processed_comments, config, content_backlog tables)
- Image storage: Supabase Storage bucket "post-images"
- Discord: discord.py bot + webhooks
- Scheduler: GitHub Actions cron
- Timezone: Asia/Karachi (PKT = UTC+5)

## WAT structure
- workflows/ — Markdown SOPs (do not modify without asking)
- tools/ — Python scripts (one per job)
- templates/ — HTML image templates (3 variants)
- .github/workflows/ — GitHub Actions cron jobs
- supabase/ — Schema SQL

## Content types
- engineer (Mon/Wed/Fri): technical posts, dark navy + orange template
- founder (Tue/Thu): business ROI posts, dark charcoal + blue template
- story (Saturday only): portfolio/journey posts, dark + green template

## Google Sheets structure
Tab 1 "Topic Bank": topic | category | key_details | audience | status | scheduled_date | post_id | image_path | post_text | headline | bullets
Tab 2 "Content Backlog": comment_text | commenter_name | original_post_topic | suggested_angle | date | used

## Weekly flow
Sunday 8am PKT: generate_posts.py + generate_images.py run
Sunday onwards: Review in Google Sheets, flip status to "approved"  
Mon-Fri 12pm PKT: publish_post.py runs, publishes that day's approved post
Every 2hrs weekdays: triage_comments.py runs
Sunday 10am PKT: weekly_digest.py runs

## Key rules when modifying
- Never use Gemini (rate limits too low) — always Groq llama-3.3-70b-versatile
- Never hardcode credentials — always .env
- Images must be uploaded to Supabase Storage (GitHub Actions ephemeral filesystem)
- LinkedIn token expires every 60 days — check LINKEDIN_ACCESS_TOKEN in GitHub Secrets
- Anti-AI voice rules in prompts must be preserved — they are critical to post quality