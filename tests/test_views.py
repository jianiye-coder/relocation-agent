"""Plan-page view models: option comparison and the phased timeline."""

from datetime import date, timedelta

from moving_agent.models import Offer, Plan
from moving_agent.timeline import Task, build
from moving_agent.web.views import option_views, price_notes, timeline_view

MOVE = date(2026, 9, 30)


def offer(service, provider, title, price, rating, day=MOVE):
    return Offer(id=f"{service}-{provider}", provider=provider, service=service, title=title, price_usd=price,
                 available_on=day, rating=rating, price_basis="basis", source="sample")


def plan(offers, budget=1500):
    total = round(sum(o.price_usd for o in offers), 2)
    return Plan(offers=offers, move_date=offers[0].available_on, total_usd=total, within_budget=total <= budget,
                over_budget_by=0.0 if total <= budget else round(total - budget, 2), reason="r")


def test_options_show_cost_timing_included_and_tradeoffs():
    rec = plan([offer("truck", "Budget Truck", "12 ft truck", 761.25, 3.8), offer("labor", "HireAHelper", "2-person crew", 480, 4.6)])
    alt = plan([offer("truck", "U-Haul", "10 ft truck", 797.50, 4.0), offer("labor", "HireAHelper", "2-person crew", 480, 4.6)])
    late = plan([offer("truck", "Budget Truck", "12 ft truck", 761.25, 3.8, MOVE + timedelta(days=2)),
                 offer("labor", "TaskRabbit", "2 movers", 440, 4.9, MOVE + timedelta(days=2))])
    views = option_views([rec, alt, late], budget=1500, requested_date=MOVE)

    r, a, l = views
    assert r["label"] == "Recommended" and r["name"] == "Budget Truck + HireAHelper"
    assert r["total"] == "$1,241" and r["tradeoffs"] == [] and "under your $1,500 budget" in r["budget_note"]
    assert r["date_note"] == "Your date"

    assert a["delta"] == "+$36"
    assert a["tradeoffs"][0] == "$36 more than the recommended option"
    assert "Truck: U-Haul 10 ft truck instead of Budget Truck 12 ft truck (rated 4.0 vs. 3.8)" in a["tradeoffs"]
    assert [i["different"] for i in a["included"]] == [True, False]

    assert l["delta"] == "−$40" and l["date_note"] == "2 days later than you asked"
    assert "Moves Fri Oct 2 instead of Wed Sep 30" in l["tradeoffs"]
    assert "Lowest price" in l["highlights"] and "Highest rated" in l["highlights"]


def test_option_included_services_keep_provider_links():
    rec = plan([offer("truck", "Budget Truck", "12 ft truck", 761.25, 3.8)])
    rec.offers[0].contact_url = "https://www.budgettruck.com/"
    included = option_views([rec], budget=1500, requested_date=MOVE)[0]["included"]
    assert included[0]["provider_url"] == "https://www.budgettruck.com/"


def test_over_budget_note():
    v = option_views([plan([offer("truck", "Budget Truck", "12 ft", 761.25, 3.8)], budget=90)], 90, MOVE)[0]
    assert not v["within_budget"] and v["budget_note"] == "$671 over your $90 budget"


def test_timeline_phases_today_marker_and_states():
    tasks = build(MOVE, lease_end=MOVE - timedelta(days=1), today=date(2026, 9, 24))
    view = timeline_view(tasks, MOVE, today=date(2026, 9, 24))
    titles = [p["title"] for p in view["phases"]]
    assert titles == ["Plan and book", "Get ready", "Final week", "Move day", "After the move"]
    flat = [i for p in view["phases"] for i in p["items"]]
    today_at = next(n for n, i in enumerate(flat) if i["kind"] == "today")
    assert all(i["due"] <= date(2026, 9, 24) for i in flat[:today_at])
    assert all(i["due"] > date(2026, 9, 24) for i in flat[today_at + 1:])
    assert view["phases"][3]["items"][0]["state"] == "move"
    assert view["phases"][3]["items"][0]["when"] == ""  # the title already says "Move day"
    assert view["overdue"] == sum(1 for i in flat if i.get("state") == "overdue") > 0
    assert view["days_left"] == 6


def test_today_marker_at_end_when_everything_is_past():
    tasks = build(MOVE, today=date(2026, 12, 1))
    flat = [i for p in timeline_view(tasks, MOVE, today=date(2026, 12, 1))["phases"] for i in p["items"]]
    assert flat[-1]["kind"] == "today"


def test_price_notes_match_where_prices_came_from():
    sample = plan([offer("truck", "U-Haul", "10 ft truck", 400, 4.0)])
    live = plan([offer("truck", "Budget", "12' truck", 427, None).model_copy(
        update={"price_kind": "firm_quote", "source": "https://www.budgettruck.com/"})])
    assert price_notes(option_views([sample], 1500, MOVE)) == [
        "Sample prices are illustrative until live provider adapters are connected."]
    notes = price_notes(option_views([live], 1500, MOVE))
    assert len(notes) == 1 and "confirm on their site before you book" in notes[0]
    assert len(price_notes(option_views([live, sample], 1500, MOVE))) == 2
