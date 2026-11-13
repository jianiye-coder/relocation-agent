"""Read-only provider discovery (Google Places) and rental listings (RentCast).

Both APIs bill per request, so successful responses are cached in memory for a
few minutes: switching list/map, changing the sort or returning after adding a
home re-renders from the cache instead of paying again. Failures are not cached.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
import time
from typing import Any, Callable

import httpx

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
RENTCAST_URL = "https://api.rentcast.io/v1/listings/rental/long-term"
RENTCAST_SOURCE = "RentCast"
SERVICES = {"movers": "moving companies", "storage": "self storage facilities", "truck": "moving truck rental"}
CACHE_SECONDS = 15 * 60
CACHE_MAX_ENTRIES = 256

_cache: dict[tuple, tuple[float, Any]] = {}


def _cached(key: tuple, fetch: Callable[[], Any], keep: Callable[[Any], bool]) -> Any:
    """Return a fresh cached value for `key`, or fetch it and cache it when `keep(value)`."""
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = fetch()
    if keep(value):
        if len(_cache) >= CACHE_MAX_ENTRIES:
            _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
        _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    _cache.clear()


def _request(method, url, **kwargs):
    try:
        response = httpx.request(method, url, timeout=8, **kwargs)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        return None


def businesses(location, service, key):
    """Nearby businesses for a service, or None when Google Places is unavailable."""
    def fetch():
        data = _request("POST", PLACES_URL, headers={
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.googleMapsUri",
        }, json={"textQuery": f"{SERVICES[service]} near {location}, USA", "pageSize": 8})
        return data.get("places", []) if isinstance(data, dict) else None
    return _cached(("places", " ".join(location.lower().split()), service), fetch, lambda v: v is not None)


def _rentcast_params(location, min_rent, max_rent, bedrooms, bathrooms):
    """RentCast query parameters, or an error message when the location can't be searched."""
    params: dict[str, Any] = {"status": "Active", "limit": 20}
    if re.fullmatch(r"[0-9]{5}", location):
        params["zipCode"] = location
    else:
        city, _, state = location.rpartition(",")
        if not city.strip() or not re.fullmatch(r"[A-Za-z]{2}", state.strip()):
            return "Enter a ZIP code or a city and state, such as San Francisco, CA."
        params.update(city=city.strip(), state=state.strip().upper())
    for name, value in (("bedrooms", bedrooms), ("bathrooms", bathrooms)):
        if value is not None:
            params[name] = value
    if min_rent is not None or max_rent is not None:
        params["price"] = f"{min_rent if min_rent is not None else '*'}:{max_rent if max_rent is not None else '*'}"
    return params


def _listing(record: dict) -> dict:
    agent = record.get("listingAgent") or {}
    office = record.get("listingOffice") or {}
    return {
        "id": record["id"], "address": record["formattedAddress"],
        "rent": record.get("price"), "property_type": record.get("propertyType"),
        "bedrooms": record.get("bedrooms"), "bathrooms": record.get("bathrooms"),
        "square_feet": record.get("squareFootage"), "listed_date": record.get("listedDate"),
        "days_on_market": record.get("daysOnMarket"), "latitude": record.get("latitude"),
        "longitude": record.get("longitude"), "photos": [], "listing_url": None,
        "contact": {"name": agent.get("name"), "office": office.get("name"),
                    "phone": agent.get("phone") or office.get("phone")} if agent or office else None,
        "disclaimer": "Data supplied by RentCast; availability and details can change.",
    }


def rentals(key, location, min_rent=None, max_rent=None, bedrooms=None, bathrooms=None):
    """Active RentCast rentals in the housing-provider result shape."""
    location = location.strip()
    params = _rentcast_params(location, min_rent, max_rent, bedrooms, bathrooms)
    if isinstance(params, str):
        return {"available": False, "source": RENTCAST_SOURCE, "message": params, "listings": []}

    def fetch():
        data = _request("GET", RENTCAST_URL, headers={"X-Api-Key": key}, params=params)
        if not isinstance(data, list):
            return {"available": False, "source": RENTCAST_SOURCE, "listings": [],
                    "message": "Rental search is temporarily unavailable. Try again later."}
        listings = [_listing(r) for r in data if isinstance(r, dict) and r.get("formattedAddress") and r.get("id")]
        return {"available": True, "source": RENTCAST_SOURCE,
                "fetched_at": datetime.now(timezone.utc).isoformat(), "listings": listings}

    result = _cached(("rentcast", tuple(sorted(params.items()))), fetch, lambda v: v["available"])
    return dict(result, listings=[dict(listing) for listing in result["listings"]])  # callers add commute fields


class RentCastProvider:
    """RentCast long-term rentals behind the shared HousingProvider contract (home.py)."""

    id = "rentcast"
    name = RENTCAST_SOURCE

    def __init__(self, key: str):
        self.key = key

    def search(self, *, location: str, min_rent: int | None = None, max_rent: int | None = None,
               bedrooms: int | None = None, bathrooms: float | None = None, limit: int = 12,
               scraper: Callable[..., list[Any]] | None = None) -> dict:
        return rentals(self.key, location, min_rent, max_rent, bedrooms, bathrooms)
