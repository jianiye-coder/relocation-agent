"""FastAPI app: intake form -> a sourced relocation plan -> optional agent chat."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError
from pydantic_ai.usage import UsageLimits

from .. import geo, home, photo_inventory, voice_intake
from ..agent import AgentDeps, build_model, fill_derived, model_configured, moving_agent, pick_model, run_without_llm, trace
from ..models import HomeSize, Intake
from ..adapters import AdapterError, ErrorCode, FMCSAAdapter, QuoteCache, RegistrySource, default_registry
from ..inventory import estimate as estimate_inventory
from ..listings import check as check_listing_rules
from .store import StoreUnavailable, session_store
from .views import option_views, price_notes, timeline_view

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _checked(iso: str) -> str:
    """ISO timestamp -> 'Sep 23 20:00 UTC' for 'when was this fetched' labels."""
    from datetime import datetime as _dt, timezone as _tz
    try:
        t = _dt.fromisoformat(iso).astimezone(_tz.utc)
    except (TypeError, ValueError):
        return ""
    return f"{t:%b} {t.day} {t:%H:%M} UTC"


def _clock(iso: str) -> str:
    """ISO timestamp -> local '8:30' (the time the user typed)."""
    from datetime import datetime as _dt
    try:
        t = _dt.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    return f"{t.hour}:{t.minute:02d}"


templates.env.filters["checked"] = _checked
templates.env.filters["clock"] = _clock
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
    listings: dict[str, dict] = field(default_factory=dict)  # candidate address -> where to contact the landlord


def _attach(session: Session) -> Session:
    """A plan loaded from Redis comes back without offer sources; give it the app's."""
    session.deps.sources = SOURCES
    return session


# A dict locally (one long-running process); Redis on Vercel, where requests can land on different instances.
SESSIONS = session_store(_attach)


# ---- access code ----

ACCESS_COOKIE = "access"
OPEN_PATHS = {"/unlock"}


def _access_code() -> str:
    """Set ACCESS_CODE on a public deployment so only people you give it to can run plans (and spend LLM credits)."""
    return os.getenv("ACCESS_CODE", "").strip()


def _access_token(code: str) -> str:
    return hmac.new(code.encode(), b"relocation-agent access", hashlib.sha256).hexdigest()


def _unlocked(request: Request, code: str) -> bool:
    return hmac.compare_digest(request.cookies.get(ACCESS_COOKIE, ""), _access_token(code))


def _safe_next(path: str) -> str:
    return path if path.startswith("/") and not path.startswith("//") else "/"


@app.get("/unlock", response_class=HTMLResponse)
def unlock_form(request: Request, next: str = "/"):
    return templates.TemplateResponse(request, "unlock.html", {"next": _safe_next(next), "error": ""})


@app.post("/unlock", response_class=HTMLResponse)
def unlock(request: Request, code: str = Form(""), next: str = Form("/")):
    expected = _access_code()
    if expected and not hmac.compare_digest(code.strip(), expected):
        return templates.TemplateResponse(request, "unlock.html", {"next": _safe_next(next), "error": "That code doesn't match."},
                                          status_code=401)
    response = RedirectResponse(_safe_next(next), status_code=303)
    if expected:
        response.set_cookie(ACCESS_COOKIE, _access_token(expected), httponly=True, samesite="lax", secure=request.url.scheme == "https",
                            max_age=60 * 60 * 24 * 30)
    return response


# ---- sessions ----

