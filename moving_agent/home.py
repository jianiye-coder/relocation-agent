"""Candidate-home data from configured sources; unavailable is always explicit."""
from __future__ import annotations
import os
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
import httpx

# The MVP city pair is LA -> SF; commutes are at the destination.
DESTINATION_TZ = ZoneInfo("America/Los_Angeles")
SIMPLYRETS_LISTINGS_URL = "https://api.simplyrets.com/properties"

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


def rental_listings(zip_code: str, min_rent: int | None = None, max_rent: int | None = None,
                    bedrooms: int | None = None, client: httpx.Client | None = None,
                    limit: int = 12) -> dict:
    """Return active, displayable MLS rental listings with licensed photos via SimplyRETS.

    Results are limited to listings that the MLS has approved for internet
    display. No sample homes or unlicensed scraped media are substituted.
    """
    key = os.getenv("SIMPLYRETS_API_KEY", "").strip()
    secret = os.getenv("SIMPLYRETS_API_SECRET", "").strip()
    if not key or not secret:
        return _unavailable("SimplyRETS / MLS", "Set SIMPLYRETS_API_KEY and SIMPLYRETS_API_SECRET from your approved MLS feed.") | {"listings": []}
    if not (zip_code.isdigit() and len(zip_code) == 5):
        return _unavailable("SimplyRETS / MLS", "Enter a 5-digit destination ZIP code.") | {"listings": []}

    params: dict[str, str | int] = {"postalCodes": zip_code, "status": "Active", "type": "rental", "limit": min(max(limit, 1), 50)}
    if min_rent is not None:
        params["minprice"] = min_rent
    if max_rent is not None:
        params["maxprice"] = max_rent
    if bedrooms is not None:
        params["minbeds"] = bedrooms
        params["maxbeds"] = bedrooms
    own, client = client is None, client or httpx.Client(timeout=10)
    try:
        response = client.get(SIMPLYRETS_LISTINGS_URL, params=params,
                              headers={"Accept": "application/json"}, auth=(key, secret))
        if response.status_code in (401, 403):
            return _unavailable("SimplyRETS / MLS", "SimplyRETS rejected the MLS credentials or feed access.") | {"listings": []}
        response.raise_for_status()
        records = response.json()
        if not isinstance(records, list):
            raise ValueError("listing response was not a list")
        listings = []
        for item in records:
            if not isinstance(item, dict) or item.get("internetAddressDisplay") is False or item.get("internetEntireListingDisplay") is False:
                continue
            address, property_data = item.get("address") or {}, item.get("property") or {}
            photos = [photo for photo in item.get("photos") or [] if isinstance(photo, str) and photo.startswith("https://")]
            listings.append({
                "id": item.get("mlsId", ""), "address": address.get("full") or "Address unavailable",
                "rent": item.get("listPrice"), "bedrooms": property_data.get("bedrooms"), "bathrooms": property_data.get("bathsFull"),
                "square_feet": property_data.get("area"), "property_type": property_data.get("subTypeText") or property_data.get("type"),
                "listed_date": item.get("listDate"), "last_seen_date": item.get("modified"), "days_on_market": None,
                "status": item.get("status"), "photos": photos, "disclaimer": item.get("disclaimer"),
            })
        return {"available": True, "source": "SimplyRETS / MLS", "fetched_at": _now(), "listings": listings}
    except (httpx.HTTPError, TypeError, ValueError):
        return _unavailable("SimplyRETS / MLS", "Live MLS rental listings are unavailable right now.") | {"listings": []}
    finally:
        if own:
            client.close()
