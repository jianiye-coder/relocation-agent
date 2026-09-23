"""End to end through the web app: intake form -> plan -> confirm -> email delivered."""

import re

import pytest
from fastapi.testclient import TestClient

from moving_agent.web import app as web
from moving_agent.emailer import SMTPSender

client = TestClient(web.app)


def form(intake, **overrides):
    data = {
        "name": intake.name, "email": intake.email, "from_zip": intake.from_zip, "to_zip": intake.to_zip,
        "distance_miles": str(intake.distance_miles), "move_date": intake.move_date.isoformat(),
        "flexible_days": "0", "home_size": "1br", "from_floor": "3", "to_floor": "1",
        "needs": ["truck", "labor"], "storage_months": "0", "budget_usd": str(intake.budget_usd),
        "items_to_sell": "IKEA sofa\nBookshelf",
    }
    data.update(overrides)
    return data


def test_intake_form_has_the_fields_we_need():
    html = client.get("/").text
    for field in ["name", "email", "from_zip", "to_zip", "distance_miles", "move_date", "flexible_days",
                  "home_size", "from_floor", "to_floor", "needs", "storage_months", "budget_usd", "items_to_sell"]:
        assert f'name="{field}"' in html, field


def test_invalid_intake_shows_errors(intake):
    r = client.post("/plan", data=form(intake, from_zip="abc"))
    assert r.status_code == 422 and "from_zip" in r.text


def test_plan_page_shows_recommendation_and_drafts(intake, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MOVING_AGENT_MODEL", raising=False)
    r = client.post("/plan", data=form(intake))
    assert r.status_code == 200
    assert "Recommended" in r.text and "within budget" in r.text
    assert "Quote request:" in r.text and "IKEA sofa" in r.text


def test_send_requires_confirmation(intake, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/plan", data=form(intake))
    rid = re.search(r'action="/send/(\w+)"', r.text).group(1)
    assert client.post(f"/send/{rid}", data={"selected": ["truck-0"]}).status_code == 400


def test_full_flow_delivers_email_over_smtp(intake, smtp_server, monkeypatch):
    controller, handler = smtp_server
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("EMAIL_MODE", "smtp")
    monkeypatch.setattr(web, "get_sender", lambda: SMTPSender("127.0.0.1", controller.port, starttls=False))

    page = client.post("/plan", data=form(intake)).text
    rid = re.search(r'action="/send/(\w+)"', page).group(1)
    selected = re.findall(r'name="selected" value="([\w-]+)" checked', page)
    assert selected

    sent = client.post(f"/send/{rid}", data={"selected": selected, "confirm": "yes"})

    assert sent.status_code == 200 and "Failed" not in sent.text
    assert len(handler.messages) == len(selected)
    assert {m["From"] for m in handler.messages} == {intake.email}


# ---- with the agent on (scripted model) ----

from .test_agent import scripted


@pytest.fixture
def agent_on(monkeypatch):
    monkeypatch.setattr(web, "model_configured", lambda: True)
    monkeypatch.setattr(web, "pick_model", lambda: "test")
    monkeypatch.setattr(web, "build_model", lambda name: scripted())


def test_agent_asks_to_send_then_user_approves(intake, smtp_server, monkeypatch, agent_on):
    controller, handler = smtp_server
    monkeypatch.setattr(web, "get_sender", lambda: SMTPSender("127.0.0.1", controller.port, starttls=False))
    c = TestClient(web.app)

    page = c.post("/plan", data=form(intake))
    assert page.status_code == 200
    assert "The agent wants to send" in page.text and "What the agent did" in page.text
    assert "Tried an alternative" in page.text and "Waiting for your approval" in page.text
    assert handler.messages == []

    rid = re.search(r'action="/approve/(\w+)"', page.text).group(1)
    done = c.post(f"/approve/{rid}", data={"decision": "approve"})
    assert done.status_code == 200 and "Booked the cheapest plan." in done.text
    assert len(handler.messages) >= 2 and "The agent wants to send" not in done.text

    again = c.post(f"/chat/{rid}", data={"message": "Can it be cheaper?"})
    assert again.status_code == 200 and "Can it be cheaper?" in again.text


def test_deny_sends_nothing(intake, smtp_server, monkeypatch, agent_on):
    controller, handler = smtp_server
    monkeypatch.setattr(web, "get_sender", lambda: SMTPSender("127.0.0.1", controller.port, starttls=False))
    c = TestClient(web.app)
    page = c.post("/plan", data=form(intake)).text
    rid = re.search(r'action="/approve/(\w+)"', page).group(1)
    c.post(f"/approve/{rid}", data={"decision": "deny"})
    assert handler.messages == []


def test_with_google_signin_user_must_connect_gmail(intake, monkeypatch, agent_on):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    c = TestClient(web.app)
    page = c.post("/plan", data=form(intake)).text
    assert "Connect Gmail to send" in page
    rid = re.search(r'action="/approve/(\w+)"', page).group(1)
    assert c.post(f"/approve/{rid}", data={"decision": "approve"}).status_code == 400
    start = c.get("/auth/google/start", params={"next": f"/plan/{rid}"}, follow_redirects=False)
    assert start.status_code == 303 and "accounts.google.com" in start.headers["location"]
    assert "gmail.send" in start.headers["location"]


def test_other_browsers_cannot_see_a_plan(intake):
    page = TestClient(web.app).post("/plan", data=form(intake))
    rid = re.search(r'/plan/(\w+)', str(page.url)).group(1)
    assert TestClient(web.app).get(f"/plan/{rid}").status_code == 404
