"""/providers, RentCast-backed /housing and intake value recovery through the real routes."""
import httpx
from fastapi.testclient import TestClient

from moving_agent import discovery, home
from moving_agent.web import app as web


def _fake_http(monkeypatch, responses):
    """Replace discovery's HTTP call; `responses` is a list of return values, and every call is recorded."""
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return httpx.Response(200, json=value, request=httpx.Request(method, url))

    monkeypatch.setattr(discovery.httpx, "request", request)
    return calls


def form(intake, **overrides):
    data = {"name": intake.name, "email": intake.email, "from_zip": intake.from_zip, "to_zip": intake.to_zip,
            "distance_miles": str(intake.distance_miles), "move_date": intake.move_date.isoformat(),
            "home_size": intake.home_size.value, "needs": intake.needs, "budget_usd": str(intake.budget_usd)}
    return data | overrides


PLACE = {"displayName": {"text": "Example Movers"}, "formattedAddress": "1 Example St, San Francisco, CA",
         "googleMapsUri": "https://maps.google.com/?cid=1"}
RENTAL = {"id": "rc1", "formattedAddress": "9 Example Ave, San Francisco, CA 94110", "price": 2900, "bedrooms": 1,
          "listingAgent": {"name": "Pat Lee", "phone": "(415) 555-0100"}}


def test_providers_without_a_key_says_search_is_unavailable(monkeypatch):
    calls = _fake_http(monkeypatch, [])
    page = TestClient(web.app).get("/providers?location=94110").text
    assert "Business search is unavailable" in page and calls == []


def test_providers_lists_businesses_and_rejects_unknown_service(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test")
    calls = _fake_http(monkeypatch, [{"places": [PLACE]}])
    page = TestClient(web.app).get("/providers?location=94110&service=helicopter").text
    assert "Example Movers" in page and 'href="https://maps.google.com/?cid=1"' in page
    assert calls[0][2]["json"]["textQuery"] == "moving companies near 94110, USA"  # unknown service falls back to movers


def test_providers_rejects_long_locations_without_calling_google(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test")
    calls = _fake_http(monkeypatch, [])
    page = TestClient(web.app).get("/providers?location=" + "a" * 161).text
    assert "Enter a city and state or a ZIP code." in page and calls == []


def test_providers_explains_when_google_is_down(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test")
    _fake_http(monkeypatch, [httpx.ConnectError("down")])
    page = TestClient(web.app).get("/providers?location=94110").text
    assert "Google Places is temporarily unavailable" in page


def test_repeated_provider_search_is_served_from_cache(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test")
    calls = _fake_http(monkeypatch, [{"places": [PLACE]}])
    client = TestClient(web.app)
    for location in ("San Francisco, CA", "san francisco,  ca"):
        assert "Example Movers" in client.get("/providers", params={"location": location}).text
    assert len(calls) == 1


def test_housing_uses_rentcast_when_configured_and_caches_view_switches(monkeypatch):
    monkeypatch.setenv("RENTCAST_API_KEY", "test")
    calls = _fake_http(monkeypatch, [[RENTAL]])
    client = TestClient(web.app)
    page = client.get("/housing?location=94110&max_rent=3000").text
    assert "9 Example Ave" in page and "Listed by Pat Lee" in page
    assert "Search active rentals from RentCast" in page and "Source: RentCast" in page
    client.get("/housing?location=94110&max_rent=3000&view=map")
    client.get("/housing?location=94110&max_rent=3000&sort=rent_low")
    assert len(calls) == 1
    assert calls[0][2]["params"]["zipCode"] == "94110" and calls[0][2]["params"]["price"] == "*:3000"


def test_rentcast_failures_are_not_cached(monkeypatch):
    monkeypatch.setenv("RENTCAST_API_KEY", "test")
    calls = _fake_http(monkeypatch, [httpx.ReadTimeout("slow"), [RENTAL]])
    client = TestClient(web.app)
    assert "temporarily unavailable" in client.get("/housing?location=94110").text
    assert "9 Example Ave" in client.get("/housing?location=94110").text
    assert len(calls) == 2


def test_housing_without_rentcast_key_keeps_default_provider(monkeypatch):
    assert [p.id for p in home.housing_providers()] == ["homeharvest_realtor"]
    monkeypatch.setenv("RENTCAST_API_KEY", "test")
    assert [p.id for p in home.housing_providers()] == ["rentcast"]


def test_invalid_intake_keeps_what_the_user_entered(intake):
    data = form(intake, budget_usd="0", needs=["storage"], vehicles=["car"], notes="Piano on the 3rd floor")
    response = TestClient(web.app).post("/plan", data=data)
    assert response.status_code == 422
    page = response.text
    assert f'value="{intake.name}"' in page and 'name="budget_usd" type="number" min="1" required value="0"' in page
    assert 'value="storage" checked' in page and 'value="truck" checked' not in page
    assert 'value="car" checked' in page
    assert ">Piano on the 3rd floor</textarea>" in page
