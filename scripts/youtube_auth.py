"""One-time script to get YouTube OAuth refresh token.

Run this once locally:
    python3 scripts/youtube_auth.py

It will:
1. Open your browser to sign in with your Google/YouTube account
2. Ask you to grant upload permissions
3. Save the refresh token to config/.env automatically
"""

import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
OAUTH_JSON = Path(__file__).parent.parent / "config" / "google_oauth.json"
ENV_PATH = Path(__file__).parent.parent / "config" / ".env"


def main():
    if not OAUTH_JSON.exists():
        print(f"ERROR: OAuth JSON not found at {OAUTH_JSON}")
        print("Copy your downloaded file: cp ~/Downloads/client_secret_*.json config/google_oauth.json")
        return

    print("Opening browser for YouTube authorization...")
    print("Sign in with the Google account that owns your YouTube channel.\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(OAUTH_JSON), SCOPES)
    credentials = flow.run_local_server(port=8090)

    # Extract tokens
    refresh_token = credentials.refresh_token
    client_id = credentials.client_id
    client_secret = credentials.client_secret

    print(f"\nAuthorization successful!")
    print(f"Refresh token: {refresh_token[:20]}...")

    # Update .env file
    env_content = ENV_PATH.read_text()
    env_content = env_content.replace("YOUTUBE_CLIENT_ID=", f"YOUTUBE_CLIENT_ID={client_id}")
    env_content = env_content.replace("YOUTUBE_CLIENT_SECRET=", f"YOUTUBE_CLIENT_SECRET={client_secret}")
    env_content = env_content.replace("YOUTUBE_REFRESH_TOKEN=", f"YOUTUBE_REFRESH_TOKEN={refresh_token}")
    ENV_PATH.write_text(env_content)

    print(f"\nCredentials saved to {ENV_PATH}")
    print("You're all set! The pipeline can now upload to YouTube.")


if __name__ == "__main__":
    main()
