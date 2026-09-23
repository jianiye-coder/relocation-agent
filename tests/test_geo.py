"""Address lookup and distance, with the external services faked."""

import httpx
import pytest
from fastapi.testclient import TestClient

from moving_agent import geo
from moving_agent.web import app as web

CHICAGO = geo.Place(matched_address="5801 S ELLIS AVE, CHICAGO, IL, 60637", zip="60637", lat=41.7896, lon=-87.6012)
SF = geo.Place(matched_address="2000 MISSION ST, SAN FRANCISCO, CA, 94110", zip="94110", lat=37.7641, lon=-122.4194)


class FakeResponse:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)


def census_payload(place):
    return {"result": {"addressMatches": [{
        "matchedAddress": place.matched_address,
        "addressComponents": {"zip": place.zip},
        "coordinates": {"x": place.lon, "y": place.lat},
    }]}}


def test_census_geocode_returns_zip(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.setattr(geo.httpx, "get", lambda url, **kw: FakeResponse(census_payload(CHICAGO)))
    place = geo.geocode("5801 S Ellis Ave, Chicago, IL")
    assert place.zip == "60637" and "ELLIS" in place.matched_address


def test_unknown_address_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.setattr(geo.httpx, "get", lambda url, **kw: FakeResponse({"result": {"addressMatches": []}}))
    with pytest.raises(geo.GeoError):
        geo.geocode("nowhere")


def test_osrm_distance_in_miles(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.setattr(geo.httpx, "get", lambda url, **kw: FakeResponse({"routes": [{"distance": 3445815.9}]}))
    d = geo.driving_distance(CHICAGO, SF)
    assert d.miles == 2141 and "OSRM" in d.source


def test_distance_falls_back_to_estimate(monkeypatch):
    def boom(url, **kw):
        raise httpx.ConnectError("offline")
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.setattr(geo.httpx, "get", boom)
    d = geo.driving_distance(CHICAGO, SF)
    assert "estimate" in d.source and 2000 < d.miles < 2600


def test_distance_api_fills_zips_and_miles(monkeypatch):
    places = {"chicago": CHICAGO, "sf": SF}
    monkeypatch.setattr(web.geo, "geocode", lambda a: places[a])
    monkeypatch.setattr(web.geo, "driving_distance", lambda a, b: geo.Distance(miles=2141, source="OSRM driving route"))
    data = TestClient(web.app).get("/api/geo/distance", params={"from_address": "chicago", "to_address": "sf"}).json()
    assert data["from"]["zip"] == "60637" and data["to"]["zip"] == "94110" and data["distance_miles"] == 2141


def test_plan_works_with_addresses_only(monkeypatch, intake):
    places = {"5801 S Ellis Ave, Chicago, IL": CHICAGO, "2000 Mission St, San Francisco, CA": SF}
    monkeypatch.setattr(web.geo, "geocode", lambda a: places[a])
    monkeypatch.setattr(web.geo, "driving_distance", lambda a, b: geo.Distance(miles=2141, source="OSRM driving route"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    data = {
        "name": "Jenny Ye", "email": "jenny@example.com",
        "from_address": "5801 S Ellis Ave, Chicago, IL", "to_address": "2000 Mission St, San Francisco, CA",
        "move_date": intake.move_date.isoformat(), "home_size": "1br", "needs": ["truck", "labor"], "budget_usd": "6000",
    }
    r = TestClient(web.app).post("/plan", data=data)
    assert r.status_code == 200, r.text[:500]
    assert "one-way" in r.text and "2000 Mission St" in r.text


def test_zip_only_destination(monkeypatch):
    payload = {"places": [{"place name": "San Francisco", "state abbreviation": "CA", "latitude": "37.7509", "longitude": "-122.4153"}]}
    monkeypatch.setattr(geo.httpx, "get", lambda url, **kw: FakeResponse(payload))
    place = geo.geocode("94110")
    assert place.zip == "94110" and "ZIP center" in place.matched_address
