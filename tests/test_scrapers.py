"""Unofficial website adapters (Public Storage, U-Haul, Budget), run against trimmed page captures,
and the rule that real prices outrank sample data."""

import asyncio
import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from moving_agent.adapters import (
    AdapterError, AdapterMetadata, BudgetTruckAdapter, Capability, ErrorCode, PriceKind, PublicStorageAdapter, Quote,
    QuoteAdapter, Registry, RegistrySource, ServiceType, SourceKind, UHaulAdapter, default_registry,
)
from moving_agent.adapters.base import CONFIDENCE_CAP, Location, MoveRequest, now

FIXTURES = Path(__file__).parent / "fixtures" / "scrapers"
TOKEN_PAGE = '<html><form><input name="__RequestVerificationToken" type="hidden" value="tok-123" /></form></html>'
SCRAPERS = {"public_storage", "uhaul", "budget_truck"}


def move(**kw) -> MoveRequest:
    base = dict(origin=Location(zip="90012"), destination=Location(zip="94110"), move_date=date(2026, 10, 14),
                distance_miles=382, volume_cuft=450, weight_lbs=3150, storage_months=2)
    base.update(kw)
    return MoveRequest(**base)


def run(adapter_cls, handler, req=None):
    adapter = adapter_cls(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return asyncio.run(adapter.fetch_quotes(req or move()))


def html(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})


def assert_contract(quotes, adapter_id, service):
    for q in quotes:
        assert q.adapter_id == adapter_id and q.service_type == service
        assert q.source and q.fetched_at.tzinfo is not None and q.price_basis
        assert 0 < q.confidence <= CONFIDENCE_CAP[q.price_kind]
        assert "Not reserved" in q.price_basis or q.service_type == ServiceType.storage


def test_scrapers_are_registered_but_off_until_opted_in(monkeypatch):
    monkeypatch.delenv("ENABLE_UNOFFICIAL_ADAPTERS", raising=False)
    assert not SCRAPERS & {a.metadata.id for a in default_registry().active()}
    monkeypatch.setenv("ENABLE_UNOFFICIAL_ADAPTERS", "uhaul,budget_truck")
    assert {"uhaul", "budget_truck"} <= {a.metadata.id for a in default_registry().active()}


# ---- Public Storage ----

def public_storage_handler(seen):
    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "api.zippopotam.us":
            return httpx.Response(200, json={"places": [{"place name": "San Francisco", "state abbreviation": "CA"}]})
        return html((FIXTURES / "public_storage_city.html").read_text())
    return handler


def test_public_storage_picks_the_cheapest_fitting_unit_per_facility():
    seen = []
    quotes = run(PublicStorageAdapter, public_storage_handler(seen))
    assert seen == ["https://api.zippopotam.us/us/94110", "https://www.publicstorage.com/self-storage-ca-san-francisco"]
    assert_contract(quotes, "public_storage", ServiceType.storage)
    # 450 cu ft at 8 ft high needs >= 56.25 sq ft. Per facility the cheapest unit that fits wins
    # (5'x15' at $69 beats 7.5'x10' at $73), then facilities are sorted by price, 2 months each.
    assert [(q.title.split(" unit")[0], q.price_usd) for q in quotes] == [
        ("Medium 5'x15'", 138.0), ("Medium 5'x16'", 186.0), ("Medium 7'x10'", 244.0)]
    assert all(q.price_kind == PriceKind.published_rate and q.price_low_usd <= q.price_high_usd for q in quotes)
    assert "not guaranteed" in quotes[0].price_basis and "× 2 mo" in quotes[0].price_basis
    assert quotes[0].source.startswith("https://www.publicstorage.com/self-storage-ca-san-francisco/")


def test_public_storage_uses_the_request_city_without_a_lookup():
    seen = []
    run(PublicStorageAdapter, public_storage_handler(seen), move(destination=Location(zip="94110", city="San Francisco", state="CA")))
    assert seen == ["https://www.publicstorage.com/self-storage-ca-san-francisco"]


def test_public_storage_skips_moves_without_storage():
    seen = []
    assert run(PublicStorageAdapter, public_storage_handler(seen), move(storage_months=0)) == []
    assert seen == []


def test_public_storage_reports_when_no_unit_is_big_enough():
    with pytest.raises(AdapterError) as err:
        run(PublicStorageAdapter, public_storage_handler([]), move(volume_cuft=5000))
    assert err.value.code == ErrorCode.no_coverage


def test_public_storage_unknown_city_is_no_coverage():
    with pytest.raises(AdapterError) as err:
        run(PublicStorageAdapter, lambda r: html("not found", 404), move(destination=Location(zip="94110", city="Nowhere", state="CA")))
    assert err.value.code == ErrorCode.no_coverage


# ---- U-Haul ----

def uhaul_handler(captured, rates=None, search=None):
    def handler(request):
        if request.method == "POST":
            captured["form"] = parse_qs(request.content.decode())
            return search or httpx.Response(200, json={"redirect": "https://www.uhaul.com/Reservations/RatesTrucks/"})
        if request.url.path.startswith("/Reservations/"):
            return rates or html((FIXTURES / "uhaul_rates.html").read_text())
        return html(TOKEN_PAGE)
    return handler


