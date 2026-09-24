"""Turn a user-approved voice transcript into editable intake fields."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class VoiceIntakeError(ValueError):
    """A user-visible voice-autofill failure."""


class VoiceIntakeFields(BaseModel):
    """Only values that belong in the intake form; unknown facts stay absent."""

    name: str | None = None
    email: str | None = None
    from_address: str | None = None
    to_address: str | None = None
    from_zip: str | None = Field(default=None, pattern=r"^\d{5}$")
    to_zip: str | None = Field(default=None, pattern=r"^\d{5}$")
    distance_miles: int | None = Field(default=None, ge=0, le=3500)
    move_date: date | None = None
    flexible_days: Literal[0, 1, 3, 7] | None = None
    home_size: Literal["studio", "1br", "2br", "3br"] | None = None
    from_floor: int | None = Field(default=None, ge=0, le=60)
    to_floor: int | None = Field(default=None, ge=0, le=60)
    from_elevator: bool | None = None
    to_elevator: bool | None = None
    household_size: int | None = Field(default=None, ge=1, le=12)
    pets: list[str] | None = None
    vehicles: list[Literal["car", "suv", "truck", "motorcycle"]] | None = None
    lease_end: date | None = None
    monthly_rent: int | None = Field(default=None, ge=0)
    inventory_text: str | None = None
    needs: list[Literal["truck", "labor", "storage", "container"]] | None = None
    storage_months: int | None = Field(default=None, ge=0, le=24)
    budget_usd: int | None = Field(default=None, gt=0)
    items_to_sell: list[str] | None = None
    notes: str | None = None


async def extract(transcript: str, model_name: str | None) -> VoiceIntakeFields:
    """Extract stated facts with the selected model; never manufacture unknown fields."""
    transcript = transcript.strip()
    if not transcript:
        raise VoiceIntakeError("Say or paste a few details before filling the form.")
    if len(transcript) > 8_000:
        raise VoiceIntakeError("Keep the voice transcript under 8,000 characters.")
    if not model_name:
        raise VoiceIntakeError("Configure an AI provider before using voice autofill. You can still complete the form manually.")

    from pydantic_ai import Agent
    from pydantic_ai.usage import UsageLimits
    from .agent import build_model

    agent = Agent(
        build_model(model_name), output_type=VoiceIntakeFields, defer_model_check=True,
        instructions=(
            "Extract only facts explicitly stated in this US moving-intake transcript. "
            "Never guess, infer, or fabricate a field. Return null for anything not stated. "
            f"Today is {date.today().isoformat()}; resolve relative dates only when unambiguous and return dates as YYYY-MM-DD. "
            "Normalize home size to studio, 1br, 2br, or 3br (use 3br for three or more bedrooms). "
            "Use only truck, labor, storage, container for needs; only car, suv, truck, motorcycle for vehicles. "
            "Keep addresses and notes as spoken; do not calculate driving distance, price, volume, or weight."
        ),
    )
    try:
        result = await asyncio.wait_for(agent.run(transcript, usage_limits=UsageLimits(request_limit=3)), timeout=45)
    except Exception as exc:
        raise VoiceIntakeError(f"Voice autofill is unavailable: {type(exc).__name__}.") from exc
    return result.output
