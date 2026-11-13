"""Errands, their prepared documents, and the arrival-pack email that only goes to the user."""

import json
import re
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from moving_agent import errands, timeline
from moving_agent.models import Intake
from moving_agent.web import app as web

FIXTURES = Path(__file__).parent / "fixtures"
MOVE = date.today() + timedelta(days=45)


def intake(**over) -> Intake:
    base = dict(name="Ana Diaz", email="ana@example.com", from_address="123 Main St, Los Angeles, CA 90012",
                to_address="456 Valencia St, San Francisco, CA 94110", from_zip="90012", to_zip="94110",
                distance_miles=382, move_date=MOVE, home_size="studio", needs=["truck"], budget_usd=1500,
                lease_end=MOVE - timedelta(days=2))
    return Intake(**(base | over))


def by_id(items):
    return {e.id: e for e in items}


def test_conditional_errands_follow_the_household():
    plain = by_id(errands.build(intake(), MOVE))
    assert not {"vet_records", "school_enrollment", "dmv_address", "car_insurance", "commute_route"} & plain.keys()
    full = by_id(errands.build(intake(pets=["cat"], vehicles=["car"], has_children=True,
                                      commute_destination="1 Market St, San Francisco, CA"), MOVE))
    assert {"vet_records", "school_enrollment", "dmv_address", "car_insurance", "commute_route"} <= full.keys()
    assert "cat" in full["vet_records"].document
    assert "google.com/maps/dir/" in full["commute_route"].document


def test_complete_intake_makes_documents_ready_with_real_values():
    items = errands.build(intake(), MOVE)
    assert items == sorted(items, key=lambda e: e.due)
    notice = by_id(items)["landlord_notice"]
    assert notice.status == "prepared_for_user" and notice.blockers == []
    assert "123 Main St" in notice.document and "456 Valencia St" in notice.document
    assert notice.due == intake().lease_end - timedelta(days=30)
    for e in items:
        assert "{" not in e.document and "}" not in e.document, e.id  # every template field was rendered
        assert "None" not in e.document, e.id


def test_missing_fields_block_and_show_placeholders():
    items = by_id(errands.build(intake(lease_end=None, from_address="Los Angeles, CA", to_address=""), MOVE))
    notice = items["landlord_notice"]
    assert notice.status == "needs_user_action"
    assert notice.blockers == ["Current street address", "Current lease end date"]
    assert "<from address>" in notice.document and "<lease end>" in notice.document
    assert items["usps_forwarding"].blockers == ["Current street address", "New street address"]
    # Details we never collect are placeholders but don't block the document.
    assert items["utilities_stop"].status == "needs_user_action"
    ready = by_id(errands.build(intake(), MOVE))["utilities_stop"]
    assert ready.status == "prepared_for_user" and "<account number>" in ready.document


def test_only_the_user_marks_errands_done():
    items = by_id(errands.build(intake(), MOVE, done=("usps_forwarding",)))
    assert items["usps_forwarding"].status == "done"
    assert all(e.status != "done" for k, e in items.items() if k != "usps_forwarding")


def test_attach_links_existing_tasks_and_adds_the_rest():
    i = intake(vehicles=["car"])
    tasks = timeline.build(MOVE, i.lease_end, has_vehicle=True)
    items = errands.build(i, MOVE)
    merged = errands.attach(tasks, items)
    linked = {t.errand: t for t in merged if t.errand}
    assert linked.keys() == {e.id for e in items}  # every errand shows up exactly once
    assert len([t for t in merged if "USPS" in t.title]) == 1
    assert linked["usps_forwarding"].title == "Set up USPS mail forwarding"
    assert linked["usps_forwarding"].due == by_id(items)["usps_forwarding"].due
    assert merged == sorted(merged, key=lambda t: t.due)


