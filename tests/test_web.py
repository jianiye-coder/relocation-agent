import pytest
from fastapi.testclient import TestClient

from moving_agent.web import app as web


def form(intake, **overrides):
    data = {"name": intake.name, "email": intake.email, "from_zip": intake.from_zip, "to_zip": intake.to_zip,
            "distance_miles": str(intake.distance_miles), "move_date": intake.move_date.isoformat(),
            "home_size": intake.home_size.value, "needs": intake.needs, "budget_usd": str(intake.budget_usd)}
    data.update(overrides)
    return data


def test_intake_and_plan_have_no_email_delivery_controls(intake, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = TestClient(web.app)
    assert "quote requests are sent" not in client.get("/").text
    page = client.post("/plan", data=form(intake)).text
    assert "Approve and send" not in page and "Connect Gmail" not in page


def test_email_delivery_routes_are_not_registered():
    client = TestClient(web.app)
    for path in ["/send/nope", "/approve/nope", "/auth/google/start", "/auth/google/callback"]:
        assert client.post(path).status_code == 404


def test_mover_check_shows_an_fmcsa_carrier_record(monkeypatch):
    from datetime import datetime, timezone
    from moving_agent.adapters.base import CarrierCheck

    async def fake_check(self, usdot=None, mc=None):
        assert usdot == 1234567 and mc is None
        return CarrierCheck(adapter_id="fmcsa_qcmobile", query="USDOT 1234567", found=True, usdot_number=1234567,
                            legal_name="EXAMPLE VAN LINES LLC", dba_name="Example Movers", allowed_to_operate=True,
                            city="Chicago", state="IL", source="FMCSA", fetched_at=datetime.now(timezone.utc))

    monkeypatch.setattr(web.FMCSAAdapter, "check", fake_check)
    client = TestClient(web.app)
    response = client.post("/mover-check", data={"usdot": "1234567"})
    assert response.status_code == 200
    for text in ["FMCSA record found", "Allowed to operate", "EXAMPLE VAN LINES LLC", "Example Movers", "1234567", "Chicago, IL"]:
        assert text in response.text


def test_mover_check_requires_exactly_one_identifier():
    client = TestClient(web.app)
    assert client.get("/mover-check").status_code == 200
    response = client.post("/mover-check", data={"usdot": "123", "mc": "MC-456"})
    assert response.status_code == 422 and "either a USDOT number or an MC number" in response.text


def test_mover_check_explains_missing_fmcsa_key(monkeypatch):
    monkeypatch.delenv("FMCSA_WEB_KEY", raising=False)
    response = TestClient(web.app).post("/mover-check", data={"mc": "MC-123456"})
    assert response.status_code == 502
    assert "Add FMCSA_WEB_KEY to .env" in response.text


def test_candidate_home_intake_survives_the_plan_session(intake, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    response = TestClient(web.app).post("/plan", data=form(
        intake,
        candidate_addresses="1 Main St, San Francisco, CA\n2 Oak St, San Francisco, CA",
        commute_destination="1 Market St, San Francisco, CA",
        commute_mode="transit",
        commute_departure_time="08:30",
    ))
    import re
    rid = re.search(r"/plan/(\w+)", str(response.url)).group(1)
    saved = web.SESSIONS[rid].intake
    assert saved.candidate_addresses == ["1 Main St, San Francisco, CA", "2 Oak St, San Francisco, CA"]
    assert saved.commute_destination == "1 Market St, San Francisco, CA"
    assert saved.commute_mode == "transit" and saved.commute_departure_time == "08:30"


@pytest.mark.parametrize("field,value,message", [
    ("candidate_addresses", "1 Main St\n2 Oak St\n3 Pine St", "Add at most two candidate homes"),
    ("commute_mode", "teleport", "Choose drive, transit, walk or bicycle"),
    ("commute_departure_time", "8:30am", "Use a 24-hour time like 08:30"),
    ("commute_departure_time", "25:00", "Use a 24-hour time like 08:30"),
])
def test_candidate_home_input_errors_are_readable(intake, field, value, message):
    response = TestClient(web.app).post("/plan", data=form(intake, **{field: value}))
    assert response.status_code == 422
    assert message in response.text
    assert "List should have at most" not in response.text  # no raw validator text
    assert "String should match pattern" not in response.text


def test_two_candidate_homes_and_blank_lines_are_fine(intake):
    response = TestClient(web.app).post("/plan", data=form(intake, candidate_addresses="\n1 Main St\n\n2 Oak St\n"))
    assert response.status_code == 200


def test_candidate_home_card_shows_schools_row_and_checked_time(intake, monkeypatch):
    from moving_agent import home
    monkeypatch.setattr(home, "commute", lambda *a, **k: {"available": True, "minutes": 25, "miles": 5.0, "source": "Google Routes",
                                                         "fetched_at": "2026-09-23T20:00:00+00:00", "departure": "2026-10-14T08:30:00-07:00"})
    response = TestClient(web.app).post("/plan", data=form(
        intake, candidate_addresses="1 Main St, San Francisco, CA", commute_destination="1 Market St, San Francisco, CA",
        commute_mode="transit", commute_departure_time="08:30"))
    assert response.status_code == 200
    for text in ["Schools:", "No verified school data source", "checked Sep 23", "leaving 8:30"]:
        assert text in response.text, text
