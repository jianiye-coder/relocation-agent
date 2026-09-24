"""Budget Truck Rental one-way rates from budgettruck.com (unofficial, opt-in).

Uses the JSON endpoints behind the site's own search box: start a blank
reservation search, submit pickup/drop-off ZIPs and date, then read the truck
list (rate, included days and miles, cargo space, sold-out flag). It stops
before choosing a truck; nothing is reserved.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx

from .base import (
    AdapterError, AdapterMetadata, Capability, ErrorCode, MoveRequest, PriceKind, Quote, QuoteAdapter,
    RateLimit, ServiceType, SourceKind, now,
)
from .scrape import check, hidden_token, new_client

HOME_URL = "https://www.budgettruck.com/"
API = "https://www.budgettruck.com/API/BudgetTruck.WebAPI/"
# The site's search module ids, sent as headers the way its own script sends them.
MODULE_HEADERS = {"ModuleId": "12812", "TabId": "56", "X-Requested-With": "XMLHttpRequest"}
CONFIDENCE = 0.6
LOCAL_MILES = 50
CUFT = re.compile(r"([\d,]+)\s*cu\.?\s*ft", re.I)
ERROR_FIELDS = ["PickupLocationError", "DropOffLocationError", "PickupDateError", "PickupTimeError"]


def capacity(description: str) -> int | None:
    match = CUFT.search(description or "")
    return int(match.group(1).replace(",", "")) if match else None


def pickup_day(value: str | None, fallback):
    try:
        return datetime.strptime((value or "").split()[0], "%m/%d/%Y").date()
    except (ValueError, IndexError):
        return fallback


class BudgetTruckAdapter(QuoteAdapter):
    metadata = AdapterMetadata(
        id="budget_truck",
        name="Budget Truck Rental (website rates, unofficial)",
        capabilities=[Capability.quotes],
        service_types=[ServiceType.truck_rental],
        coverage=["US"],
        rate_limit=RateLimit(requests=10, per_seconds=60),
        source_kind=SourceKind.unofficial_scrape,
        cache_ttl_seconds=2 * 3600,
        docs_url="https://www.budgettruck.com/",
    )

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client

    async def fetch_quotes(self, req: MoveRequest) -> list[Quote]:
        if req.distance_miles <= LOCAL_MILES:
            raise AdapterError(ErrorCode.no_coverage, "Budget local rentals are priced per mile; only one-way moves are read")
        day = f"{req.move_date:%m/%d/%Y}"
        client = self.client or new_client()
        try:
            headers = {**MODULE_HEADERS, "RequestVerificationToken": hidden_token(check(await client.get(HOME_URL), "Budget").text)}
            reservation = check(await client.get(API + "QuoteReservation/GetReservationObj",
                                                 params={"returnBlank": "true"}, headers=headers), "Budget").json()
            reservation.update(ApplicationType="P", PickupDate=day, DueInDate="", PickupTime="09:00 AM", IsLocal=False,
                               IsFlexible=False, CouponID1="", PickUpLocation=req.origin.zip,
                               DropOffLocation=req.destination.zip)
            search = check(await client.post(API + "Home/SearchReservation", json=reservation, headers=headers), "Budget")
            problems = _problems(search)
            if problems:
                raise AdapterError(ErrorCode.invalid_request, f"Budget: {problems}")
            data = check(await client.get(API + "Truck/GetTrucks", headers=headers, params={
                "pickupDate": day, "dropOffDate": "", "isEditModel": "false"}), "Budget").json()
        finally:
            if self.client is None:
                await client.aclose()

        trucks = data.get("trucksSorted") if isinstance(data, dict) else None
        if trucks is None:
            raise AdapterError(ErrorCode.unavailable, "Budget response layout changed: no truck list")
        fetched = now()
        quotes = []
        for t in trucks:
            rate = float(t.get("BaseTruckRate") or 0)
            cuft = capacity(t.get("Description", ""))
            if t.get("IsSoldOut") or rate <= 0 or (cuft and cuft < req.volume_cuft):
                continue
            allowance = f"includes {t.get('RentalDays')} days and {t.get('NoOfMiles')} miles" if t.get("NoOfMiles") else "included miles not shown"
            quotes.append(Quote(
                adapter_id=self.metadata.id,
                provider="Budget",
                service_type=ServiceType.truck_rental,
                title=f"{t.get('Name', 'Truck')} one-way",
                price_usd=rate,
                available_on=pickup_day(t.get("TruckPickupDate"), req.move_date),
                source=HOME_URL,
                fetched_at=fetched,
                confidence=CONFIDENCE,
                price_kind=PriceKind.firm_quote,
                price_basis=(f"Budget website rate {req.origin.zip} → {req.destination.zip} on "
                             f"{req.move_date:%b} {req.move_date.day}: ${rate:,.2f} per trip; {allowance}"
                             f"{f'; {cuft:,} cu ft' if cuft else ''}. Taxes, fees, fuel and protection not included. Not reserved."),
                contact_url=HOME_URL,
            ))
        if not quotes:
            raise AdapterError(ErrorCode.no_coverage, f"no available Budget truck fits {req.volume_cuft} cu ft on this date")
        return quotes


def _problems(response: httpx.Response) -> str:
    """The search returns an empty body when it's valid, or an object naming each bad field."""
    if not response.text.strip():
        return ""
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict) or body.get("IsValid", True):
        return ""
    return "; ".join(str(body[f]) for f in ERROR_FIELDS if body.get(f)) or "search was rejected"
