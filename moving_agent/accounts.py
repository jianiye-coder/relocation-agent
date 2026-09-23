"""Per-user Gmail connections.

Each browser session connects its own Gmail through Google OAuth. We ask only
for `gmail.send` (plus `openid email` to learn which address was connected).
Refresh tokens are encrypted with a local key before they're stored in SQLite.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

from .emailer import GmailSender

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("ACCOUNTS_DB", ROOT / "data" / "accounts.db"))
KEY_PATH = Path(os.getenv("TOKEN_KEY_FILE", ROOT / ".token_key"))

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = ["openid", "email", "https://www.googleapis.com/auth/gmail.send"]


class OAuthError(Exception):
    pass


def _fernet() -> Fernet:
    key = os.getenv("TOKEN_ENCRYPTION_KEY")
    if not key:
        if not KEY_PATH.exists():
            KEY_PATH.write_bytes(Fernet.generate_key())
            KEY_PATH.chmod(0o600)
        key = KEY_PATH.read_text().strip()
    return Fernet(key.encode() if isinstance(key, str) else key)


def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS gmail (sid TEXT PRIMARY KEY, email TEXT NOT NULL, token BLOB NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS oauth_state (state TEXT PRIMARY KEY, sid TEXT NOT NULL, next TEXT NOT NULL)")
    return con


def oauth_configured() -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def redirect_uri() -> str:
    return os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8787/auth/google/callback")


def start_url(sid: str, next_path: str) -> str:
    """Google consent URL. `state` ties the callback to this browser session."""
    state = secrets.token_urlsafe(24)
    with _db() as con:
        con.execute("INSERT INTO oauth_state VALUES (?, ?, ?)", (state, sid, next_path))
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def finish(code: str, state: str, sid: str) -> tuple[str, str]:
    """Exchange the code for tokens and store them for this session. Returns (email, next_path)."""
    with _db() as con:
        row = con.execute("SELECT sid, next FROM oauth_state WHERE state = ?", (state,)).fetchone()
        con.execute("DELETE FROM oauth_state WHERE state = ?", (state,))
    if not row or row[0] != sid:
        raise OAuthError("This sign-in link expired or belongs to another browser. Try Connect Gmail again.")
    r = httpx.post(TOKEN_URL, data={
        "code": code,
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "redirect_uri": redirect_uri(),
        "grant_type": "authorization_code",
    }, timeout=15)
    if r.status_code != 200:
        raise OAuthError(f"Google refused the sign-in ({r.status_code}). Try again.")
    tokens = r.json()
    if "https://www.googleapis.com/auth/gmail.send" not in tokens.get("scope", ""):
        raise OAuthError("Permission to send email wasn't granted. Tick the Gmail box on Google's screen.")
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise OAuthError("Google didn't return a long-lived token. Remove the app at myaccount.google.com/permissions and connect again.")
    email = _email_from_id_token(tokens.get("id_token", ""))
    with _db() as con:
        con.execute("INSERT OR REPLACE INTO gmail VALUES (?, ?, ?)", (sid, email, _fernet().encrypt(refresh.encode())))
    return email, row[1]


def _email_from_id_token(id_token: str) -> str:
    # The token comes straight from Google's token endpoint over TLS, so we only read its payload.
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))["email"]
    except Exception as exc:
        raise OAuthError("Couldn't read which Gmail address was connected.") from exc


def connected_email(sid: str) -> str | None:
    with _db() as con:
        row = con.execute("SELECT email FROM gmail WHERE sid = ?", (sid,)).fetchone()
    return row[0] if row else None


def sender_for(sid: str) -> tuple[GmailSender, str] | None:
    """The user's own Gmail sender and address, or None if they haven't connected."""
    with _db() as con:
        row = con.execute("SELECT email, token FROM gmail WHERE sid = ?", (sid,)).fetchone()
    if not row:
        return None
    refresh = _fernet().decrypt(row[1]).decode()
    return GmailSender(refresh, os.environ["GOOGLE_CLIENT_ID"], os.environ["GOOGLE_CLIENT_SECRET"]), row[0]


def disconnect(sid: str) -> None:
    with _db() as con:
        con.execute("DELETE FROM gmail WHERE sid = ?", (sid,))
