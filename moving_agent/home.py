"""Candidate-home data from configured sources; unavailable is always explicit."""
from __future__ import annotations
import os
from datetime import date, datetime, time, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo
import httpx

# The MVP city pair is LA -> SF; commutes are at the destination.
DESTINATION_TZ = ZoneInfo("America/Los_Angeles")
HOMEHARVEST_SOURCE = "HomeHarvest · Realtor.com (unofficial scrape)"

def _now() -> str: return datetime.now(timezone.utc).isoformat()
def _unavailable(source: str, message: str) -> dict: return {"available": False, "source": source, "message": message}

def departure_time(on: date, hhmm: str, tz: ZoneInfo = DESTINATION_TZ) -> datetime | None:
    """Local departure on the first commute day. None if unset or already past (Google rejects past times)."""
    if not hhmm:
        return None
    hour, minute = (int(x) for x in hhmm.split(":"))
    leave = datetime.combine(on, time(hour, minute), tzinfo=tz)
    return leave if leave > datetime.now(timezone.utc) else None


def commute(origin: str, destination: str, mode: str, client: httpx.Client | None = None, departure: datetime | None = None) -> dict:
    key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not key: return _unavailable("Google Routes", "Set GOOGLE_MAPS_API_KEY to compare commutes.")
    if not origin or not destination: return _unavailable("Google Routes", "Add a candidate address and commute destination.")
    body = {"origin": {"address": origin}, "destination": {"address": destination}, "travelMode": mode.upper()}
    if departure:
        body["departureTime"] = departure.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if mode.lower() == "drive":
            body["routingPreference"] = "TRAFFIC_AWARE"  # needed for traffic at that time
    own, client = client is None, client or httpx.Client(timeout=10)
    try:
        response = client.post("https://routes.googleapis.com/directions/v2:computeRoutes", headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "routes.duration,routes.distanceMeters"}, json=body)
        response.raise_for_status(); routes = response.json().get("routes") or []; route = routes[0] if routes else None
        if not route: return _unavailable("Google Routes", "No route was returned for these locations.")
        seconds = float(str(route["duration"]).removesuffix("s"))
        result = {"available": True, "minutes": round(seconds / 60), "miles": round(route["distanceMeters"] / 1609.344, 1), "source":"Google Routes", "fetched_at":_now()}
        if departure:
            result["departure"] = departure.isoformat()
        return result
    except (httpx.HTTPError, KeyError, TypeError, ValueError): return _unavailable("Google Routes", "Commute lookup is unavailable right now.")
    finally:
        if own: client.close()


def schools(address: str) -> dict:
    """No verified official school data source is wired in yet; say so instead of guessing."""
    if not address: return _unavailable("School data", "Add a candidate address to check schools.")
    return _unavailable("School data", "No verified school data source is configured yet.")


def utilities(address: str) -> dict:
    if not address: return {"broadband":_unavailable("FCC broadband data", "Add a candidate address to check internet."), "electricity":_unavailable("NREL/OpenEI utility rates", "Add a candidate address to check electricity.")}
    return {"broadband":_unavailable("FCC broadband data", "Internet availability is not configured for this address."), "electricity":_unavailable("NREL/OpenEI utility rates", "Electricity availability is not configured for this address.")}


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _listing_photos(property_data: Any) -> list[str]:
    """Extract the source's primary photo and alternates without assuming its exact shape."""
    description = _field(property_data, "description")
    candidates = [_field(description, "primary_photo"), *(_field(description, "alt_photos", []) or [])]
    photos: list[str] = []
    for candidate in candidates:
        url = str(candidate)
        if url.startswith("https://") and url not in photos:
            photos.append(url)
    return photos


def _as_iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


LISTING_HOST = "https://www.realtor.com/"


def listing_url(value: Any) -> str:
    """The listing's own Realtor.com page, where the landlord or agent is contacted. Anything else is dropped."""
    url = str(value or "")
    return url if url.startswith(LISTING_HOST) else ""


