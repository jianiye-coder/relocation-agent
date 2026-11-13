"""Moving errands: the paperwork around a move, prepared for the user to act on.

Each errand is one thing the user has to do themselves (stop utilities, forward
mail, give notice) plus a document that makes it quick: a call script, a letter
to sign, or a checklist. Nothing here contacts anyone. Documents are built from
the intake with plain string templates, never by the model; a value we don't
know renders as a visible <placeholder> instead of disappearing.

Statuses are honest about what happened:
- ``needs_user_action``: we can't finish the document yet; ``blockers`` names
  the intake fields that are missing.
- ``prepared_for_user``: the document is ready; the user still has to use it.
- ``done``: the user marked it done. We never mark anything done on our own.

The errand roster and the status model are adapted from
https://github.com/vnmoorthy/relocate-ai (MIT), narrowed to US moves.
"""

from __future__ import annotations

import string
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal
from urllib.parse import urlencode

from pydantic import BaseModel

from .models import EmailDraft, Intake
from .timeline import Task

Kind = Literal["call_script", "letter", "checklist"]
Status = Literal["needs_user_action", "prepared_for_user", "done"]

KIND_LABEL = {"call_script": "Call script", "letter": "Letter", "checklist": "Checklist"}
STATUS_LABEL = {"needs_user_action": "Needs your input", "prepared_for_user": "Ready to use", "done": "Done"}

# Intake fields an errand can require, with the label shown when one is missing.
FIELD_LABEL = {
    "from_address": "Current street address",
    "to_address": "New street address",
    "lease_end": "Current lease end date",
    "commute_destination": "Work or school address",
}


class Errand(BaseModel):
    id: str
    title: str
    kind: Kind
    due: date
    status: Status
    blockers: list[str] = []
    document: str
    source: str = ""
    task_key: str = ""  # the timeline task this errand belongs to, if one already exists

    @property
    def kind_label(self) -> str:
        return KIND_LABEL[self.kind]

    @property
    def status_label(self) -> str:
        return STATUS_LABEL[self.status]


class _Placeholders(dict):
    def __missing__(self, key: str) -> str:
        return f"<{key.replace('_', ' ')}>"


def render(template: str, values: dict[str, str]) -> str:
    """Fill a template; unknown keys become <placeholders> so gaps stay visible."""
    return string.Formatter().vformat(template, (), _Placeholders({k: v for k, v in values.items() if v}))


def _day(d: date | None) -> str:
    return f"{d:%A, %B} {d.day}, {d.year}" if d else ""


def _is_street_address(address: str, zip_code: str) -> bool:
    """The intake accepts a city or ZIP; letters need a real street address."""
    a = address.strip()
    return bool(a) and a != zip_code and any(c.isdigit() for c in a.split(",")[0])


def _values(intake: Intake, move_date: date) -> dict[str, str]:
    from_ok = _is_street_address(intake.from_address, intake.from_zip)
    to_ok = _is_street_address(intake.to_address, intake.to_zip)
    pets = ", ".join(intake.pets)
    return {
        "name": intake.name,
        "email": str(intake.email),
        "from_address": intake.from_address.strip() if from_ok else "",
        "to_address": intake.to_address.strip() if to_ok else "",
        "from_zip": intake.from_zip,
        "to_zip": intake.to_zip,
        "move_date": _day(move_date),
        "day_before_move": _day(move_date - timedelta(days=1)),
        "day_after_move": _day(move_date + timedelta(days=1)),
        "lease_end": _day(intake.lease_end),
        "pets": pets,
        "pet_names": pets,
        "commute_destination": intake.commute_destination.strip(),
        "today": _day(date.today()),
    }


@dataclass(frozen=True)
class _Spec:
    id: str
    title: str
    kind: Kind
    due: Callable[[Intake, date], date]
    template: str
    requires: tuple[str, ...] = ()
    when: Callable[[Intake], bool] = lambda i: True
    source: str = ""
    task_key: str = ""


def _before(days: int) -> Callable[[Intake, date], date]:
    return lambda intake, move: move - timedelta(days=days)


def _notice_due(intake: Intake, move: date) -> date:
    return (intake.lease_end or move) - timedelta(days=30)


