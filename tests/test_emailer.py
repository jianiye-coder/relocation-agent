import base64
import email
from unittest.mock import MagicMock, patch

from moving_agent.drafts import quote_request
from moving_agent.emailer import GmailSender, OutboxSender, SMTPSender
from moving_agent.models import EmailDraft
from moving_agent.planner import gather_offers
from moving_agent.providers.catalog import SampleCatalog


def a_draft(intake) -> EmailDraft:
    offer = gather_offers(intake, [SampleCatalog()])["truck"][0]
    return quote_request(intake, offer)


def test_smtp_sender_delivers_to_a_real_smtp_server(intake, smtp_server):
    controller, handler = smtp_server
    draft = a_draft(intake)
    sender = SMTPSender("127.0.0.1", controller.port, starttls=False)

    results = sender.send_all([draft], sender=intake.email)

    assert results[0].ok, results[0].detail
    assert len(handler.messages) == 1
    got = handler.messages[0]
    assert got["To"] == draft.to
    assert got["From"] == intake.email
    assert got["Subject"] == draft.subject
    assert "cubic feet" in got.get_payload()


def test_one_failure_does_not_stop_the_rest(intake, smtp_server):
    controller, handler = smtp_server
    good = a_draft(intake)
    bad = good.model_copy(update={"offer_id": "x", "to": ""})
    results = SMTPSender("127.0.0.1", controller.port, starttls=False).send_all([bad, good], sender=intake.email)
    assert [r.ok for r in results] == [False, True]
    assert len(handler.messages) == 1


def test_gmail_sender_calls_gmail_api_with_raw_message(intake):
    draft = a_draft(intake)
    service = MagicMock()
    service.users.return_value.messages.return_value.send.return_value.execute.return_value = {"id": "abc123"}
    with patch("googleapiclient.discovery.build", return_value=service) as build:
        result = GmailSender("refresh", "client-id", "secret").send(draft, sender=intake.email)

    assert result.ok and "abc123" in result.detail
    assert build.call_args.args[:2] == ("gmail", "v1")
    kwargs = service.users.return_value.messages.return_value.send.call_args.kwargs
    assert kwargs["userId"] == "me"
    parsed = email.message_from_bytes(base64.urlsafe_b64decode(kwargs["body"]["raw"]))
    assert parsed["To"] == draft.to and parsed["Subject"] == draft.subject
    assert parsed["From"] is None  # Gmail sets the sender to the signed-in user


def test_outbox_sender_writes_eml(intake, tmp_path):
    draft = a_draft(intake)
    result = OutboxSender(tmp_path).send(draft, sender=intake.email)
    assert result.ok
    saved = email.message_from_bytes((tmp_path / f"{draft.offer_id}.eml").read_bytes())
    assert saved["To"] == draft.to
