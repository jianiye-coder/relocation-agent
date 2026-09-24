"""The adapter interface: one normalized request in, normalized results out.

Every provider (a sample catalog, an official API like FMCSA or Warp, an optional
user-run scraper) implements the same interface, declares what it can do in
`AdapterMetadata`, and fails with one of the shared `ErrorCode`s. Every price it
returns carries its source, a timestamp and a confidence.
"""

from __future__ import annotations

from abc import ABC
from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------- request ----------

class Location(BaseModel):
    zip: str = Field(pattern=r"^\d{5}$")
    city: str = ""
    state: str = Field(default="", description="Two-letter state code")
    address: str = ""
    lat: float | None = None
    lon: float | None = None


class Access(BaseModel):
    floor: int = 1
    elevator: bool = False


class Vehicle(BaseModel):
    kind: Literal["car", "suv", "truck", "motorcycle"] = "car"
    operable: bool = True


class MoveRequest(BaseModel):
    """Normalized request built from the intake profile. Adapters read only this."""

    origin: Location
    destination: Location
    move_date: date
    flexible_days: int = 0
    distance_miles: int
    volume_cuft: int
    weight_lbs: int
    origin_access: Access = Field(default_factory=Access)
    destination_access: Access = Field(default_factory=Access)
    storage_months: int = 0
    vehicles: list[Vehicle] = Field(default_factory=list)
    household_size: int = 1
    pets: list[str] = Field(default_factory=list)
    lease_end: date | None = None
    budget_usd: int | None = None


# ---------- results ----------

class ServiceType(str, Enum):
    truck_rental = "truck_rental"
    full_service_movers = "full_service_movers"
    labor_only = "labor_only"
    container = "container"
    ltl_freight = "ltl_freight"
    storage = "storage"
    vehicle_shipping = "vehicle_shipping"
    vehicle_drive = "vehicle_drive"


class PriceKind(str, Enum):
    """How firm a price is. Confidence scores are capped by kind (see CONFIDENCE_CAP)."""

    firm_quote = "firm_quote"          # the provider quoted this move
    published_rate = "published_rate"  # public rate card applied to this move
    estimate = "estimate"              # our model (rules of thumb, ranges)
    sample = "sample"                  # illustrative demo data, not a real price


CONFIDENCE_CAP = {PriceKind.firm_quote: 1.0, PriceKind.published_rate: 0.8, PriceKind.estimate: 0.5, PriceKind.sample: 0.3}


class Quote(BaseModel):
    """One normalized price for one service for this move."""

    adapter_id: str
    provider: str
    service_type: ServiceType
    title: str
    price_usd: float = Field(ge=0)
    price_low_usd: float | None = None
    price_high_usd: float | None = None
    available_on: date
    valid_until: datetime | None = None
    source: str = Field(min_length=1, description="URL or name of where the number came from")
    fetched_at: datetime
    confidence: float = Field(ge=0, le=1)
    price_kind: PriceKind
    price_basis: str = Field(description="How the price was calculated, shown to the user")
    rating: float | None = None
    crew_size: int | None = None
    contact_email: str | None = None
    contact_url: str | None = None

    def model_post_init(self, _ctx) -> None:
        cap = CONFIDENCE_CAP[self.price_kind]
        if self.confidence > cap:
            raise ValueError(f"confidence {self.confidence} is too high for a {self.price_kind.value} price (max {cap})")


class CarrierCheck(BaseModel):
    """Result of vetting a mover against FMCSA records."""

    adapter_id: str
    query: str
    found: bool
    usdot_number: int | None = None
    mc_number: int | None = None
    legal_name: str = ""
    dba_name: str = ""
    allowed_to_operate: bool | None = None
    city: str = ""
    state: str = ""
    source: str
    fetched_at: datetime
    notes: list[str] = Field(default_factory=list)


# ---------- metadata ----------

class Capability(str, Enum):
    quotes = "quotes"
    listings = "listings"
    storage = "storage"
    vetting = "vetting"
    vehicle = "vehicle"


class SourceKind(str, Enum):
    official_api = "official_api"
    public_data = "public_data"
    sample = "sample"
    unofficial_scrape = "unofficial_scrape"


class RateLimit(BaseModel):
    requests: int
    per_seconds: int


class AdapterMetadata(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    capabilities: list[Capability] = Field(min_length=1)
    service_types: list[ServiceType] = Field(default_factory=list)
    coverage: list[str] = Field(default_factory=lambda: ["US"], description='"US", state codes like "CA", or city pairs like "CA:Los Angeles>CA:San Francisco"')
    auth: Literal["none", "api_key", "oauth"] = "none"
    auth_env: list[str] = Field(default_factory=list, description="Environment variables the adapter needs")
    rate_limit: RateLimit | None = None
    source_kind: SourceKind
    cache_ttl_seconds: int = 6 * 3600
    docs_url: str = ""

    @property
    def official(self) -> bool:
        return self.source_kind in (SourceKind.official_api, SourceKind.public_data)

    @property
    def enabled_by_default(self) -> bool:
        """Unofficial/scraped adapters are opt-in only (ToS risk)."""
        return self.source_kind != SourceKind.unofficial_scrape


# ---------- errors ----------

class ErrorCode(str, Enum):
    unavailable = "unavailable"        # provider down, timeout, 5xx
    blocked = "blocked"                # 403, captcha, bot detection
    no_coverage = "no_coverage"        # request outside the adapter's area or service
    stale = "stale"                    # only an expired cached result is available
    auth_missing = "auth_missing"      # required key not configured
    rate_limited = "rate_limited"      # 429 or our own limiter
    invalid_request = "invalid_request"  # request can't be served as given


class AdapterError(Exception):
    def __init__(self, code: ErrorCode, message: str, retry_after_s: int | None = None):
        super().__init__(message)
        self.code, self.message, self.retry_after_s = code, message, retry_after_s


class AdapterResult(BaseModel):
    """What the registry returns per adapter: results or a typed error, never an exception."""

    adapter_id: str
    quotes: list[Quote] = Field(default_factory=list)
    error: ErrorCode | None = None
    message: str = ""
    from_cache: bool = False
    stale: bool = False
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------- the interface ----------

def now() -> datetime:
    return datetime.now(timezone.utc)


def _state_pair(req: MoveRequest) -> tuple[str, str]:
    return req.origin.state.upper(), req.destination.state.upper()


class QuoteAdapter(ABC):
    """Implement `metadata` and `fetch_quotes`. Raise AdapterError for anything that goes wrong."""

    metadata: AdapterMetadata

    def covers(self, req: MoveRequest) -> bool:
        cov = self.metadata.coverage
        if "US" in cov:
            return True
        o, d = _state_pair(req)
        pair = f"{o}:{req.origin.city}>{d}:{req.destination.city}"
        return pair in cov or (o in cov and d in cov)

    async def fetch_quotes(self, req: MoveRequest) -> list[Quote]:  # pragma: no cover - interface
        raise NotImplementedError


class VettingAdapter(ABC):
    metadata: AdapterMetadata

    async def check(self, usdot: int | None = None, mc: str | None = None) -> CarrierCheck:  # pragma: no cover
        raise NotImplementedError