SPECS: list[_Spec] = [
    _Spec(
        id="landlord_notice", title="Give written notice to your current landlord", kind="letter",
        due=_notice_due, requires=("from_address", "lease_end"), task_key="landlord_notice",
        template="""{today}

To: {landlord_name}
Re: Notice of intent to vacate, {from_address}

Dear {landlord_name},

This letter is my written notice that I will move out of {from_address} at the end of my lease on {lease_end}.

Please send my security deposit and an itemized statement of any deductions to my new address:
{to_address}

I would like to schedule a walk-through before I return the keys. You can reach me at {email}.

Sincerely,
{name}

Before you send it:
- Check your lease for the notice period and how notice must be delivered (mail, email, portal).
- Keep a copy and proof of delivery.
""",
    ),
    _Spec(
        id="utilities_start", title="Start electricity and gas at the new address", kind="call_script",
        due=_before(21), requires=("to_address",), task_key="utilities_start",
        template="""Who to call: the electric and gas utility for {to_address}. Your landlord or the property listing usually names it.

Script:
"Hi, I'm {name}. I'm moving to {to_address} and need to start electric and gas service in my name on {day_before_move}, so the power is on when I arrive on {move_date}."

Have ready:
- Your new address and move-in date
- A way to verify identity (the utility will tell you what it accepts)
- Your email: {email}

Write down: confirmation number <confirmation number>, start date, and any deposit they quote.
""",
    ),
    _Spec(
        id="utilities_stop", title="Stop utilities at your old address", kind="call_script",
        due=lambda intake, move: move + timedelta(days=1), requires=("from_address", "to_address"),
        task_key="utilities_stop",
        template="""Who to call: your electric, gas, and (if they're in your name) water and trash providers for {from_address}.

Script:
"Hi, I'm {name}, account <account number>. I'm moving out of {from_address}. Please stop service in my name effective {day_after_move}, and send the final bill to {to_address}."

Ask:
- Is a final meter reading scheduled, or should I take a photo of the meter?
- Will any deposit be refunded, and how?

Write down: confirmation number <confirmation number> and the stop date.
""",
    ),
    _Spec(
        id="internet", title="Move or cancel internet service", kind="call_script",
        due=_before(21), requires=("from_address", "to_address"),
        template="""Two calls: cancel (or transfer) service at {from_address}, and order service at {to_address}.

Cancel or transfer script:
"Hi, I'm {name}, account <account number>. I'm moving on {move_date}. Can my service transfer to {to_address}? If not, please cancel effective {move_date} and tell me how to return the equipment."

New service: compare providers that serve {to_address} on the FCC National Broadband Map, then book installation for {day_before_move} or soon after.

Write down: equipment return deadline, return method, and confirmation numbers.
""",
        source="https://broadbandmap.fcc.gov/",
    ),
    _Spec(
        id="usps_forwarding", title="Set up USPS mail forwarding", kind="checklist",
        due=_before(7), requires=("from_address", "to_address"), task_key="usps",
        template="""Set up a change of address on the official USPS site only (moversguide.usps.com). Look-alike sites charge extra fees.

- Old address: {from_address}
- New address: {to_address}
- Start date: {move_date}
- Choose individual or family; a family change covers everyone with the same last name.
- USPS verifies your identity online with a small card charge; have a card with a billing address you can confirm.
- Save the confirmation code USPS emails you.
""",
        source="https://moversguide.usps.com",
    ),
    _Spec(
        id="dmv_address", title="Update your address with the California DMV", kind="checklist",
        due=lambda intake, move: move + timedelta(days=10), requires=("to_address",),
        when=lambda i: bool(i.vehicles), task_key="dmv",
        source="https://www.dmv.ca.gov/portal/dmv-virtual-office/change-of-address/",
        template="""California asks you to update your address within 10 days of moving.

- Use the DMV online change of address; it covers your driver's license and vehicle registration together.
- New address: {to_address}
- Have ready: driver's license number <license number> and license plate(s) <plate number>.
- Your card doesn't have to be reissued; the DMV updates its records.
- Moving out of California instead? Your new state sets its own deadline to register the vehicle and switch your license.
""",
    ),
    _Spec(
        id="car_insurance", title="Update the garaging address on your car insurance", kind="call_script",
        due=_before(7), requires=("to_address",), when=lambda i: bool(i.vehicles),
        template="""Who to call: your auto insurer (or use their app).

Script:
"Hi, I'm {name}, policy <policy number>. I'm moving on {move_date}. Please update the address where my vehicle is kept to {to_address}, effective {move_date}, and tell me whether my premium changes."

Rates depend on ZIP code, so ask for the new premium before you agree to the change.
Write down: the effective date and the new premium.
""",
    ),
    _Spec(
        id="vet_records", title="Get your pet's records from the vet", kind="letter",
        due=_before(21), when=lambda i: bool(i.pets),
        template="""To: <vet clinic> (<vet email>)
Subject: Records request for {pet_names}

Hello,

I'm moving on {move_date} and would like a copy of the complete medical records for my pet(s): {pet_names}, including vaccination history and current prescriptions. Please email them to {email}.

Thank you,
{name}

Also:
- Ask for enough of any medication to last until you find a new vet.
- Check whether your new city requires a pet license.
- Update the address on your pet's microchip registry.
""",
    ),
    _Spec(
        id="school_enrollment", title="Contact the school district at your new address", kind="letter",
        due=_before(42), requires=("to_address",), when=lambda i: i.has_children,
        template="""To: <school district enrollment office>
Subject: Enrollment inquiry for a family moving to {to_zip}

Hello,

My family is moving to {to_address} on {move_date}. I would like to enroll my child, <child's name>, in grade <grade>.

Could you tell me which school serves this address, what documents you need (proof of address, immunization records, transcripts), and the enrollment timeline?

Thank you,
{name}
{email}

Ask the current school for records and a withdrawal letter before you leave.
""",
    ),
    _Spec(
        id="memberships_cancel", title="Cancel memberships you won't keep", kind="letter",
        due=_before(30),
        template="""{today}

To: <gym or club name>
Re: Membership cancellation, member number <member number>

I am writing to cancel my membership effective {move_date}, because I am moving out of the area. Please stop all charges after that date and confirm the cancellation in writing to {email}.

{name}
Signature: ______________________

Before you send it: check your contract for the notice period and the required cancellation method (some require certified mail or an in-person visit). Many contracts waive fees when you move far away; ask.
""",
    ),
    _Spec(
        id="health_records", title="Transfer prescriptions and doctor records", kind="checklist",
        due=_before(21),
        template="""- Refill prescriptions so you have enough to last past {move_date}.
- Ask your pharmacy to transfer prescriptions to a pharmacy near {to_zip}. Chains can usually do it in-store.
- Request copies of your medical records from your doctors (each office has a records-release form you sign).
- Check which doctors near {to_zip} are in your health plan's network before you book.
- Update your address with your health insurer.
""",
    ),
    _Spec(
        id="bank_and_cards", title="Update your address with banks and cards", kind="checklist",
        due=_before(7),
        template="""Do this yourself in each app or website, or by phone. Never give anyone your password or one-time codes.

- Checking and savings accounts
- Credit cards (a wrong billing address can decline online purchases)
- Investment and retirement accounts
- Loans and student loans
- Payment apps

New address for all of them: {to_address}
""",
    ),
    _Spec(
        id="address_list", title="Tell everyone else your new address", kind="checklist",
        due=lambda intake, move: move + timedelta(days=7),
        template="""New address: {to_address}

- Employer and payroll (your state tax withholding may change)
- IRS: file Form 8822 if you won't file a return from the new address soon
- Voter registration: California asks you to re-register when you move (registertovote.ca.gov)
- Health, renters, and life insurance
- Subscriptions and online stores with saved addresses
- Family and friends
""",
    ),
    _Spec(
        id="furniture_setup", title="Plan the furniture for the new place", kind="checklist",
        due=_before(14),
        template="""- Measure doorways, stairs and the elevator at {to_address}, and your largest pieces.
- Decide what won't fit or isn't worth moving; sell or donate it before {move_date}.
- Schedule new furniture deliveries for after {move_date}, not on move day.
- Ask the building about elevator reservations and delivery hours.
""",
    ),
    _Spec(
        id="first_48_hours", title="Plan your first 48 hours", kind="checklist",
        due=lambda intake, move: move,
        template="""Pack a first-night box that travels with you, not in the truck:
- Phone chargers, medication, documents, keys
- Sheets, towels, toilet paper, soap, a change of clothes
- Basic tools, box cutter, light bulbs, trash bags
- Snacks and water; pet food and bowls if you have pets

On arrival at {to_address}:
- Photograph every room before unpacking (for your deposit and any damage claims).
- Check that power, water and internet work.
- Find the nearest grocery store and pharmacy near {to_zip}.
""",
    ),
    _Spec(
        id="commute_route", title="Try your new commute", kind="checklist",
        due=lambda intake, move: move + timedelta(days=1), requires=("to_address", "commute_destination"),
        when=lambda i: bool(i.commute_destination.strip()),
        template="""From {to_address} to {commute_destination}.

- Check the route at the time you'll actually leave: {commute_link}
- Try it once before your first day.
- Look up transit passes, parking permits, or bike parking at the destination.
""",
    ),
]

