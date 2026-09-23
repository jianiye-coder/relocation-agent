"""Candidate-home data from configured sources; unavailable is always explicit."""
from __future__ import annotations
import os
from datetime import datetime, timezone
import httpx

def _now() -> str: return datetime.now(timezone.utc).isoformat()
def _unavailable(source: str, message: str) -> dict: return {"available": False, "source": source, "message": message}

def commute(origin: str, destination: str, mode: str, client: httpx.Client | None = None) -> dict:
    key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not key: return _unavailable("Google Routes", "Set GOOGLE_MAPS_API_KEY to compare commutes.")
    if not origin or not destination: return _unavailable("Google Routes", "Add a candidate address and commute destination.")
    own, client = client is None, client or httpx.Client(timeout=10)
    try:
        response = client.post("https://routes.googleapis.com/directions/v2:computeRoutes", headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "routes.duration,routes.distanceMeters"}, json={"origin":{"address":origin},"destination":{"address":destination},"travelMode":mode.upper()})
        response.raise_for_status(); route = response.json().get("routes", [None])[0]
        if not route: return _unavailable("Google Routes", "No route was returned for these locations.")
        seconds = float(str(route["duration"]).removesuffix("s"))
        return {"available": True, "minutes": round(seconds / 60), "miles": round(route["distanceMeters"] / 1609.344, 1), "source":"Google Routes", "fetched_at":_now()}
    except (httpx.HTTPError, KeyError, TypeError, ValueError): return _unavailable("Google Routes", "Commute lookup is unavailable right now.")
    finally:
        if own: client.close()

def utilities(address: str) -> dict:
    if not address: return {"broadband":_unavailable("FCC broadband data", "Add a candidate address to check internet."), "electricity":_unavailable("NREL/OpenEI utility rates", "Add a candidate address to check electricity.")}
    return {"broadband":_unavailable("FCC broadband data", "Internet availability is not configured for this address."), "electricity":_unavailable("NREL/OpenEI utility rates", "Electricity availability is not configured for this address.")}
