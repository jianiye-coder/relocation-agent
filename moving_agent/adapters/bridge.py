"""Lets the agent and planner read quotes from the adapter Registry (sources, confidence, cache, errors)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import time

from ..models import Intake, Offer
from .base import AdapterResult, Quote, ServiceType
from .registry import Registry
from .request import request_from_intake

SERVICE_TYPES = {
    "truck": {ServiceType.truck_rental},
    "labor": {ServiceType.labor_only, ServiceType.full_service_movers},
    "storage": {ServiceType.storage},
    "container": {ServiceType.container, ServiceType.ltl_freight},
}


def run_sync(coro):
    """Run a coroutine from sync code, whether or not an event loop is already running in this thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def to_offer(q: Quote, index: int) -> Offer:
    service = next(s for s, types in SERVICE_TYPES.items() if q.service_type in types)
    return Offer(
        id=f"{service}-{q.adapter_id}-{index}", provider=q.provider, service=service, title=q.title,
        price_usd=q.price_usd, available_on=q.available_on, rating=q.rating, crew_size=q.crew_size,
        contact_email=q.contact_email, contact_url=q.contact_url, price_basis=q.price_basis,
        source=q.source, confidence=q.confidence, price_kind=q.price_kind.value, fetched_at=q.fetched_at,
    )


class RegistrySource:
    """OfferSource backed by the Registry. Runs all adapters once per distinct request."""

    MEMO_SECONDS = 120  # reuse one registry run across the tool calls of a turn; the Registry cache handles the rest

    def __init__(self, registry: Registry):
        self.registry = registry
        self._last: dict[str, tuple[float, list[AdapterResult]]] = {}

    def results(self, intake: Intake) -> list[AdapterResult]:
        key = request_from_intake(intake).model_dump_json()
        hit = self._last.get(key)
        if not hit or time.time() - hit[0] > self.MEMO_SECONDS:
            self._last = {k: v for k, v in self._last.items() if time.time() - v[0] <= self.MEMO_SECONDS}
            hit = self._last[key] = (time.time(), run_sync(self.registry.quotes(request_from_intake(intake))))
        return hit[1]

    def offers(self, intake: Intake, service: str) -> list[Offer]:
        wanted = SERVICE_TYPES[service]
        out = []
        for r in self.results(intake):
            for i, q in enumerate(r.quotes):
                if q.service_type in wanted:
                    out.append(to_offer(q, i))
        return out

    def quotes_of(self, intake: Intake, *types: ServiceType) -> list[Quote]:
        return [q for r in self.results(intake) for q in r.quotes if q.service_type in types]

    def errors(self, intake: Intake) -> list[AdapterResult]:
        return [r for r in self.results(intake) if r.error]
