"""
generate_images.py — Weekly batch image generator.

Renders HTML/CSS templates with Playwright (headless Chromium) to produce
branded 1080x1350px PNG infographics for the week's LinkedIn posts.

Called by: .github/workflows/weekly_generation.yml (runs after generate_posts.py)
Depends on:  tools/sheets_helper.py, templates/{technical,business,story}_post.html
Output:      images/ directory (local) + Supabase Storage bucket 'post-images'
             Google Sheets image_path column updated with public Supabase URL

Environment variables required:
  GOOGLE_SHEETS_SPREADSHEET_ID, GOOGLE_SERVICE_ACCOUNT_JSON (or _PATH)
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Optional:
  SUPABASE_IMAGE_BUCKET  (default: post-images)
"""

import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.sheets_helper as sheets

load_dotenv()

SPREADSHEET_ID  = os.environ['GOOGLE_SHEETS_SPREADSHEET_ID']
SUPABASE_BUCKET = os.getenv('SUPABASE_IMAGE_BUCKET', 'post-images')

TEMPLATES_DIR = Path(__file__).parent.parent / 'templates'
IMAGES_DIR    = Path(__file__).parent.parent / 'images'

# Map Sheets category value → display label on the infographic
CATEGORY_LABELS: dict[str, str] = {
    'build-log':      'BUILD LOG',
    'transformation': 'AUTOMATION TIP',
    'hot-take':       'HOT TAKE',
    'behind-scenes':  'BEHIND THE SCENES',
    'founder-roi':    'FOUNDER ROI',
    'story':          'MY STORY',
}

# Map Sheets audience value → HTML template filename
AUDIENCE_TEMPLATES: dict[str, str] = {
    'engineer': 'technical_post.html',
    'founder':  'business_post.html',
    'story':    'story_post.html',
}


# ── Supabase ──────────────────────────────────────────────────────────────────

def get_supabase() -> Client:
    return create_client(
        os.environ['SUPABASE_URL'],
        os.environ['SUPABASE_SERVICE_ROLE_KEY'],
    )


def upload_image_to_supabase(supabase: Client, image_path: str, filename: str) -> str:
    """Upload a local PNG to Supabase Storage and return the public URL."""
    with open(image_path, 'rb') as f:
        supabase.storage.from_(SUPABASE_BUCKET).upload(
            filename, f, {'content-type': 'image/png', 'upsert': 'true'}
        )
    public_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(filename)
    return public_url


# ── Template rendering ────────────────────────────────────────────────────────

def parse_bullets(bullet_str: str) -> list[str]:
    """Split pipe-separated bullet string into a list of up to 5 items."""
    if not bullet_str:
        return []
    return [b.strip() for b in bullet_str.split('|') if b.strip()][:5]


def build_html(template_path: Path, post: dict, bullets: list[str], post_number: int) -> str:
    """
    Load a template file and replace every {{VARIABLE}} placeholder with real values.

    Bullet visibility is controlled by injecting 'display:none' into the style
    attribute of bullet_4 / bullet_5 divs when fewer bullets are present.
    """
    html = template_path.read_text(encoding='utf-8')

    bullet_count = len(bullets)
    padded = (bullets + [''] * 5)[:5]

    audience_titles = {
        'engineer': 'AI Automation Engineer',
        'founder':  'Automation Strategist',
        'story':    'AI Automation Engineer',
    }
    audience_key = post.get('audience', 'engineer')

    replacements = {
        '{{CATEGORY_LABEL}}': post.get('category_label', 'BUILD LOG'),
        '{{HEADLINE}}':       post.get('headline', ''),
        '{{BULLET_1}}':       padded[0],
        '{{BULLET_2}}':       padded[1],
        '{{BULLET_3}}':       padded[2],
        '{{BULLET_4}}':       padded[3],
        '{{BULLET_5}}':       padded[4],
        '{{BULLET_COUNT}}':   str(bullet_count),
        '{{POST_NUMBER}}':    str(post_number),
        '{{NAME}}':           'Muhammad Usman',
        '{{TITLE}}':          audience_titles.get(audience_key, 'AI Automation Engineer'),
        # Injected into style="" — empty string means visible, 'display:none' hides
        '{{BULLET_4_STYLE}}': 'display:none' if bullet_count < 4 else '',
        '{{BULLET_5_STYLE}}': 'display:none' if bullet_count < 5 else '',
    }

    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)

    return html