@app.middleware("http")
async def ensure_sid(request: Request, call_next):
    code = _access_code()
    if code and request.url.path not in OPEN_PATHS and not _unlocked(request, code):
        if request.method == "GET":
            return RedirectResponse("/unlock?" + urlencode({"next": request.url.path}), status_code=303)
        return JSONResponse({"detail": "Enter the access code first."}, status_code=401)
    request.state.sid = request.cookies.get(SID_COOKIE) or secrets.token_urlsafe(18)
    request.state.touched = {}  # plans read or created in this request, saved back once it's handled
    response = await call_next(request)
    try:
        for rid, session in request.state.touched.items():
            SESSIONS[rid] = session
    except StoreUnavailable:
        return _store_unavailable(request)
    if not request.cookies.get(SID_COOKIE):
        response.set_cookie(SID_COOKIE, request.state.sid, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return response


def _store_unavailable(request: Request):
    return templates.TemplateResponse(request, "message.html", {
        "title": "Plans are unavailable right now",
        "text": "We couldn't reach the database that keeps plans, so this step wasn't saved. Try again in a minute."},
        status_code=503)


@app.exception_handler(StoreUnavailable)
async def store_unavailable(request: Request, exc: StoreUnavailable):
    return _store_unavailable(request)


# Plain-language messages for fields whose raw validator text would confuse users.
FIELD_MESSAGES = {
    "from_zip": "Moving from: enter a street address, city or ZIP we can find.",
    "to_zip": "Moving to: enter a city, neighborhood or ZIP we can find.",
    "distance_miles": "Enter where you're moving from and to so we can work out the distance.",
    "name": "Name: enter your name.",
    "email": "Email: enter an address like you@example.com.",
    "move_date": "Move date: choose a date.",
    "flexible_days": "Date flexibility: choose one of the options.",
    "home_size": "Home size: choose one of the options.",
    "from_floor": "Current floor: enter a whole number from 0 to 60.",
    "to_floor": "New floor: enter a whole number from 0 to 60.",
    "household_size": "People moving: enter a whole number from 1 to 12.",
    "vehicles": "Vehicles: choose from the listed options.",
    "lease_end": "Current lease ends: enter a date, or leave it blank.",
    "monthly_rent": "Expected rent: enter a whole dollar amount, or leave it blank.",
    "needs": "What you need: choose at least one service.",
    "storage_months": "Storage months: enter a whole number from 0 to 24.",
    "budget_usd": "Total budget: enter a whole dollar amount above $0.",
}


def _readable(error: dict) -> str:
    loc = [str(p) for p in error.get("loc") or () if p != "body"]  # FastAPI form errors start with "body"
    field = loc[0] if loc else ""
    if field == "distance_miles" and error.get("type") == "less_than_equal":
        return "Those addresses are more than 3,500 miles apart. We plan moves within the US only."
    return FIELD_MESSAGES.get(field) or f"{'.'.join(loc) or 'Form'}: {error['msg']}"


# What the intake form sends, so a form with errors comes back filled in. The hidden ZIP and
# distance fields are left out: they're worked out again from the addresses on the next submit.
INTAKE_TEXT_FIELDS = ("name", "email", "from_address", "to_address", "move_date", "flexible_days", "home_size",
                      "from_floor", "to_floor", "household_size", "pets", "lease_end", "monthly_rent",
                      "inventory_text", "storage_months", "budget_usd", "items_to_sell", "notes")
INTAKE_CHECKBOXES = ("from_elevator", "to_elevator")
INTAKE_MULTI = ("needs", "vehicles")


def _submitted(form) -> dict:
    values: dict = {key: str(form.get(key) or "") for key in INTAKE_TEXT_FIELDS}
    values |= {key: bool(form.get(key)) for key in INTAKE_CHECKBOXES}
    values |= {key: [str(v) for v in form.getlist(key)] for key in INTAKE_MULTI}
    return values


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def voice_intake_enabled() -> bool:
    """Voice intake is hidden for the MVP; ENABLE_VOICE_INTAKE=1 turns it back on."""
    return _flag("ENABLE_VOICE_INTAKE")


def photo_inventory_enabled() -> bool:
    """Room photo scan is hidden for the MVP; ENABLE_PHOTO_INVENTORY=1 turns it back on."""
    return _flag("ENABLE_PHOTO_INVENTORY")


def _form_context(errors: list[str], form: dict | None = None) -> dict:
    # The earliest pickable date is yesterday: the server runs on UTC, ahead of a US user's evening.
    return {"home_sizes": [h.value for h in HomeSize], "errors": errors, "today": (date.today() - timedelta(days=1)).isoformat(),
            "voice_intake": voice_intake_enabled(), "photo_inventory": photo_inventory_enabled(), "form": form or {}}


@app.exception_handler(RequestValidationError)
async def form_errors(request: Request, exc: RequestValidationError):
    """A malformed intake (empty budget, a date that isn't one) shows the form again, filled in, instead of raw JSON."""
    if request.method == "POST" and request.url.path == "/plan":
        errors = list(dict.fromkeys(_readable(e) for e in exc.errors()))
        body = exc.body if hasattr(exc.body, "getlist") else None  # FastAPI hands back the parsed form
        context = _form_context(errors, _submitted(body) if body is not None else None)
        return templates.TemplateResponse(request, "intake.html", context, status_code=422)
    return await request_validation_exception_handler(request, exc)


@app.get("/", response_class=HTMLResponse)
def intake_form(request: Request):
    return templates.TemplateResponse(request, "intake.html", _form_context([]))


# ---- address lookup ----

class VoiceAutofillRequest(BaseModel):
    transcript: str


@app.post("/api/intake/voice-autofill")
async def voice_autofill(payload: VoiceAutofillRequest):
    """Return user-reviewable form values extracted from a browser voice transcript."""
    if not voice_intake_enabled():
        raise HTTPException(404, "Voice intake is turned off.")
    try:
        fields = await voice_intake.extract(payload.transcript, pick_model())
    except voice_intake.VoiceIntakeError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"fields": fields.model_dump(mode="json", exclude_none=True)}

