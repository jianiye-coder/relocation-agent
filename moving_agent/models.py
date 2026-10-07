"""Shared data models: intake, offers, plans, email drafts."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, model_validator


class HomeSize(str, Enum):
    studio = "studio"
    one_bed = "1br"
    two_bed = "2br"
    three_bed = "3br"


# Typical volume moved per home size, in cubic feet (industry rule of thumb).
HOME_VOLUME_CUFT: dict[HomeSize, int] = {
    HomeSize.studio: 250,
    HomeSize.one_bed: 450,
    HomeSize.two_bed: 750,
    HomeSize.three_bed: 1100,
}

Service = Literal["truck", "labor", "storage", "container"]


class Intake(BaseModel):
    """What the user tells us on the intake form."""

    name: str = Field(min_length=1)
    email: EmailStr
    from_address: str = ""
    to_address: str = ""
    from_zip: str = Field(pattern=r"^\d{5}$")
    to_zip: str = Field(pattern=r"^\d{5}$")
    distance_miles: int = Field(ge=0, le=3500, description="Approximate driving distance")
    move_date: date
    flexible_days: int = Field(default=0, ge=0, le=14, description="Days the move can shift either way")
    home_size: HomeSize
    volume_cuft: int | None = Field(default=None, ge=20, le=3000, description="Overrides the home-size estimate")
    from_floor: int = Field(default=1, ge=0, le=60)
    from_elevator: bool = False
    to_floor: int = Field(default=1, ge=0, le=60)
    to_elevator: bool = False
    needs: list[Service] = Field(min_length=1)
    storage_months: int = Field(default=0, ge=0, le=24)
    budget_usd: int = Field(gt=0)
    items_to_sell: list[str] = Field(default_factory=list)
    notes: str = ""
    household_size: int = Field(default=1, ge=1, le=12)
    pets: list[str] = Field(default_factory=list)
    vehicles: list[Literal["car", "suv", "truck", "motorcycle"]] = Field(default_factory=list)
    has_children: bool = Field(default=False, description="School-age children moving too")
    lease_end: date | None = None
    monthly_rent: int | None = Field(default=None, ge=0, description="Expected rent at the new place")
    candidate_addresses: list[str] = Field(default_factory=list, max_length=2)
    commute_destination: str = ""
    commute_mode: Literal["drive", "transit", "walk", "bicycle"] = "drive"
    commute_departure_time: str = Field(default="", pattern=r"^$|^([01]\d|2[0-3]):[0-5]\d$")
    inventory_text: str = Field(default="", description="Rooms or items, one per line; overrides the home-size estimate")
    weight_lbs: int | None = None

    @model_validator(mode="after")
    def _storage_needs_months(self) -> "Intake":
        if "storage" in self.needs and self.storage_months == 0:
            self.storage_months = 1
        return self

    @property
    def volume(self) -> int:
        return self.volume_cuft or HOME_VOLUME_CUFT[self.home_size]

    @property
    def weight(self) -> int:
        return self.weight_lbs or self.volume * 7

    @property
    def is_local(self) -> bool:
        return self.distance_miles <= 50


class Offer(BaseModel):
    """One priced option from one provider for this specific move."""

    id: str
    provider: str
    service: Service
    title: str
    price_usd: float
    available_on: date
    rating: float | None = None
    crew_size: int | None = None
    contact_email: str | None = None
    contact_url: str | None = None
    price_basis: str = Field(description="How the price was calculated, shown to the user")
    source: str = Field(description="Where the rate came from, e.g. 'sample catalog' or a live API")
    confidence: float | None = None
    price_kind: str | None = None
    fetched_at: datetime | None = None


class Requirements(BaseModel):
    """Extra constraints the agent picked up from the notes or the chat."""

    exclude_providers: list[str] = Field(default_factory=list)
    min_movers: int | None = None
    max_movers: int | None = None
    earliest_move_date: date | None = None
    latest_move_date: date | None = None
    email_note: str = ""


class Plan(BaseModel):
    """A combination of offers that covers every service the user needs."""

    offers: list[Offer]
    move_date: date
    total_usd: float
    within_budget: bool
    over_budget_by: float = 0.0
    reason: str


class EmailDraft(BaseModel):
    offer_id: str
    to: str
    subject: str
    body: str


class ListingDraft(BaseModel):
    item: str
    title: str
    description: str


class AgentResult(BaseModel):
    """What the agent hands back to the app."""

    summary: str
    plans: list[Plan]
    emails: list[EmailDraft]
    listings: list[ListingDraft] = Field(default_factory=list)
