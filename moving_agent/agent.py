"""The moving agent, built with Pydantic AI.

The agent makes the decisions: which services to search, what to try when the
budget doesn't fit (another date, a container instead of a truck, a smaller
crew), how to read the user's notes and chat messages, which plan to pick, and
which plan to pick. Tools do the work in plain code, so every price, total and
date comes from code, never from the model's own writing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext

from . import drafts, listings, timeline, truecost
from .adapters import AdapterError, FMCSAAdapter, ServiceType
from .adapters.bridge import run_sync
from .inventory import Inventory
from .models import EmailDraft, Intake, ListingDraft, Offer, Plan, Requirements, Service
from .planner import OfferSource, build_plans


@dataclass
class Variant:
    label: str
    intake: Intake
    offers: dict[Service, list[Offer]]
    plans: list[Plan]


@dataclass
class AgentDeps:
    intake: Intake
    sources: list[OfferSource]
    requirements: Requirements = field(default_factory=Requirements)
    offers: dict[Service, list[Offer]] = field(default_factory=dict)
    plans: list[Plan] = field(default_factory=list)
    chosen: int = 0
    # Kept as inert state for compatibility; no agent tool or web route populates or sends it.
    emails: list[EmailDraft] = field(default_factory=list)
    listings: list[ListingDraft] = field(default_factory=list)
    variants: dict[str, Variant] = field(default_factory=dict)
    sent: list = field(default_factory=list)
    inventory: Inventory | None = None
    vehicle_options: list[dict] = field(default_factory=list)
    true_cost: truecost.TrueCost | None = None
    timeline: list[timeline.Task] = field(default_factory=list)
    listing_checks: list[dict] = field(default_factory=list)
    vetting: list[dict] = field(default_factory=list)
    home_results: list[dict] = field(default_factory=list)


class AgentReply(BaseModel):
    """What the agent says to the user at the end of a turn."""

    summary: str = Field(description="2-5 plain sentences (plain text, no markdown, no bullet lists): what you did, what you recommend and why, with exact prices from the tools")


INSTRUCTIONS = """\
You are a moving agent for people moving within the US. You work for the user: find the best
combination of moving services for their needs, dates and budget.

How to work:
1. Read the intake form and the user's notes. If the notes add constraints (a piano, "must be out
   by Friday", "no TaskRabbit", "I want 3 movers"), call update_requirements first.
2. Call search_offers for every service the user needs, then build_plans.
3. If the best plan is over budget, or clearly expensive, call what_if to test alternatives:
   another date within about a week, a container instead of a truck, or a smaller crew.
   Adopt a variant with adopt_variant only if it saves money and still fits the user's needs.
4. Call choose_plan with the plan you recommend (prefer higher ratings when totals are within ~5%).
5. If the user listed items to sell, call draft_listings.
5b. If the user has vehicles, call compare_vehicle_options. Then call estimate_true_cost and plan_timeline
   so the user sees the full cost (move + deposit + first month + utilities + vehicle) and what to do when.
   If the user gives a mover's USDOT or MC number, call vet_mover. If they paste a rental listing, call check_listing.
6. If no plan fits the budget or the date, explain the gap in dollars, what you tried, and the options.
7. End with a short summary. Quote prices exactly as the tools returned them.