@app.get("/api/geo/place")
def api_place(address: str):
    try:
        return geo.geocode(address).model_dump()
    except geo.GeoError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception:
        return JSONResponse({"error": "Address lookup is unavailable right now. Try again in a moment."}, status_code=502)


@app.get("/api/geo/distance")
def api_distance(from_address: str, to_address: str):
    try:
        a, b = geo.geocode(from_address), geo.geocode(to_address)
    except geo.GeoError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception:
        return JSONResponse({"error": "Address lookup is unavailable right now. Try again in a moment."}, status_code=502)
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
        except geo.GeoError as exc:
            errors.append(f"{'Moving from' if label == 'from' else 'Moving to'}: {exc}")
        except Exception:
            errors.append(f"{'Moving from' if label == 'from' else 'Moving to'}: address lookup is unavailable right now. Try again in a moment.")
    from_zip = from_zip or (places["from"].zip if "from" in places else "")
    to_zip = to_zip or (places["to"].zip if "to" in places else "")
    if not distance and from_address and to_address and not errors:
        try:
            a = places.get("from") or geo.geocode(from_address)
            b = places.get("to") or geo.geocode(to_address)
            distance = str(geo.driving_distance(a, b).miles)
        except Exception:
            errors.append("We couldn't calculate the driving distance between those addresses. Try again in a moment.")
    return from_zip, to_zip, distance, errors


@app.post("/api/inventory/photos")
async def inventory_photos(request: Request):
    if not photo_inventory_enabled():
        raise HTTPException(404, "Photo inventory is turned off.")
    # Multipart files may spool to temporary storage; the context closes and
    # removes them on success and on errors. Filenames are never used as paths.
    async with request.form(max_files=10, max_fields=0) as form:
        uploads = form.getlist("photos")
        from starlette.datastructures import UploadFile
        if not 1 <= len(uploads) <= 10 or any(not isinstance(f, UploadFile) for f in uploads):
            raise HTTPException(422, "Upload 1 to 10 images.")
        images = []
        for upload in uploads:
            data = await upload.read(photo_inventory.MAX_IMAGE_BYTES + 1)
            if len(data) > photo_inventory.MAX_IMAGE_BYTES:
                raise HTTPException(422, "Each image must be 8 MB or smaller.")
            images.append((upload.filename or "photo", upload.content_type or "", data))
        try:
            result = await photo_inventory.analyze(images, pick_model())
        except photo_inventory.PhotoInventoryError as exc:
            raise HTTPException(422, str(exc)) from exc
    return result.model_dump()


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