def render_image(html_string: str, output_path: str) -> bool:
    """
    Render an HTML string to a 1080x1350px PNG using Playwright headless Chromium.

    Waits for 'networkidle' so Google Fonts have time to load before screenshot.
    Returns True on success, False on any error.
    """
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
        print(f"[{datetime.now()}] Playwright render error: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"[{datetime.now()}] Starting weekly image generation")

    IMAGES_DIR.mkdir(exist_ok=True)

    # ── Load pending posts for the upcoming week ──────────────────────────────
    week_rows = sheets.get_week_schedule(SPREADSHEET_ID)
    pending = [
        r for r in week_rows
        if r.get('status') == 'pending' and r.get('headline')
    ]

    if not pending:
        print(
            f"[{datetime.now()}] No pending posts with headlines found for the upcoming week. "
            "Run generate_posts.py first."
        )
        return

    print(f"[{datetime.now()}] Found {len(pending)} posts to render")

    supabase_client = get_supabase()
    successes = 0

    for post in pending:
        audience   = post.get('audience', 'engineer')
        category   = post.get('category', 'build-log')
        scheduled  = post.get('scheduled_date', '')
        headline   = post.get('headline', 'Untitled Post')
        bullet_str = post.get('bullet_points', '')
        row_index  = post['_row_index']
        # Row 2 is the first data row → Post #1
        post_number = row_index - 1

        template_file  = AUDIENCE_TEMPLATES.get(audience, 'technical_post.html')
        template_path  = TEMPLATES_DIR / template_file
        category_label = CATEGORY_LABELS.get(category, category.upper().replace('-', ' '))

        # Build output filename: images/2026-04-07_build-log.png
        safe_date = scheduled.replace('/', '-')
        safe_cat  = category.replace('/', '-')
        filename  = f"{safe_date}_{safe_cat}.png"
        local_path = str(IMAGES_DIR / filename)

        print(
            f"[{datetime.now()}] Row {row_index}: {filename} "
            f"(audience={audience}, category={category})"
        )

        bullets = parse_bullets(bullet_str)
        if not bullets:
            print(f"[{datetime.now()}] Row {row_index}: no bullet points found — skipping")
            continue

        # ── Build HTML ────────────────────────────────────────────────────────
        post['category_label'] = category_label
        try:
            html = build_html(template_path, post, bullets, post_number)
        except Exception as e:
            print(f"[{datetime.now()}] Row {row_index}: template build error — {e}")
            continue

        # ── Render PNG ────────────────────────────────────────────────────────
        if not render_image(html, local_path):
            print(f"[{datetime.now()}] Row {row_index}: render failed — skipping (post will publish text-only)")
            continue

        print(f"[{datetime.now()}] Image generated: {filename}")

        # ── Upload to Supabase Storage ────────────────────────────────────────
        image_url = local_path  # fallback if upload fails
        try:
            image_url = upload_image_to_supabase(supabase_client, local_path, filename)
            print(f"[{datetime.now()}] Row {row_index}: uploaded → {image_url}")
        except Exception as e:
            print(
                f"[{datetime.now()}] Row {row_index}: Supabase upload failed "
                f"(falling back to local path): {e}"
            )

        # ── Update Google Sheets ──────────────────────────────────────────────
        try:
            sheets.update_row_status(SPREADSHEET_ID, row_index, {'image_path': image_url})
            print(f"[{datetime.now()}] Row {row_index}: Sheets image_path updated")
        except Exception as e:
            print(f"[{datetime.now()}] Row {row_index}: Sheets update failed: {e}")

        successes += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"[{datetime.now()}] Generated {successes}/{len(pending)} images successfully")


if __name__ == '__main__':
    main()
