"""End-to-end feature integration on recorded fixtures: candidate homes, photos, Warp.

Runs in CI (see .github/workflows/tests.yml) with no network and no credentials.
"""
import asyncio
import json
import re
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from moving_agent import home, photo_inventory
from moving_agent.adapters.warp import WarpLTLAdapter
from moving_agent.adapters.base import AdapterError, ErrorCode
from moving_agent.adapters import request_from_intake
from moving_agent.models import Intake
from moving_agent.web import app as web

ROOT = Path(__file__).resolve().parents[1]


def fixture(path):
    return json.loads((ROOT / "tests/fixtures" / path).read_text())


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    from moving_agent.adapters import Registry, RegistrySource, SampleCatalogAdapter, VehicleEstimateAdapter
    monkeypatch.setattr(web, "model_configured", lambda: False)
    monkeypatch.setattr(web, "SOURCES", [RegistrySource(Registry([SampleCatalogAdapter(), VehicleEstimateAdapter()]))])
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "fixture")
    monkeypatch.setenv("WARP_API_KEY", "fixture")
    def blocked(*args, **kwargs):
        raise AssertionError("Real HTTP forbidden in integration fixtures")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)


def test_two_homes_with_sourced_commutes(monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=fixture("routes/success.json"))))
    real_commute = home.commute
    monkeypatch.setattr(home, "commute", lambda origin, dest, mode, departure=None: real_commute(origin, dest, mode, client=client, departure=departure))
    profile = fixture("intake/la_to_sf.json")
    profile.update(pets="cat")
    browser = TestClient(web.app)
    response = browser.post("/plan", data=profile)
    rid = re.search(r"/plan/(\w+)", str(response.url)).group(1)
    for address in ("1 Main St, San Francisco, CA", "2 Oak St, San Francisco, CA"):
        response = browser.post(f"/housing/{rid}/select", data={
            "address": address, "commute_destination": "1 Market St, San Francisco, CA", "commute_mode": "drive",
        })
    assert response.status_code == 200
    assert response.text.count("30 min") == 2
    assert "Internet availability is not configured" in response.text
    client.close()


def test_extracted_photo_fixture_is_editable_and_deterministic(monkeypatch):
    async def extracted(images, model):
        return [photo_inventory.DetectedItem(**item) for item in fixture("inventory/detected.json")["items"]]
    monkeypatch.setattr(photo_inventory, "_run_vision", extracted)
    image = BytesIO()
    Image.new("RGB", (2, 2)).save(image, format="JPEG")
    result = asyncio.run(photo_inventory.analyze([("fixture.jpg", "image/jpeg", image.getvalue())], "fixture"))
    assert result.editable_text == "sofa\nupright piano"
    assert result.estimate.total_cuft == 105 and result.estimate.total_lbs == 745


def test_warp_response_fixture():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=fixture("warp/success.json")))) as client:
            return await WarpLTLAdapter(client=client).fetch_quotes(request_from_intake(Intake(**fixture("intake/la_to_sf.json"))))
    quotes = asyncio.run(run())
    assert quotes[0].price_usd == 247.5 and "PRICING_fixture" in quotes[0].price_basis


@pytest.mark.parametrize("status,expected", [(401, ErrorCode.blocked), (429, ErrorCode.rate_limited), (503, ErrorCode.unavailable)])
def test_warp_failure_has_no_fabricated_quote(status, expected):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(status))) as client:
            return await WarpLTLAdapter(client=client).fetch_quotes(request_from_intake(Intake(**fixture("intake/la_to_sf.json"))))
    with pytest.raises(AdapterError) as error:
        asyncio.run(run())
    assert error.value.code == expected
