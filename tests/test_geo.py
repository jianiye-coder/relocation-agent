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


def photon_feature(name, lat, lon, state, kind="city", postcode=None):
    return {"geometry": {"coordinates": [lon, lat]},
            "properties": {"name": name, "type": kind, "osm_value": kind, "state": state, "postcode": postcode, "countrycode": "US"}}


def census_zcta(zip_code):
    return {"result": {"geographies": {geo.ZCTA_LAYER: [{"ZCTA5": zip_code}] if zip_code else []}}}


def fake_area_services(calls, photon=None, zcta="60605", zippopotam=None):
    def fake_get(url, **kwargs):
        calls.append(url)
        if url == geo.CENSUS_URL:
            return FakeResponse({"result": {"addressMatches": []}})
        if url == geo.PHOTON_URL:
            if isinstance(photon, Exception):
                raise photon
            return FakeResponse({"features": photon or []})
        if url == geo.CENSUS_COORDS_URL:
            return FakeResponse(census_zcta(zcta))
        if url.startswith("https://api.zippopotam.us/us/"):
            return FakeResponse({"places": zippopotam or []}, 200 if zippopotam else 404)
        raise AssertionError(url)
    return fake_get


@pytest.fixture
def area_lookup(monkeypatch):
    geo._city_only.cache_clear()
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    yield
    geo._city_only.cache_clear()


def test_big_city_without_state_resolves_to_the_zip_at_its_center(monkeypatch, area_lookup):
    # Photon returns big cities without a postcode; the Census names the ZIP area at the center.
    calls = []
    monkeypatch.setattr(geo.httpx, "get", fake_area_services(calls, [photon_feature("Chicago", 41.8756, -87.6244, "Illinois")], "60605"))
    place = geo.geocode("chicago")
    assert (place.zip, place.lat) == ("60605", pytest.approx(41.8756))
    assert place.matched_address == "Chicago, IL 60605 (city center)"
    assert geo.PHOTON_URL in calls and geo.CENSUS_COORDS_URL in calls


def test_census_zip_beats_a_single_building_postcode(monkeypatch, area_lookup):
    feature = photon_feature("Mission", 37.7599, -122.4148, "California", kind="suburb", postcode="94143")
    monkeypatch.setattr(geo.httpx, "get", fake_area_services([], [feature], "94110"))
    place = geo.geocode("Mission, San Francisco")
    assert place.zip == "94110" and place.matched_address.endswith("(area center)")


def test_neighborhood_postcode_is_used_when_census_has_no_zip_area(monkeypatch, area_lookup):
    feature = photon_feature("Cupertino", 37.3229, -122.0323, "California", postcode="95014")
    monkeypatch.setattr(geo.httpx, "get", fake_area_services([], [feature], zcta=None))
    assert geo.geocode("Cupertino").zip == "95014"


def test_city_and_state_fall_back_to_zippopotam_when_photon_is_down(monkeypatch, area_lookup):
    # Zippopotam lists New York as "New York City"; a name that starts with what was typed counts.
    zips = [{"place name": "New York City", "post code": z, "latitude": str(lat), "longitude": str(lon)}
            for z, lat, lon in [("10007", 40.71, -74.00), ("10463", 40.88, -73.91), ("10306", 40.57, -74.12)]]
    monkeypatch.setattr(geo.httpx, "get", fake_area_services([], httpx.ConnectError("down"), zippopotam=zips))
    place = geo.geocode("New York, NY")
    assert place.zip == "10007" and place.matched_address == "New York City, NY 10007 (city center)"


def test_city_without_state_explains_when_lookup_is_down(monkeypatch, area_lookup):
    monkeypatch.setattr(geo.httpx, "get", fake_area_services([], httpx.ConnectError("down")))
    with pytest.raises(geo.GeoError, match="Add the state"):
        geo.geocode("chicago")


def test_unknown_area_is_a_clear_error(monkeypatch, area_lookup):
    monkeypatch.setattr(geo.httpx, "get", fake_area_services([], []))
    with pytest.raises(geo.GeoError, match="couldn't resolve"):
        geo.geocode("qwxzv nowhere")


def test_repeated_lookups_are_cached(monkeypatch, area_lookup):
    calls = []
    monkeypatch.setattr(geo.httpx, "get", fake_area_services(calls, [photon_feature("Chicago", 41.8756, -87.6244, "Illinois")]))
    geo.geocode("chicago"); geo.geocode("chicago")
    assert calls.count(geo.PHOTON_URL) == 1


