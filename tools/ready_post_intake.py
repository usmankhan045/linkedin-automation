"""
ready_post_intake.py — Handle ready-made LinkedIn posts submitted to Discord.

Called from dm_ghostwriter.py when a message is received in the #ready-posts channel.

The user pastes their complete LinkedIn post text — nothing else.
The bot automatically:
  1. Extracts hook, headline, bullet points, category, and audience via Groq
  2. Renders a branded infographic image using the HTML template + Playwright
  3. Saves the post to Supabase
  4. Queues it on the next available weekday slot in Google Sheets
  5. Replies with a preview and image URL

Differs from story_intake.py:
  - No AI rewriting — post content is used verbatim
  - Image is rendered from the HTML template (not DALL-E)
  - Slots are next available weekday (Mon-Fri), not next Saturday

Env vars: GROQ_API_KEY, GROQ_MODEL, GOOGLE_SHEETS_SPREADSHEET_ID,
          SHEETS_TOPIC_BANK_TAB, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytz
from groq import Groq
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.db_client as db
import tools.sheets_helper as sheets

load_dotenv()

PKT = pytz.timezone('Asia/Karachi')
TOPIC_BANK_TAB = os.getenv('SHEETS_TOPIC_BANK_TAB', 'Topic Bank')
GROQ_MODEL = os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')
MIN_POST_LENGTH = 50

TEMPLATES_DIR = Path(__file__).parent.parent / 'templates'

CATEGORY_LABELS: dict[str, str] = {
    'build-log':      'BUILD LOG',
    'transformation': 'AUTOMATION TIP',
    'hot-take':       'HOT TAKE',
    'behind-scenes':  'BEHIND THE SCENES',
    'founder-roi':    'FOUNDER ROI',
    'story':          'MY STORY',
}

AUDIENCE_TEMPLATES: dict[str, str] = {
    'engineer': 'technical_post.html',
    'founder':  'business_post.html',
    'story':    'story_post.html',
}


# ── Groq extraction ────────────────────────────────────────────────────────────

EXTRACTION_SYSTEM = (
    'You analyze LinkedIn posts and extract structured data for branded infographic images. '
    'Be concise, punchy, and visual. Respond in JSON only.'
)

EXTRACTION_TEMPLATE = (
    'Analyze this LinkedIn post and extract the following in JSON:\n\n'
    'Post:\n---\n{post_text}\n---\n\n'
    'Fields to extract:\n'
    '- hook: The single most striking sentence from the post. Max 80 chars. '
    'Should work as a large visual headline. Pull directly from the post text — do not rewrite.\n'
    '- headline: A short supporting subtitle (3-8 words). Max 90 chars. Captures the core theme.\n'
    '- bullet_points: Array of 3-5 key takeaways. Each point: 2-7 words, scannable and punchy.\n'
    '- category: One of: hot-take, build-log, transformation, behind-scenes, founder-roi, story\n'
    '- audience: One of: engineer (technical/dev content), founder (business/leadership), story (personal narrative)\n\n'
    'JSON only, no explanation:\n'
    '{{"hook": "...", "headline": "...", "bullet_points": ["...", "...", "..."], '
    '"category": "...", "audience": "..."}}'
)


def extract_post_structure(groq_client: Groq, post_text: str) -> dict:
    """
    Call Groq to extract hook, headline, bullet_points, category, and audience from the post.

    Retries once on JSON parse failure. Raises on second failure.
    """
    user_prompt = EXTRACTION_TEMPLATE.format(post_text=post_text[:3000])

    for attempt in range(2):
        try:
            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {'role': 'system', 'content': EXTRACTION_SYSTEM},
                    {'role': 'user', 'content': user_prompt},
                ],
                temperature=0.4,
                max_tokens=512,
            )
            raw = response.choices[0].message.content.strip()

            if raw.startswith('```'):
                lines = raw.splitlines()
                raw = '\n'.join(lines[1:-1] if lines[-1].strip() == '```' else lines[1:]).strip()

            result = json.loads(raw)

            # Validate required keys and normalise
            result['hook'] = str(result.get('hook', '') or '')[:80]
            result['headline'] = str(result.get('headline', '') or '')[:90]
            result['bullet_points'] = [
                str(b) for b in result.get('bullet_points', []) if b
            ][:5]
            result['category'] = result.get('category', 'build-log')
            result['audience'] = result.get('audience', 'engineer')

            # Fallback if hook is empty
            if not result['hook']:
                result['hook'] = post_text.split('\n')[0].strip()[:80]

            return result

        except json.JSONDecodeError as e:
            if attempt == 0:
                print(f'[WARN] Groq JSON parse error on extraction, retrying: {e}')
                time.sleep(2)
            else:
                raise


# ── Image rendering ────────────────────────────────────────────────────────────

def _render_image(html_string: str, output_path: str) -> bool:
    """Render HTML to a 1080×1350 PNG using Playwright. Returns True on success."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width': 1080, 'height': 1350})
            page.set_content(html_string)
            page.wait_for_load_state('networkidle')
            page.screenshot(path=output_path, full_page=False)
            browser.close()
        return True
    except Exception as e:
        print(f'[WARN] Playwright render error: {e}')
        return False


