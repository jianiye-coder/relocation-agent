"""Finds the best combination of offers for the user's needs, dates and budget.

This is plain code on purpose: prices and totals must be exact and explainable,
so the LLM never does this math.
"""

from __future__ import annotations

from itertools import product

from .models import Intake, Offer, Plan, Service


class OfferSource:
    """Anything that can price offers for a move (sample catalog or live API)."""

    def offers(self, intake: Intake, service: Service) -> list[Offer]:  # pragma: no cover - protocol
        raise NotImplementedError


def gather_offers(intake: Intake, sources: list[OfferSource]) -> dict[Service, list[Offer]]:
    found: dict[Service, list[Offer]] = {}
    for service in intake.needs:
        found[service] = [o for src in sources for o in src.offers(intake, service)]
    return found


def _same_day(offers: tuple[Offer, ...], intake: Intake) -> bool:
    """Truck/container and labor must happen on the same day; storage starts that day."""
    days = {o.available_on for o in offers if o.service != "storage"}
    return len(days) <= 1


def build_plans(intake: Intake, offers: dict[Service, list[Offer]], limit: int = 3) -> list[Plan]:
    """Every combination with one offer per needed service, ranked cheapest first.

    Plans within budget come first. If nothing fits the budget, the cheapest
    plans are returned with how far over budget they are.
    """
    if any(not offers.get(s) for s in intake.needs):
        return []

    combos = []
    for combo in product(*(offers[s] for s in intake.needs)):
        if not _same_day(combo, intake):
            continue
        total = round(sum(o.price_usd for o in combo), 2)
        avg_rating = sum(o.rating or 0 for o in combo) / len(combo)
        combos.append((total, -avg_rating, combo))

    combos.sort(key=lambda c: (c[0] > intake.budget_usd, c[0], c[1]))

    plans = []
    for total, neg_rating, combo in combos[:limit]:
        move_day = next((o.available_on for o in combo if o.service != "storage"), intake.move_date)
        within = total <= intake.budget_usd
        shift = (move_day - intake.move_date).days
        when = "on your move date" if shift == 0 else f"{abs(shift)} day(s) {'after' if shift > 0 else 'before'} your move date"
        if within:
            reason = f"${total:,.0f} total, ${intake.budget_usd - total:,.0f} under budget, {when}; average rating {-neg_rating:.1f}"
        else:
            reason = f"Cheapest option found, but ${total - intake.budget_usd:,.0f} over budget ({when})"
        plans.append(
            Plan(
                offers=list(combo),
                move_date=move_day,
                total_usd=total,
                within_budget=within,
                over_budget_by=0.0 if within else round(total - intake.budget_usd, 2),
                reason=reason,
            )
        )
    return plans
