"""
templates/technical.py — Image template for technical audience posts.

Generates a branded LinkedIn post image for the 'engineer' audience.
Visual style: dark background (#0D1117), blue/purple accent glows,
abstract circuit or data-flow aesthetic. No human faces. No text overlay.

This module is called by tools/generate_images.py. It receives a DALL-E 3
image (bytes) and optionally composites branding elements on top using Pillow.

Alternatively, if using a purely prompt-based approach (DALL-E only),
this module just returns an enhanced prompt tuned for the technical aesthetic.

Input:  image_bytes (bytes) from DALL-E, post metadata (dict)
Output: final_image_bytes (bytes) ready for Supabase Storage upload
"""

import os
from PIL import Image, ImageDraw, ImageFont
import io


ACCENT_COLOR  = (88, 101, 242)    # Blurple/electric blue
BG_COLOR      = (13, 17, 23)      # GitHub dark
TEXT_COLOR    = (201, 209, 217)   # GitHub text grey
CANVAS_SIZE   = (1080, 1080)      # 1:1 square for LinkedIn


def enhance_prompt(base_prompt: str) -> str:
    """Return a DALL-E prompt tuned for the technical visual style."""
    # TODO: Append style keywords to base_prompt:
    #   "dark background #0D1117, glowing circuit nodes, electric blue and purple accents,
    #    abstract data flow, professional tech aesthetic, no text, no faces, no logos,
    #    cinematic lighting, 8K quality"
    pass


def render(image_bytes: bytes, post: dict) -> bytes:
    """
    Composite branding onto the DALL-E image.
    Currently a pass-through; add Pillow overlays here as needed.

    Args:
        image_bytes: Raw PNG/JPEG bytes from DALL-E
        post: dict with keys topic, category, audience (for optional text overlay)

    Returns:
        Final image bytes (PNG)
    """
    # TODO: Open image_bytes with PIL Image.open(io.BytesIO(image_bytes))
    # TODO: Resize to CANVAS_SIZE if needed
    # TODO: Optional: add subtle bottom gradient overlay with brand colour
    # TODO: Optional: add small logo/watermark in corner
    # TODO: Save to io.BytesIO, return .getvalue()
    pass


def main():
    # TODO: Smoke test — load a sample image, run render(), save output to .tmp/test_technical.png
    pass


if __name__ == '__main__':
    main()