def _build_html(template_path: Path, post: dict, bullets: list[str], post_number: int) -> str:
    """Fill template placeholders with post data."""
    html = template_path.read_text(encoding='utf-8')

    audience_titles = {
        'engineer': 'AI Automation Engineer',
        'founder':  'Automation Strategist',
        'story':    'AI Automation Engineer',
    }

    padded = (bullets + [''] * 5)[:5]
    bullet_count = len(bullets)

    replacements = {
        '{{CATEGORY_LABEL}}': post.get('category_label', 'BUILD LOG'),
        '{{HOOK}}':           post.get('hook', '')[:80],
        '{{HEADLINE}}':       post.get('headline', ''),
        '{{BULLET_1}}':       padded[0],
        '{{BULLET_2}}':       padded[1],
        '{{BULLET_3}}':       padded[2],
        '{{BULLET_4}}':       padded[3],
        '{{BULLET_5}}':       padded[4],
        '{{BULLET_COUNT}}':   str(bullet_count),
        '{{POST_NUMBER}}':    str(post_number),
        '{{NAME}}':           'Muhammad Usman',
        '{{TITLE}}':          audience_titles.get(post.get('audience', 'engineer'), 'AI Automation Engineer'),
        '{{BULLET_4_STYLE}}': 'display:none' if bullet_count < 4 else '',
        '{{BULLET_5_STYLE}}': 'display:none' if bullet_count < 5 else '',
    }

    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)
    return html


def render_and_upload_image(post_id: str, structure: dict, post_number: int) -> str | None:
    """
    Render the appropriate HTML template and upload the result to Supabase Storage.

    Returns the public image URL, or None on failure.
    """
    audience = structure.get('audience', 'engineer')
    category = structure.get('category', 'build-log')

    template_file = AUDIENCE_TEMPLATES.get(audience, 'technical_post.html')
    template_path = TEMPLATES_DIR / template_file

    if not template_path.exists():
        print(f'[WARN] Template not found: {template_path}')
        return None

    category_label = CATEGORY_LABELS.get(category, category.upper().replace('-', ' '))
    post_data = {**structure, 'category_label': category_label}
    bullets = structure.get('bullet_points', [])

    if not bullets:
        print(f'[WARN] No bullet points extracted for post {post_id}')
        return None

    try:
        html = _build_html(template_path, post_data, bullets, post_number)
    except Exception as e:
        print(f'[WARN] Template build error for post {post_id}: {e}')
        return None

    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        if not _render_image(html, tmp_path):
            return None

        with open(tmp_path, 'rb') as f:
            image_bytes = f.read()

        image_url = db.upload_image(post_id, image_bytes)
        return image_url

    except Exception as e:
        print(f'[WARN] Image upload failed for post {post_id}: {e}')
        return None

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Scheduling ─────────────────────────────────────────────────────────────────

def next_available_weekday_slot(spreadsheet_id: str) -> tuple[str, bool]:
    """
    Find the next Mon-Fri that has no pending/queued row in the Topic Bank.

    Looks up to 30 calendar days ahead. Returns (date_str, overbooked).
    """
    today = datetime.now(PKT).date()

    try:
        ws = sheets.get_sheet_client().open_by_key(spreadsheet_id).worksheet(TOPIC_BANK_TAB)
        all_rows = ws.get_all_values()
        headers = all_rows[0] if all_rows else []
        date_col = headers.index('scheduled_date') if 'scheduled_date' in headers else -1
        status_col = headers.index('status') if 'status' in headers else -1
        data_rows = all_rows[1:] if len(all_rows) > 1 else []
    except Exception as e:
        print(f'[WARN] Could not read Topic Bank for slot check: {e}')
        candidate = today + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate.strftime('%Y-%m-%d'), False

    taken: set[str] = set()
    for row in data_rows:
        if date_col >= 0 and len(row) > date_col and row[date_col]:
            is_published = (
                status_col >= 0
                and len(row) > status_col
                and row[status_col] == 'published'
            )
            if not is_published:
                taken.add(row[date_col])

    total_rows = len(data_rows)

    for days_ahead in range(1, 31):
        candidate = today + timedelta(days=days_ahead)
        if candidate.weekday() < 5:
            candidate_str = candidate.strftime('%Y-%m-%d')
            if candidate_str not in taken:
                return candidate_str, False

    overbooked = today + timedelta(days=31)
    while overbooked.weekday() >= 5:
        overbooked += timedelta(days=1)
    return overbooked.strftime('%Y-%m-%d'), True


