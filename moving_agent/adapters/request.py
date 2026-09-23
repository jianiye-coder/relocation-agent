"""Build the normalized MoveRequest from the intake profile."""

from __future__ import annotations

from ..models import Intake
from .base import Access, Location, MoveRequest, Vehicle

LBS_PER_CUFT = 7  # industry rule of thumb for household goods


def request_from_intake(intake: Intake, origin_state: str = "", destination_state: str = "",
                        origin_city: str = "", destination_city: str = "") -> MoveRequest:
    return MoveRequest(
        origin=Location(zip=intake.from_zip, address=intake.from_address, state=origin_state, city=origin_city),
        destination=Location(zip=intake.to_zip, address=intake.to_address, state=destination_state, city=destination_city),
        move_date=intake.move_date,
        flexible_days=intake.flexible_days,
        distance_miles=intake.distance_miles,
        volume_cuft=intake.volume,
        weight_lbs=intake.weight,
        origin_access=Access(floor=intake.from_floor, elevator=intake.from_elevator),
        destination_access=Access(floor=intake.to_floor, elevator=intake.to_elevator),
        storage_months=intake.storage_months if "storage" in intake.needs else 0,
        vehicles=[Vehicle(kind=k) for k in intake.vehicles],
        household_size=intake.household_size,
        pets=intake.pets,
        lease_end=intake.lease_end,
        budget_usd=intake.budget_usd,
    )
