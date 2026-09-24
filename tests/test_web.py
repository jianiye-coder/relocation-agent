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
