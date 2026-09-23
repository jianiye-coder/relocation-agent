"""Vehicle: cost to ship on a carrier vs. drive it yourself. Rule-of-thumb estimates, labeled as such."""

from __future__ import annotations

import math

from .base import (
    AdapterMetadata, Capability, MoveRequest, PriceKind, Quote, QuoteAdapter, ServiceType, SourceKind, now,
)

# Open-carrier auto transport: per-mile rate falls with distance; minimum per vehicle.
SHIP_BANDS = [(500, 1.20), (1500, 0.85), (10_000, 0.60)]
SHIP_MIN = 500
SHIP_SPREAD = 0.2  # +/- 20% range
SUV_TRUCK_SURCHARGE = 1.15

# Driving: fuel + one hotel night per 500 miles + meals, per vehicle.
MPG, GAS_PER_GALLON, HOTEL_NIGHT, MEALS_PER_DAY, MILES_PER_DAY = 28, 4.50, 140, 45, 500


def ship_price(miles: int, kind: str) -> float:
    rate = next(r for limit, r in SHIP_BANDS if miles <= limit)
    price = max(SHIP_MIN, miles * rate)
    return round(price * (SUV_TRUCK_SURCHARGE if kind in ("suv", "truck") else 1), 2)


def drive_price(miles: int) -> tuple[float, str]:
    days = max(1, math.ceil(miles / MILES_PER_DAY))
    fuel = miles / MPG * GAS_PER_GALLON
    hotel = (days - 1) * HOTEL_NIGHT
    meals = days * MEALS_PER_DAY
    basis = f"fuel {miles} mi / {MPG} mpg x ${GAS_PER_GALLON} = ${fuel:,.0f}; {days - 1} hotel night(s); meals for {days} day(s)"
    return round(fuel + hotel + meals, 2), basis


class VehicleEstimateAdapter(QuoteAdapter):
    metadata = AdapterMetadata(
        id="vehicle_estimate",
        name="Ship vs. drive estimate",
        capabilities=[Capability.vehicle],
        service_types=[ServiceType.vehicle_shipping, ServiceType.vehicle_drive],
        source_kind=SourceKind.public_data,
        cache_ttl_seconds=7 * 24 * 3600,
    )

    async def fetch_quotes(self, req: MoveRequest) -> list[Quote]:
        fetched, out = now(), []
        for i, v in enumerate(req.vehicles, start=1):
            ship = ship_price(req.distance_miles, v.kind)
            out.append(Quote(
                adapter_id=self.metadata.id, provider="Auto transport (open carrier)", service_type=ServiceType.vehicle_shipping,
                title=f"Ship vehicle {i} ({v.kind})", price_usd=ship,
                price_low_usd=round(ship * (1 - SHIP_SPREAD), 2), price_high_usd=round(ship * (1 + SHIP_SPREAD), 2),
                available_on=req.move_date, source="rule of thumb: open-carrier per-mile bands", fetched_at=fetched,
                confidence=0.4, price_kind=PriceKind.estimate,
                price_basis=f"{req.distance_miles} mi at the per-mile rate for that distance, min ${SHIP_MIN}"
                + (" (+15% SUV/truck)" if v.kind in ("suv", "truck") else "")
                + ("; non-running vehicles cost more" if not v.operable else ""),
            ))
            if v.operable:
                cost, basis = drive_price(req.distance_miles)
                out.append(Quote(
                    adapter_id=self.metadata.id, provider="Drive it yourself", service_type=ServiceType.vehicle_drive,
                    title=f"Drive vehicle {i}", price_usd=cost, available_on=req.move_date,
                    source="rule of thumb: fuel, lodging and meals", fetched_at=fetched, confidence=0.5,
                    price_kind=PriceKind.estimate, price_basis=basis,
                ))
        return out
