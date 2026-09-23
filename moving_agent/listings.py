"""Rule-based rental listing checks: below-market price, scam language, duplicates, missing basics.

Market rents are rough San Francisco medians for the demo; replace them with a real rent data source.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

SF_MEDIAN_RENT = {0: 2400, 1: 3100, 2: 4200, 3: 5400}  # bedrooms -> rough monthly median, estimate
MEDIAN_SOURCE = "rough San Francisco median rents (estimate for the demo)"
BELOW_MARKET_RATIO = 0.7

SCAM_PATTERNS = {
    r"\bwire\b|western union|moneygram": "Asks for a wire transfer. Legitimate landlords don't need wired money.",
    r"gift ?card|bitcoin|crypto": "Asks for gift cards or crypto.",
    r"(zelle|venmo|cash ?app|paypal).{0,60}(deposit|hold|before)|(deposit|hold).{0,60}(zelle|venmo|cash ?app|paypal)":
        "Wants a deposit by app before you've seen the place or signed a lease.",
    r"out of (the )?(country|town)|overseas|missionary|deployed": "Owner says they're away and can't show the place.",
    r"(can'?t|cannot|unable to) (show|meet)|no (viewings?|showings?|tours?)": "No viewing offered.",
    r"send (the )?(deposit|money|payment).{0,40}(hold|reserve|secure)|to (hold|reserve|secure) (it|the (unit|apartment|place))":
        "Pressure to pay to hold the unit.",
    r"keys? (will be )?(mailed|shipped|sent)": "Offers to mail the keys.",
    r"(sign|lease).{0,30}(without|before) (seeing|viewing)": "Wants you to sign before seeing it.",
}


class ListingFlag(BaseModel):
    kind: str  # below_market | scam_language | duplicate | missing_info
    message: str


class ListingCheck(BaseModel):
    risk: str  # low | medium | high
    flags: list[ListingFlag]
    market_rent_usd: int | None = None
    basis: str = MEDIAN_SOURCE


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def check(text: str, price_usd: int | None = None, bedrooms: int | None = None, address: str = "",
          other_listings: list[str] | None = None) -> ListingCheck:
    flags: list[ListingFlag] = []
    low = text.lower()
    for pattern, message in SCAM_PATTERNS.items():
        if re.search(pattern, low):
            flags.append(ListingFlag(kind="scam_language", message=message))
    market = SF_MEDIAN_RENT.get(min(bedrooms, 3)) if bedrooms is not None else None
    if price_usd and market and price_usd < BELOW_MARKET_RATIO * market:
        pct = round(100 * (1 - price_usd / market))
        flags.append(ListingFlag(kind="below_market", message=f"${price_usd:,} is {pct}% below the rough median (${market:,}) for {bedrooms} bedroom(s)."))
    for other in other_listings or []:
        if similarity(text, other) >= 0.8:
            flags.append(ListingFlag(kind="duplicate", message="Nearly the same text as another listing (often a copied or reposted scam)."))
            break
    if not address and not re.search(r"\d+\s+\w+\s+(st|street|ave|avenue|blvd|rd|road|way|ct|court|pl|place)\b", low):
        flags.append(ListingFlag(kind="missing_info", message="No street address. Ask for it and look it up before paying anything."))
    scam = sum(f.kind == "scam_language" for f in flags)
    risk = "high" if scam >= 2 or (scam and any(f.kind == "below_market" for f in flags)) else "medium" if flags else "low"
    return ListingCheck(risk=risk, flags=flags, market_rent_usd=market)
