"""Candidate-home data from configured sources; unavailable is always explicit."""
from __future__ import annotations
import os
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
import httpx

# The MVP city pair is LA -> SF; commutes are at the destination.
DESTINATION_TZ = ZoneInfo("America/Los_Angeles")

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
