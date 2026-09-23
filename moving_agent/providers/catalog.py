"""Prices the sample catalog for one specific move.

Each provider type turns catalog rates into an `Offer` for the user's volume,
distance and dates. Live adapters (see "APIs we need" in the PRD) return the same `Offer`
shape, so the planner doesn't care where an offer came from.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

from ..models import Intake, Offer, Service

CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "catalog.json"
SOURCE = "sample catalog (illustrative rates)"

# About how many cubic feet a 2-person crew loads or unloads per hour,
# before stairs are taken into account.
CUFT_PER_MOVER_HOUR = 60


def load_catalog(path: Path = CATALOG_PATH) -> dict:
    return json.loads(path.read_text())


def candidate_dates(intake: Intake) -> list[date]:
    """The move date first, then alternating later/earlier days within the flexible window."""
    days = [intake.move_date]
    for d in range(1, intake.flexible_days + 1):
        days += [intake.move_date + timedelta(days=d), intake.move_date - timedelta(days=d)]
    return days


def labor_hours(intake: Intake, movers: int) -> float:
    """Loading + unloading time, with a penalty for stairs without an elevator."""
    stairs = 0
    if not intake.from_elevator:
        stairs += max(intake.from_floor - 1, 0)
    if not intake.to_elevator:
        stairs += max(intake.to_floor - 1, 0)
    stair_factor = 1 + 0.15 * stairs
    hours = intake.volume / (CUFT_PER_MOVER_HOUR * movers) * stair_factor
    return math.ceil(hours * 2) / 2  # round up to half hours


class SampleCatalog:
    """Offers from `data/catalog.json`."""

    def __init__(self, data: dict | None = None):
        self.data = data or load_catalog()

    def offers(self, intake: Intake, service: Service) -> list[Offer]:
        builder = {
            "truck": self._trucks,
            "labor": self._labor,
            "storage": self._storage,
            "container": self._containers,
        }[service]
        return builder(intake)

    def _trucks(self, intake: Intake) -> list[Offer]:
        out = []
        for i, t in enumerate(self.data["trucks"]):
            if t["capacity_cuft"] < intake.volume:
                continue
            if intake.is_local:
                price = t["local_base"] + t["local_per_mile"] * intake.distance_miles * 2
                basis = f"${t['local_base']} + ${t['local_per_mile']}/mi x {intake.distance_miles * 2} mi (round trip)"
            else:
                price = max(t["one_way_min"], t["one_way_per_mile"] * intake.distance_miles)
                basis = f"one-way: ${t['one_way_per_mile']}/mi x {intake.distance_miles} mi (min ${t['one_way_min']})"
            for day in candidate_dates(intake):
                if day.isoformat() in t["blackout"]:
                    continue
                out.append(self._offer(f"truck-{i}", t, "truck", round(price, 2), day, basis))
                break
        return out

    def _labor(self, intake: Intake) -> list[Offer]:
        out = []
        for i, l in enumerate(self.data["labor"]):
            hours = max(l["min_hours"], labor_hours(intake, l["movers"]))
            # A crew is needed at both ends of the move.
            total_hours = hours if intake.is_local else hours * 2
            price = total_hours * l["movers"] * l["hourly_per_mover"]
            basis = f"{l['movers']} movers x ${l['hourly_per_mover']}/hr x {total_hours:g} hr"
            if not intake.is_local:
                basis += f" ({hours:g} hr loading at {intake.from_zip} + {hours:g} hr unloading at {intake.to_zip}, booked separately)"
            for day in candidate_dates(intake):
                if day.weekday() in l["available_weekdays"]:
                    out.append(self._offer(f"labor-{i}", l, "labor", round(price, 2), day, basis))
                    break
        return out

    def _storage(self, intake: Intake) -> list[Offer]:
        out = []
        for i, s in enumerate(self.data["storage"]):
            if s["capacity_cuft"] < intake.volume:
                continue
            months = intake.storage_months
            price = s["monthly"] * (1 - s["first_month_discount"]) + s["monthly"] * (months - 1)
            basis = f"${s['monthly']}/mo x {months} mo"
            if s["first_month_discount"]:
                basis += f", first month {int(s['first_month_discount'] * 100)}% off"
            out.append(self._offer(f"storage-{i}", s, "storage", round(price, 2), intake.move_date, basis))
        return out

    def _containers(self, intake: Intake) -> list[Offer]:
        out = []
        for i, c in enumerate(self.data["containers"]):
            if c["capacity_cuft"] < intake.volume:
                continue
            price = c["base"] + c["per_mile"] * intake.distance_miles
            basis = f"${c['base']} + ${c['per_mile']}/mi x {intake.distance_miles} mi"
            out.append(self._offer(f"container-{i}", c, "container", round(price, 2), intake.move_date, basis))
        return out

    @staticmethod
    def _offer(oid: str, row: dict, service: Service, price: float, day: date, basis: str) -> Offer:
        return Offer(
            id=oid,
            provider=row["provider"],
            service=service,
            title=row["title"],
            price_usd=price,
            available_on=day,
            rating=row.get("rating"),
            crew_size=row.get("movers"),
            contact_email=row.get("contact_email"),
            contact_url=row.get("contact_url"),
            price_basis=basis,
            source=SOURCE,
        )
