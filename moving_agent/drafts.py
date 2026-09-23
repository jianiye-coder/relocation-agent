"""Deterministic drafts for quote-request emails and resale listings.

The agent may rewrite the wording, but every number here comes from the intake
and the offer, never from the model.
"""

from __future__ import annotations

from .models import EmailDraft, Intake, ListingDraft, Offer

SERVICE_ASK = {
    "truck": "a truck rental",
    "labor": "movers to help load and unload",
    "storage": "a storage unit",
    "container": "a moving container",
}


def _access(floor: int, elevator: bool) -> str:
    if floor <= 1:
        return "ground floor"
    return f"floor {floor}, {'with' if elevator else 'no'} elevator"


def _where(address: str, zip_code: str) -> str:
    """Full address when we have one, otherwise the ZIP (e.g. no new home yet)."""
    if address and address.strip() != zip_code:
        return address.strip()
    return f"ZIP {zip_code}"


def _day(d) -> str:
    return f"{d:%A, %B} {d.day}, {d.year}"


def quote_request(intake: Intake, offer: Offer, note: str = "") -> EmailDraft:
    to = offer.contact_email or ""
    subject = f"Quote request: {offer.title} on {offer.available_on:%a %b} {offer.available_on.day}, {offer.available_on.year}"
    lines = [
        "Hello,",
        "",
        f"I'm moving on {_day(offer.available_on)} and would like to book {SERVICE_ASK[offer.service]} ({offer.title}).",
        "",
        "Move details:",
        f"- From {_where(intake.from_address, intake.from_zip)} ({_access(intake.from_floor, intake.from_elevator)})",
        f"- To {_where(intake.to_address, intake.to_zip)} ({_access(intake.to_floor, intake.to_elevator)})",
        f"- About {intake.distance_miles} miles",
        f"- About {intake.volume} cubic feet of belongings ({intake.home_size.value} home)",
    ]
    if offer.service == "labor" and not intake.is_local:
        lines.append(f"- I need help loading at ZIP {intake.from_zip}; please tell me if you also cover unloading at ZIP {intake.to_zip}")
    if offer.service == "storage":
        lines.append(f"- Storage needed for {intake.storage_months} month(s), starting that day")
    if intake.flexible_days:
        lines.append(f"- I can move up to {intake.flexible_days} day(s) earlier or later if that helps")
    if note:
        lines.append(f"- Please note: {note}")
    lines += [
        "",
        f"The rate I found is about ${offer.price_usd:,.2f} ({offer.price_basis}). "
        "Could you confirm availability and your final price?",
        "",
        "Thank you,",
        intake.name,
        intake.email,
    ]
    return EmailDraft(offer_id=offer.id, to=to, subject=subject, body="\n".join(lines))


def listing(item: str, to_zip_hint: str) -> ListingDraft:
    clean = item.strip()
    return ListingDraft(
        item=clean,
        title=f"{clean} - moving sale, must go",
        description=(
            f"Selling my {clean} because I'm moving. Good condition, smoke-free home. "
            "Pickup only; you'll need to bring help for large items. Message me with a time that works."
        ),
    )
