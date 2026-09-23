"""Warp's documented LTL quote API, normalized for the relocation planner.

Warp's quote endpoint is ``POST /api/v1/ltl/quote``. It requires palletized dry
freight dimensions, so household volume is converted to 60-cubic-foot standard
pallets. This adapter only asks for a quote; it never calls Warp's booking API.
"""

from __future__ import annotations

import math
import os
from datetime import datetime

import httpx

from .base import (
    AdapterError, AdapterMetadata, Capability, ErrorCode, MoveRequest, PriceKind,
    Quote, QuoteAdapter, RateLimit, ServiceType, SourceKind, now,
)


QUOTE_URL = "https://www.wearewarp.com/api/v1/ltl/quote"
PALLET_CUFT = 60
PALLET_DIMENSIONS_IN = (48, 40, 48)


class WarpLTLAdapter(QuoteAdapter):
    metadata = AdapterMetadata(
        id="warp_ltl",
        name="Warp LTL freight",
        capabilities=[Capability.quotes],
        service_types=[ServiceType.ltl_freight],
        coverage=["US"],
        auth="api_key",
        auth_env=["WARP_API_KEY"],
        rate_limit=RateLimit(requests=60, per_seconds=60),
        source_kind=SourceKind.official_api,
        cache_ttl_seconds=2 * 3600,
        docs_url="https://www.wearewarp.com/agents/docs/ltl",
    )

    def __init__(self, client: httpx.AsyncClient | None = None, quote_url: str = QUOTE_URL):
        self.client = client
        self.quote_url = quote_url

    async def fetch_quotes(self, req: MoveRequest) -> list[Quote]:
        key = os.getenv("WARP_API_KEY")
        if not key:
            raise AdapterError(ErrorCode.auth_missing, "set WARP_API_KEY")
        if req.volume_cuft <= 0 or req.weight_lbs <= 0:
            raise AdapterError(ErrorCode.invalid_request, "Warp needs a positive inventory volume and weight")

        pallets = max(1, math.ceil(req.volume_cuft / PALLET_CUFT))
        payload = {
            "origin_zip": req.origin.zip,
            "destination_zip": req.destination.zip,
            "pickup_date": req.move_date.isoformat(),
            "pallets": pallets,
            "weight_lbs_per_pallet": math.ceil(req.weight_lbs / pallets),
            "commodity": "household goods",
            "length_in": PALLET_DIMENSIONS_IN[0],
            "width_in": PALLET_DIMENSIONS_IN[1],
            "height_in": PALLET_DIMENSIONS_IN[2],
        }
        client = self.client or httpx.AsyncClient(timeout=15)
        try:
            response = await client.post(
                self.quote_url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
        finally:
            if self.client is None:
                await client.aclose()

        if response.status_code in (401, 403):
            raise AdapterError(ErrorCode.blocked, f"Warp refused the request ({response.status_code})")
        if response.status_code == 429:
            raise AdapterError(ErrorCode.rate_limited, "Warp rate limit", _retry_after(response))
        if 400 <= response.status_code < 500:
            raise AdapterError(ErrorCode.invalid_request, _message(response, "Warp rejected this shipment"))
        if response.status_code >= 500:
            raise AdapterError(ErrorCode.unavailable, f"Warp error {response.status_code}")

        try:
            data = response.json()
            quote_id = str(data["quote_id"])
            price = float(data["price_usd"])
            pickup = datetime.fromisoformat(str(data.get("pickup_date", req.move_date.isoformat()))).date()
            expires_at = _parse_datetime(data.get("expires_at"))
        except (KeyError, TypeError, ValueError) as exc:
            raise AdapterError(ErrorCode.unavailable, f"invalid Warp quote response: {exc}") from exc
        if price < 0:
            raise AdapterError(ErrorCode.unavailable, "invalid Warp quote response: negative price")

        transit_days = data.get("transit_days")
        transit_note = f"; estimated transit {transit_days} day(s)" if isinstance(transit_days, int | float) else ""
        return [Quote(
            adapter_id=self.metadata.id,
            provider="Warp",
            service_type=ServiceType.ltl_freight,
            title=f"Warp LTL quote {quote_id}",
            price_usd=price,
            available_on=pickup,
            valid_until=expires_at,
            source=QUOTE_URL,
            fetched_at=now(),
            confidence=0.95,
            price_kind=PriceKind.firm_quote,
            price_basis=(
                f"Live Warp LTL API quote {quote_id}: {pallets} pallet(s), "
                f"{payload['weight_lbs_per_pallet']} lb/pallet, 48 × 40 × 48 in{transit_note}"
            ),
        )]


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.astimezone()


def _message(response: httpx.Response, fallback: str) -> str:
    try:
        return str(response.json().get("message") or fallback)
    except (ValueError, AttributeError):
        return fallback


def _retry_after(response: httpx.Response) -> int | None:
    try:
        return int(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
