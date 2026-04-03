"""
sheets_helper.py — Google Sheets utility module.

Shared by generate_posts.py, publish_post.py, and other tools that need
read/write access to the LinkedIn Automation spreadsheet.

Auth priority:
  1. GOOGLE_SERVICE_ACCOUNT_JSON   — full JSON string (GitHub Actions / CI)
  2. GOOGLE_SERVICE_ACCOUNT_JSON_PATH — local file path (dev, defaults to ./credentials.json)

Sheets structure expected:
  Tab 1 — Topic Bank:      topic | category | key_details | audience |
                            status | scheduled_date | post_id | image_path |
                            post_text | bullet_points | headline
  Tab 2 — Content Backlog: comment_text | commenter_name | original_post_topic |
                            suggested_angle | date | used
"""

import os
import json
from datetime import datetime, date, timedelta
from typing import Optional

import gspread
import pytz
from dotenv import load_dotenv

load_dotenv()

PKT = pytz.timezone('Asia/Karachi')

TOPIC_BANK_TAB = os.getenv('SHEETS_TOPIC_BANK_TAB', 'Topic Bank')
CONTENT_BACKLOG_TAB = os.getenv('SHEETS_CONTENT_BACKLOG_TAB', 'Content Backlog')

# Module-level client cache — avoid re-authenticating on every call
_client: Optional[gspread.Client] = None


def get_sheet_client() -> gspread.Client:
    """
    Return an authenticated gspread client (singleton, cached across calls).

    Loads credentials from GOOGLE_SERVICE_ACCOUNT_JSON (full JSON string, for CI)
    or GOOGLE_SERVICE_ACCOUNT_JSON_PATH (local file path, for development).
    """
    global _client
    if _client is not None:
        return _client

    sa_json_str = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    if sa_json_str:
        sa_info = json.loads(sa_json_str)
    else:
        sa_path = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON_PATH', './credentials.json')
        with open(sa_path, 'r') as f:
            sa_info = json.load(f)

    _client = gspread.service_account_from_dict(sa_info)
    return _client


def _open_worksheet(spreadsheet: gspread.Spreadsheet, tab_name: str) -> gspread.Worksheet:
    """Open a worksheet by name, raising a clear RuntimeError if not found."""
    try:
        return spreadsheet.worksheet(tab_name)
    except gspread.exceptions.WorksheetNotFound as e:
        available_tabs = [ws.title for ws in spreadsheet.worksheets()]
        raise RuntimeError(
            f"Worksheet '{tab_name}' not found in spreadsheet '{spreadsheet.id}'. "
            f"Available tabs: {available_tabs}. "
            "Fix the relevant SHEETS_* secret or create the missing tab."
        ) from e


def _parse_rows(ws: gspread.Worksheet) -> list:
    """
    Read all rows from a worksheet and return a list of dicts.

    Each dict uses the header row as keys. A '_row_index' key is added with
    the 1-based sheet row number (row 1 = header, row 2 = first data row).
    Rows shorter than the header are right-padded with empty strings.
    """
    all_values = ws.get_all_values()
    if not all_values or len(all_values) < 2:
        return []

    headers = all_values[0]
    rows = []
    for i, row_values in enumerate(all_values[1:], start=2):
        # Pad to header length so zip always covers all columns
        padded = row_values + [''] * (len(headers) - len(row_values))
        row_dict = dict(zip(headers, padded))
        row_dict['_row_index'] = i
        rows.append(row_dict)
    return rows


def get_topic_bank(sheet_id: str) -> list:
    """
    Return all Topic Bank rows with status='pending', each including '_row_index'.

    Used by generate_posts.py to find topics for the coming week.
    """
    ws = _open_worksheet(get_sheet_client().open_by_key(sheet_id), TOPIC_BANK_TAB)
    all_rows = _parse_rows(ws)
    return [r for r in all_rows if r.get('status') == 'pending']


