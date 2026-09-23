"""Sending email. Only our code calls these, after the user confirms; the agent never can.

Three senders share one interface:
- GmailSender: sends from the user's own Gmail through the Gmail API (gmail.send scope).
- SMTPSender: any SMTP server (Gmail SMTP with an app password, Mailpit, etc.).
- OutboxSender: writes .eml files to disk, for demos without credentials.
"""

from __future__ import annotations

import base64
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from .models import EmailDraft


@dataclass
class SendResult:
    offer_id: str
    to: str
    ok: bool
    detail: str


def build_message(draft: EmailDraft, sender: str) -> EmailMessage:
    if not draft.to:
        raise ValueError(f"No recipient for offer {draft.offer_id}")
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = draft.to
    msg["Subject"] = draft.subject
    msg["Reply-To"] = sender
    msg.set_content(draft.body)
    return msg


class Sender:
    def send(self, draft: EmailDraft, sender: str) -> SendResult:  # pragma: no cover - interface
        raise NotImplementedError

    def send_all(self, drafts: list[EmailDraft], sender: str) -> list[SendResult]:
        results = []
        for d in drafts:
            try:
                results.append(self.send(d, sender))
            except Exception as exc:  # one failure shouldn't stop the others
                results.append(SendResult(d.offer_id, d.to, False, str(exc)))
        return results


class SMTPSender(Sender):
    def __init__(self, host: str, port: int, username: str | None = None, password: str | None = None, starttls: bool = True):
        self.host, self.port = host, port
        self.username, self.password, self.starttls = username, password, starttls

    def send(self, draft: EmailDraft, sender: str) -> SendResult:
        msg = build_message(draft, sender)
        with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
            if self.starttls:
                smtp.starttls()
            if self.username:
                smtp.login(self.username, self.password or "")
            refused = smtp.send_message(msg)
        if refused:
            return SendResult(draft.offer_id, draft.to, False, f"refused: {refused}")
        return SendResult(draft.offer_id, draft.to, True, f"sent via SMTP {self.host}:{self.port}")


class GmailSender(Sender):
    """Sends as the user through the Gmail API using their OAuth refresh token."""

    SCOPE = "https://www.googleapis.com/auth/gmail.send"

    def __init__(self, refresh_token: str, client_id: str, client_secret: str):
        from google.oauth2.credentials import Credentials

        self.creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=[self.SCOPE],
        )

    def send(self, draft: EmailDraft, sender: str) -> SendResult:
        from googleapiclient.discovery import build

        msg = build_message(draft, sender)
        del msg["From"]  # Gmail fills in the signed-in user's address
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service = build("gmail", "v1", credentials=self.creds, cache_discovery=False)
        sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return SendResult(draft.offer_id, draft.to, True, f"sent via Gmail API, id {sent['id']}")


class OutboxSender(Sender):
    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)

    def send(self, draft: EmailDraft, sender: str) -> SendResult:
        msg = build_message(draft, sender)
        path = self.folder / f"{draft.offer_id}.eml"
        path.write_bytes(msg.as_bytes())
        return SendResult(draft.offer_id, draft.to, True, f"saved to {path} (outbox mode, not delivered)")


def sender_from_env() -> Sender:
    """Picks the sender from environment variables. See README for each mode."""
    mode = os.getenv("EMAIL_MODE", "outbox")
    if mode == "gmail":
        return GmailSender(
            refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
            client_id=os.environ["GOOGLE_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        )
    if mode == "smtp":
        return SMTPSender(
            host=os.environ["SMTP_HOST"],
            port=int(os.getenv("SMTP_PORT", "587")),
            username=os.getenv("SMTP_USERNAME"),
            password=os.getenv("SMTP_PASSWORD"),
            starttls=os.getenv("SMTP_STARTTLS", "true").lower() == "true",
        )
    return OutboxSender(Path(os.getenv("OUTBOX_DIR", Path(__file__).resolve().parents[1] / "outbox")))
