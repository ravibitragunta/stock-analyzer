"""
auth.py — Upstox OAuth2 Authentication Helper.

Upstox v2 uses a standard OAuth2 Authorization Code flow.
This script handles the complete flow:
  1. Opens the Upstox login URL in your browser
  2. Starts a local HTTP server to catch the redirect
  3. Exchanges the auth code for an access_token
  4. Saves the token to .token file and prints it

Usage:
  python auth.py

Requirements:
  - UPSTOX_API_KEY and UPSTOX_REDIRECT_URI set in config.py
  - Your Upstox App must have redirect URI set to: http://localhost:8888/callback

Upstox Developer Portal: https://developer.upstox.com/
"""

import json
import logging
import os
import sys
import threading
import urllib.parse
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

# Add parent dir to path so config is importable
sys.path.insert(0, str(Path(__file__).parent))
import config

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TOKEN_FILE = Path(__file__).parent / ".upstox_token"

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

# Set these in config.py or here directly
UPSTOX_API_KEY    = config.UPSTOX_API_KEY or os.getenv("UPSTOX_API_KEY", "")
UPSTOX_API_SECRET = os.getenv("UPSTOX_API_SECRET", "")  # From Upstox developer portal
REDIRECT_URI      = "http://127.0.0.1:8888/callback"
AUTH_URL          = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL         = "https://api.upstox.com/v2/login/authorization/token"
CALLBACK_PORT     = 8888

# ─────────────────────────────────────────────
# OAUTH2 FLOW
# ─────────────────────────────────────────────

_auth_code: dict = {}


class _CallbackHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler to catch the OAuth redirect and extract the auth code."""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            _auth_code["code"] = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
            <html><body style="background:#0f1117;color:#e8e9f0;font-family:sans-serif;padding:40px;text-align:center">
            <h2 style="color:#00c853">&#x2705; Auth Code Captured!</h2>
            <p>You can close this window and return to the terminal.</p>
            </body></html>""")
        elif "error" in params:
            _auth_code["error"] = params.get("error_description", ["Unknown error"])[0]
            self.send_response(400)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass   # Suppress default HTTP log noise


def _capture_auth_code() -> str | None:
    """Start local server and wait for auth code redirect."""
    server = HTTPServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler)
    server.timeout = 120   # Wait up to 2 minutes for user to log in

    logger.info("Waiting for Upstox login callback (timeout: 2 min) …")
    server.handle_request()   # Blocks until one request is received

    if "error" in _auth_code:
        logger.error("Auth error: %s", _auth_code["error"])
        return None
    return _auth_code.get("code")


def exchange_code_for_token(auth_code: str) -> dict | None:
    """Exchange the auth code for an access_token via Upstox token endpoint."""
    payload = {
        "code":          auth_code,
        "client_id":     UPSTOX_API_KEY,
        "client_secret": UPSTOX_API_SECRET,
        "redirect_uri":  REDIRECT_URI,
        "grant_type":    "authorization_code",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept":        "application/json",
    }

    resp = requests.post(TOKEN_URL, data=payload, headers=headers, timeout=30)
    if resp.status_code == 200:
        return resp.json()

    logger.error("Token exchange failed [HTTP %d]: %s", resp.status_code, resp.text[:300])
    return None


def save_token(token_data: dict):
    """Save the full token response to .upstox_token file."""
    token_data["saved_at"] = datetime.now().isoformat()
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)
    logger.info("Token saved to %s", TOKEN_FILE)


def load_token() -> dict | None:
    """Load a previously saved token. Returns None if missing or expired."""
    if not TOKEN_FILE.exists():
        return None
    with open(TOKEN_FILE) as f:
        return json.load(f)


def get_valid_token() -> str | None:
    """
    Get a valid access token. Uses cached token if available.
    Upstox access tokens are valid for 1 trading day (expire at midnight).

    Returns the access_token string, or None if auth is needed.
    """
    token_data = load_token()
    if token_data:
        saved_at = datetime.fromisoformat(token_data.get("saved_at", "2000-01-01"))
        if saved_at.date() == date.today():
            access_token = token_data.get("access_token")
            if access_token:
                logger.info("Using cached token (saved at %s)", saved_at.strftime("%H:%M"))
                return access_token
    return None


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def run_auth_flow() -> str | None:
    """
    Run the full OAuth2 flow.
    Returns the access_token string on success, None on failure.
    """
    if not UPSTOX_API_KEY:
        print("\n❌ UPSTOX_API_KEY is not set.")
        print("   1. Go to https://developer.upstox.com/")
        print("   2. Create an app and get your API Key + Secret")
        print("   3. Set redirect URI to: http://127.0.0.1:8888/callback")
        print("   4. Add to config.py: UPSTOX_API_KEY = 'your_key'")
        print("   5. Set env var: export UPSTOX_API_SECRET='your_secret'")
        return None

    if not UPSTOX_API_SECRET:
        print("\n❌ UPSTOX_API_SECRET is not set.")
        print("   Run: export UPSTOX_API_SECRET='your_secret_from_developer_portal'")
        return None

    # Check cache first
    cached = get_valid_token()
    if cached:
        print(f"\n✅ Using today's cached token (valid until midnight)")
        return cached

    # Build auth URL
    params = urllib.parse.urlencode({
        "client_id":     UPSTOX_API_KEY,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
    })
    url = f"{AUTH_URL}?{params}"

    print("\n" + "=" * 60)
    print("  Upstox OAuth2 Login")
    print("=" * 60)
    print(f"\n  Opening your browser to Upstox login …")
    print(f"  If browser doesn't open, go to:\n  {url}\n")

    webbrowser.open(url)

    # Capture redirect
    auth_code = _capture_auth_code()
    if not auth_code:
        print("\n❌ Failed to capture auth code. Did you log in successfully?")
        return None

    print("  ✅ Auth code captured — exchanging for token …")

    # Exchange for token
    token_data = exchange_code_for_token(auth_code)
    if not token_data:
        print("\n❌ Token exchange failed. Check your API_KEY and API_SECRET.")
        return None

    access_token = token_data.get("access_token")
    if not access_token:
        print(f"\n❌ No access_token in response: {token_data}")
        return None

    save_token(token_data)

    print("\n" + "=" * 60)
    print("  ✅ Authentication Successful!")
    print("=" * 60)
    print(f"\n  Access Token (first 20 chars): {access_token[:20]}…")
    print(f"  Token saved to: {TOKEN_FILE}")
    print(f"\n  Add to config.py or set as env var:")
    print(f"  export UPSTOX_ACCESS_TOKEN='{access_token}'")
    print()

    return access_token


if __name__ == "__main__":
    token = run_auth_flow()
    if token:
        # Optionally auto-patch config.py
        print("  To use this token automatically, set in config.py:")
        print(f"  UPSTOX_ACCESS_TOKEN = '{token[:20]}...'")
        sys.exit(0)
    else:
        sys.exit(1)