def get_todays_post(sheet_id: str) -> Optional[dict]:
    """
    Return the Topic Bank row where status='approved' AND scheduled_date=today (PKT).

    Returns None if no matching row is found. The returned dict includes '_row_index'
    so callers can pass it directly to update_row_status().
    """
    today_str = datetime.now(PKT).date().strftime('%Y-%m-%d')
    ws = _open_worksheet(get_sheet_client().open_by_key(sheet_id), TOPIC_BANK_TAB)
    all_rows = _parse_rows(ws)
    for row in all_rows:
        if row.get('status') == 'approved' and row.get('scheduled_date') == today_str:
            return row
    return None


def update_row_status(sheet_id: str, row_index: int, updates: dict) -> None:
    """
    Write specific column values to a Topic Bank row.

    Args:
        sheet_id:  Google Sheets spreadsheet ID.
        row_index: 1-based sheet row number (row 1 = header, 2 = first data row).
                   Use the '_row_index' value returned by get_topic_bank() / get_todays_post().
        updates:   Dict mapping column header names to new values.
                   Example: {"status": "published", "post_id": "urn:li:share:123"}

    Unknown column names are skipped with a warning. Uses batch update to minimise
    API round-trips.
    """
    ws = _open_worksheet(get_sheet_client().open_by_key(sheet_id), TOPIC_BANK_TAB)
    headers = ws.row_values(1)

    cell_list = []
    for key, value in updates.items():
        if key in headers:
            col = headers.index(key) + 1  # gspread uses 1-based column indices
            cell_list.append(gspread.Cell(row_index, col, str(value) if value is not None else ''))
        else:
            print(f"[{datetime.now()}] Warning: column '{key}' not found in Topic Bank — skipped")

    if cell_list:
        ws.update_cells(cell_list, value_input_option='USER_ENTERED')


def append_content_backlog(sheet_id: str, data: dict) -> None:
    """
    Append a new row to the Content Backlog tab.

    Expected keys in data: comment_text, commenter_name, original_post_topic, suggested_angle.
    The 'date' and 'used' columns are filled automatically.
    """
    ws = _open_worksheet(get_sheet_client().open_by_key(sheet_id), CONTENT_BACKLOG_TAB)
    today_str = datetime.now(PKT).strftime('%Y-%m-%d')
    row = [
        data.get('comment_text', ''),
        data.get('commenter_name', ''),
        data.get('original_post_topic', ''),
        data.get('suggested_angle', ''),
        today_str,
        'no',
    ]
    ws.append_row(row, value_input_option='USER_ENTERED')


def get_week_schedule(sheet_id: str) -> list:
    """
    Return all Topic Bank rows whose scheduled_date falls in the upcoming Mon-Fri.

    'Upcoming' means the next Monday relative to today in PKT, so running this on
    Sunday returns the Mon-Fri that starts tomorrow.

    Used by generate_posts.py to check if the week is already generated before
    running the batch — returns rows regardless of status.
    """
    today = datetime.now(PKT).date()

    # weekday(): 0=Mon ... 6=Sun. Find days until the next Monday.
    days_ahead = (7 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # Today IS Monday — target next week's Monday

    next_monday = today + timedelta(days=days_ahead)
    week_dates = {
        (next_monday + timedelta(days=i)).strftime('%Y-%m-%d')
        for i in range(5)
    }

    ws = _open_worksheet(get_sheet_client().open_by_key(sheet_id), TOPIC_BANK_TAB)
    all_rows = _parse_rows(ws)
    return [r for r in all_rows if r.get('scheduled_date') in week_dates]


if __name__ == '__main__':
    # Smoke test: print first 3 pending rows from Topic Bank
    spreadsheet_id = os.environ['GOOGLE_SHEETS_SPREADSHEET_ID']
    print(f"[{datetime.now()}] Connecting to sheet: {spreadsheet_id}")
    topics = get_topic_bank(spreadsheet_id)
    print(f"[{datetime.now()}] Found {len(topics)} pending topics")
    for row in topics[:3]:
        # Hide internal key from display
        display = {k: v for k, v in row.items() if k != '_row_index'}
        print(f"  Row {row['_row_index']}: {display}")