def test_uhaul_submits_the_rate_form_and_reads_the_truck_list():
    captured = {}
    quotes = run(UHaulAdapter, uhaul_handler(captured))
    form = captured["form"]
    assert form["PickupLocation"] == ["90012"] and form["DropoffLocation"] == ["94110"]
    assert form["PickupDate"] == ["10/14/2026"] and form["__RequestVerificationToken"] == ["tok-123"]
    assert_contract(quotes, "uhaul", ServiceType.truck_rental)
    # The 10' truck (402 cu ft) is too small for 450 cu ft; the disabled pickup truck is skipped.
    assert [(q.title, q.price_usd) for q in quotes] == [("15' Truck one-way", 455.0), ("20' Truck one-way", 567.0)]
    assert "$450.00 + fees = $455.00" in quotes[0].price_basis and "3 days and 486 miles" in quotes[0].price_basis
    assert quotes[0].price_kind == PriceKind.firm_quote


def test_uhaul_skips_local_moves_without_requests():
    with pytest.raises(AdapterError) as err:
        run(UHaulAdapter, lambda r: pytest.fail("no request expected"), move(distance_miles=12))
    assert err.value.code == ErrorCode.no_coverage


@pytest.mark.parametrize("response, code", [
    (html("Forbidden", 403), ErrorCode.blocked),
    (html("<html>Please complete the CAPTCHA to continue</html>"), ErrorCode.blocked),
    (httpx.Response(429), ErrorCode.rate_limited),
    (httpx.Response(503), ErrorCode.unavailable),
])
def test_uhaul_failures_are_typed_and_bot_checks_are_not_bypassed(response, code):
    with pytest.raises(AdapterError) as err:
        run(UHaulAdapter, uhaul_handler({}, rates=response))
    assert err.value.code == code


def test_uhaul_rejected_search_is_invalid_request():
    with pytest.raises(AdapterError) as err:
        run(UHaulAdapter, uhaul_handler({}, search=httpx.Response(200, json={"errors": ["bad location"]})))
    assert err.value.code == ErrorCode.invalid_request


def test_uhaul_missing_token_means_the_layout_changed():
    with pytest.raises(AdapterError) as err:
        run(UHaulAdapter, lambda r: html("<html><body>new layout</body></html>"))
    assert err.value.code == ErrorCode.unavailable


# ---- Budget ----

def budget_handler(captured, search_body=""):
    def handler(request):
        path = request.url.path
        if path.endswith("GetReservationObj"):
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json={"PickUpLocation": None, "DropOffLocation": None, "IsFlexible": True})
        if path.endswith("SearchReservation"):
            captured["search"] = json.loads(request.content)
            return httpx.Response(200, text=search_body, headers={"content-type": "application/json"})
        if path.endswith("GetTrucks"):
            captured["trucks_params"] = dict(request.url.params)
            return httpx.Response(200, json=json.loads((FIXTURES / "budget_trucks.json").read_text()))
        return html(TOKEN_PAGE)
    return handler


def test_budget_runs_the_site_search_and_reads_available_trucks():
    captured = {}
    quotes = run(BudgetTruckAdapter, budget_handler(captured))
    assert captured["headers"]["requestverificationtoken"] == "tok-123"
    search = captured["search"]
    assert (search["PickUpLocation"], search["DropOffLocation"], search["PickupDate"]) == ("90012", "94110", "10/14/2026")
    assert captured["trucks_params"]["pickupDate"] == "10/14/2026"
    assert_contract(quotes, "budget_truck", ServiceType.truck_rental)
    # Sold-out cargo van and the 443 cu ft 12' truck are skipped for 450 cu ft.
    assert [(q.title, q.price_usd) for q in quotes] == [("16' Moving Truck one-way", 496.0), ("26' Moving Truck one-way", 811.0)]
    assert "3 days and 482 miles" in quotes[0].price_basis and "658 cu ft" in quotes[0].price_basis
    assert quotes[0].available_on == date(2026, 10, 14)


def test_budget_validation_errors_are_reported():
    body = json.dumps({"IsValid": False, "PickupLocationError": "Please enter a valid U.S. city, state or zip code."})
    with pytest.raises(AdapterError) as err:
        run(BudgetTruckAdapter, budget_handler({}, search_body=body))
    assert err.value.code == ErrorCode.invalid_request and "valid U.S. city" in err.value.message


def test_budget_block_is_reported_not_retried():
    with pytest.raises(AdapterError) as err:
        run(BudgetTruckAdapter, lambda r: html("Forbidden", 403))
    assert err.value.code == ErrorCode.blocked


# ---- real prices outrank sample data ----

class FixedAdapter(QuoteAdapter):
    def __init__(self, adapter_id, kind, price):
        self.metadata = AdapterMetadata(id=adapter_id, name=adapter_id, capabilities=[Capability.quotes],
                                        service_types=[ServiceType.truck_rental], source_kind=SourceKind.sample)
        self.kind, self.price = kind, price

    async def fetch_quotes(self, req):
        return [Quote(adapter_id=self.metadata.id, provider=self.metadata.id, service_type=ServiceType.truck_rental,
                      title="truck", price_usd=self.price, available_on=req.move_date, source="test", fetched_at=now(),
                      confidence=0.2, price_kind=self.kind, price_basis="test")]


def test_sample_prices_drop_out_once_a_real_price_exists(intake):
    real = RegistrySource(Registry([FixedAdapter("sample", PriceKind.sample, 100), FixedAdapter("live", PriceKind.firm_quote, 400)]))
    assert [o.provider for o in real.offers(intake, "truck")] == ["live"]
    only_sample = RegistrySource(Registry([FixedAdapter("sample", PriceKind.sample, 100)]))
    assert [o.provider for o in only_sample.offers(intake, "truck")] == ["sample"]