SPEC_BY_ID = {s.id: s for s in SPECS}


def _missing(spec: _Spec, values: dict[str, str]) -> list[str]:
    return [FIELD_LABEL[f] for f in spec.requires if not values.get(f)]


def build(intake: Intake, move_date: date, done: tuple[str, ...] | set[str] = ()) -> list[Errand]:
    """Every errand that applies to this move, with its document and status, in due-date order."""
    values = _values(intake, move_date)
    if values["to_address"] and values["commute_destination"]:
        values["commute_link"] = "https://www.google.com/maps/dir/?" + urlencode(
            {"api": 1, "origin": values["to_address"], "destination": values["commute_destination"],
             "travelmode": {"drive": "driving", "bicycle": "bicycling"}.get(intake.commute_mode, intake.commute_mode)})
    errands = []
    for spec in SPECS:
        if not spec.when(intake):
            continue
        blockers = _missing(spec, values)
        status: Status = "done" if spec.id in done else "needs_user_action" if blockers else "prepared_for_user"
        errands.append(Errand(
            id=spec.id, title=spec.title, kind=spec.kind, due=spec.due(intake, move_date), status=status,
            blockers=blockers, document=render(spec.template, values).strip(), source=spec.source,
            task_key=spec.task_key,
        ))
    errands.sort(key=lambda e: e.due)
    return errands


