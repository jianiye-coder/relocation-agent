"""U-Haul one-way truck rates from its public rate search (unofficial, opt-in).

Does what the "Get rates" form on uhaul.com does: read the form's request token,
post pickup/drop-off ZIPs and date, then read the truck list it redirects to.
The quoted rate is U-Haul's price for this route and date, with its stated
included days and miles. It stops there; nothing is reserved.
"""

from __future__ import annotations

import re

import httpx

from .base import (
    AdapterError, AdapterMetadata, Capability, ErrorCode, MoveRequest, PriceKind, Quote, QuoteAdapter,
    RateLimit, ServiceType, SourceKind, now,
)
from .scrape import check, hidden_token, new_client, text

HOME_URL = "https://www.uhaul.com/"
SEARCH_URL = ("https://www.uhaul.com/Misc/EquipmentSearch/"
              "?Area=&scenario=TruckOnly&isActionForm=False&isAlternateLayout=False&isTowMycar=True")
CONFIDENCE = 0.6
LOCAL_MILES = 50
# Cargo space from U-Haul's truck pages, by the model code on the rate page. Unknown codes aren't filtered out.
CAPACITY_CUFT = {"TM": 402, "DC": 764, "TT": 1016, "JH": 1682}
INCLUDED = re.compile(r"includes up to (\d+) days? of use and ([\d,]+) miles", re.I)
PRICE = re.compile(r'data-show-price-fees="(true|false)"[^>]*>\s*<b[^>]*>\s*\$([\d,]+\.\d\d)')


def trucks(page: str) -> list[dict]:
    """One dict per available truck on the rate page: code, name, fits, base rate, rate with fees."""
    out, seen = [], set()
    for chunk in page.split("<li data-model-type=")[1:]:
        code = re.match(r'"(\w+)"', chunk)
        if not code or code.group(1) in seen:
            continue
        seen.add(code.group(1))
        if 'data-model-disabled="True"' in chunk[:300]:
            continue
        name = re.search(r"<h3[^>]*>(.*?)</h3>", chunk, re.S)
        fits = re.search(r'id="TruckCapacity_\w+"[^>]*>(.*?)</dd>', chunk, re.S)
        prices = {}
        for with_fees, amount in PRICE.findall(chunk):
            prices.setdefault(with_fees, float(amount.replace(",", "")))
        if not name or "false" not in prices:
            continue
        out.append({"code": code.group(1), "name": text(name.group(1)), "fits": text(fits.group(1)) if fits else "",
                    "base": prices["false"], "with_fees": prices.get("true", prices["false"])})
    return out


class UHaulAdapter(QuoteAdapter):
    metadata = AdapterMetadata(
        id="uhaul",
        name="U-Haul (website rates, unofficial)",
        capabilities=[Capability.quotes],
        service_types=[ServiceType.truck_rental],
        coverage=["US"],
        rate_limit=RateLimit(requests=10, per_seconds=60),
        source_kind=SourceKind.unofficial_scrape,
        cache_ttl_seconds=2 * 3600,
        docs_url="https://www.uhaul.com/Truck-Rentals/",
    )

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client

    async def fetch_quotes(self, req: MoveRequest) -> list[Quote]:
        if req.distance_miles <= LOCAL_MILES:
            raise AdapterError(ErrorCode.no_coverage, "U-Haul in-town rates are per mile; only one-way moves are read")
        client = self.client or new_client()
        try:
            token = hidden_token(check(await client.get(HOME_URL), "U-Haul").text)
            search = check(await client.post(SEARCH_URL, headers={"X-Requested-With": "XMLHttpRequest", "Referer": HOME_URL}, data={
                "PickupLocation": req.origin.zip, "DropoffLocation": req.destination.zip,
                "PickupDate": f"{req.move_date:%m/%d/%Y}", "Scenario": "TruckOnly",
                "__RequestVerificationToken": token,
            }), "U-Haul")
            try:
                rates_url = search.json()["redirect"]
            except (ValueError, KeyError, TypeError):
                raise AdapterError(ErrorCode.invalid_request, "U-Haul didn't accept this route or date") from None
            page = check(await client.get(rates_url), "U-Haul").text
        finally:
            if self.client is None:
                await client.aclose()

        found = trucks(page)
        if not found:
            raise AdapterError(ErrorCode.no_coverage, "U-Haul listed no available trucks for this route and date")
        included = INCLUDED.search(text(page))
        allowance = f"includes {included.group(1)} days and {included.group(2)} miles" if included else "included days/miles not shown"
        fetched = now()
        quotes = []
        for t in found:
            cuft = CAPACITY_CUFT.get(t["code"])
            if cuft and cuft < req.volume_cuft:
                continue
            space = f"{cuft:,} cu ft" if cuft else t["fits"]
            quotes.append(Quote(
                adapter_id=self.metadata.id,
                provider="U-Haul",
                service_type=ServiceType.truck_rental,
                title=f"{t['name']} one-way",
                price_usd=t["with_fees"],
                available_on=req.move_date,
                source=rates_url,
                fetched_at=fetched,
                confidence=CONFIDENCE,
                price_kind=PriceKind.firm_quote,
                price_basis=(f"U-Haul website rate {req.origin.zip} → {req.destination.zip} on {req.move_date:%b} {req.move_date.day}: "
                             f"${t['base']:,.2f} + fees = ${t['with_fees']:,.2f}; {allowance}; {space}. "
                             "Taxes, fuel and damage protection not included. Not reserved."),
                contact_url="https://www.uhaul.com/Truck-Rentals/",
            ))
        if not quotes:
            raise AdapterError(ErrorCode.no_coverage, f"no available U-Haul truck fits {req.volume_cuft} cu ft")
        return quotes
