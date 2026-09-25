"""Address -> ZIP and coordinates, and driving distance between two addresses.

With GOOGLE_MAPS_API_KEY set, uses Google Geocoding and Routes. Without it, uses
free services that need no key: the US Census geocoder for street addresses,
Photon (OpenStreetMap) plus the Census ZIP areas for cities and neighborhoods,
and the public OSRM router (fine for a demo, not for production traffic). If routing fails, falls
back to straight-line distance x 1.25, labeled as an estimate.
"""

from __future__ import annotations

import functools
import math
import os
import re

import httpx
from pydantic import BaseModel

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
PHOTON_URL = "https://photon.komoot.io/api/"
US_BBOX = "-125,24,-66,50"
AREA_HEADERS = {"User-Agent": "relocation-agent/1.0 (area lookup)"}
CENSUS_COORDS_URL = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
ZCTA_LAYER = "2020 Census ZIP Code Tabulation Areas"
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


NOT_FOUND = "We couldn't resolve that area. Try a city name, city and state, or ZIP."


STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
CITY_TYPES = {"city", "town", "village", "municipality"}


@functools.lru_cache(maxsize=256)
def _city_only(location: str) -> Place:
    """Resolve a city (with or without a state), neighborhood or region to a representative ZIP.

    One Photon (OpenStreetMap) search finds the place's center, and the Census names the ZIP area
    that contains it (residential ZIPs only, never a PO box or single-building ZIP). If Photon is
    unavailable, "City, ST" still works through Zippopotam. Cached, because the page and the server
    look up the same place more than once.
    """
    try:
        return _photon(location)
    except GeoError:
        match = re.fullmatch(r"\s*(.+?)\s*,\s*([A-Za-z]{2})\s*", location)
        place = _city_center(*match.groups()) if match else None
        if place:
            return place
        raise


def _city_center(city: str, state: str) -> Place | None:
    """The ZIP nearest the middle of all ZIPs Zippopotam lists for this city.

    Place names can differ from what people type ("New York" is listed as "New York City"), so an
    exact name wins, then a name that starts with what was typed.
    """
    state = state.upper()
    try:
        r = httpx.get(CITY_URL.format(state=state, city=city), timeout=TIMEOUT)
        places = r.json().get("places", []) if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return None
    wanted = city.strip().casefold()
    names = {p.get("place name", "") for p in places}
    name = next((n for n in names if n.casefold() == wanted), None) or \
        min((n for n in names if n.casefold().startswith(wanted)), key=len, default=None)
    zips = [p for p in places if p.get("place name") == name]
    if not zips:
        return None
    lat = sum(float(p["latitude"]) for p in zips) / len(zips)
    lon = sum(float(p["longitude"]) for p in zips) / len(zips)
    center = min(zips, key=lambda p: (float(p["latitude"]) - lat) ** 2 + (float(p["longitude"]) - lon) ** 2)
    return Place(
        matched_address=f"{name}, {state} {center['post code']} (city center)",
        zip=center["post code"], lat=float(center["latitude"]), lon=float(center["longitude"]),
    )


def _photon(location: str) -> Place:
    try:
        r = httpx.get(PHOTON_URL, params={"q": location, "limit": 1, "lang": "en", "bbox": US_BBOX},
                      headers=AREA_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        features = r.json().get("features", [])
    except (httpx.HTTPError, ValueError, AttributeError):
        raise GeoError("Area lookup is unavailable right now. Add the state (for example: Chicago, IL) or a ZIP.") from None
    feature = next((f for f in features if f.get("properties", {}).get("countrycode") == "US"), None)
    if not feature:
        raise GeoError(NOT_FOUND)
    props = feature["properties"]
    lon, lat = feature["geometry"]["coordinates"]
    name = props.get("name") or location
    state = STATES.get(str(props.get("state", "")).casefold(), "")
    postcode = (props.get("postcode") or "").split(";")[0].split("-")[0].strip()
    zip_code = _zip_at(lat, lon) or (postcode if re.fullmatch(r"\d{5}", postcode) else "")
    if not zip_code:
        raise GeoError(NOT_FOUND)
    kind = "city center" if props.get("type") in CITY_TYPES or props.get("osm_value") in CITY_TYPES else "area center"
    return Place(matched_address=f"{name}, {state + ' ' if state else ''}{zip_code} ({kind})",
                 zip=zip_code, lat=float(lat), lon=float(lon))


def _zip_at(lat: float, lon: float) -> str:
    """The Census ZIP code tabulation area that contains a point."""
    try:
        r = httpx.get(CENSUS_COORDS_URL, params={"x": lon, "y": lat, "benchmark": "Public_AR_Current",
                                                 "vintage": "Current_Current", "layers": ZCTA_LAYER, "format": "json"},
                      timeout=TIMEOUT)
        r.raise_for_status()
        areas = r.json()["result"]["geographies"].get(ZCTA_LAYER, [])
        return areas[0]["ZCTA5"] if areas else ""
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        return ""


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
