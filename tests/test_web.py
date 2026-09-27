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


def test_intake_uses_a_destination_area_instead_of_asking_for_a_to_zip():
    page = TestClient(web.app).get("/").text
    assert '<label for="to_address">Moving to</label>' in page
    assert "City, neighborhood, or area" in page
    assert "Start broad" not in page
    assert '<input id="to_zip" name="to_zip" type="hidden">' in page
    assert '<label for="to_zip">' not in page


def test_intake_explains_each_service_choice():
    page = TestClient(web.app).get("/").text
    for explanation in [
        "I’ll drive and load it",
        "Someone else handles the heavy lifting",
        "I need a place to keep things",
        "I’ll load it; the provider transports it",
    ]:
        assert explanation in page


def test_intake_marks_new_floor_optional_and_keeps_notes_outside_selling_section():
    page = TestClient(web.app).get("/").text
    assert 'for="to_floor">New floor <span class="hint">· optional</span>' in page
    selling_start = page.index("<h2>Selling before you move")
    additional_start = page.index("<h2>Additional details")
    selling_section = page[selling_start:additional_start]
    assert 'for="notes"' not in selling_section
    assert 'for="notes"' in page[additional_start:]


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
    assert response.status_code == 422 and "company name, USDOT number, or MC number" in response.text


def test_mover_check_searches_by_company_name(monkeypatch):
    from datetime import datetime, timezone
    from moving_agent.adapters.base import CarrierCheck

    async def fake_search(self, name):
        assert name == "Bay Movers"
        return [CarrierCheck(adapter_id="fmcsa_qcmobile", query="name Bay Movers", found=True, usdot_number=1234567,
                             mc_number=987654, legal_name="BAY MOVERS LLC", dba_name="Bay Movers",
                             allowed_to_operate=True, city="San Francisco", state="CA", source="FMCSA",
                             fetched_at=datetime.now(timezone.utc))]

    monkeypatch.setattr(web.FMCSAAdapter, "search", fake_search)
    response = TestClient(web.app).post("/mover-check", data={"name": "Bay Movers"})
    assert response.status_code == 200
    for text in ["Choose the mover", "BAY MOVERS LLC", "USDOT 1234567", "MC 987654", "Use this mover"]:
        assert text in response.text


def test_mover_check_explains_missing_fmcsa_key(monkeypatch):
    monkeypatch.delenv("FMCSA_WEB_KEY", raising=False)
    response = TestClient(web.app).post("/mover-check", data={"mc": "MC-123456"})
    assert response.status_code == 502
    assert "Add FMCSA_WEB_KEY to .env" in response.text


def test_mover_check_frames_registration_as_scam_safety_check():
    response = TestClient(web.app).get("/mover-check")
    assert response.status_code == 200
    assert "spot scams before you pay" in response.text
    assert "real, registered carrier" in response.text


def test_safety_checks_groups_mover_and_listing_tools():
    response = TestClient(web.app).get("/safety-checks")
    assert response.status_code == 200
    assert 'href="/mover-check"' in response.text
    assert 'href="/listing-check"' in response.text
    assert "not find a mover or search rental listings" in response.text
    assert "does not search for movers" in TestClient(web.app).get("/mover-check").text
    assert "does not search for rental listings" in TestClient(web.app).get("/listing-check").text


def test_candidate_home_controls_live_in_housing_not_intake(intake, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = TestClient(web.app)
    intake_page = client.get("/").text
    assert "Candidate homes" not in intake_page and "commute_destination" not in intake_page
    response = client.post("/plan", data=form(intake))
    import re
    rid = re.search(r"/plan/(\w+)", str(response.url)).group(1)
    housing_page = client.get(f"/housing?rid={rid}").text
    assert "Compare commute" in housing_page and "Leave at" not in housing_page


def test_selecting_housing_result_adds_candidate_and_commute_data(intake, monkeypatch):
    from moving_agent import home
    monkeypatch.setattr(home, "commute", lambda *a, **k: {"available": True, "minutes": 25, "miles": 5.0, "source": "Google Routes",
                                                         "fetched_at": "2026-09-23T20:00:00+00:00", "departure": "2026-10-14T08:30:00-07:00"})
    client = TestClient(web.app)
    response = client.post("/plan", data=form(intake))
    import re
    rid = re.search(r"/plan/(\w+)", str(response.url)).group(1)
    response = client.post(f"/housing/{rid}/select", data={"address": "1 Main St, San Francisco, CA", "commute_destination": "1 Market St, San Francisco, CA", "commute_mode": "transit",
                                                          "listing_url": "https://www.realtor.com/rentals/details/1-Main-St", "contact_name": "Pat Lee", "contact_phone": "(415) 555-0100"})
    assert response.status_code == 200
    assert 'href="https://www.realtor.com/rentals/details/1-Main-St"' in response.text and "Listed by Pat Lee" in response.text
    saved = web.SESSIONS[rid].intake
    assert saved.candidate_addresses == ["1 Main St, San Francisco, CA"]
    assert saved.commute_destination == "1 Market St, San Francisco, CA" and saved.commute_mode == "transit"
    for text in ["Home added to your plan", "Homes you’re comparing", "Schools:", "No verified school data source", "checked Sep 23"]:
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
                      "photos": ["https://images.example/home.jpg", "https://images.example/home-2.jpg"], "disclaimer": "Realtor.com data",
                      "listing_url": "https://www.realtor.com/rentals/details/123-Valencia-St", "contact": {"name": "Pat Lee", "office": "", "phone": "(415) 555-0100"}}],
    })
    response = TestClient(web.app).get("/housing?zip_code=94110&min_rent=2500&max_rent=3000&bedrooms=1")
    assert 'href="https://www.realtor.com/rentals/details/123-Valencia-St" target="_blank" rel="noopener noreferrer">Contact the landlord on Realtor.com' in response.text
    assert "Listed by Pat Lee" in response.text and 'href="tel:+14155550100"' in response.text
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


