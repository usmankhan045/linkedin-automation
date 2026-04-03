"""
sheets_client.py — Google Sheets API wrapper.

Provides read/write access to the LinkedIn Automation Google Spreadsheet.
Auth uses a service account: reads GOOGLE_SERVICE_ACCOUNT_JSON env var
(full JSON string for GitHub Actions) or GOOGLE_SERVICE_ACCOUNT_JSON_PATH
(local path for development).

Used by: generate_posts.py, generate_images.py, publish_post.py, triage_comments.py
Sheets structure:
  Tab 1 — Topic Bank:      topic | category | key_details | audience |
                            status | scheduled_date | post_id | image_path
  Tab 2 — Content Backlog: comment_text | commenter_name | original_post_topic |
                            suggested_angle | date | used
"""

import os
import sys
import json

from google.oauth2 import service_account
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']


def get_service():
    # TODO: Load credentials from GOOGLE_SERVICE_ACCOUNT_JSON (env var, full JSON string)
    #       or fall back to GOOGLE_SERVICE_ACCOUNT_JSON_PATH (local file path)
    # TODO: Build and return googleapiclient Sheets service object
    pass


def get_pending_topics(tab_name: str, limit: int = 5) -> list[dict]:
    """Return up to `limit` rows with status='pending' from the Topic Bank tab."""
    # TODO: Call spreadsheets.values.get for the full tab range
    # TODO: Parse header row → map to dict keys
    # TODO: Filter rows where status == 'pending'
    # TODO: Return first `limit` rows as list of dicts
    pass


def update_row(tab_name: str, row_index: int, updates: dict) -> None:
    """Write specific column values to a row by its 1-based sheet row index."""
    # TODO: For each key in updates, find the column letter from header
    # TODO: Use spreadsheets.values.batchUpdate to write changes
    pass


def append_to_backlog(tab_name: str, row: dict) -> None:
    """Append a new row to the Content Backlog tab."""
    # TODO: Build row values in correct column order
    # TODO: Use spreadsheets.values.append
    pass


def main():
    # TODO: Quick smoke-test — fetch first 3 rows from Topic Bank and print them
    pass


if __name__ == '__main__':
    main()
