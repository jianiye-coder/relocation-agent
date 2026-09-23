"""Sample catalog adapter: illustrative rates from data/catalog.json, clearly labeled as sample data."""

from __future__ import annotations

from types import SimpleNamespace

from ..providers.catalog import SampleCatalog
from .base import (
    AdapterMetadata, Capability, MoveRequest, PriceKind, Quote, QuoteAdapter, ServiceType, SourceKind, now,
)

SERVICE_MAP = {
    "truck": ServiceType.truck_rental,
    "labor": ServiceType.labor_only,
    "storage": ServiceType.storage,
    "container": ServiceType.container,
}


def _shape(req: MoveRequest) -> SimpleNamespace:
    """The fields SampleCatalog reads, taken from the normalized request."""
    return SimpleNamespace(
        volume=req.volume_cuft, distance_miles=req.distance_miles, is_local=req.distance_miles <= 50,
        move_date=req.move_date, flexible_days=req.flexible_days, storage_months=max(req.storage_months, 1),
        from_floor=req.origin_access.floor, from_elevator=req.origin_access.elevator,
        to_floor=req.destination_access.floor, to_elevator=req.destination_access.elevator,
        from_zip=req.origin.zip, to_zip=req.destination.zip,
    )


class SampleCatalogAdapter(QuoteAdapter):
    metadata = AdapterMetadata(
        id="sample_catalog",
        name="Sample catalog (illustrative rates)",
        capabilities=[Capability.quotes, Capability.storage],
        service_types=list(SERVICE_MAP.values()),
        coverage=["US"],
        source_kind=SourceKind.sample,
        cache_ttl_seconds=24 * 3600,
    )

    def __init__(self, catalog: SampleCatalog | None = None, services: list[str] | None = None):
        self.catalog = catalog or SampleCatalog()
        self.services = services or ["truck", "labor", "container", "storage"]

    async def fetch_quotes(self, req: MoveRequest) -> list[Quote]:
        shape, fetched = _shape(req), now()
        out = []
        for service in self.services:
            if service == "storage" and req.storage_months == 0:
                continue
            for o in self.catalog.offers(shape, service):
                out.append(Quote(
                    adapter_id=self.metadata.id, provider=o.provider, service_type=SERVICE_MAP[service],
                    title=o.title, price_usd=o.price_usd, available_on=o.available_on,
                    source="data/catalog.json (sample)", fetched_at=fetched, confidence=0.2,
                    price_kind=PriceKind.sample, price_basis=o.price_basis, rating=o.rating,
                    crew_size=o.crew_size, contact_email=o.contact_email, contact_url=o.contact_url,
                ))
        return out
