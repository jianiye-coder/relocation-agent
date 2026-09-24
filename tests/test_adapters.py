"""The adapter contract, the registry (cache, errors, opt-in) and each adapter."""

import asyncio
import re
import json
from datetime import date, timedelta
from math import ceil
from pathlib import Path

import httpx
import pytest

from moving_agent.adapters import (
    AdapterError, AdapterMetadata, Capability, ErrorCode, FMCSAAdapter, MoveRequest, PriceKind, Quote, QuoteAdapter,
    QuoteCache, Registry, SampleCatalogAdapter, ServiceType, SourceKind, VehicleEstimateAdapter, WarpLTLAdapter,
    default_registry, request_from_intake,
)
from moving_agent.adapters.base import CONFIDENCE_CAP, Location, Vehicle, now

FIXTURES = Path(__file__).parent / "fixtures" / "fmcsa"


def la_to_sf(**kw) -> MoveRequest:
    base = dict(
        origin=Location(zip="90012", city="Los Angeles", state="CA"),
        destination=Location(zip="94110", city="San Francisco", state="CA"),
        move_date=date.today() + timedelta(days=14), flexible_days=3, distance_miles=382,
        volume_cuft=450, weight_lbs=3150, storage_months=1, vehicles=[Vehicle(kind="car")], budget_usd=3500,
    )
    base.update(kw)
    return MoveRequest(**base)


ALL_QUOTE_ADAPTERS = [SampleCatalogAdapter(), VehicleEstimateAdapter(), WarpLTLAdapter()]


# ---- contract: every adapter ----

@pytest.mark.parametrize("adapter", ALL_QUOTE_ADAPTERS + [FMCSAAdapter()], ids=lambda a: a.metadata.id)
def test_metadata_is_complete(adapter):
    m = adapter.metadata
    assert isinstance(m, AdapterMetadata) and m.capabilities and m.cache_ttl_seconds > 0
    if m.auth != "none":
        assert m.auth_env, "adapters that need auth must name the env var"
    if m.source_kind == SourceKind.unofficial_scrape:
        assert not m.enabled_by_default


@pytest.mark.parametrize("adapter", [a for a in ALL_QUOTE_ADAPTERS if a.metadata.source_kind in (SourceKind.sample, SourceKind.public_data)], ids=lambda a: a.metadata.id)
def test_every_quote_carries_source_timestamp_confidence(adapter):
    quotes = asyncio.run(adapter.fetch_quotes(la_to_sf()))
    assert quotes
    for q in quotes:
        assert q.adapter_id == adapter.metadata.id
        assert q.source and q.fetched_at.tzinfo is not None
        assert 0 <= q.confidence <= CONFIDENCE_CAP[q.price_kind]
        assert q.price_basis and q.price_usd >= 0
        assert q.service_type in adapter.metadata.service_types


def test_confidence_cannot_exceed_price_kind():
    with pytest.raises(ValueError, match="too high"):
        Quote(adapter_id="x", provider="p", service_type=ServiceType.storage, title="t", price_usd=1,
              available_on=date.today(), source="s", fetched_at=now(), confidence=0.9,
              price_kind=PriceKind.sample, price_basis="b")


# ---- registry ----

class FlakyAdapter(QuoteAdapter):
    metadata = AdapterMetadata(id="flaky", name="Flaky", capabilities=[Capability.quotes],
                               service_types=[ServiceType.storage], source_kind=SourceKind.official_api, cache_ttl_seconds=60)

    def __init__(self):
        self.fail_with: Exception | None = None
        self.calls = 0

    async def fetch_quotes(self, req):
        self.calls += 1
        if self.fail_with:
            raise self.fail_with
        return [Quote(adapter_id="flaky", provider="P", service_type=ServiceType.storage, title="5x10", price_usd=80,
                      available_on=req.move_date, source="https://example.com", fetched_at=now(), confidence=0.9,
                      price_kind=PriceKind.firm_quote, price_basis="quoted")]


def test_cache_hit_then_stale_on_failure(monkeypatch):
    a, cache = FlakyAdapter(), QuoteCache(":memory:")
    reg = Registry([a], cache=cache)
    first = asyncio.run(reg.quotes(la_to_sf()))[0]
    second = asyncio.run(reg.quotes(la_to_sf()))[0]
    assert not first.from_cache and second.from_cache and a.calls == 1

    a.metadata = a.metadata.model_copy(update={"cache_ttl_seconds": 0})  # everything is expired now
    a.fail_with = httpx.ConnectError("down")
    third = asyncio.run(reg.quotes(la_to_sf()))[0]
    assert third.stale and third.error == ErrorCode.stale and third.quotes


