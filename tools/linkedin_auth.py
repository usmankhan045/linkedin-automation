"""
linkedin_auth.py — LinkedIn OAuth 2.0 token generator (run once, locally).

Opens a browser to the LinkedIn OAuth authorization URL,
starts a local callback server to capture the authorization code,
exchanges the code for access + refresh tokens,
and prints them to stdout so they can be saved to .env and GitHub Secrets.

Run this locally whenever tokens need to be refreshed (every 12 months for
refresh token, or every 60 days for access token if not using refresh flow).

NOT called by GitHub Actions. Developer tool only.

Usage:
    python tools/linkedin_auth.py

Required .env keys: LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET
Output: Prints LINKEDIN_ACCESS_TOKEN and LINKEDIN_REFRESH_TOKEN to stdout
"""

import os
import sys
import json
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
from dotenv import load_dotenv

load_dotenv()

REDIRECT_URI  = 'http://localhost:8080/callback'
AUTH_BASE_URL = 'https://www.linkedin.com/oauth/v2/authorization'
TOKEN_URL     = 'https://www.linkedin.com/oauth/v2/accessToken'
SCOPES        = ['w_member_social', 'r_liteprofile', 'r_emailaddress']


def main():
    # TODO: Build authorization URL with client_id, redirect_uri, scope, response_type=code
    # TODO: Print the URL and open it in the browser via webbrowser.open()
    # TODO: Start a local HTTPServer on port 8080 to capture the callback
    # TODO: Parse ?code= from the callback URL
    # TODO: POST to TOKEN_URL with code, client_id, client_secret, redirect_uri, grant_type
    # TODO: Print access_token and refresh_token clearly to stdout
    # TODO: Print instructions for adding them to .env and GitHub Secrets
    pass


if __name__ == '__main__':
    main()
