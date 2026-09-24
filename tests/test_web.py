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


def test_candidate_home_controls_live_in_housing_not_intake(intake, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = TestClient(web.app)
    intake_page = client.get("/").text
    assert "Candidate homes" not in intake_page and "commute_destination" not in intake_page
    response = client.post("/plan", data=form(intake))
    import re
    rid = re.search(r"/plan/(\w+)", str(response.url)).group(1)
    housing_page = client.get(f"/housing?rid={rid}").text
    assert "Compare commute" in housing_page


def test_selecting_housing_result_adds_candidate_and_commute_data(intake, monkeypatch):
    from moving_agent import home
    monkeypatch.setattr(home, "commute", lambda *a, **k: {"available": True, "minutes": 25, "miles": 5.0, "source": "Google Routes",
                                                         "fetched_at": "2026-09-23T20:00:00+00:00", "departure": "2026-10-14T08:30:00-07:00"})
    client = TestClient(web.app)
    response = client.post("/plan", data=form(intake))
    import re
    rid = re.search(r"/plan/(\w+)", str(response.url)).group(1)
    response = client.post(f"/housing/{rid}/select", data={"address": "1 Main St, San Francisco, CA", "commute_destination": "1 Market St, San Francisco, CA", "commute_mode": "transit", "commute_departure_time": "08:30"})
    assert response.status_code == 200
    saved = web.SESSIONS[rid].intake
    assert saved.candidate_addresses == ["1 Main St, San Francisco, CA"]
    assert saved.commute_destination == "1 Market St, San Francisco, CA" and saved.commute_mode == "transit"
    for text in ["Home added to your plan", "Homes you’re comparing", "Schools:", "No verified school data source", "checked Sep 23", "leaving 8:30"]:
        assert text in response.text, text


def test_housing_selection_stops_at_two_homes(intake):
    client = TestClient(web.app)
    response = client.post("/plan", data=form(intake))
    import re
    rid = re.search(r"/plan/(\w+)", str(response.url)).group(1)
    for address in ["1 Main St, San Francisco, CA", "2 Oak St, San Francisco, CA"]:
        client.post(f"/housing/{rid}/select", data={"address": address})
    response = client.post(f"/housing/{rid}/select", data={"address": "3 Pine St, San Francisco, CA"})
    assert response.status_code == 422 and "already has two" in response.text


def test_housing_page_explains_when_the_live_scraper_is_unavailable(monkeypatch):
    monkeypatch.setattr(web.home, "rental_listings", lambda **filters: {
        "available": False, "source": "HomeHarvest · Realtor.com (unofficial scrape)", "listings": [],
        "message": "Live Realtor.com rental listings are unavailable right now. Try again shortly."})
    response = TestClient(web.app).get("/housing?zip_code=94110")
    assert response.status_code == 200
    assert "Live Realtor.com rental listings are unavailable" in response.text
    assert "HomeHarvest" in response.text


def test_housing_page_renders_real_listing_fields(monkeypatch):
    monkeypatch.setattr(web.home, "rental_listings", lambda **filters: {
        "available": True, "source": "HomeHarvest · Realtor.com (unofficial scrape)", "fetched_at": "2026-09-23T20:00:00+00:00",
        "listings": [{"id": "a", "address": "123 Valencia St, San Francisco, CA 94110", "rent": 2895,
                      "bedrooms": 1, "bathrooms": 1, "square_feet": 610, "property_type": "Condo",
                      "listed_date": "2026-09-20T00:00:00.000Z", "last_seen_date": "", "days_on_market": 3, "status": "Active", "latitude": 37.76, "longitude": -122.42,
                      "photos": ["https://images.example/home.jpg", "https://images.example/home-2.jpg"], "disclaimer": "Realtor.com data"}],
    })
    response = TestClient(web.app).get("/housing?zip_code=94110&min_rent=2500&max_rent=3000&bedrooms=1")
    assert response.status_code == 200
    for text in ["123 Valencia St", "$2,895", "610 sq ft", "checked Sep 23", "3 days on market", "View all 2 photos", "Realtor.com data"]:
        assert text in response.text
    assert 'src="https://images.example/home.jpg"' in response.text
    assert 'width="640" height="480"' in response.text


def test_housing_results_offer_sorting_filter_chips_and_map_view(monkeypatch):
    monkeypatch.setattr(web.home, "rental_listings", lambda **filters: {
        "available": True, "source": "HomeHarvest", "fetched_at": "2026-09-23T20:00:00+00:00",
        "listings": [
            {"id": "later", "address": "2 Oak St", "rent": 3200, "bedrooms": 1, "bathrooms": 1, "square_feet": 600, "property_type": "Condo", "listed_date": "2026-09-23", "last_seen_date": "", "days_on_market": 1, "status": "for_rent", "latitude": 37.76, "longitude": -122.42, "photos": [], "disclaimer": ""},
            {"id": "cheaper", "address": "1 Main St", "rent": 2800, "bedrooms": 1, "bathrooms": 1, "square_feet": 600, "property_type": "Condo", "listed_date": "2026-09-20", "last_seen_date": "", "days_on_market": 4, "status": "for_rent", "latitude": 37.77, "longitude": -122.41, "photos": [], "disclaimer": ""},
        ],
    })
    response = TestClient(web.app).get("/housing?zip_code=94110&min_rent=2500&bedrooms=1&sort=rent_low&view=map")
    assert response.status_code == 200
    assert response.text.index("1 Main St") < response.text.index("2 Oak St")
    for text in ["Filters", "Min $2,500", "1 bed", "Rent: low to high", "List", "Map", "map-pin"]:
        assert text in response.text, text
    assert 'class="clear-filters" href="/housing"' in response.text


def test_housing_accepts_a_city_or_neighborhood_location(monkeypatch):
    captured = {}
    monkeypatch.setattr(web.home, "rental_listings", lambda **filters: captured.update(filters) or {
        "available": True, "source": "HomeHarvest", "fetched_at": "2026-09-23T20:00:00+00:00", "listings": []})
    response = TestClient(web.app).get("/housing?location=Lakeview%2C+Chicago")
    assert response.status_code == 200 and captured["location"] == "Lakeview, Chicago"
    for text in ["Destination area", "Lakeview, Chicago", "city, neighborhood, larger area, or ZIP code"]:
        assert text in response.text


def test_housing_page_has_specific_filter_errors():
    response = TestClient(web.app).get("/housing?zip_code=94110&min_rent=3000&max_rent=2000&bedrooms=one")
    assert response.status_code == 200
    assert "Minimum rent cannot be higher" in response.text
    assert "Enter a whole number for bedrooms" in response.text
