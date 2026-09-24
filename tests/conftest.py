from datetime import date, timedelta

import os
import socket

os.environ["QUOTE_CACHE"] = "off"  # before the web app is imported

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Message

from moving_agent.models import Intake


def next_weekday(weekday: int) -> date:
    """Next date (at least 7 days out) that falls on `weekday` (0 = Monday)."""
    d = date.today() + timedelta(days=7)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


@pytest.fixture
def intake() -> Intake:
    return Intake(
        name="Jenny Ye",
        email="jenny@example.com",
        from_zip="60637",
        to_zip="60614",
        distance_miles=12,
        move_date=next_weekday(5),  # a Saturday
        home_size="1br",
        from_floor=3,
        needs=["truck", "labor"],
        budget_usd=800,
        items_to_sell=["IKEA sofa", "Bookshelf"],
    )


class Capture(Message):
    def __init__(self):
        super().__init__()
        self.messages = []

    def handle_message(self, message):
        self.messages.append(message)


@pytest.fixture
def smtp_server():
    """A real SMTP server on localhost that keeps every message it receives."""
    handler = Capture()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    controller = Controller(handler, hostname="127.0.0.1", port=port)
    controller.start()
    try:
        yield controller, handler
    finally:
        controller.stop()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Tests never use real keys from .env, and Gmail accounts go to a temp database."""
    for var in ["FLATKEY_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "MOVING_AGENT_MODEL",
                "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "EMAIL_REDIRECT_TO", "GOOGLE_MAPS_API_KEY", "TOKEN_ENCRYPTION_KEY",
                "WARP_API_KEY", "WARP_PRODUCTION_API_KEY", "NREL_API_KEY", "BROADBAND_API_KEY", "FMCSA_WEB_KEY", "ENABLE_UNOFFICIAL_ADAPTERS", "ENABLE_VOICE_INTAKE", "ENABLE_PHOTO_INVENTORY"]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("EMAIL_MODE", "outbox")
    monkeypatch.setenv("OUTBOX_DIR", str(tmp_path / "outbox"))
    from moving_agent import accounts
    monkeypatch.setattr(accounts, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(accounts, "KEY_PATH", tmp_path / "token.key")


@pytest.fixture(autouse=True)
def no_external_http(monkeypatch):
    """MockTransport remains usable; accidental real HTTP fails the suite."""
    import httpx

    def blocked(*args, **kwargs):
        raise AssertionError("External HTTP is disabled in tests; use a recorded or synthetic fixture")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
