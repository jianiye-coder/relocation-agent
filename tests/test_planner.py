from moving_agent.models import Intake
from moving_agent.planner import build_plans, gather_offers
from moving_agent.providers.catalog import SampleCatalog, labor_hours

from .conftest import next_weekday

catalog = SampleCatalog()


def plans_for(intake: Intake):
    return build_plans(intake, gather_offers(intake, [catalog]))


def test_finds_truck_and_movers_within_budget(intake):
    plans = plans_for(intake)
    assert plans, "expected at least one plan"
    best = plans[0]
    assert best.within_budget
    assert best.total_usd <= intake.budget_usd
    assert {o.service for o in best.offers} == {"truck", "labor"}
    assert best.total_usd == round(sum(o.price_usd for o in best.offers), 2)
    # Cheapest within budget comes first.
    within = [p.total_usd for p in plans if p.within_budget]
    assert within == sorted(within)


def test_truck_is_big_enough(intake):
    for plan in plans_for(intake):
        truck = next(o for o in plan.offers if o.service == "truck")
        cap = next(t["capacity_cuft"] for t in catalog.data["trucks"] if t["title"] == truck.title and t["provider"] == truck.provider)
        assert cap >= intake.volume


def test_truck_and_movers_on_same_day(intake):
    for plan in plans_for(intake):
        days = {o.available_on for o in plan.offers if o.service != "storage"}
        assert len(days) == 1


def test_respects_crew_availability_and_flexible_dates(intake):
    # Ana's crew only works weekends. On a Wednesday with no flexibility she can't be booked.
    wednesday = intake.model_copy(update={"move_date": next_weekday(2), "flexible_days": 0})
    offers = gather_offers(wednesday, [catalog])["labor"]
    assert all("Ana" not in o.title for o in offers)
    # With 3 flexible days she's offered on the weekend instead.
    flexible = wednesday.model_copy(update={"flexible_days": 3})
    ana = [o for o in gather_offers(flexible, [catalog])["labor"] if "Ana" in o.title]
    assert ana and ana[0].available_on.weekday() in (5, 6)


def test_storage_priced_for_months(intake):
    storing = intake.model_copy(update={"needs": ["storage"], "storage_months": 3, "volume_cuft": 350})
    offers = gather_offers(storing, [catalog])["storage"]
    assert offers
    extra = next(o for o in offers if o.provider == "Extra Space Storage")
    public = next(o for o in offers if o.provider == "Public Storage")
    # 5x10 at $79: first month 50% off, then 2 full months.
    assert extra.price_usd == round(79 * 0.5 + 79 * 2, 2)
    assert public.price_usd == 129 * 3


def test_long_distance_uses_one_way_pricing(intake):
    far = intake.model_copy(update={"distance_miles": 2130, "to_zip": "94110", "budget_usd": 6000})
    plans = plans_for(far)
    truck = next(o for o in plans[0].offers if o.service == "truck")
    assert "one-way" in truck.price_basis


def test_over_budget_reports_gap(intake):
    tight = intake.model_copy(update={"budget_usd": 50})
    plans = plans_for(tight)
    assert plans and not plans[0].within_budget
    assert plans[0].over_budget_by == round(plans[0].total_usd - 50, 2)


def test_stairs_add_labor_time(intake):
    ground = intake.model_copy(update={"from_floor": 1, "to_floor": 1})
    assert labor_hours(intake, 2) > labor_hours(ground, 2)


def test_no_plan_when_a_service_has_no_offers(intake):
    huge = intake.model_copy(update={"volume_cuft": 3000, "needs": ["truck"]})
    assert plans_for(huge) == []
