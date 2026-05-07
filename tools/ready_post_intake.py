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

import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import discord
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


# ── Approve button ─────────────────────────────────────────────────────────────

class ApproveView(discord.ui.View):
    """Sends an Approve button with the post preview. Updates Sheets status to 'approved' on click."""

    def __init__(self, post_id: str, spreadsheet_id: str):
        super().__init__(timeout=None)  # persists until bot restart
        self.post_id = post_id
        self.spreadsheet_id = spreadsheet_id

    @discord.ui.button(label='✅ Approve & Schedule', style=discord.ButtonStyle.success)
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            ws = sheets.get_sheet_client().open_by_key(self.spreadsheet_id).worksheet(TOPIC_BANK_TAB)
            all_values = ws.get_all_values()
            headers = all_values[0] if all_values else []

            try:
                post_id_col = headers.index('post_id')
                status_col = headers.index('status')
            except ValueError:
                await interaction.followup.send('⚠️ Required columns not found in Sheets.', ephemeral=True)
                return

            row_index = None
            current_status = None
            for i, row in enumerate(all_values[1:], start=2):
                if len(row) > post_id_col and row[post_id_col] == self.post_id:
                    row_index = i
                    current_status = row[status_col] if len(row) > status_col else ''
                    break

            if row_index is None:
                await interaction.followup.send('⚠️ Post not found in Sheets — it may have been moved or deleted.', ephemeral=True)
                return

            if current_status == 'approved':
                await interaction.followup.send('Already approved.', ephemeral=True)
                return

            # 1-based column index for gspread
            ws.update_cell(row_index, status_col + 1, 'approved')

            button.disabled = True
            button.label = '✅ Approved'
            button.style = discord.ButtonStyle.secondary
            await interaction.message.edit(view=self)
            await interaction.followup.send(f'✅ Post approved and scheduled for publishing.', ephemeral=False)

        except Exception as e:
            print(f'[ERROR] Approve button failed for post {self.post_id}: {e}')
            await interaction.followup.send(f'⚠️ Failed to approve: `{e}`', ephemeral=True)


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


async def render_to_temp(post_id: str, structure: dict, post_number: int) -> tuple[str | None, str | None]:
    """
    Build the HTML template and render it to a temporary PNG file.

    Returns (tmp_path, error_message).
    tmp_path is the path to the rendered PNG on success; None on failure.
    Caller is responsible for deleting tmp_path after use.
    Uses async Playwright API since this runs inside the Discord asyncio event loop.
    """
    audience = structure.get('audience', 'engineer')
    category = structure.get('category', 'build-log')

    template_file = AUDIENCE_TEMPLATES.get(audience, 'technical_post.html')
    template_path = TEMPLATES_DIR / template_file

    if not template_path.exists():
        return None, f'template not found: {template_file}'

    category_label = CATEGORY_LABELS.get(category, category.upper().replace('-', ' '))
    post_data = {**structure, 'category_label': category_label}
    bullets = structure.get('bullet_points', [])

    if not bullets:
        return None, 'no bullet points extracted'

    try:
        html = _build_html(template_path, post_data, bullets, post_number)
    except Exception as e:
        return None, f'template build error: {e}'

    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={'width': 1080, 'height': 1350})
            # 'load' fires once HTML + fonts are fetched — avoids hanging on
            # networkidle when Google Fonts CDN is slow or unreachable.
            await page.set_content(html, wait_until='load')
            await page.wait_for_timeout(1500)  # let fonts paint before screenshot
            await page.screenshot(path=tmp_path, full_page=False)
            await browser.close()
        return tmp_path, None

    except Exception as e:
        print(f'[WARN] Playwright render error for post {post_id}: {e}')
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return None, f'Playwright error: {e}'


