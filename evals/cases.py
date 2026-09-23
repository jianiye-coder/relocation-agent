"""LA -> SF eval cases. Each case is an intake profile plus what a good agent must do."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field
from pydantic_evals import Case

from moving_agent.models import Intake

LA_TO_SF = dict(
    name="Eval User", email="eval@example.com",
    from_address="200 N Spring St, Los Angeles, CA", to_address="2000 Mission St, San Francisco, CA",
    from_zip="90012", to_zip="94110", distance_miles=382,
)
WEDNESDAY, SATURDAY = date(2026, 10, 14), date(2026, 10, 17)


class Expect(BaseModel):
    """What the case checks. Unset fields aren't checked."""

    plan_within_budget: bool | None = None       # True: the chosen plan must fit; False: must honestly be over
    excluded_providers: list[str] = Field(default_factory=list)
    email_mentions: list[str] = Field(default_factory=list)  # words every drafted email must contain
    must_try_alternatives: bool = False           # the agent must call what_if at least once
    no_plan_possible: bool = False                # the agent must not claim a plan it can't build
    move_on_weekday: bool = False
    full_picture: bool = False                    # must compute true cost (with deposit) and a timeline


def case(name: str, expect: Expect, notes: str = "", **intake) -> Case[Intake, dict, Expect]:
    return Case(name=name, inputs=Intake(**LA_TO_SF, notes=notes, **intake), metadata=expect)


CASES = [
    case("studio_within_budget",
         Expect(plan_within_budget=True),
         home_size="studio", move_date=WEDNESDAY, needs=["truck", "labor"], budget_usd=1200),
    case("piano_no_taskrabbit_weekend",
         Expect(plan_within_budget=True, excluded_providers=["TaskRabbit"], email_mentions=["piano"],
                must_try_alternatives=True, move_on_weekday=True),
         notes="I have an upright piano. Please don't use TaskRabbit.",
         home_size="1br", from_floor=3, move_date=SATURDAY, flexible_days=3, needs=["truck", "labor"], budget_usd=1600),
    case("budget_cannot_be_met",
         Expect(plan_within_budget=False, must_try_alternatives=True),
         home_size="2br", move_date=WEDNESDAY, needs=["truck", "labor", "storage"], storage_months=2, budget_usd=1500),
    case("container_move",
         Expect(plan_within_budget=True),
         home_size="1br", move_date=WEDNESDAY, needs=["container", "labor"], budget_usd=1600),
    case("car_and_rent_full_picture",
         Expect(plan_within_budget=True, full_picture=True),
         notes="I'm bringing my car. Rent at the new place will be about $3,000.",
         home_size="1br", move_date=WEDNESDAY, needs=["truck", "labor"], budget_usd=1600,
         vehicles=["car"], monthly_rent=3000, lease_end=date(2026, 10, 12)),
    case("weekend_no_labor_no_flex",
         Expect(excluded_providers=["TaskRabbit"], no_plan_possible=True),
         notes="Don't use TaskRabbit. The date is fixed.",
         home_size="1br", move_date=SATURDAY, flexible_days=0, needs=["truck", "labor"], budget_usd=3000),
]
