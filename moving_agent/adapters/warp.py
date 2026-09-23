"""Warp LTL freight quotes (official API). Needs WARP_API_KEY; the request mapping is TODO until we have Warp's docs."""

from __future__ import annotations

from .base import (
    AdapterError, AdapterMetadata, Capability, ErrorCode, MoveRequest, Quote, QuoteAdapter, RateLimit, ServiceType, SourceKind,
)


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
        docs_url="https://www.wearewarp.com/",
    )

    async def fetch_quotes(self, req: MoveRequest) -> list[Quote]:
        # Warp quotes palletized freight. Household goods would be ~1 pallet per 60 cu ft.
        # TODO(jenny): map MoveRequest -> Warp quote request once we have the API docs and a key;
        # confirm they accept residential pickup/delivery and household goods.
        raise AdapterError(ErrorCode.unavailable, "Warp adapter not implemented yet: waiting for API docs")