@pytest.mark.parametrize("exc,code", [
    (AdapterError(ErrorCode.no_coverage, "x"), ErrorCode.no_coverage),
    (httpx.ConnectError("x"), ErrorCode.unavailable),
    (httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://a"), response=httpx.Response(429)), ErrorCode.rate_limited),
    (httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://a"), response=httpx.Response(403)), ErrorCode.blocked),
    (asyncio.TimeoutError(), ErrorCode.unavailable),
])
def test_failures_map_to_shared_error_codes(exc, code):
    a = FlakyAdapter()
    a.fail_with = exc
    result = asyncio.run(Registry([a]).quotes(la_to_sf()))[0]
    assert result.error == code and not result.quotes


WARP_RESPONSE = {
    "quote_id": "PRICING_123", "mode": "ltl", "price_usd": 247.50,
    "currency": "USD", "transit_days": 2, "pickup_date": "2026-10-14",
    "delivery_date": "2026-10-16", "expires_at": "2026-10-12T15:30:00Z",
    "quote_tier": "firm",
}


def _warp_quote(monkeypatch, key):
    monkeypatch.setenv("WARP_API_KEY", key)
    captured = {}

    def handler(request):
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=WARP_RESPONSE)

    req = la_to_sf(move_date=date(2026, 10, 14), volume_cuft=121, weight_lbs=1_001)
    adapter = WarpLTLAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return asyncio.run(adapter.fetch_quotes(req))[0], captured


def test_warp_quote_maps_the_documented_request_and_response(monkeypatch):
    quote, captured = _warp_quote(monkeypatch, "wak_live_123")
    assert captured["body"] == {
        "origin_zip": "90012", "destination_zip": "94110", "pickup_date": "2026-10-14",
        "pallets": ceil(121 / 60), "weight_lbs_per_pallet": ceil(1_001 / ceil(121 / 60)),
        "commodity": "household goods", "length_in": 48, "width_in": 40, "height_in": 48,
    }
    assert captured["headers"]["authorization"] == "Bearer wak_live_123"
    assert quote.price_usd == 247.50 and quote.valid_until and quote.available_on == date(2026, 10, 14)
    assert "PRICING_123" in quote.price_basis and quote.source.endswith("/api/v1/ltl/quote")


def test_warp_production_quote_is_firm_but_states_our_assumptions(monkeypatch):
    """Warp's price is real, but our pallet conversion and household/residential fit are assumptions."""
    quote, _ = _warp_quote(monkeypatch, "wak_live_123")
    assert quote.price_kind == PriceKind.firm_quote and quote.confidence <= 0.6
    for caveat in ("60 cu ft", "48 × 40 × 48", "residential", "household"):
        assert caveat in quote.price_basis, caveat
    assert "sandbox" not in quote.price_basis.lower()


@pytest.mark.parametrize("key", ["wak_test_abc", "wak_test"])
def test_warp_sandbox_key_is_labeled_mock_data(monkeypatch, key):
    """A test key returns mock data; it must never look like a real price."""
    quote, _ = _warp_quote(monkeypatch, key)
    assert quote.price_kind == PriceKind.sample and quote.confidence <= 0.3
    assert "sandbox" in quote.title.lower() and "mock data" in quote.price_basis.lower()
    assert not re.search(r"\blive\b", quote.price_basis, re.I)


@pytest.mark.parametrize("status,code", [
    (401, ErrorCode.blocked), (429, ErrorCode.rate_limited), (400, ErrorCode.invalid_request), (503, ErrorCode.unavailable),
])
def test_warp_failures_are_typed(monkeypatch, status, code):
    monkeypatch.setenv("WARP_API_KEY", "wak_test")
    adapter = WarpLTLAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status))))
    with pytest.raises(AdapterError) as error:
        asyncio.run(adapter.fetch_quotes(la_to_sf()))
    assert error.value.code == code