In later chat turns, do what the user asks (cheaper, different date, drop a provider)
using the same tools. Never invent providers, prices, or dates.
"""


def pick_model() -> str | None:
    """A good model for whichever API key is configured. None means run without an LLM."""
    keys = ("FLATKEY_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY")
    if not any(os.getenv(k, "").strip() for k in keys):
        return None
    if os.getenv("MOVING_AGENT_MODEL", "").strip():
        return os.environ["MOVING_AGENT_MODEL"].strip()
    if os.getenv("FLATKEY_API_KEY"):
        return "flatkey:claude-sonnet-5"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic:claude-sonnet-5"
    if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
        return "google:gemini-flash-latest"
    if os.getenv("OPENAI_API_KEY"):
        return "openai:gpt-5.2"
    return None


FLATKEY_BASE_URL = "https://router.flatkey.ai/v1"


def build_model(name: str):
    """Turn a model name into a model object.

    `flatkey:<model>` goes through Flatkey's OpenAI-compatible gateway. Anthropic gets an explicit
    endpoint so a stray ANTHROPIC_BASE_URL in the shell can't redirect the user's key.
    """
    if name.startswith("flatkey:"):
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        provider = OpenAIProvider(base_url=os.getenv("FLATKEY_BASE_URL", FLATKEY_BASE_URL), api_key=os.environ["FLATKEY_API_KEY"].strip())
        return OpenAIChatModel(name.split(":", 1)[1], provider=provider)
    if name.startswith("anthropic:") and os.getenv("ANTHROPIC_API_KEY"):
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider(api_key=os.environ["ANTHROPIC_API_KEY"], base_url="https://api.anthropic.com")
        return AnthropicModel(name.split(":", 1)[1], provider=provider)
    return name


def model_configured() -> bool:
    return pick_model() is not None


moving_agent = Agent(
    deps_type=AgentDeps,
    output_type=AgentReply,
    instructions=INSTRUCTIONS,
    name="moving_agent",
    retries=3,
    defer_model_check=True,
)


@moving_agent.instructions
def current_context(ctx: RunContext[AgentDeps]) -> str:
    d = ctx.deps
    inv = ""
    if d.inventory:
        inv = (f"\nInventory estimate: {d.inventory.total_cuft} cu ft, {d.inventory.total_lbs} lb"
               + (f"; special items: {', '.join(d.inventory.special_items)}" if d.inventory.special_items else ""))
    return (
        f"Today is {date.today().isoformat()}.\n"
        f"Intake form:\n{d.intake.model_dump_json(indent=2, exclude={'email', 'inventory_text'})}\n"
        f"Current requirements: {d.requirements.model_dump_json(exclude_defaults=True)}{inv}"
    )


def _filter(offers: list[Offer], req: Requirements) -> list[Offer]:
    out = []
    for o in offers:
        if any(x.lower() in o.provider.lower() for x in req.exclude_providers):
            continue
        if o.service == "labor" and o.crew_size:
            if req.min_movers and o.crew_size < req.min_movers:
                continue
            if req.max_movers and o.crew_size > req.max_movers:
                continue
        if o.service != "storage":
            if req.earliest_move_date and o.available_on < req.earliest_move_date:
                continue
            if req.latest_move_date and o.available_on > req.latest_move_date:
                continue
        out.append(o)
    return out


def _search(deps: AgentDeps, intake: Intake, service: Service, req: Requirements | None = None) -> list[Offer]:
    raw = [o for src in deps.sources for o in src.offers(intake, service)]
    return _filter(raw, req or deps.requirements)


def _nearby_availability(deps: AgentDeps, service: Service) -> list[str]:
    """When nothing fits the date, say which providers could do it on other days within a week."""
    wide = deps.intake.model_copy(update={"flexible_days": 7})
    days: dict[str, set[date]] = {}
    for src in deps.sources:
        for day_offset in range(-7, 8):
            d = deps.intake.move_date.fromordinal(deps.intake.move_date.toordinal() + day_offset)
            probe = wide.model_copy(update={"move_date": d, "flexible_days": 0})
            for o in _filter(src.offers(probe, service), deps.requirements):
                days.setdefault(o.provider, set()).add(d)
    fmt = lambda d: d.strftime("%a %b ") + str(d.day)
    return [f"{p}: {', '.join(fmt(d) for d in sorted(v))}" for p, v in days.items()][:5]


def _plan_rows(plans: list[Plan]) -> list[dict]:
    return [
        {"index": i, "total_usd": p.total_usd, "within_budget": p.within_budget, "move_date": p.move_date.isoformat(),
         "providers": [f"{o.id}: {o.provider} - {o.title} (${o.price_usd:,.2f})" for o in p.offers], "reason": p.reason}
        for i, p in enumerate(plans)
    ]


@moving_agent.tool
def update_requirements(
    ctx: RunContext[AgentDeps],
    exclude_providers: list[str] | None = None,
    min_movers: int | None = None,
    max_movers: int | None = None,
    earliest_move_date: date | None = None,
    latest_move_date: date | None = None,
) -> dict:
    """Record constraints from the user's notes or messages. Only pass what changes.

    After this, call search_offers again for each service.
    """
    req = ctx.deps.requirements.model_copy()
    if exclude_providers is not None:
        req.exclude_providers = exclude_providers
    for name, value in (("min_movers", min_movers), ("max_movers", max_movers),
                        ("earliest_move_date", earliest_move_date), ("latest_move_date", latest_move_date)):
        if value is not None:
            setattr(req, name, value)
    ctx.deps.requirements = req
    ctx.deps.offers, ctx.deps.plans = {}, []
    return req.model_dump(exclude_defaults=True, mode="json")


@moving_agent.tool
def search_offers(ctx: RunContext[AgentDeps], service: Service) -> list[dict]:
    """Find priced offers for one service (truck, labor, storage or container) for this move."""
    found = _search(ctx.deps, ctx.deps.intake, service)
    ctx.deps.offers[service] = found
    if not found:
        return [{"note": f"No {service} offers match the move and current requirements.",
                 "available_nearby": _nearby_availability(ctx.deps, service)}]
    return [
        {"id": o.id, "provider": o.provider, "title": o.title, "price_usd": o.price_usd, "how": o.price_basis,
         "date": o.available_on.isoformat(), "rating": o.rating, "crew_size": o.crew_size}
        for o in found
    ]


@moving_agent.tool(name="build_plans")
def build_plans_tool(ctx: RunContext[AgentDeps]) -> list[dict]:
    """Combine the offers found so far into ranked plans (cheapest within budget first)."""
    missing = [s for s in ctx.deps.intake.needs if s not in ctx.deps.offers]
    if missing:
        raise ModelRetry(f"Call search_offers first for: {', '.join(missing)}")
    ctx.deps.plans, ctx.deps.chosen = build_plans(ctx.deps.intake, ctx.deps.offers), 0
    if not ctx.deps.plans:
        return [{"note": "No combination covers every service. Try what_if, or tell the user which service had no offers."}]
    return _plan_rows(ctx.deps.plans)


@moving_agent.tool
def what_if(
    ctx: RunContext[AgentDeps],
    move_date: date | None = None,
    container_instead_of_truck: bool = False,
    max_movers: int | None = None,
) -> dict:
    """Price an alternative without changing the current plan: another move date, a container
    instead of a truck, or a smaller crew. Returns a variant_id you can adopt."""
    base = ctx.deps.intake
    updates: dict = {}
    label = []
    if move_date:
        updates["move_date"] = move_date
        label.append(f"move on {move_date.isoformat()}")
    if container_instead_of_truck:
        updates["needs"] = ["container" if s == "truck" else s for s in base.needs]
        label.append("container instead of truck")
    intake = base.model_copy(update=updates)
    req = ctx.deps.requirements.model_copy(update={"max_movers": max_movers} if max_movers else {})
    if max_movers:
        label.append(f"at most {max_movers} movers")
    offers = {s: _search(ctx.deps, intake, s, req) for s in intake.needs}
    plans = build_plans(intake, offers)
    current = ctx.deps.plans[0].total_usd if ctx.deps.plans else None
    vid = f"v{len(ctx.deps.variants) + 1}"
    ctx.deps.variants[vid] = Variant(", ".join(label) or "no change", intake, offers, plans)
    if not plans:
        return {"variant_id": vid, "note": "No combination covers every service with this change."}
    best = plans[0]
    return {
        "variant_id": vid, "change": ctx.deps.variants[vid].label, "best_total_usd": best.total_usd,
        "within_budget": best.within_budget,
        "saves_usd": round(current - best.total_usd, 2) if current is not None else None,
        "providers": [f"{o.provider} - {o.title}" for o in best.offers],
    }


@moving_agent.tool
def adopt_variant(ctx: RunContext[AgentDeps], variant_id: str) -> list[dict]:
    """Make a what_if variant the current plan set."""
    v = ctx.deps.variants.get(variant_id)
    if not v:
        raise ModelRetry(f"Unknown variant_id. Known: {', '.join(ctx.deps.variants) or 'none'}")
    if not v.plans:
        raise ModelRetry("That variant has no plans to adopt.")
    ctx.deps.intake, ctx.deps.offers, ctx.deps.plans, ctx.deps.chosen = v.intake, v.offers, v.plans, 0
    return _plan_rows(v.plans)


@moving_agent.tool
def choose_plan(ctx: RunContext[AgentDeps], plan_index: int) -> list[dict]:
    """Pick the plan to recommend."""
    if not ctx.deps.plans:
        raise ModelRetry("Call build_plans first.")
    if not 0 <= plan_index < len(ctx.deps.plans):
        raise ModelRetry(f"plan_index must be between 0 and {len(ctx.deps.plans) - 1}")
    ctx.deps.chosen = plan_index
    return _plan_rows([ctx.deps.plans[plan_index]])


@moving_agent.tool
def draft_listings(ctx: RunContext[AgentDeps]) -> list[dict]:
    """Draft resale listings for the items the user wants to sell before moving."""
    ctx.deps.listings = [drafts.listing(i, ctx.deps.intake.to_zip) for i in ctx.deps.intake.items_to_sell if i.strip()]
    return [{"item": l.item, "title": l.title} for l in ctx.deps.listings]


def _vehicle_options(deps: AgentDeps) -> list[dict]:
    src = next((s for s in deps.sources if hasattr(s, "quotes_of")), None)
    if not src or not deps.intake.vehicles:
        return []
    quotes = src.quotes_of(deps.intake, ServiceType.vehicle_shipping, ServiceType.vehicle_drive)
    return [{"title": q.title, "price_usd": q.price_usd, "low": q.price_low_usd, "high": q.price_high_usd,
             "how": q.price_basis, "confidence": q.confidence, "kind": q.service_type.value} for q in quotes]


def _cheapest_vehicle(options: list[dict]) -> tuple[float | None, str]:
    if not options:
        return None, ""
    by_vehicle: dict[str, dict] = {}
    for o in options:
        n = o["title"].split()[-1] if o["kind"] == "vehicle_drive" else o["title"].split()[2]
        if n not in by_vehicle or o["price_usd"] < by_vehicle[n]["price_usd"]:
            by_vehicle[n] = o
    total = round(sum(o["price_usd"] for o in by_vehicle.values()), 2)
    return total, "; ".join(f"{o['title']} ${o['price_usd']:,.0f}" for o in by_vehicle.values())


@moving_agent.tool
def compare_vehicle_options(ctx: RunContext[AgentDeps]) -> list[dict]:
    """Cost to ship each of the user's vehicles on a carrier vs. drive it (estimates with ranges)."""
    ctx.deps.vehicle_options = _vehicle_options(ctx.deps)
    return ctx.deps.vehicle_options or [{"note": "The user has no vehicles."}]