def _get_next_post_number(spreadsheet_id: str) -> int:
    """Return the next sequential post number based on current Topic Bank row count."""
    try:
        ws = sheets.get_sheet_client().open_by_key(spreadsheet_id).worksheet(TOPIC_BANK_TAB)
        return max(1, len(ws.get_all_values()) - 1 + 1)  # header + existing rows + 1 new
    except Exception:
        return 1


# ── Deduplication ──────────────────────────────────────────────────────────────

def ready_post_exists(discord_message_id: str) -> bool:
    """Return True if this Discord message was already processed."""
    result = (
        db.get_client()
        .table('posts')
        .select('id')
        .eq('topic', f'ready:{discord_message_id}')
        .execute()
    )
    return len(result.data) > 0


# ── Discord handler ────────────────────────────────────────────────────────────

async def handle_ready_post(message, groq_client: Groq, spreadsheet_id: str) -> None:
    """
    Process a Discord #ready-posts message end-to-end.

    1. Validates minimum length
    2. Deduplicates by Discord message ID
    3. Extracts hook, headline, bullets, category, audience via Groq
    4. Renders a branded infographic image with Playwright
    5. Saves post to Supabase
    6. Writes to Google Sheets Topic Bank (next available weekday)
    7. Replies with preview
    """
    content = message.content.strip()

    if len(content) < MIN_POST_LENGTH:
        await message.channel.send(
            f'⚠️ Post is too short ({len(content)} chars). '
            f'Minimum is {MIN_POST_LENGTH} characters.'
        )
        return

    if ready_post_exists(str(message.id)):
        return

    await message.channel.send('⏳ Analyzing your post and generating image...')

    # Extract structure via Groq
    try:
        structure = extract_post_structure(groq_client, content)
    except Exception as e:
        print(f'[ERROR] Groq extraction failed for message {message.id}: {e}')
        await message.channel.send(
            '⚠️ Could not analyze the post structure. Try again in a moment.'
        )
        return

    bullets_str = ' | '.join(structure['bullet_points'])
    category = structure['category']
    audience = structure['audience']

    # Save post to Supabase
    post_row = db.create_post(
        post_text=content,
        audience=audience,
        topic=f'ready:{message.id}',
        category=category,
    )
    post_id = post_row['id']

    # Render and upload image
    post_number = _get_next_post_number(spreadsheet_id)
    image_url = render_and_upload_image(post_id, structure, post_number)

    if image_url:
        db.update_post(post_id, image_url=image_url)

    # Find next available weekday slot
    slot_date, overbooked = next_available_weekday_slot(spreadsheet_id)

    # Write to Google Sheets Topic Bank
    sheets_error = None
    try:
        ws = sheets.get_sheet_client().open_by_key(spreadsheet_id).worksheet(TOPIC_BANK_TAB)
        ws.append_row(
            [
                content[:100],         # topic (preview)
                category,              # category
                bullets_str,           # key_details (pipe-separated bullets)
                audience,              # audience
                'pending',             # status
                slot_date,             # scheduled_date
                post_id,               # post_id
                image_url or '',       # image_path
                content,               # post_text (full, for publisher)
                bullets_str,           # bullet_points
                structure['headline'], # headline
            ],
            value_input_option='USER_ENTERED',
        )
    except Exception as e:
        sheets_error = str(e)
        print(f'[WARN] Sheets Topic Bank write failed (non-fatal): {e}')

    # Build Discord reply
    char_count = len(content)
    preview = content[:400] + ('...' if len(content) > 400 else '')
    category_label = CATEGORY_LABELS.get(category, category.upper())

    reply_lines = [
        f'✅ **Post queued for {slot_date}**',
        f'📊 {char_count} chars  ·  🏷️ {category_label}  ·  👥 {audience}',
        '',
        '---',
        preview,
        '---',
        '',
        f'**Hook:** {structure["hook"]}',
        f'**Headline:** {structure["headline"]}',
        f'**Bullets:** {bullets_str}',
    ]

    if image_url:
        reply_lines.append(f'\n🖼️ **Image:** {image_url}')
    else:
        reply_lines.append('\n⚠️ Image render failed — post saved, image can be generated manually.')

    if sheets_error:
        reply_lines.append(f'\n⚠️ Sheets write failed: {sheets_error}')
    else:
        reply_lines.append('\n✅ Set status to **approved** in Google Sheets when ready to publish.')

    if overbooked:
        reply_lines.append(
            f'\n⚠️ Calendar is packed — scheduled for {slot_date}. '
            'You may want to reschedule something.'
        )

    await message.channel.send('\n'.join(reply_lines))
    print(
        f'[{datetime.now(PKT)}] Ready post from {message.author} → '
        f'{category}/{audience} queued for {slot_date} (post_id={post_id})'
    )
