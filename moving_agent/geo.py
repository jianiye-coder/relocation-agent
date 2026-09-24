"""Address -> ZIP and coordinates, and driving distance between two addresses.

With GOOGLE_MAPS_API_KEY set, uses Google Geocoding and Routes. Without it, uses
free services that need no key: the US Census geocoder and the public OSRM
router (fine for a demo, not for production traffic). If routing fails, falls
back to straight-line distance x 1.25, labeled as an estimate.
"""

from __future__ import annotations

import math
import os
import re

import httpx
from pydantic import BaseModel

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{a_lon},{a_lat};{b_lon},{b_lat}"
ZIP_URL = "https://api.zippopotam.us/us/{zip}"
CITY_URL = "https://api.zippopotam.us/us/{state}/{city}"
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
METERS_PER_MILE = 1609.344
TIMEOUT = 10.0


class Place(BaseModel):
    matched_address: str
    zip: str
    lat: float
    lon: float


class Distance(BaseModel):
    miles: int
    source: str


class GeoError(Exception):
    pass


def geocode(address: str) -> Place:
    address = address.strip()
    if not address:
        raise GeoError("Enter an address.")
    if re.fullmatch(r"\d{5}", address):
        return _zip_only(address)
    key = os.getenv("GOOGLE_MAPS_API_KEY")
    if key:
        return _google_geocode(address, key)
    try:
        return _census_geocode(address)
    except GeoError:
        # Census only returns street-address matches. A city/state is enough to
        # start planning, so use the city's representative ZIP center instead.
        return _city_only(address)


def _zip_only(zip_code: str) -> Place:
    """For users who only know the destination ZIP: use the ZIP's center point."""
    r = httpx.get(ZIP_URL.format(zip=zip_code), timeout=TIMEOUT)
    if r.status_code == 404:
        raise GeoError(f"{zip_code} isn't a US ZIP code we know.")
    r.raise_for_status()
    p = r.json()["places"][0]
    return Place(
        matched_address=f"{p['place name']}, {p['state abbreviation']} {zip_code} (ZIP center)",
        zip=zip_code, lat=float(p["latitude"]), lon=float(p["longitude"]),
    )


def _city_only(location: str) -> Place:
    """Resolve ``City, ST`` to an exact city match and its first ZIP center.

    This intentionally avoids guessing for broad labels (for example, "East
    Bay"). Those labels need a city or neighborhood before a move quote can
    usefully estimate a driving route.
    """
    match = re.fullmatch(r"\s*(.+?)\s*,\s*([A-Za-z]{2})\s*", location)
    if not match:
        raise GeoError("We couldn't resolve that area. Add a city and state, for example Mountain View, CA.")
    city, state = match.groups()
    r = httpx.get(
        CITY_URL.format(state=state.upper(), city=city),
        timeout=TIMEOUT,
    )
    if r.status_code == 404:
        raise GeoError("We couldn't resolve that area. Add a city and state, for example Mountain View, CA.")
    r.raise_for_status()
    places = r.json().get("places", [])
    exact = next((p for p in places if p.get("place name", "").casefold() == city.casefold()), None)
    if not exact:
        raise GeoError("We couldn't resolve that area. Add a city and state, for example Mountain View, CA.")
    zip_code = exact["post code"]
    return Place(
        matched_address=f"{exact['place name']}, {state.upper()} {zip_code} (city center)",
        zip=zip_code,
        lat=float(exact["latitude"]),
        lon=float(exact["longitude"]),
    )


def _census_geocode(address: str) -> Place:
    r = httpx.get(CENSUS_URL, params={"address": address, "benchmark": "Public_AR_Current", "format": "json"}, timeout=TIMEOUT)
    r.raise_for_status()
    matches = r.json()["result"]["addressMatches"]
    if not matches:
        raise GeoError("We couldn't find that address. Include the street, city and state.")
    m = matches[0]
    return Place(
        matched_address=m["matchedAddress"],
        zip=m["addressComponents"]["zip"],
        lat=m["coordinates"]["y"],
        lon=m["coordinates"]["x"],
    )


def _google_geocode(address: str, key: str) -> Place:
    r = httpx.get(GOOGLE_GEOCODE_URL, params={"address": address, "components": "country:US", "key": key}, timeout=TIMEOUT)
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        raise GeoError("We couldn't find that address. Include the street, city and state.")
    res = results[0]
    zip_code = next((c["short_name"] for c in res["address_components"] if "postal_code" in c["types"]), None)
    if not zip_code:
        raise GeoError("That address has no ZIP code. Try a full street address.")
    loc = res["geometry"]["location"]
    return Place(matched_address=res["formatted_address"], zip=zip_code, lat=loc["lat"], lon=loc["lng"])


def driving_distance(a: Place, b: Place) -> Distance:
    key = os.getenv("GOOGLE_MAPS_API_KEY")
    try:
        return _google_route(a, b, key) if key else _osrm_route(a, b)
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return Distance(miles=round(_haversine_miles(a, b) * 1.25), source="estimate (straight line x 1.25)")


def _osrm_route(a: Place, b: Place) -> Distance:
    url = OSRM_URL.format(a_lon=a.lon, a_lat=a.lat, b_lon=b.lon, b_lat=b.lat)
    r = httpx.get(url, params={"overview": "false"}, timeout=TIMEOUT)
    r.raise_for_status()
    meters = r.json()["routes"][0]["distance"]
    return Distance(miles=round(meters / METERS_PER_MILE), source="OSRM driving route")


def _google_route(a: Place, b: Place, key: str) -> Distance:
    body = {
        "origin": {"location": {"latLng": {"latitude": a.lat, "longitude": a.lon}}},
        "destination": {"location": {"latLng": {"latitude": b.lat, "longitude": b.lon}}},
        "travelMode": "DRIVE",
    }
    headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": "routes.distanceMeters"}
    r = httpx.post(GOOGLE_ROUTES_URL, json=body, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    meters = r.json()["routes"][0]["distanceMeters"]
    return Distance(miles=round(meters / METERS_PER_MILE), source="Google Routes")


def _haversine_miles(a: Place, b: Place) -> float:
    r = 3958.8
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))