@moving_agent.tool
def estimate_true_cost(ctx: RunContext[AgentDeps]) -> dict:
    """Total cost of the move for the chosen plan: movers/truck/storage + deposit + first month + applications
    + utilities + the cheaper vehicle option. Each line says where its number comes from."""
    d = ctx.deps
    plan = d.plans[d.chosen] if d.plans else None
    if not d.vehicle_options and d.intake.vehicles:
        d.vehicle_options = _vehicle_options(d)
    vcost, vbasis = _cheapest_vehicle(d.vehicle_options)
    d.true_cost = truecost.compute(plan, d.intake.home_size, d.intake.monthly_rent, vehicle_cost=vcost, vehicle_basis=vbasis)
    return d.true_cost.model_dump()


@moving_agent.tool
def plan_timeline(ctx: RunContext[AgentDeps]) -> list[dict]:
    """Moving to-do list planned backward from the move date (and the current lease end)."""
    d = ctx.deps
    move_day = d.plans[d.chosen].move_date if d.plans else d.intake.move_date
    d.timeline = timeline.build(
        move_day, d.intake.lease_end, has_vehicle=bool(d.intake.vehicles), selling=bool(d.intake.items_to_sell),
        needs_storage="storage" in d.intake.needs,
        special_items=d.inventory.special_items if d.inventory else None,
    )
    return [{"due": t.due.isoformat(), "task": t.title, "overdue": t.overdue} for t in d.timeline]


