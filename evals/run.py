"""Run the eval suite.

    .venv/bin/python -m evals.run                    # the model from .env (e.g. flatkey:claude-sonnet-5)
    .venv/bin/python -m evals.run --model scripted   # no LLM: checks the harness itself
    .venv/bin/python -m evals.run --case piano_no_taskrabbit_weekend --repeat 3

Writes a JSON report to evals/reports/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_evals import Dataset

from moving_agent.agent import AgentDeps, build_model, fill_derived, moving_agent, pick_model
from moving_agent.models import Intake
from moving_agent.adapters import Registry, RegistrySource, SampleCatalogAdapter, VehicleEstimateAdapter

from .cases import CASES
from .evaluators import ALL

ROOT = Path(__file__).resolve().parents[1]
REPORTS = Path(__file__).parent / "reports"


def make_task(model_name: str):
    async def task(intake: Intake) -> dict:
        # Evals use only deterministic sources, even if the shell has live keys.
        deps = AgentDeps(intake=intake, sources=[RegistrySource(Registry([SampleCatalogAdapter(), VehicleEstimateAdapter()]))])
        model = scripted_model(deps) if model_name == "scripted" else build_model(model_name)
        prompt = "Plan my move." + (f" My notes: {intake.notes}" if intake.notes else "")
        start = time.perf_counter()
        run = await moving_agent.run(prompt, deps=deps, model=model)
        elapsed = time.perf_counter() - start
        fill_derived(deps)

        calls = [p.tool_name for m in run.all_messages() if isinstance(m, ModelResponse)
                 for p in m.parts if isinstance(p, ToolCallPart)]
        paused = isinstance(run.output, DeferredToolRequests)
        summary = "" if paused else run.output.summary
        plan = deps.plans[deps.chosen] if deps.plans else None
        from .evaluators import _money

        known = {float(intake.budget_usd)}
        all_offers = [o for offers in deps.offers.values() for o in offers]
        all_offers += [o for v in deps.variants.values() for offers in v.offers.values() for o in offers]
        for o in all_offers:
            known.add(o.price_usd)
            known |= _money(o.price_basis)  # rates like "$48/hr" the tools showed the model
        for p in deps.plans:
            known |= {p.total_usd, p.over_budget_by, round(abs(intake.budget_usd - p.total_usd), 2)}
        for v in deps.variants.values():
            for p in v.plans:
                known |= {p.total_usd, round(abs(intake.budget_usd - p.total_usd), 2)}
                known |= {o.price_usd for o in p.offers}
        return {
            "summary": summary,
            "asked_to_send": paused,
            "sent_count": len(deps.sent),
            "tool_calls": calls,
            "chosen_plan": None if plan is None else {
                "total_usd": plan.total_usd, "move_date": plan.move_date.isoformat(), "weekday": plan.move_date.weekday()},
            "chosen_providers": [o.provider for o in plan.offers] if plan else [],
            "emails": [e.model_dump() for e in deps.emails],
            "known_amounts": sorted(known | _extra_amounts(deps)),
            "true_cost": deps.true_cost.model_dump() if deps.true_cost else None,
            "timeline_tasks": len(deps.timeline),
            "seconds": round(elapsed, 1),
        }
    return task


def _extra_amounts(deps) -> set[float]:
    """Numbers from the cost and vehicle tools, which the summary may quote."""
    out: set[float] = set()
    if deps.true_cost:
        out |= {l.amount_usd for l in deps.true_cost.lines} | {deps.true_cost.total_usd, deps.true_cost.due_before_move_in_usd}
    for v in deps.vehicle_options:
        out |= {v["price_usd"]} | {x for x in (v.get("low"), v.get("high")) if x}
        from .evaluators import _money
        out |= _money(v.get("how", ""))
    return out


def scripted_model(deps):
    """Exercise planning tools with a deterministic policy, not an LLM quality score.

    The policy reads intake and tool state only, never evaluation expectations.
    It recognizes provider exclusions used in the demo and searches allowed dates.
    """
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def model(messages, info: AgentInfo) -> ModelResponse:
        returns = [p for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]
        done = [r.tool_name for r in returns]
        needs = deps.intake.needs
        if "update_requirements" not in done:
            excluded = ["TaskRabbit"] if "don't use taskrabbit" in deps.intake.notes.lower() else []
            return ModelResponse(parts=[ToolCallPart("update_requirements", {"exclude_providers": excluded})])
        searched = [r.content for r in returns if r.tool_name == "search_offers"]
        if len(searched) < len(needs):
            return ModelResponse(parts=[ToolCallPart("search_offers", {"service": needs[len(searched)]})])
        if "build_plans" not in done:
            return ModelResponse(parts=[ToolCallPart("build_plans", {})])
        alternatives = [
            {"move_date": (deps.intake.move_date + timedelta(days=offset)).isoformat()}
            for offset in range(-deps.intake.flexible_days, deps.intake.flexible_days + 1) if offset
        ]
        if "truck" in needs:
            alternatives.append({"container_instead_of_truck": True})
        attempted = done.count("what_if")
        if attempted < len(alternatives) and "adopt_variant" not in done:
            return ModelResponse(parts=[ToolCallPart("what_if", alternatives[attempted])])
        if "adopt_variant" not in done:
            viable = [(key, value) for key, value in deps.variants.items() if value.plans]
            if viable:
                key, variant = min(viable, key=lambda pair: pair[1].plans[0].total_usd)
                if not deps.plans or variant.plans[0].total_usd < deps.plans[0].total_usd:
                    return ModelResponse(parts=[ToolCallPart("adopt_variant", {"variant_id": key})])
        if deps.plans and "choose_plan" not in done:
            return ModelResponse(parts=[ToolCallPart("choose_plan", {"plan_index": 0})])
        summary = "No compatible plan is available for the requested services and date."
        if deps.plans:
            plan = deps.plans[deps.chosen]
            summary = f"The recommended plan costs ${plan.total_usd:,.2f}."
            if not plan.within_budget:
                summary += " This is over your budget."
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"summary": summary})])

    return FunctionModel(model)


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None, help="scripted, or a model like flatkey:claude-sonnet-5 (default: from .env)")
    parser.add_argument("--case", action="append", help="run only these case names")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()

    model_name = args.model or pick_model()
    if not model_name:
        raise SystemExit("No model configured. Set FLATKEY_API_KEY in .env or pass --model scripted.")
    cases = [c for c in CASES if not args.case or c.name in args.case]
    dataset = Dataset(name="la_to_sf", cases=cases, evaluators=ALL)
    report = dataset.evaluate_sync(make_task(model_name), name=f"moving-agent:{model_name}", repeat=args.repeat,
                                   max_concurrency=5, progress=False)
    report.print(include_input=False, include_output=False, include_durations=True)

    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"{datetime.now():%Y%m%d-%H%M%S}-{model_name.replace(':', '_').replace('/', '_')}.json"
    rows = []
    for c in report.cases:
        rows.append({
            "case": c.name,
            "assertions": {k: v.value for k, v in c.assertions.items()},
            "scores": {k: v.value for k, v in c.scores.items()},
            "seconds": c.output.get("seconds"),
            "summary": c.output.get("summary"),
            "chosen_plan": c.output.get("chosen_plan"),
        })
    failures = [{"case": f.name, "error": f.error_message} for f in report.failures]
    total = sum(len(r["assertions"]) for r in rows)
    passed = sum(sum(1 for v in r["assertions"].values() if v) for r in rows)
    out.write_text(json.dumps({"model": model_name, "passed": passed, "total": total, "cases": rows, "failures": failures}, indent=2))
    print(f"\n{passed}/{total} checks passed across {len(rows)} case run(s); {len(failures)} crashed. Report: {out.relative_to(ROOT)}")
    if failures or not total or passed != total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
