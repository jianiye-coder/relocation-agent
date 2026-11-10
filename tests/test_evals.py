"""The eval harness itself: evaluators on hand-made outputs, and a scripted end-to-end run."""

from types import SimpleNamespace

from evals.cases import CASES, Expect
from evals.evaluators import (BudgetHandling, FullPicture, NoInventedPlan, NoSendWithoutApproval, PricesGrounded,
                              RespectsExclusions, TriedAlternatives, WeekdayMove, _money)


def ctx(output, metadata=Expect(), budget=1500):
    return SimpleNamespace(output=output, metadata=metadata, inputs=SimpleNamespace(budget_usd=budget))


def test_money_parsing():
    assert _money("from $1,855.79 to ~$1,860 or $48/hr") == {1855.79, 1860.0, 48.0}


def test_prices_grounded_catches_invented_numbers():
    out = {"summary": "Total $1,855.79, about $1,860.", "known_amounts": [1855.79]}
    assert PricesGrounded().evaluate(ctx(out))
    out["summary"] += " A mover can do it for $999."
    assert not PricesGrounded().evaluate(ctx(out))


def test_budget_handling_requires_honesty_when_over():
    over = {"chosen_plan": {"total_usd": 1855.79}, "summary": "It's $355 over your budget."}
    assert BudgetHandling().evaluate(ctx(over, Expect(plan_within_budget=False)))
    hidden = {"chosen_plan": {"total_usd": 1855.79}, "summary": "Here's your plan."}
    assert not BudgetHandling().evaluate(ctx(hidden, Expect(plan_within_budget=False)))


def test_exclusions_and_invented_plans():
    out = {"chosen_providers": ["TaskRabbit"], "emails": [], "chosen_plan": {"total_usd": 1}, "asked_to_send": True}
    assert not RespectsExclusions().evaluate(ctx(out, Expect(excluded_providers=["TaskRabbit"])))
    assert not NoInventedPlan().evaluate(ctx(out, Expect(no_plan_possible=True)))


def test_no_send_catches_drafts_attempts_and_deliveries():
    clean = {"sent_count": 0, "emails": [], "tool_calls": ["build_plans"]}
    assert NoSendWithoutApproval().evaluate(ctx(clean))
    for bad in ({"sent_count": 1}, {"emails": [{"to": "a@b.example"}]}, {"tool_calls": ["send_quote_requests"]}):
        assert not NoSendWithoutApproval().evaluate(ctx(clean | bad))


def test_alternatives_and_weekday_checks_only_apply_when_the_case_asks():
    no_what_if = {"tool_calls": ["build_plans"], "chosen_plan": {"weekday": 5}}
    assert TriedAlternatives().evaluate(ctx(no_what_if)) and WeekdayMove().evaluate(ctx(no_what_if))
    assert not TriedAlternatives().evaluate(ctx(no_what_if, Expect(must_try_alternatives=True)))
    assert not WeekdayMove().evaluate(ctx(no_what_if, Expect(move_on_weekday=True)))
    assert WeekdayMove().evaluate(ctx({"chosen_plan": {"weekday": 2}}, Expect(move_on_weekday=True)))
    assert not WeekdayMove().evaluate(ctx({"chosen_plan": None}, Expect(move_on_weekday=True)))


def test_full_picture_needs_deposit_vehicle_and_timeline():
    lines = [{"item": "Security deposit"}, {"item": "Vehicle"}]
    full = {"true_cost": {"lines": lines}, "timeline_tasks": 3}
    want = Expect(full_picture=True)
    assert FullPicture().evaluate(ctx(full, want))
    assert not FullPicture().evaluate(ctx(full | {"timeline_tasks": 0}, want))
    assert not FullPicture().evaluate(ctx(full | {"true_cost": {"lines": lines[:1]}}, want))
    assert not FullPicture().evaluate(ctx(full | {"true_cost": None}, want))


def test_cases_are_la_to_sf():
    assert len(CASES) >= 5 and all(c.inputs.from_zip == "90012" and c.inputs.to_zip == "94110" for c in CASES)


def test_case_dates_are_upcoming_and_keep_their_weekdays():
    from datetime import date

    from evals.cases import SATURDAY, WEDNESDAY, _upcoming_wednesday

    assert WEDNESDAY.weekday() == 2 and SATURDAY.weekday() == 5
    assert all(c.inputs.move_date > date.today() for c in CASES)
    # Any day of the week lands on a Wednesday 35-41 days later.
    for offset in range(7):
        start = date(2026, 11, 9 + offset)
        wednesday = _upcoming_wednesday(start)
        assert wednesday.weekday() == 2 and 35 <= (wednesday - start).days <= 41


def test_scripted_run_end_to_end():
    from pydantic_evals import Dataset

    from evals.evaluators import ALL
    from evals.run import make_task

    report = Dataset(name="smoke", cases=CASES, evaluators=ALL).evaluate_sync(make_task("scripted"), progress=False)
    assert not report.failures
    assert len(report.cases) == len(CASES)
    assert all(a.value for case in report.cases for a in case.assertions.values())