@moving_agent.tool
def vet_mover(ctx: RunContext[AgentDeps], usdot: int | None = None, mc: str | None = None) -> dict:
    """Check a mover's registration with FMCSA by USDOT or MC number. Movers not allowed to operate must not be booked."""
    try:
        result = run_sync(FMCSAAdapter().check(usdot=usdot, mc=mc)).model_dump(mode="json")
    except AdapterError as exc:
        result = {"error": exc.code.value, "message": exc.message}
    ctx.deps.vetting.append(result)
    return result


@moving_agent.tool
def check_listing(ctx: RunContext[AgentDeps], text: str, price_usd: int | None = None,
                  bedrooms: int | None = None, address: str = "") -> dict:
    """Check a rental listing the user pasted for scam signs: wire/deposit-before-viewing language,
    below-market price, duplicates of listings checked earlier, missing address."""
    earlier = [c["text"] for c in ctx.deps.listing_checks]
    result = listings.check(text, price_usd, bedrooms, address, other_listings=earlier)
    ctx.deps.listing_checks.append({"text": text, "price_usd": price_usd, **result.model_dump()})
    return result.model_dump()


# ---- helpers for the web app ----

TOOL_LABELS = {
    "update_requirements": "Updated requirements",
    "search_offers": "Searched offers",
    "build_plans": "Built plans",
    "what_if": "Tried an alternative",
    "adopt_variant": "Switched to the alternative",
    "choose_plan": "Chose a plan and drafted emails",
    "draft_listings": "Drafted resale listings",
    "compare_vehicle_options": "Compared shipping vs. driving the car",
    "estimate_true_cost": "Added up the true cost",
    "plan_timeline": "Planned the timeline",
    "vet_mover": "Checked a mover with FMCSA",
    "check_listing": "Checked a rental listing",
}


