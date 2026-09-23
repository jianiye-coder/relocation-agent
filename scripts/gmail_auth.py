"""Get a Gmail refresh token for EMAIL_MODE=gmail (run once, locally).

1. In Google Cloud Console, create an OAuth client of type "Desktop app" and
   download it as client_secret.json into this folder (never commit it).
2. .venv/bin/python scripts/gmail_auth.py
3. Sign in with the Gmail account that should send the emails.
4. Copy the three printed lines into your .env (never commit it).
"""

from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
secret = Path(__file__).resolve().parents[1] / "client_secret.json"

flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
print("EMAIL_MODE=gmail")
print(f"GOOGLE_CLIENT_ID={creds.client_id}")
print("GOOGLE_CLIENT_SECRET=<from client_secret.json>")
print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
