"""
templates/business.py — Image template for business/founder audience posts.

Generates a branded LinkedIn post image for the 'founder' audience.
Visual style: clean white/light grey background, green or teal accent,
upward-trend metaphors, professional office or graph aesthetic.
No human faces. No text overlay (LinkedIn renders post text separately).

Called by tools/generate_images.py when post.audience == 'founder'.

Input:  image_bytes (bytes) from DALL-E, post metadata (dict)
Output: final_image_bytes (bytes) ready for Supabase Storage upload
"""

import os
from PIL import Image, ImageDraw, ImageFont
import io


ACCENT_COLOR  = (0, 168, 107)     # Emerald green
BG_COLOR      = (255, 255, 255)   # Clean white
TEXT_COLOR    = (30, 30, 30)      # Near-black
CANVAS_SIZE   = (1080, 1080)


def enhance_prompt(base_prompt: str) -> str:
    """Return a DALL-E prompt tuned for the business visual style."""
    # TODO: Append style keywords to base_prompt:
    #   "clean white background, professional business aesthetic, emerald green accent,
    #    upward trend metaphor, modern office or abstract graph, bright and airy,
    #    no text, no faces, no logos, commercial photography style"
    pass


def render(image_bytes: bytes, post: dict) -> bytes:
    """
    Composite branding onto the DALL-E image.

    Args:
        image_bytes: Raw PNG/JPEG bytes from DALL-E
        post: dict with keys topic, category, audience

    Returns:
        Final image bytes (PNG)
    """
    # TODO: Open image_bytes with PIL Image.open(io.BytesIO(image_bytes))
    # TODO: Resize to CANVAS_SIZE if needed
    # TODO: Optional: add thin green accent border or bottom bar
    # TODO: Optional: add small logo/watermark in corner
    # TODO: Save to io.BytesIO, return .getvalue()
    pass


def main():
    # TODO: Smoke test — load a sample image, run render(), save output to .tmp/test_business.png
    pass


if __name__ == '__main__':
    main()
