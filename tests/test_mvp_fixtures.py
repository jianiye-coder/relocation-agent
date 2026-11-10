"""Credential-free planning and disabled-delivery regressions."""
import json
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from moving_agent.web import app as web

FIXTURES = Path(__file__).parent / "fixtures"


def test_full_intake_fixture_plans_with_derived_results():
    profile = json.loads((FIXTURES / "intake/la_to_sf.json").read_text())
    profile["pets"] = ",".join(profile["pets"])
    # The recorded dates have passed and the form only plans upcoming moves; keep their 2-day gap.
    move = date.today() + timedelta(days=30)
    profile["move_date"], profile["lease_end"] = move.isoformat(), (move - timedelta(days=2)).isoformat()
    client = TestClient(web.app)
    response = client.post("/plan", data=profile)
    assert response.status_code == 200
    session = web.SESSIONS[str(response.url).rsplit("/", 1)[-1]]
    assert session.intake.pets == ["cat"] and session.intake.household_size == 2
    assert session.deps.inventory.total_cuft == 105
    assert session.deps.inventory.total_lbs == 745
    assert session.deps.plans and session.deps.vehicle_options and session.deps.timeline
    assert session.deps.true_cost and not session.deps.emails and not session.deps.sent


def test_no_delivery_even_when_credentials_are_present(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "fake")
    client = TestClient(web.app)
    for method, path in [
        ("POST", "/send/anything"), ("POST", "/approve/anything"),
        ("GET", "/auth/google/start"), ("GET", "/auth/google/callback"),
        ("POST", "/auth/google/disconnect"),
    ]:
        assert client.request(method, path).status_code == 404
