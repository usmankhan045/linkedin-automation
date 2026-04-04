"""
generate_image.py — Generate an image for a post using DALL-E 3, upload to Supabase Storage.

Usage:
    python tools/generate_image.py --post_id <uuid>

Updates post status from 'image_pending' to 'queued' on success.
"""

import argparse
import os
import sys

import requests
from openai import OpenAI
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from tools.db_client import get_post, update_post, upload_image

load_dotenv()


def generate_image_bytes(prompt: str) -> bytes:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    response = client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size="1024x1024",
        quality="standard",
        style="vivid",
        n=1,
    )

    image_url = response.data[0].url
    img_response = requests.get(image_url, timeout=30)
    img_response.raise_for_status()
    return img_response.content


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--post_id", required=True)
    args = parser.parse_args()

    post = get_post(args.post_id)

    image_prompt = post.get("image_prompt") or post.get("post_text", "")
    if not image_prompt:
        print("Error: post has no content to use as image prompt", file=sys.stderr)
        sys.exit(1)

    print(f"Generating image for post {args.post_id}...", file=sys.stderr)
    print(f"Prompt: {image_prompt[:120]}...", file=sys.stderr)

    try:
        image_bytes = generate_image_bytes(image_prompt)
    except Exception as e:
        print(f"Image generation failed: {e}", file=sys.stderr)
        sys.exit(1)

    print("Uploading image to Supabase Storage...", file=sys.stderr)
    try:
        image_url = upload_image(args.post_id, image_bytes)
    except Exception as e:
        # Retry once
        try:
            image_url = upload_image(args.post_id, image_bytes)
        except Exception as e2:
            print(f"Image upload failed: {e2}", file=sys.stderr)
            sys.exit(1)

    update_post(args.post_id, image_url=image_url)
    print(f"Image ready: {image_url}", file=sys.stderr)
    print(args.post_id)  # stdout — pass to publish step


if __name__ == "__main__":
    main()