def _phone(phones: Any) -> str:
    phones = phones if isinstance(phones, list) else [phones] if phones else []
    ordered = sorted(phones, key=lambda p: not _field(p, "primary"))
    digits = next(("".join(ch for ch in str(_field(p, "number") or "") if ch.isdigit()) for p in ordered if _field(p, "number")), "")
    digits = digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}" if len(digits) == 10 else ""


def listing_contact(property_data: Any) -> dict | None:
    """Who advertises the listing, when the source says: agent or leasing office name and a phone."""
    advertisers = _field(property_data, "advertisers")
    agent, office = _field(advertisers, "agent"), _field(advertisers, "office")
    name = _field(agent, "name") or _field(office, "name") or _field(_field(advertisers, "broker"), "name")
    phone = _phone(_field(agent, "phones")) or _phone(_field(office, "phones"))
    if not (name or phone):
        return None
    office_name = _field(office, "name")
    return {"name": name or "", "office": office_name if office_name and office_name != name else "", "phone": phone}


def _display_value(value: Any) -> str | None:
    if value is None:
        return None
    value = getattr(value, "value", value)
    return str(value).replace("_", " ").title()


def rental_listings(location: str, min_rent: int | None = None, max_rent: int | None = None,
                    bedrooms: int | None = None, bathrooms: float | None = None, limit: int = 12,
                    scraper: Callable[..., list[Any]] | None = None) -> dict:
    """Return current Realtor.com rentals through HomeHarvest.

    HomeHarvest is an unofficial scraper of Realtor.com. Its live data can
    change or become unavailable without notice, so callers must show its
    source and never substitute sample homes.
    """
    location = location.strip()
    if not location:
        return _unavailable(HOMEHARVEST_SOURCE, "Enter a city, neighborhood, area, or ZIP code.") | {"listings": []}

    if scraper is None:
        try:
            from homeharvest import scrape_property
        except ImportError:
            return _unavailable(HOMEHARVEST_SOURCE, "HomeHarvest is not installed. Install the project dependencies and try again.") | {"listings": []}
        scraper = scrape_property
    options: dict[str, Any] = {
        "location": location, "listing_type": "for_rent", "return_type": "pydantic",
        "price_min": min_rent, "price_max": max_rent,
        "beds_min": bedrooms, "beds_max": bedrooms,
        "baths_min": bathrooms, "baths_max": bathrooms,
        "sort_by": "list_date", "sort_direction": "desc", "limit": min(max(limit, 1), 50),
        # The listing detail call supplies the full photo gallery used by the page.
        "extra_property_data": True, "parallel": False,
    }
    options = {key: value for key, value in options.items() if value is not None}
    try:
        records = scraper(**options)
        if not isinstance(records, list):
            raise ValueError("HomeHarvest response was not a list")
        listings = []
        for item in records:
            address, description = _field(item, "address"), _field(item, "description")
            formatted_address = _field(address, "formatted_address") or _field(address, "full_line")
            if not formatted_address:
                continue
            listings.append({
                "id": _field(item, "listing_id") or _field(item, "property_id", ""),
                "address": formatted_address, "rent": _field(item, "list_price"),
                "bedrooms": _field(description, "beds"), "bathrooms": _field(description, "baths_full"),
                "square_feet": _field(description, "sqft"),
                "property_type": _display_value(_field(description, "style") or _field(description, "type")),
                "listed_date": _as_iso(_field(item, "list_date")), "last_seen_date": _as_iso(_field(item, "last_update_date")),
                "days_on_market": _field(item, "days_on_mls"), "status": _field(item, "status"),
                "latitude": _field(item, "latitude"), "longitude": _field(item, "longitude"),
                "photos": _listing_photos(item),
                "listing_url": listing_url(_field(item, "property_url")), "contact": listing_contact(item),
                "disclaimer": "Data supplied by Realtor.com via HomeHarvest; availability and details can change.",
            })
        return {"available": True, "source": HOMEHARVEST_SOURCE, "fetched_at": _now(), "listings": listings}
    except Exception:
        return _unavailable(HOMEHARVEST_SOURCE, "Live Realtor.com rental listings are unavailable right now. Try again shortly.") | {"listings": []}
