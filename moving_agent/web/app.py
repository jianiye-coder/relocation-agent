"""FastAPI app: intake form -> a sourced relocation plan -> optional agent chat."""

from __future__ import annotations

import os
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from pydantic_ai.usage import UsageLimits

from .. import geo, home
from ..agent import AgentDeps, build_model, fill_derived, model_configured, moving_agent, pick_model, run_without_llm, trace
from ..models import HomeSize, Intake
from ..adapters import QuoteCache, RegistrySource, default_registry
from ..inventory import estimate as estimate_inventory
from ..listings import check as check_listing_rules

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# Models sometimes add markdown emphasis; show plain text.
templates.env.filters["plain"] = lambda text: (text or "").replace("**", "").replace("__", "")
app = FastAPI(title="Moving agent")
_cache = None if os.getenv("QUOTE_CACHE") == "off" else QuoteCache()
SOURCES = [RegistrySource(default_registry(_cache))]
SID_COOKIE = "sid"


@dataclass
class Session:
    sid: str
    intake: Intake
    deps: AgentDeps
    used_llm: bool
    summary: str = ""
    history: list = field(default_factory=list)
    chat: list[dict] = field(default_factory=list)
    error: str = ""


# In-memory store is enough for a demo; swap for Postgres later.
SESSIONS: dict[str, Session] = {}


# ---- sessions ----