def _candidate_home_results(intake: Intake, listings: dict[str, dict] | None = None) -> list[dict]:
    """Populate decision data only for homes the user chose from Find a home."""
    leave = home.departure_time(intake.move_date, intake.commute_departure_time)
    return [{"address": address,
             "commute": home.commute(address, intake.commute_destination, intake.commute_mode, departure=leave),
             "schools": home.schools(address), "utilities": home.utilities(address),
             "listing": (listings or {}).get(address)}
            for address in intake.candidate_addresses]


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
):
    from_zip, to_zip, distance_miles, geo_errors = _fill_from_addresses(
        from_address, to_address, from_zip.strip(), to_zip.strip(), distance_miles.strip()
    )
    if not distance_miles and not geo_errors:
        geo_errors.append("Enter where you're moving from and to so we can work out the distance.")
    form_errors: list[str] = []
    # A day of slack: the server's date can be ahead of the user's evening.
    if move_date < date.today() - timedelta(days=1):
        form_errors.append("Move date: choose today or a later date.")
    rent: int | None = None
    if monthly_rent.strip():
        try:
            rent = int(monthly_rent)
        except ValueError:
            form_errors.append(FIELD_MESSAGES["monthly_rent"])
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
            lease_end=lease_end or None, monthly_rent=rent,
        )
    except ValidationError as exc:
        errs = exc.errors()
        if geo_errors:  # the address errors already explain the missing ZIPs and distance
            errs = [e for e in errs if not e.get("loc") or e["loc"][0] not in ("from_zip", "to_zip", "distance_miles")]
        form_errors += [_readable(e) for e in errs]
        intake = None
    if intake is None or geo_errors or form_errors:
        errors = list(dict.fromkeys(geo_errors + form_errors))
        return templates.TemplateResponse(request, "intake.html", _form_context(errors, _submitted(await request.form())),
                                          status_code=422)

    inventory = None
    if intake.inventory_text.strip():
        inventory = estimate_inventory(intake.inventory_text)
        if inventory.total_cuft:
            intake = intake.model_copy(update={"volume_cuft": max(20, min(3000, inventory.total_cuft)), "weight_lbs": inventory.total_lbs})

    sid = request.state.sid
    home_results = _candidate_home_results(intake)
    deps = AgentDeps(intake=intake, sources=SOURCES, inventory=inventory, home_results=home_results)
    s = Session(sid=sid, intake=intake, deps=deps, used_llm=model_configured())
    rid = uuid.uuid4().hex[:12]
    SESSIONS[rid] = s
    request.state.touched[rid] = s
    if s.used_llm:
        prompt = "Plan my move."
        if intake.notes:
            prompt += f" My notes: {intake.notes}"
        s.chat.append({"role": "user", "text": prompt})
        await run_turn(s, prompt)
    else:
        s.summary = run_without_llm(deps)
    return RedirectResponse(f"/plan/{rid}", status_code=303)


class PlanNotFound(Exception):
    """A plan from another browser, one that expired, or (without Redis) one lost when the app restarted."""


@app.exception_handler(PlanNotFound)
async def plan_not_found(request: Request, exc: PlanNotFound):
    context = _form_context([]) | {"notice": "That plan is no longer available. It may have expired or been made in another browser. "
                                             "Fill in the form again to make a new one."}
    return templates.TemplateResponse(request, "intake.html", context, status_code=404)


def _session(rid: str, request: Request) -> Session:
    s = SESSIONS.get(rid)
    if not s or s.sid != request.state.sid:
        raise PlanNotFound(rid)
    request.state.touched[rid] = s
    return s


@app.get("/plan/{rid}", response_class=HTMLResponse)
def show_plan(request: Request, rid: str):
    return _render_plan(request, rid, "overview")


@app.get("/plan/{rid}/{view}", response_class=HTMLResponse)
def show_plan_step(request: Request, rid: str, view: str):
    if view not in {"home", "sell", "timeline"}:
        raise HTTPException(404, "Plan step not found.")
    return _render_plan(request, rid, view)


