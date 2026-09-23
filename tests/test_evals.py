"""The eval harness itself: evaluators on hand-made outputs, and a scripted end-to-end run."""

from types import SimpleNamespace

from evals.cases import CASES, Expect
from evals.evaluators import BudgetHandling, NoInventedPlan, PricesGrounded, RespectsExclusions, _money


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


def test_cases_are_la_to_sf():
    assert len(CASES) >= 5 and all(c.inputs.from_zip == "90012" and c.inputs.to_zip == "94110" for c in CASES)


def test_scripted_run_end_to_end():
    from pydantic_evals import Dataset

    from evals.evaluators import ALL
    from evals.run import make_task

    report = Dataset(name="smoke", cases=CASES[:1], evaluators=ALL).evaluate_sync(make_task("scripted"), progress=False)
    assert not report.failures
    assert all(a.value for a in report.cases[0].assertions.values())
