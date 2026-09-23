"""Per-user Gmail connection (Google's token endpoint is faked)."""

import base64
import json
import sqlite3
from urllib.parse import parse_qs, urlparse

import pytest

from moving_agent import accounts
from moving_agent.emailer import GmailSender


def id_token(email):
    payload = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return f"header.{payload}.sig"


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d


@pytest.fixture
def google(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    tokens = {"refresh_token": "refresh-123", "scope": "openid email https://www.googleapis.com/auth/gmail.send",
              "id_token": id_token("jenny@gmail.com")}
    monkeypatch.setattr(accounts.httpx, "post", lambda url, **kw: Resp(tokens))
    return tokens


def state_from(url):
    q = parse_qs(urlparse(url).query)
    assert q["scope"][0].split() == accounts.SCOPES and q["access_type"] == ["offline"]
    return q["state"][0]


def test_connect_stores_encrypted_token(google):
    state = state_from(accounts.start_url("sid-1", "/plan/abc"))
    email, next_path = accounts.finish("code", state, "sid-1")
    assert (email, next_path) == ("jenny@gmail.com", "/plan/abc")
    raw = sqlite3.connect(accounts.DB_PATH).execute("SELECT token FROM gmail").fetchone()[0]
    assert b"refresh-123" not in raw
    sender, addr = accounts.sender_for("sid-1")
    assert isinstance(sender, GmailSender) and addr == "jenny@gmail.com"
    assert sender.creds.refresh_token == "refresh-123"
    assert accounts.sender_for("someone-else") is None


def test_state_must_match_session(google):
    state = state_from(accounts.start_url("sid-1", "/"))
    with pytest.raises(accounts.OAuthError):
        accounts.finish("code", state, "sid-2")


def test_send_scope_required(google, monkeypatch):
    google["scope"] = "openid email"
    state = state_from(accounts.start_url("sid-1", "/"))
    with pytest.raises(accounts.OAuthError, match="send email"):
        accounts.finish("code", state, "sid-1")


def test_disconnect(google):
    accounts.finish("code", state_from(accounts.start_url("sid-1", "/")), "sid-1")
    accounts.disconnect("sid-1")
    assert accounts.connected_email("sid-1") is None
