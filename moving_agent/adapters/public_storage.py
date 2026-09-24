"""Public Storage unit prices from its public city pages (unofficial, opt-in).

Approach adapted from https://github.com/ksylvest/publicstorage (MIT, Ruby). A
city page such as /self-storage-ca-san-francisco embeds schema.org JSON-LD for
every nearby facility: address, rating and an offer per unit size with an
online-price range. One request covers a city. Public Storage labels these
prices "not guaranteed", and the admin fee and taxes are not included.
"""

from __future__ import annotations

import json
import re

import httpx

from .base import (
    AdapterError, AdapterMetadata, Capability, ErrorCode, MoveRequest, PriceKind, Quote, QuoteAdapter,
    RateLimit, ServiceType, SourceKind, now,
)
from .scrape import check, city_state, new_client

CITY_URL = "https://www.publicstorage.com/self-storage-{state}-{city}"
UNIT_HEIGHT_FT = 8  # typical ceiling; turns floor area into usable volume
CONFIDENCE = 0.5
MAX_RESULTS = 3
LD_JSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
SIZE = re.compile(r"(\d+(?:\.\d+)?)'\s*x\s*(\d+(?:\.\d+)?)'")


def city_url(city: str, state: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", city.lower()).strip("-")
    return CITY_URL.format(state=state.lower(), city=slug)


def facilities(page: str) -> list[dict]:
    """Every SelfStorage entry in the page's JSON-LD."""
    out = []
    for block in LD_JSON.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "SelfStorage":
                out.append(item)
    return out


def unit_cuft(name: str) -> float | None:
    match = SIZE.search(name)
    return float(match.group(1)) * float(match.group(2)) * UNIT_HEIGHT_FT if match else None


def best_unit(facility: dict, volume_cuft: int) -> tuple[dict, float] | None:
    """The cheapest unit at this facility that fits the volume."""
    fits = []
    for offer in facility.get("makesOffer", []):
        name = offer.get("itemOffered", {}).get("name", "")
        cuft = unit_cuft(name)
        try:
            low = float(offer["lowPrice"])
        except (KeyError, TypeError, ValueError):
            continue
        if cuft and cuft >= volume_cuft:
            fits.append((low, offer, cuft))
    if not fits:
        return None
    low, offer, cuft = min(fits, key=lambda f: f[0])
    return offer, cuft


class PublicStorageAdapter(QuoteAdapter):
    metadata = AdapterMetadata(
        id="public_storage",
        name="Public Storage (website prices, unofficial)",
        capabilities=[Capability.quotes, Capability.storage],
        service_types=[ServiceType.storage],
        coverage=["US"],
        rate_limit=RateLimit(requests=10, per_seconds=60),
        source_kind=SourceKind.unofficial_scrape,
        cache_ttl_seconds=6 * 3600,
        docs_url="https://github.com/ksylvest/publicstorage",
    )

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client

    async def fetch_quotes(self, req: MoveRequest) -> list[Quote]:
        if req.storage_months == 0:
            return []
        client = self.client or new_client()
        try:
            city, state = await city_state(client, req.destination)
            url = city_url(city, state)
            page = check(await client.get(url), "Public Storage").text
        finally:
            if self.client is None:
                await client.aclose()

        found = facilities(page)
        if not found:
            raise AdapterError(ErrorCode.unavailable, "Public Storage page layout changed: no facility data")
        fetched, months = now(), req.storage_months
        quotes = []
        for facility in found:
            best = best_unit(facility, req.volume_cuft)
            if not best:
                continue
            offer, cuft = best
            low, high = float(offer["lowPrice"]), float(offer.get("highPrice") or offer["lowPrice"])
            size = offer["itemOffered"]["name"]
            address = facility.get("address", {})
            street = ", ".join(filter(None, [address.get("streetAddress"), address.get("addressLocality")]))
            rating = facility.get("aggregateRating", {}).get("ratingValue")
            quotes.append(Quote(
                adapter_id=self.metadata.id,
                provider="Public Storage",
                service_type=ServiceType.storage,
                title=f"{size} unit · {street}" if street else f"{size} unit",
                price_usd=round(low * months, 2),
                price_low_usd=round(low * months, 2),
                price_high_usd=round(high * months, 2),
                available_on=req.move_date,
                source=facility.get("url") or url,
                fetched_at=fetched,
                confidence=CONFIDENCE,
                price_kind=PriceKind.published_rate,
                price_basis=(f"Public Storage website: ${low:,.0f}–${high:,.0f}/mo for a {size} unit "
                             f"(about {cuft:,.0f} cu ft at {UNIT_HEIGHT_FT} ft high) × {months} mo. "
                             "Online prices are not guaranteed; admin fee and taxes not included."),
                rating=float(rating) if rating else None,
                contact_url=facility.get("url") or url,
            ))
        if not quotes:
            raise AdapterError(ErrorCode.no_coverage, f"no Public Storage unit near {city} fits {req.volume_cuft} cu ft")
        return sorted(quotes, key=lambda q: q.price_usd)[:MAX_RESULTS]