def test_warp_rejects_invalid_response(monkeypatch):
    monkeypatch.setenv("WARP_API_KEY", "wak_test")
    adapter = WarpLTLAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"quote_id": "q"}))))
    with pytest.raises(AdapterError, match="invalid") as error:
        asyncio.run(adapter.fetch_quotes(la_to_sf()))
    assert error.value.code == ErrorCode.unavailable


def test_missing_auth_and_no_coverage():
    class CAOnly(FlakyAdapter):
        metadata = FlakyAdapter.metadata.model_copy(update={"id": "ca_only", "coverage": ["NY"]})
    results = asyncio.run(Registry([WarpLTLAdapter(), CAOnly()]).quotes(la_to_sf()))
    assert [r.error for r in results] == [ErrorCode.auth_missing, ErrorCode.no_coverage]


def test_unofficial_adapters_are_opt_in():
    class Scraper(FlakyAdapter):
        metadata = FlakyAdapter.metadata.model_copy(update={"id": "scraper", "source_kind": SourceKind.unofficial_scrape})
    assert Registry([Scraper()]).active() == []
    assert len(Registry([Scraper()], opt_in_unofficial={"scraper"}).active()) == 1


def test_default_registry_la_to_sf():
    results = {r.adapter_id: r for r in asyncio.run(default_registry().quotes(la_to_sf()))}
    assert results["sample_catalog"].quotes and results["vehicle_estimate"].quotes
    assert results["warp_ltl"].error == ErrorCode.auth_missing


# ---- vehicle ----

def test_ship_vs_drive_la_to_sf():
    quotes = asyncio.run(VehicleEstimateAdapter().fetch_quotes(la_to_sf()))
    ship = next(q for q in quotes if q.service_type == ServiceType.vehicle_shipping)
    drive = next(q for q in quotes if q.service_type == ServiceType.vehicle_drive)
    assert ship.price_usd == 500  # 382 mi x $1.20 = $458, below the $500 minimum
    assert ship.price_low_usd < ship.price_usd < ship.price_high_usd
    assert drive.price_usd < ship.price_usd  # a short drive beats shipping


# ---- FMCSA (recorded fixtures, no network) ----

def fmcsa_with(fixture: str, status: int = 200) -> FMCSAAdapter:
    body = json.loads((FIXTURES / fixture).read_text()) if fixture else {}
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    return FMCSAAdapter(client=httpx.AsyncClient(transport=transport))


def test_fmcsa_needs_a_key():
    with pytest.raises(AdapterError) as e:
        asyncio.run(FMCSAAdapter().check(usdot=1))
    assert e.value.code == ErrorCode.auth_missing


def test_fmcsa_allowed_carrier(monkeypatch):
    monkeypatch.setenv("FMCSA_WEB_KEY", "k")
    c = asyncio.run(fmcsa_with("carrier_ok.json").check(usdot=1234567))
    assert c.found and c.allowed_to_operate and c.legal_name == "EXAMPLE VAN LINES LLC" and not c.notes


def test_fmcsa_flags_not_allowed(monkeypatch):
    monkeypatch.setenv("FMCSA_WEB_KEY", "k")
    c = asyncio.run(fmcsa_with("carrier_not_allowed.json").check(usdot=7654321))
    assert c.found and c.allowed_to_operate is False and "Do not book" in c.notes[0]


def test_fmcsa_docket_lookup_and_not_found(monkeypatch):
    monkeypatch.setenv("FMCSA_WEB_KEY", "k")
    assert asyncio.run(fmcsa_with("docket_list.json").check(mc="MC-1515")).usdot_number == 1234567
    missing = asyncio.run(fmcsa_with("not_found.json").check(usdot=1))
    assert not missing.found and missing.notes


def test_fmcsa_errors(monkeypatch):
    monkeypatch.setenv("FMCSA_WEB_KEY", "k")
    for status, code in [(403, ErrorCode.blocked), (429, ErrorCode.rate_limited), (503, ErrorCode.unavailable)]:
        with pytest.raises(AdapterError) as e:
            asyncio.run(fmcsa_with("", status).check(usdot=1))
        assert e.value.code == code


# ---- intake -> request ----

def test_request_from_intake(intake):
    req = request_from_intake(intake.model_copy(update={"vehicles": ["suv"], "pets": ["cat"]}), "IL", "IL")
    assert req.volume_cuft == intake.volume and req.weight_lbs == intake.volume * 7
    assert req.vehicles[0].kind == "suv" and req.pets == ["cat"] and req.origin_access.floor == 3