def _render_plan(request: Request, rid: str, view: str):
    s = _session(rid, request)
    d = s.deps
    plans = d.plans
    if plans:
        plans = [plans[d.chosen]] + [p for i, p in enumerate(plans) if i != d.chosen]
    move_day = plans[0].move_date if plans else d.intake.move_date
    options = option_views(plans, d.intake.budget_usd, d.intake.move_date)
    return templates.TemplateResponse(request, "plan.html", {
        "rid": rid, "view": view, "s": s, "intake": d.intake, "plans": plans, "listings": d.listings,
        "options": options, "price_notes": price_notes(options),
        "tl": timeline_view(d.timeline, move_day) if d.timeline else None,
        "steps": trace(s.history),
        "model": pick_model(), "inventory": d.inventory, "vehicle_options": d.vehicle_options,
        "true_cost": d.true_cost, "timeline": d.timeline, "listing_checks": d.listing_checks,
        "home_results": d.home_results,
        "housing_url": f"/housing?{urlencode({'location': d.intake.to_zip, 'rid': rid})}",
        "home_selected": request.query_params.get("home_selected") == "1",
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


@app.get("/safety-checks", response_class=HTMLResponse)
def safety_checks(request: Request):
    """Safety tools for listings the user has already found elsewhere."""
    return templates.TemplateResponse(request, "safety_checks.html", {})


# ---- housing search ----

def _housing_filters(location: str, min_rent: str, max_rent: str, bedrooms: str, bathrooms: str) -> tuple[dict, list[str]]:
    """Validate browser query strings before sending only requested HomeHarvest filters."""
    errors: list[str] = []
    values: dict[str, int | float | str | None] = {"location": location.strip(), "min_rent": None, "max_rent": None, "bedrooms": None, "bathrooms": None}
    for key, raw, label in (("min_rent", min_rent, "minimum rent"), ("max_rent", max_rent, "maximum rent"), ("bedrooms", bedrooms, "bedrooms")):
        if not raw.strip():
            continue
        try:
            values[key] = int(raw)
        except ValueError:
            errors.append(f"Enter a whole number for {label}.")
    if bathrooms.strip():
        try:
            values["bathrooms"] = float(bathrooms)
        except ValueError:
            errors.append("Enter a number for bathrooms.")
    if values["min_rent"] is not None and values["max_rent"] is not None and values["min_rent"] > values["max_rent"]:
        errors.append("Minimum rent cannot be higher than maximum rent.")
    if values["bedrooms"] is not None and not 0 <= values["bedrooms"] <= 10:
        errors.append("Bedrooms must be between 0 and 10.")
    if values["bathrooms"] is not None and not 0 <= values["bathrooms"] <= 20:
        errors.append("Bathrooms must be between 0 and 20.")
    return values, errors


def _housing_links(filters: dict, rid: str, commute: dict, sort: str) -> dict[str, str]:
    """Preserve a housing search while switching a view or clearing its filters."""
    query = {key: value for key, value in {
        **filters, "rid": rid, "commute_destination": commute["destination"],
        "commute_mode": commute["mode"], "sort": sort,
    }.items() if value not in (None, "")}
    base = "/housing"
    return {
        "list": f"{base}?{urlencode({**query, 'view': 'list'})}",
        "map": f"{base}?{urlencode({**query, 'view': 'map'})}",
        "clear": f"{base}?{urlencode({'rid': rid})}" if rid else base,
    }


def _sort_housing_results(results: dict, sort: str, commute: dict) -> str | None:
    """Sort locally; commute lookups happen only after an explicit commute-sort request."""
    listings = results.get("listings", [])
    if sort == "rent_low":
        listings.sort(key=lambda home: (home["rent"] is None, home["rent"] or 0))
    elif sort == "newest":
        listings.sort(key=lambda home: home.get("listed_date") or "", reverse=True)
    elif sort == "commute":
        if not commute["destination"]:
            return "Add a work or school destination before sorting by commute."
        for listing in listings:
            route = home.commute(listing["address"], commute["destination"], commute["mode"])
            listing["commute_minutes"] = route.get("minutes") if route.get("available") else None
        listings.sort(key=lambda home: (home.get("commute_minutes") is None, home.get("commute_minutes") or 0))
    return None


def _map_points(listings: list[dict]) -> list[dict]:
    """Project listing coordinates to a small, dependency-free map overview."""
    located = [home for home in listings if isinstance(home.get("latitude"), (int, float)) and isinstance(home.get("longitude"), (int, float))]
    if not located:
        return []
    lats, lngs = [home["latitude"] for home in located], [home["longitude"] for home in located]
    lat_span, lng_span = max(lats) - min(lats), max(lngs) - min(lngs)
    points = []
    for index, listing in enumerate(located, start=1):
        left = 50 if lng_span == 0 else 10 + (listing["longitude"] - min(lngs)) / lng_span * 80
        top = 50 if lat_span == 0 else 90 - ((listing["latitude"] - min(lats)) / lat_span * 80)
        points.append({"id": listing["id"], "label": index, "address": listing["address"], "left": round(left, 1), "top": round(top, 1)})
    return points


@app.get("/housing", response_class=HTMLResponse)
def housing(request: Request, location: str = "", zip_code: str = "", min_rent: str = "", max_rent: str = "", bedrooms: str = "", bathrooms: str = "",
            rid: str = "", commute_destination: str = "", commute_mode: str = "drive",
            sort: str = "newest", view: str = "list"):
    plan_session = _session(rid, request) if rid else None
    # Keep old shared ZIP links working while making the user-facing input a flexible area search.
    location = location.strip() or zip_code.strip()
    if plan_session and not location:
        location = plan_session.intake.to_zip
    filters, errors = _housing_filters(location, min_rent, max_rent, bedrooms, bathrooms)
    searched = bool(location)
    results = None
    commute = {"destination": commute_destination, "mode": commute_mode}
    if sort not in {"newest", "rent_low", "commute"}:
        sort = "newest"
    if view not in {"list", "map"}:
        view = "list"
    if searched and not errors:
        results = home.rental_listings(**filters)
        if results.get("available"):
            sort_error = _sort_housing_results(results, sort, commute)
            if sort_error:
                errors.append(sort_error)
    return templates.TemplateResponse(request, "housing.html", {
        "filters": {"location": location, "min_rent": min_rent, "max_rent": max_rent, "bedrooms": bedrooms, "bathrooms": bathrooms},
        "errors": errors, "searched": searched, "results": results, "rid": rid,
        "commute": commute, "sort": sort, "view": view,
        "links": _housing_links(filters, rid, commute, sort),
        "map_points": _map_points(results["listings"]) if results and results.get("available") else [],
        "saved_addresses": plan_session.intake.candidate_addresses if plan_session else [],
    })


@app.post("/housing/{rid}/select")
def select_candidate_home(request: Request, rid: str, address: str = Form(...), commute_destination: str = Form(""),
                          commute_mode: str = Form("drive"), listing_url: str = Form(""), contact_name: str = Form(""),
                          contact_phone: str = Form("")):
    """Attach one Find-a-home result to the current move plan (at most two)."""
    s = _session(rid, request)
    address = address.strip()
    if not address:
        raise HTTPException(422, "Choose a home first.")
    if commute_mode not in {"drive", "transit", "walk", "bicycle"}:
        raise HTTPException(422, "Choose drive, transit, walk or bicycle for the commute.")
    addresses = s.intake.candidate_addresses
    if address not in addresses and len(addresses) >= 2:
        raise HTTPException(422, "Your plan already has two candidate homes.")
    updated = s.intake.model_copy(update={
        "candidate_addresses": addresses if address in addresses else [*addresses, address],
        "commute_destination": commute_destination.strip(), "commute_mode": commute_mode,
        "commute_departure_time": "",
    })
    link = home.listing_url(listing_url.strip())
    if link or contact_name.strip() or contact_phone.strip():
        s.listings[address] = {"url": link, "name": contact_name.strip()[:120], "phone": contact_phone.strip()[:20]}
    s.intake = updated
    s.deps.intake = updated
    s.deps.home_results = _candidate_home_results(updated, s.listings)
    # Choosing a home completes the first step; move-service quotes are next.
    return RedirectResponse(f"/plan/{rid}/home?home_selected=1", status_code=303)


def _listing_numbers(price_usd: str, bedrooms: str) -> tuple[int | None, int | None, list[str]]:
    """Rent may be typed as "$2,450" or "2450.50"; bedrooms must be a whole number. Blank means unknown."""
    errors: list[str] = []
    rent = rooms = None
    raw_rent = price_usd.strip().removeprefix("$").replace(",", "")
    if raw_rent:
        try:
            rent = round(float(raw_rent))
            if not 0 <= rent <= 1_000_000:
                raise ValueError
        except (ValueError, OverflowError):  # "abc", "nan", "inf"
            errors.append("Rent: enter the monthly rent in dollars, for example 2450.")
            rent = None
    if bedrooms.strip():
        try:
            rooms = int(bedrooms)
            if not 0 <= rooms <= 20:
                raise ValueError
        except ValueError:
            errors.append("Bedrooms: enter a whole number, or 0 for a studio.")
            rooms = None
    return rent, rooms, errors


@app.post("/listing-check", response_class=HTMLResponse)
def listing_check(request: Request, text: str = Form(...), price_usd: str = Form(""), bedrooms: str = Form(""), address: str = Form("")):
    rent, rooms, errors = _listing_numbers(price_usd, bedrooms)
    result = None if errors else check_listing_rules(text, rent, rooms, address)
    return templates.TemplateResponse(request, "listing_check.html", {
        "result": result, "errors": errors,
        "form": {"text": text, "price_usd": price_usd, "bedrooms": bedrooms, "address": address}}, status_code=422 if errors else 200)


# ---- mover vetting ----

def _mover_check_context(*, result=None, matches=None, error: str = "", name: str = "", usdot: str = "", mc: str = "") -> dict:
    return {"result": result, "matches": matches or [], "error": error,
            "form": {"name": name, "usdot": usdot, "mc": mc}}


@app.get("/mover-check", response_class=HTMLResponse)
def mover_check_form(request: Request):
    return templates.TemplateResponse(request, "mover_check.html", _mover_check_context())


@app.post("/mover-check", response_class=HTMLResponse)
async def mover_check(request: Request, name: str = Form(""), usdot: str = Form(""), mc: str = Form("")):
    """Look up a carrier by name or one identifier in FMCSA's QCMobile registry."""
    name, usdot, mc = name.strip(), usdot.strip(), mc.strip()
    if sum(bool(value) for value in (name, usdot, mc)) != 1:
        return templates.TemplateResponse(
            request, "mover_check.html",
            _mover_check_context(error="Enter a company name, USDOT number, or MC number — one only.", name=name, usdot=usdot, mc=mc),
            status_code=422,
        )
    if usdot and not usdot.isdigit():
        return templates.TemplateResponse(
            request, "mover_check.html",
            _mover_check_context(error="USDOT numbers contain digits only.", name=name, usdot=usdot, mc=mc), status_code=422,
        )
    if mc and not any(char.isdigit() for char in mc):
        return templates.TemplateResponse(
            request, "mover_check.html",
            _mover_check_context(error="Enter the digits from the MC number, for example MC-123456.", name=name, usdot=usdot, mc=mc), status_code=422,
        )
    try:
        adapter = FMCSAAdapter()
        if name:
            matches = await adapter.search(name)
            return templates.TemplateResponse(request, "mover_check.html", _mover_check_context(matches=matches, name=name))
        result = await adapter.check(usdot=int(usdot) if usdot else None, mc=mc or None)
    except AdapterError as exc:
        message = (
            "Add FMCSA_WEB_KEY to .env before checking live records."
            if exc.code == ErrorCode.auth_missing else exc.message
        )
        return templates.TemplateResponse(
            request, "mover_check.html",
            _mover_check_context(error=f"FMCSA lookup unavailable: {message}", name=name, usdot=usdot, mc=mc), status_code=502,
        )
    return templates.TemplateResponse(request, "mover_check.html", _mover_check_context(result=result, usdot=usdot, mc=mc))