@app.middleware("http")
async def ensure_sid(request: Request, call_next):
    request.state.sid = request.cookies.get(SID_COOKIE) or secrets.token_urlsafe(18)
    response = await call_next(request)
    if not request.cookies.get(SID_COOKIE):
        response.set_cookie(SID_COOKIE, request.state.sid, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return response


def _form_context(errors: list[str]) -> dict:
    return {"home_sizes": [h.value for h in HomeSize], "errors": errors, "today": date.today().isoformat()}


@app.get("/", response_class=HTMLResponse)
def intake_form(request: Request):
    return templates.TemplateResponse(request, "intake.html", _form_context([]))


# ---- address lookup ----

@app.get("/api/geo/place")
def api_place(address: str):
    try:
        return geo.geocode(address).model_dump()
    except geo.GeoError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception:
        return JSONResponse({"error": "Address lookup is unavailable right now. Enter the ZIP yourself."}, status_code=502)


@app.get("/api/geo/distance")
def api_distance(from_address: str, to_address: str):
    try:
        a, b = geo.geocode(from_address), geo.geocode(to_address)
    except geo.GeoError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception:
        return JSONResponse({"error": "Address lookup is unavailable right now. Enter the ZIP and distance yourself."}, status_code=502)
    d = geo.driving_distance(a, b)
    return {"from": a.model_dump(), "to": b.model_dump(), "distance_miles": d.miles, "distance_source": d.source}


def _fill_from_addresses(from_address: str, to_address: str, from_zip: str, to_zip: str, distance: str) -> tuple[str, str, str, list[str]]:
    """Server-side fallback when the browser didn't fill ZIPs or distance (no JavaScript, or lookup failed)."""
    errors: list[str] = []
    places = {}
    for label, addr, current in (("from", from_address, from_zip), ("to", to_address, to_zip)):
        if current or not addr:
            continue
        try:
            places[label] = geo.geocode(addr)
        except Exception as exc:
            errors.append(f"{label}_address: {exc}")
    from_zip = from_zip or (places["from"].zip if "from" in places else "")
    to_zip = to_zip or (places["to"].zip if "to" in places else "")
    if not distance and from_address and to_address and not errors:
        try:
            a = places.get("from") or geo.geocode(from_address)
            b = places.get("to") or geo.geocode(to_address)
            distance = str(geo.driving_distance(a, b).miles)
        except Exception as exc:
            errors.append(f"distance_miles: {exc}")
    return from_zip, to_zip, distance, errors


# ---- running the agent ----

async def run_turn(s: Session, prompt: str) -> None:
    """One agent turn: a new message from the user, or the user's approve/deny decision."""
    s.error = ""
    try:
        run = await moving_agent.run(
            prompt,
            deps=s.deps,
            model=build_model(pick_model()),
            message_history=s.history or None,
            usage_limits=UsageLimits(request_limit=30),
        )
    except Exception as exc:  # bad key, network, model error: keep the page usable
        s.error = f"The agent couldn't finish: {type(exc).__name__}: {str(exc)[:300]}"
        if not s.deps.plans:
            s.summary = run_without_llm(s.deps)
        return
    s.history = run.all_messages()
    fill_derived(s.deps)
    s.summary = run.output.summary
    s.chat.append({"role": "agent", "text": run.output.summary})


@app.post("/plan", response_class=HTMLResponse)
async def plan(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    from_address: str = Form(""),
    to_address: str = Form(""),
    from_zip: str = Form(""),
    to_zip: str = Form(""),
    distance_miles: str = Form(""),
    move_date: date = Form(...),
    flexible_days: int = Form(0),
    home_size: str = Form(...),
    from_floor: int = Form(1),
    from_elevator: bool = Form(False),
    to_floor: int = Form(1),
    to_elevator: bool = Form(False),
    needs: list[str] = Form(default=[]),
    storage_months: int = Form(0),
    budget_usd: int = Form(...),
    items_to_sell: str = Form(""),
    notes: str = Form(""),
    inventory_text: str = Form(""),
    household_size: int = Form(1),
    pets: str = Form(""),
    vehicles: list[str] = Form(default=[]),
    lease_end: str = Form(""),
    monthly_rent: str = Form(""),
    candidate_addresses: str = Form(""),
    commute_destination: str = Form(""),
    commute_mode: str = Form("drive"),
    commute_departure_time: str = Form(""),
):
    from_zip, to_zip, distance_miles, geo_errors = _fill_from_addresses(
        from_address, to_address, from_zip.strip(), to_zip.strip(), distance_miles.strip()
    )
    if not distance_miles and not geo_errors:
        geo_errors.append("distance_miles: enter both addresses or the distance")
    try:
        intake = Intake(
            name=name, email=email, from_address=from_address.strip(), to_address=to_address.strip(),
            from_zip=from_zip, to_zip=to_zip, distance_miles=distance_miles or -1,
            move_date=move_date, flexible_days=flexible_days, home_size=home_size,
            from_floor=from_floor, from_elevator=from_elevator, to_floor=to_floor, to_elevator=to_elevator,
            needs=needs, storage_months=storage_months, budget_usd=budget_usd,
            items_to_sell=[i for i in items_to_sell.splitlines() if i.strip()], notes=notes,
            inventory_text=inventory_text, household_size=household_size,
            pets=[p.strip() for p in pets.split(",") if p.strip()], vehicles=vehicles,
            lease_end=lease_end or None, monthly_rent=int(monthly_rent) if monthly_rent.strip() else None,
            candidate_addresses=[a.strip() for a in candidate_addresses.splitlines() if a.strip()],
            commute_destination=commute_destination.strip(), commute_mode=commute_mode.lower(),
            commute_departure_time=commute_departure_time.strip(),
        )
    except (ValidationError, ValueError) as exc:
        errs = exc.errors() if isinstance(exc, ValidationError) else [{"loc": ("form",), "msg": str(exc)}]
        errors = geo_errors + [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errs]
        return templates.TemplateResponse(request, "intake.html", _form_context(errors), status_code=422)

    inventory = None
    if intake.inventory_text.strip():
        inventory = estimate_inventory(intake.inventory_text)
        if inventory.total_cuft:
            intake = intake.model_copy(update={"volume_cuft": max(20, min(3000, inventory.total_cuft)), "weight_lbs": inventory.total_lbs})

    sid = request.state.sid
    home_results = [{"address": address, "commute": home.commute(address, intake.commute_destination, intake.commute_mode), "utilities": home.utilities(address)} for address in intake.candidate_addresses]
    deps = AgentDeps(intake=intake, sources=SOURCES, inventory=inventory, home_results=home_results)
    s = Session(sid=sid, intake=intake, deps=deps, used_llm=model_configured())
    rid = uuid.uuid4().hex[:12]
    SESSIONS[rid] = s
    if s.used_llm:
        prompt = "Plan my move."
        if intake.notes:
            prompt += f" My notes: {intake.notes}"
        s.chat.append({"role": "user", "text": prompt})
        await run_turn(s, prompt)
    else:
        s.summary = run_without_llm(deps)
    return RedirectResponse(f"/plan/{rid}", status_code=303)


def _session(rid: str, request: Request) -> Session:
    s = SESSIONS.get(rid)
    if not s or s.sid != request.state.sid:
        raise HTTPException(404, "Plan not found. Start again from the intake form.")
    return s


@app.get("/plan/{rid}", response_class=HTMLResponse)
def show_plan(request: Request, rid: str):
    s = _session(rid, request)
    d = s.deps
    plans = d.plans
    if plans:
        plans = [plans[d.chosen]] + [p for i, p in enumerate(plans) if i != d.chosen]
    return templates.TemplateResponse(request, "plan.html", {
        "rid": rid, "s": s, "intake": d.intake, "plans": plans, "listings": d.listings,
        "steps": trace(s.history),
        "model": pick_model(), "inventory": d.inventory, "vehicle_options": d.vehicle_options,
        "true_cost": d.true_cost, "timeline": d.timeline, "listing_checks": d.listing_checks,
        "home_results": d.home_results,
        "adapter_errors": [e for src in d.sources if hasattr(src, "errors") for e in src.errors(d.intake)],
    })


@app.post("/chat/{rid}")
async def chat(request: Request, rid: str, message: str = Form(...)):
    s = _session(rid, request)
    if not s.used_llm:
        raise HTTPException(400, "Add an API key in .env to chat with the agent.")
    s.chat.append({"role": "user", "text": message.strip()})
    await run_turn(s, message.strip())
    return RedirectResponse(f"/plan/{rid}", status_code=303)


@app.get("/listing-check", response_class=HTMLResponse)
def listing_check_form(request: Request):
    return templates.TemplateResponse(request, "listing_check.html", {"result": None, "form": {}})


@app.post("/listing-check", response_class=HTMLResponse)
def listing_check(request: Request, text: str = Form(...), price_usd: str = Form(""), bedrooms: str = Form(""), address: str = Form("")):
    result = check_listing_rules(text, int(price_usd) if price_usd.strip() else None,
                                 int(bedrooms) if bedrooms.strip() else None, address)
    return templates.TemplateResponse(request, "listing_check.html", {
        "result": result, "form": {"text": text, "price_usd": price_usd, "bedrooms": bedrooms, "address": address}})
