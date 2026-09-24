"""Inventory estimator, true cost, timeline and listing checks."""

import re
from datetime import date, timedelta

from fastapi.testclient import TestClient

from moving_agent import listings, timeline, truecost
from moving_agent.inventory import estimate
from moving_agent.models import HomeSize


def test_inventory_rooms_items_and_special():
    inv = estimate("2 bedrooms\nliving room\nkitchen\nupright piano\nboxes x 20\nkayak")
    assert inv.total_cuft > 400 and inv.total_lbs >= inv.total_cuft * 7 - 1
    assert inv.special_items == ["upright piano"] and inv.unknown == ["kayak"]
    piano = next(l for l in inv.lines if l.item == "upright piano")
    assert piano.lbs == 500  # heavier than the 7 lb/cu ft default


def test_inventory_quantity_formats():
    for text in ["2 sofas", "sofa x2", "two sofa", "sofa: 2"]:
        assert estimate(text).lines[0].qty == 2, text


def test_true_cost_with_rent_and_deposit_cap():
    tc = truecost.compute(None, HomeSize.one_bed, monthly_rent=3000, vehicle_cost=120, vehicle_basis="drive")
    items = {l.item: l for l in tc.lines}
    assert items["Security deposit"].amount_usd == 3000 and "AB 12" in items["Security deposit"].basis
    assert tc.due_before_move_in_usd == 3000 + 3000 + 150
    assert tc.total_usd == round(sum(l.amount_usd for l in tc.lines), 2)


def test_timeline_backward_and_lease_gap():
    move = date.today() + timedelta(days=30)
    tasks = timeline.build(move, lease_end=move - timedelta(days=5), has_vehicle=True, special_items=["upright piano"])
    titles = [t.title for t in tasks]
    assert tasks == sorted(tasks, key=lambda t: t.due)
    assert any("notice" in t for t in titles) and any("Gap between lease end" in t for t in titles)
    assert any("DMV" in t for t in titles) and any("piano" in t for t in titles)
    assert any(t.overdue for t in tasks)  # 42 days before a move 30 days out is already past


def test_listing_scam_signals():
    bad = listings.check("Lovely 2BR. I'm out of the country, wire the deposit to hold it and I'll mail the keys.", 1500, 2)
    assert bad.risk == "high" and {f.kind for f in bad.flags} >= {"scam_language", "below_market", "missing_info"}
    ok = listings.check("Sunny 1BR at 2000 Mission St, tours Saturday, apply online.", 3100, 1)
    assert ok.risk == "low" and not ok.flags


def test_listing_duplicates():
    a = "Sunny 1BR at 2000 Mission St with bay windows, hardwood floors, laundry in unit, tours Saturday."
    assert any(f.kind == "duplicate" for f in listings.check(a + " Call now.", other_listings=[a]).flags)


def test_listing_check_page():
    from moving_agent.web import app as web
    r = TestClient(web.app).post("/listing-check", data={"text": "Send the deposit by Zelle to hold it, no viewings", "price_usd": "1200", "bedrooms": "1"})
    assert r.status_code == 200 and "High risk" in r.text


def test_plan_page_shows_inventory_vehicle_cost_timeline(intake):
    from moving_agent.web import app as web
    from .test_web import form
    data = form(intake, inventory_text="bedroom\nliving room\nupright piano", vehicles=["car"], monthly_rent="3000",
                lease_end=(intake.move_date - timedelta(days=3)).isoformat())
    r = TestClient(web.app).post("/plan", data=data)
    assert r.status_code == 200
    for text in ["What you're moving", "Special items", "ship or drive", "True cost", "Security deposit", "Timeline", "confidence"]:
        assert text in r.text, text


def test_plan_steps_render_as_separate_pages(intake):
    from moving_agent.web import app as web
    from .test_web import form

    client = TestClient(web.app)
    r = client.post("/plan", data=form(intake, inventory_text="desk", items_to_sell="desk"))
    rid = re.search(r"/plan/(\w+)", str(r.url)).group(1)
    home = client.get(f"/plan/{rid}/home")
    sell = client.get(f"/plan/{rid}/sell")
    timeline_page = client.get(f"/plan/{rid}/timeline")
    assert home.status_code == sell.status_code == timeline_page.status_code == 200
    assert "Your options" in home.text and "Timeline" not in home.text
    assert "What you're moving" in sell.text and "Listings for things you're selling" in sell.text
    assert "Timeline" in timeline_page.text and "Your options" not in timeline_page.text