def trace(messages) -> list[dict]:
    """Turn the run's messages into readable steps: what the agent asked for and what came back."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart

    steps: list[dict] = []
    calls: dict[str, dict] = {}
    for m in messages:
        if isinstance(m, ModelResponse):
            for p in m.parts:
                if isinstance(p, ToolCallPart) and p.tool_name in TOOL_LABELS:
                    step = {"kind": "tool", "tool": p.tool_name, "label": TOOL_LABELS[p.tool_name],
                            "args": p.args_as_dict(), "result": None}
                    calls[p.tool_call_id] = step
                    steps.append(step)
        elif isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                    steps.append({"kind": "user", "text": p.content})
                elif isinstance(p, ToolReturnPart) and p.tool_call_id in calls:
                    calls[p.tool_call_id]["result"] = _short(p.content)
    return steps


def _short(content) -> str:
    if isinstance(content, list):
        if content and isinstance(content[0], dict) and "total_usd" in content[0]:
            best = content[0]
            budget_note = "" if best.get("within_budget", False) else " (over budget)"
            return f"{len(content)} plan(s); best ${best['total_usd']:,.2f}" + budget_note
        if content and isinstance(content[0], dict) and "price_usd" in content[0]:
            cheapest = min(content, key=lambda o: o["price_usd"])
            provider = cheapest.get("provider") or cheapest.get("title") or "provider unavailable"
            return f"{len(content)} offer(s); cheapest {provider} ${cheapest['price_usd']:,.2f}"
        if content and isinstance(content[0], dict) and "ok" in content[0]:
            return f"{sum(1 for r in content if r['ok'])} of {len(content)} sent"
        if content and isinstance(content[0], dict) and "note" in content[0]:
            return content[0]["note"]
        return f"{len(content)} item(s)"
    if isinstance(content, dict):
        if "best_total_usd" in content:
            saves = content.get("saves_usd")
            return f"{content['change']}: ${content['best_total_usd']:,.2f}" + (f" (saves ${saves:,.2f})" if saves and saves > 0 else "")
        return content.get("note") or ", ".join(f"{k}={v}" for k, v in content.items()) or "done"
    return str(content)[:160]


def fill_derived(deps: AgentDeps) -> None:
    """After every turn, make sure the vehicle comparison, true cost and timeline match the current plan,
    whether or not the model called those tools. Plain code, so it's always consistent."""
    deps.vehicle_options = _vehicle_options(deps)
    vcost, vbasis = _cheapest_vehicle(deps.vehicle_options)
    plan = deps.plans[deps.chosen] if deps.plans else None
    deps.true_cost = truecost.compute(plan, deps.intake.home_size, deps.intake.monthly_rent,
                                      vehicle_cost=vcost, vehicle_basis=vbasis)
    deps.timeline = timeline.build(plan.move_date if plan else deps.intake.move_date, deps.intake.lease_end,
                                   has_vehicle=bool(deps.intake.vehicles), selling=bool(deps.intake.items_to_sell),
                                   needs_storage="storage" in deps.intake.needs,
                                   special_items=deps.inventory.special_items if deps.inventory else None)


def run_without_llm(deps: AgentDeps) -> str:
    """Same tools in a fixed order, no model and no sending. Used when no API key is set."""
    for s in deps.intake.needs:
        deps.offers[s] = _search(deps, deps.intake, s)
    deps.plans = build_plans(deps.intake, deps.offers)
    deps.chosen = 0
    deps.listings = [drafts.listing(i, deps.intake.to_zip) for i in deps.intake.items_to_sell if i.strip()]
    fill_derived(deps)
    if deps.plans:
        p = deps.plans[0]
        return f"Recommended: {', '.join(f'{o.provider} {o.title}' for o in p.offers)}. {p.reason}."
    empty = [s for s in deps.intake.needs if not deps.offers.get(s)]
    return f"No offers found for: {', '.join(empty)}. Try a larger budget, more flexible dates, or a smaller volume."
