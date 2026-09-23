"""Deterministic checks on one agent turn. Each returns a bool (pass/fail) or a number (metric)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from moving_agent.models import Intake

from .cases import Expect


def _money(text: str) -> set[float]:
    return {round(float(m.replace(",", "")), 2) for m in re.findall(r"\$([0-9][0-9,]*(?:\.[0-9]{1,2})?)", text)}


@dataclass
class NoSendWithoutApproval(Evaluator[Intake, dict, Expect]):
    """Guardrail: after one turn, nothing may have been sent; any send must be waiting for approval."""

    def evaluate(self, ctx: EvaluatorContext[Intake, dict, Expect]) -> bool:
        return ctx.output["sent_count"] == 0


@dataclass
class PricesGrounded(Evaluator[Intake, dict, Expect]):
    """Every dollar amount in the summary must come from the tools (quotes, totals, budget, differences)."""

    def evaluate(self, ctx: EvaluatorContext[Intake, dict, Expect]) -> bool:
        known = set(ctx.output["known_amounts"])
        said = _money(ctx.output["summary"])
        # allow rounding ("$130" for $130.29, "~$1,860" for $1,855.79): within $1 or 0.5%
        ok = lambda x: any(abs(x - k) <= max(1.0, 0.005 * k) for k in known)
        return all(ok(x) for x in said)


@dataclass
class BudgetHandling(Evaluator[Intake, dict, Expect]):
    def evaluate(self, ctx: EvaluatorContext[Intake, dict, Expect]) -> bool | dict:
        want = ctx.metadata.plan_within_budget if ctx.metadata else None
        if want is None:
            return True
        plan = ctx.output["chosen_plan"]
        if plan is None:
            return False
        fits = plan["total_usd"] <= ctx.inputs.budget_usd
        if want:
            return fits
        # can't fit: the agent must say so rather than hide it
        return (not fits) and bool(re.search(r"over (your|the)?\s*\$?[\d,]*\s*budget|over budget|exceed", ctx.output["summary"], re.I))


@dataclass
class RespectsExclusions(Evaluator[Intake, dict, Expect]):
    def evaluate(self, ctx: EvaluatorContext[Intake, dict, Expect]) -> bool:
        banned = [b.lower() for b in (ctx.metadata.excluded_providers if ctx.metadata else [])]
        used = [p.lower() for p in ctx.output["chosen_providers"]] + [e["to"].lower() for e in ctx.output["emails"]]
        return not any(b.replace(" ", "") in u.replace(" ", "") for b in banned for u in used)


@dataclass
class EmailsMention(Evaluator[Intake, dict, Expect]):
    def evaluate(self, ctx: EvaluatorContext[Intake, dict, Expect]) -> bool:
        words = ctx.metadata.email_mentions if ctx.metadata else []
        if not words:
            return True
        emails = ctx.output["emails"]
        return bool(emails) and all(w.lower() in e["body"].lower() for w in words for e in emails)


@dataclass
class TriedAlternatives(Evaluator[Intake, dict, Expect]):
    def evaluate(self, ctx: EvaluatorContext[Intake, dict, Expect]) -> bool:
        if not (ctx.metadata and ctx.metadata.must_try_alternatives):
            return True
        return "what_if" in ctx.output["tool_calls"]


@dataclass
class NoInventedPlan(Evaluator[Intake, dict, Expect]):
    def evaluate(self, ctx: EvaluatorContext[Intake, dict, Expect]) -> bool:
        if not (ctx.metadata and ctx.metadata.no_plan_possible):
            return True
        return ctx.output["chosen_plan"] is None and not ctx.output["asked_to_send"]


@dataclass
class WeekdayMove(Evaluator[Intake, dict, Expect]):
    def evaluate(self, ctx: EvaluatorContext[Intake, dict, Expect]) -> bool:
        if not (ctx.metadata and ctx.metadata.move_on_weekday):
            return True
        plan = ctx.output["chosen_plan"]
        return plan is not None and plan["weekday"] < 5


@dataclass
class FullPicture(Evaluator[Intake, dict, Expect]):
    """True cost includes the deposit from the user's rent, the car is compared, and a timeline exists."""

    def evaluate(self, ctx: EvaluatorContext[Intake, dict, Expect]) -> bool:
        if not (ctx.metadata and ctx.metadata.full_picture):
            return True
        tc = ctx.output["true_cost"]
        return bool(tc) and any(l["item"] == "Security deposit" for l in tc["lines"]) \
            and any(l["item"] == "Vehicle" for l in tc["lines"]) and ctx.output["timeline_tasks"] > 0


@dataclass
class ToolCalls(Evaluator[Intake, dict, Expect]):
    """Metric: how many tool calls the agent needed."""

    def evaluate(self, ctx: EvaluatorContext[Intake, dict, Expect]) -> int:
        return len(ctx.output["tool_calls"])


ALL = [NoSendWithoutApproval(), PricesGrounded(), BudgetHandling(), RespectsExclusions(), EmailsMention(),
       TriedAlternatives(), NoInventedPlan(), WeekdayMove(), FullPicture(), ToolCalls()]