def upload_image_file(post_id: str, tmp_path: str) -> str | None:
    """Upload a rendered PNG file to Supabase Storage. Returns public URL or None."""
    try:
        with open(tmp_path, 'rb') as f:
            image_bytes = f.read()
        return db.upload_image(post_id, image_bytes)
    except Exception as e:
        print(f'[WARN] Image upload failed for post {post_id}: {e}')
        return None


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

    try:
        await _process_ready_post(message, groq_client, spreadsheet_id, content)
    except Exception as e:
        print(f'[ERROR] Unhandled exception in handle_ready_post for message {message.id}: {e}')
        import traceback
        traceback.print_exc()
        try:
            await message.channel.send(f'⚠️ Something went wrong processing your post: `{e}`')
        except Exception:
            pass


async def _process_ready_post(message, groq_client: Groq, spreadsheet_id: str, content: str) -> None:
    """Inner implementation — wrapped by handle_ready_post for top-level error reporting."""
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

    # Render image to a temp file (kept alive so we can attach it to Discord)
    post_number = _get_next_post_number(spreadsheet_id)
    try:
        tmp_path, render_error = await asyncio.wait_for(
            render_to_temp(post_id, structure, post_number),
            timeout=35.0,
        )
    except asyncio.TimeoutError:
        tmp_path, render_error = None, 'render timed out (>35s) — Playwright/Chromium may not be installed'

    # Upload to Supabase Storage
    image_url = None
    if tmp_path:
        image_url = upload_image_file(post_id, tmp_path)
        if image_url:
            try:
                db.update_post(post_id, image_url=image_url)
            except Exception as e:
                print(f'[WARN] update_post image_url failed for {post_id}: {e}')

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

    # Build Discord embed
    char_count = len(content)
    category_label = CATEGORY_LABELS.get(category, category.upper())

    embed = discord.Embed(
        description=content[:4000] + ('...' if len(content) > 4000 else ''),
        color=0x0A66C2,  # LinkedIn blue
    )
    embed.set_author(name='Muhammad Usman  •  AI Automation Engineer')
    embed.add_field(name='📅 Scheduled', value=slot_date, inline=True)
    embed.add_field(name='🏷️ Category', value=category_label, inline=True)
    embed.add_field(name='👥 Audience', value=audience, inline=True)
    embed.add_field(name='🪝 Hook', value=structure['hook'] or '—', inline=False)
    embed.add_field(name='💬 Headline', value=structure['headline'] or '—', inline=False)
    if structure['bullet_points']:
        embed.add_field(
            name='• Key Points',
            value='\n'.join(f'• {b}' for b in structure['bullet_points']),
            inline=False,
        )

    footer_parts = [f'{char_count} chars', f'post_id: {post_id}']
    if render_error:
        footer_parts.append(f'⚠️ image failed: {str(render_error)[:200]}')
    if sheets_error:
        footer_parts.append(f'⚠️ sheets: {str(sheets_error)[:200]}')
    if overbooked:
        footer_parts.append('⚠️ calendar packed — consider rescheduling')
    embed.set_footer(text='  ·  '.join(footer_parts)[:2048])

    # Send reply — attach the rendered image directly so it shows inline in Discord
    approve_view = ApproveView(post_id=post_id, spreadsheet_id=spreadsheet_id)
    try:
        if tmp_path and os.path.exists(tmp_path):
            discord_file = discord.File(tmp_path, filename='post.png')
            embed.set_image(url='attachment://post.png')
            await message.channel.send(
                content='📋 Post queued — click **Approve & Schedule** when ready to publish.',
                embed=embed,
                file=discord_file,
                view=approve_view,
            )
        else:
            await message.channel.send(
                content='📋 Post queued — click **Approve & Schedule** when ready to publish.',
                embed=embed,
                view=approve_view,
            )
    except Exception as e:
        print(f'[ERROR] Discord send failed for post {post_id}: {e}')
        try:
            await message.channel.send(
                f'⚠️ Post saved (id: `{post_id}`) but Discord send failed: {e}\n'
                f'Scheduled for {slot_date}. Check Google Sheets.'
            )
        except Exception:
            pass
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    print(
        f'[{datetime.now(PKT)}] Ready post from {message.author} → '
        f'{category}/{audience} queued for {slot_date} (post_id={post_id})'
    )
