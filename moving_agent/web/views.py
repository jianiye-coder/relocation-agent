"""View models for the plan page: compare options at a glance and group the timeline into phases.

Kept out of the template so the comparison logic (deltas, tradeoffs, phases, where "today" falls)
is plain Python with tests.
"""

from __future__ import annotations

from datetime import date

from ..models import Offer, Plan
from ..timeline import Task

SERVICE_LABELS = {"truck": "Truck", "labor": "Movers", "storage": "Storage", "container": "Container"}


def _money(x: float) -> str:
    return f"${x:,.0f}"


def _day(d: date) -> str:
    return f"{d:%a %b} {d.day}"


def _avg_rating(plan: Plan) -> float | None:
    ratings = [o.rating for o in plan.offers if o.rating]
    return round(sum(ratings) / len(ratings), 1) if ratings else None


def _date_note(move_day: date, requested: date) -> str:
    shift = (move_day - requested).days
    if shift == 0:
        return "Your date"
    return f"{abs(shift)} day{'s' if abs(shift) != 1 else ''} {'later' if shift > 0 else 'earlier'} than you asked"


def _service(o: Offer) -> str:
    return SERVICE_LABELS.get(o.service, o.service.capitalize())


def _tradeoffs(plan: Plan, base: Plan) -> list[str]:
    """How this option differs from the recommended one, in plain words."""
    out = []
    diff = round(plan.total_usd - base.total_usd, 2)
    if abs(diff) >= 1:
        out.append(f"{_money(abs(diff))} {'more' if diff > 0 else 'less'} than the recommended option")
    base_by_service = {o.service: o for o in base.offers}
    for o in plan.offers:
        b = base_by_service.get(o.service)
        if b and (b.provider, b.title) != (o.provider, o.title):
            change = f"{_service(o)}: {o.provider} {o.title} instead of {b.provider} {b.title}"
            if o.rating and b.rating and o.rating != b.rating:
                change += f" (rated {o.rating} vs. {b.rating})"
            out.append(change)
    if plan.move_date != base.move_date:
        out.append(f"Moves {_day(plan.move_date)} instead of {_day(base.move_date)}")
    return out


def option_views(plans: list[Plan], budget: int, requested_date: date) -> list[dict]:
    """One dict per option, recommended first (the caller already put the chosen plan first)."""
    if not plans:
        return []
    base = plans[0]
    cheapest = min(p.total_usd for p in plans)
    ratings = [r for r in (_avg_rating(p) for p in plans) if r is not None]
    best_rating = max(ratings) if ratings else None
    views = []
    for i, p in enumerate(plans):
        rating = _avg_rating(p)
        base_keys = {(o.service, o.provider, o.title) for o in base.offers}
        highlights = []
        if p.total_usd == cheapest:
            highlights.append("Lowest price")
        if rating is not None and rating == best_rating and len(plans) > 1:
            highlights.append("Highest rated")
        views.append({
            "anchor": f"plan-{i}",
            "recommended": i == 0,
            "label": "Recommended" if i == 0 else f"Option {i + 1}",
            "name": " + ".join(o.provider for o in p.offers),
            "total": _money(p.total_usd),
            "delta": "" if i == 0 else (
                f"+{_money(p.total_usd - base.total_usd)}" if p.total_usd > base.total_usd
                else f"−{_money(base.total_usd - p.total_usd)}" if p.total_usd < base.total_usd else "same price"),
            "within_budget": p.within_budget,
            "budget_note": (f"{_money(budget - p.total_usd)} under your {_money(budget)} budget" if p.within_budget
                            else f"{_money(p.over_budget_by)} over your {_money(budget)} budget"),
            "move_day": _day(p.move_date),
            "date_note": _date_note(p.move_date, requested_date),
            "rating": rating,
            "highlights": highlights,
            "tradeoffs": [] if i == 0 else _tradeoffs(p, base),
            "included": [{
                "service": o.service,
                "service_label": _service(o),
                "provider": o.provider,
                "provider_url": o.contact_url,
                "title": o.title,
                "price": f"${o.price_usd:,.2f}",
                "rating": o.rating,
                "different": i > 0 and (o.service, o.provider, o.title) not in base_keys,
                "basis": o.price_basis,
                "kind": o.price_kind or "estimate",
                "confidence": o.confidence,
                "source": o.source,
                "fetched_at": o.fetched_at,
            } for o in p.offers],
        })
    return views


PHASES = [  # (title, what it's for, lowest days-before-move that belongs here)
    ("Plan and book", "3+ weeks before", 21),
    ("Get ready", "1–3 weeks before", 8),
    ("Final week", "the last 7 days", 1),
    ("Move day", "", 0),
    ("After the move", "", None),
]


def _phase(days_before: int) -> int:
    if days_before >= 21:
        return 0
    if days_before >= 8:
        return 1
    if days_before >= 1:
        return 2
    if days_before == 0:
        return 3
    return 4


def timeline_view(tasks: list[Task], move_date: date, today: date | None = None) -> dict:
    """Group tasks into phases and place a 'today' marker at its chronological position."""
    today = today or date.today()
    groups: list[list[dict]] = [[] for _ in PHASES]
    for t in sorted(tasks, key=lambda t: t.due):
        days_before = (move_date - t.due).days
        state = "move" if t.due == move_date else "overdue" if t.overdue else "today" if t.due == today else "upcoming"
        groups[_phase(days_before)].append({
            "kind": "task", "due": t.due, "state": state, "title": t.title, "detail": t.detail, "source": t.source,
            "month": f"{t.due:%b}".upper(), "day": t.due.day, "weekday": f"{t.due:%a}",
            "when": ("" if days_before == 0 else f"{days_before} days before" if days_before > 1
                     else "1 day before" if days_before == 1 else f"{-days_before} day{'s' if days_before != -1 else ''} after"),
        })

    # The today marker goes before the first task that is due later than today.
    marker = {"kind": "today", "label": f"Today · {_day(today)}"}
    placed = False
    for g, items in enumerate(groups):
        for i, item in enumerate(items):
            if item["due"] > today:
                items.insert(i, marker)
                placed = True
                break
        if placed:
            break
    if not placed and any(groups):
        last = max(g for g, items in enumerate(groups) if items)
        groups[last].append(marker)

    phases = []
    for (title, subtitle, _), items in zip(PHASES, groups):
        real = [i for i in items if i["kind"] == "task"]
        if not real:
            continue
        phases.append({"title": title, "subtitle": subtitle, "items": items,
                       "overdue": sum(i["state"] == "overdue" for i in real)})
    days_left = (move_date - today).days
    return {
        "phases": phases,
        "overdue": sum(p["overdue"] for p in phases),
        "move_day": _day(move_date),
        "days_left": days_left,
    }