def test_arrival_pack_goes_to_the_user_and_skips_done_errands():
    i = intake(lease_end=None)
    items = errands.build(i, MOVE, done=("internet",))
    draft = errands.arrival_pack(i, items, MOVE)
    assert draft.to == "ana@example.com"
    assert "Move or cancel internet service" not in draft.body
    assert "Give written notice to your current landlord: Current lease end date" in draft.body
    assert "nothing here was sent to anyone else" in draft.body
    assert draft.body.count(errands.SEPARATOR) == 2 * (len(items) - 1)


# ---- web ----

def _plan(client: TestClient, **over) -> str:
    profile = json.loads((FIXTURES / "intake/la_to_sf.json").read_text()) | over
    profile["pets"] = ",".join(profile["pets"])
    response = client.post("/plan", data=profile)
    assert response.status_code == 200
    return str(response.url).rstrip("/").rsplit("/", 1)[-1]


def test_timeline_shows_documents_and_mark_done_round_trip():
    client = TestClient(web.app)
    rid = _plan(client, has_children="true")
    deps = web.SESSIONS[rid].deps
    assert deps.intake.has_children
    assert {"school_enrollment", "vet_records", "dmv_address"} <= {t.errand for t in deps.timeline}

    page = client.get(f"/plan/{rid}/timeline").text
    assert "Email all documents to me" in page and 'id="errand-usps_forwarding"' in page
    assert "Needs your input" in page  # the fixture has ZIPs, not street addresses

    r = client.post(f"/plan/{rid}/errands/vet_records", data={"done": "1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("#errand-vet_records")
    assert web.SESSIONS[rid].deps.errands_done == ("vet_records",)
    assert re.search(r'is-done"[^>]*id="errand-vet_records"', client.get(f"/plan/{rid}/timeline").text)
    client.post(f"/plan/{rid}/errands/vet_records", data={"done": "0"})
    assert web.SESSIONS[rid].deps.errands_done == ()
    assert client.post(f"/plan/{rid}/errands/not_real").status_code == 404


def test_arrival_pack_preview_send_once_and_download(tmp_path):
    client = TestClient(web.app)
    rid = _plan(client)
    preview = client.get(f"/plan/{rid}/arrival-pack").text
    assert "fixture@example.com" in preview and "Nothing is sent until you click the button" in preview
    assert web.SESSIONS[rid].deps.arrival_pack is None  # previewing sends nothing

    client.post(f"/plan/{rid}/arrival-pack")
    sent = web.SESSIONS[rid].deps.arrival_pack
    assert sent["ok"] and sent["to"] == "fixture@example.com" and sent["mode"] == "outbox"
    eml = tmp_path / "outbox" / f"arrival-pack-{rid}.eml"
    first = eml.read_bytes()
    assert b"To: fixture@example.com" in first

    eml.unlink()
    client.post(f"/plan/{rid}/arrival-pack")  # a second click doesn't send again
    assert not eml.exists()
    assert "Saved to the outbox" in client.get(f"/plan/{rid}/arrival-pack").text

    download = client.get(f"/plan/{rid}/arrival-pack.txt")
    assert download.headers["content-disposition"].startswith("attachment")
    assert "Set up USPS mail forwarding" in download.text


def test_arrival_pack_respects_redirect_and_reports_failures(monkeypatch, tmp_path):
    client = TestClient(web.app)
    monkeypatch.setenv("EMAIL_REDIRECT_TO", "operator@example.com")
    rid = _plan(client)
    client.post(f"/plan/{rid}/arrival-pack")
    eml = (tmp_path / "outbox" / f"arrival-pack-{rid}.eml").read_text()
    assert "To: operator@example.com" in eml and "meant for fixture@example.com" in eml

    monkeypatch.delenv("EMAIL_REDIRECT_TO")
    monkeypatch.setenv("EMAIL_MODE", "smtp")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    rid = _plan(client)
    client.post(f"/plan/{rid}/arrival-pack")
    sent = web.SESSIONS[rid].deps.arrival_pack
    assert sent["ok"] is False and "SMTP_HOST" in sent["detail"]
    assert "The email didn't go out" in client.get(f"/plan/{rid}/arrival-pack").text
    # A failed send can be retried.
    assert 'action="/plan/' in client.get(f"/plan/{rid}/arrival-pack").text
