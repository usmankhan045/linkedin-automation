"""
templates/story.py — Image template for story/portfolio posts (Saturday).

Generates a branded LinkedIn post image for the 'story' audience.
Visual style: warm tones, soft green accent lighting, human and authentic feel.
Can reference Pakistan context (landscapes, culture) when relevant.
No human faces. No text overlay.

Called by tools/generate_images.py when post.audience == 'story'.

Input:  image_bytes (bytes) from DALL-E, post metadata (dict)
Output: final_image_bytes (bytes) ready for Supabase Storage upload
"""

import os
from PIL import Image, ImageDraw, ImageFont
import io


ACCENT_COLOR  = (80, 200, 120)    # Soft green (distinguishes story posts visually)
BG_COLOR      = (245, 240, 235)   # Warm off-white
TEXT_COLOR    = (50, 45, 40)      # Warm dark
CANVAS_SIZE   = (1080, 1080)


def enhance_prompt(base_prompt: str) -> str:
    """Return a DALL-E prompt tuned for the story visual style."""
    # TODO: Append style keywords to base_prompt:
    #   "warm tones, soft green accent lighting, authentic and human atmosphere,
    #    candid documentary style, professional but personal, cinematic depth,
    #    no text, no faces, no logos — can include Pakistan landscape or cultural context
    #    if relevant to the story"
    pass


def render(image_bytes: bytes, post: dict) -> bytes:
    """
    Composite branding onto the DALL-E image.
    Story images get a distinctive green accent element to stand out in the feed.

    Args:
        image_bytes: Raw PNG/JPEG bytes from DALL-E
        post: dict with keys topic, category, audience

    Returns:
        Final image bytes (PNG)
    """
    # TODO: Open image_bytes with PIL Image.open(io.BytesIO(image_bytes))
    # TODO: Resize to CANVAS_SIZE if needed
    # TODO: Add left-edge green accent bar (distinctive story marker: ~8px wide, full height)
    # TODO: Optional: add small logo/watermark in bottom-right corner
    # TODO: Save to io.BytesIO, return .getvalue()
    pass


def main():
    # TODO: Smoke test — load a sample image, run render(), save output to .tmp/test_story.png
    pass


if __name__ == '__main__':
    main()