def attach(tasks: list[Task], errands: list[Errand], today: date | None = None) -> list[Task]:
    """Link each errand to its timeline task, adding a task for errands the timeline doesn't have yet."""
    today = today or date.today()
    by_key = {t.key: t for t in tasks if t.key}
    out = list(tasks)
    for e in errands:
        task = by_key.get(e.task_key) if e.task_key else None
        if task is not None:
            task.errand = e.id
        else:
            out.append(Task(due=e.due, title=e.title, errand=e.id, source=e.source, overdue=e.due < today))
    out.sort(key=lambda t: t.due)
    return out


SEPARATOR = "-" * 40  # not "=": quoted-printable email encoding turns each one into "=3D"


def arrival_pack(intake: Intake, errands: list[Errand], move_date: date) -> EmailDraft:
    """One email with every open errand's document, addressed only to the user."""
    open_ = [e for e in errands if e.status != "done"]
    blocked = [e for e in open_ if e.status == "needs_user_action"]
    lines = [
        f"Hi {intake.name},",
        "",
        f"Here are the documents for your move on {_day(move_date)}: {len(open_)} errand{'s' if len(open_) != 1 else ''}, "
        "in the order they're due. Each one is something you do yourself; nothing here was sent to anyone else.",
        "Values in <angle brackets> are details we don't have. Fill them in before you use a document.",
    ]
    if blocked:
        lines += ["", "Some documents need details we don't have yet:"]
        lines += [f"- {e.title}: {', '.join(e.blockers)}" for e in blocked]
    for n, e in enumerate(open_, 1):
        lines += ["", SEPARATOR, f"{n}. {e.title}", f"{e.kind_label} · due {_day(e.due)}", SEPARATOR, "", e.document]
        if e.source:
            lines += ["", f"Official page: {e.source}"]
    lines += ["", "-- ", "Relocation Agent"]
    return EmailDraft(
        offer_id="arrival-pack",
        to=str(intake.email),
        subject=f"Your moving documents for {move_date:%b} {move_date.day}",
        body="\n".join(lines),
    )


def sent_record(ok: bool, to: str, detail: str, mode: str) -> dict:
    return {"ok": ok, "to": to, "detail": detail, "mode": mode, "at": datetime.now().isoformat(timespec="seconds")}