def test_housing_accepts_bathroom_filter(monkeypatch):
    captured = {}
    monkeypatch.setattr(web.home, "rental_listings", lambda **filters: captured.update(filters) or {
        "available": True, "source": "HomeHarvest", "fetched_at": "2026-09-23T20:00:00+00:00", "listings": []})
    response = TestClient(web.app).get("/housing?location=94110&bathrooms=1.5")
    assert response.status_code == 200 and captured["bathrooms"] == 1.5
    assert "Bathrooms" in response.text and "1.5+ bathroom" in response.text


def test_housing_page_has_specific_filter_errors():
    response = TestClient(web.app).get("/housing?zip_code=94110&min_rent=3000&max_rent=2000&bedrooms=one")
    assert response.status_code == 200
    assert "Minimum rent cannot be higher" in response.text
    assert "Enter a whole number for bedrooms" in response.text


def test_intake_asks_for_addresses_not_zip_or_distance():
    page = TestClient(web.app).get("/").text
    assert "From ZIP" not in page and "Driving distance (miles)" not in page
    for field in ["from_zip", "to_zip", "distance_miles"]:
        assert f'id="{field}" name="{field}" type="hidden"' in page


def test_plan_fills_zips_and_distance_from_the_addresses(intake, monkeypatch):
    places = {"1 Main St, Los Angeles, CA": web.geo.Place(matched_address="LA", zip="90012", lat=34.05, lon=-118.24),
              "Mission, San Francisco": web.geo.Place(matched_address="SF", zip="94110", lat=37.75, lon=-122.41)}
    monkeypatch.setattr(web.geo, "geocode", lambda address: places[address])
    monkeypatch.setattr(web.geo, "driving_distance", lambda a, b: web.geo.Distance(miles=382, source="test"))
    data = form(intake, from_zip="", to_zip="", distance_miles="",
                from_address="1 Main St, Los Angeles, CA", to_address="Mission, San Francisco")
    client = TestClient(web.app)
    response = client.post("/plan", data=data)
    assert response.status_code == 200
    import re
    saved = web.SESSIONS[re.search(r"/plan/(\w+)", str(response.url)).group(1)].intake
    assert (saved.from_zip, saved.to_zip, saved.distance_miles) == ("90012", "94110", 382)


def test_unfound_address_gets_a_plain_error(intake, monkeypatch):
    def geocode(address):
        raise web.geo.GeoError("We couldn't find that address.")
    monkeypatch.setattr(web.geo, "geocode", geocode)
    data = form(intake, from_zip="", to_zip="", distance_miles="", from_address="nowhere", to_address="Mission, San Francisco")
    response = TestClient(web.app).post("/plan", data=data)
    assert response.status_code == 422
    assert "Moving from: We couldn&#39;t find that address." in response.text
    assert "String should match pattern" not in response.text and "we can find" not in response.text


def test_saved_home_never_links_outside_realtor(intake):
    client = TestClient(web.app)
    response = client.post("/plan", data=form(intake))
    import re
    rid = re.search(r"/plan/(\w+)", str(response.url)).group(1)
    response = client.post(f"/housing/{rid}/select", data={"address": "1 Main St, San Francisco, CA", "listing_url": "https://evil.example/phish"})
    assert "evil.example" not in response.text


def test_missing_plan_shows_the_intake_form_with_an_explanation():
    response = TestClient(web.app).get("/plan/doesnotexist")
    assert response.status_code == 404
    assert "That plan is no longer available" in response.text and "Find my options" in response.text
    assert '"detail"' not in response.text
