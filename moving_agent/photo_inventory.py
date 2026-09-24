"""Photo-assisted inventory extraction with deterministic moving math.

Images are accepted only for the duration of a request. The visual model may
name and count visible objects; :mod:`moving_agent.inventory` remains the sole
source of cubic-foot and weight calculations.
"""

from __future__ import annotations

from collections.abc import Sequence
import asyncio
from io import BytesIO
import warnings

from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError

from .inventory import Inventory, estimate


MAX_IMAGES = 10
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


class PhotoInventoryError(ValueError):
    """A user-visible validation or visual-model availability error."""


class DetectedItem(BaseModel):
    item: str = Field(min_length=1, max_length=80)
    qty: int = Field(ge=1, le=99)


class DetectedInventory(BaseModel):
    items: list[DetectedItem] = Field(max_length=50)


class PhotoInventoryResult(BaseModel):
    items: list[DetectedItem]
    editable_text: str
    estimate: Inventory
    disclaimer: str = "Photo estimate: review and correct the detected items before planning. Volumes and weight use deterministic moving rules."


def _validate(images: Sequence[tuple[str, str, bytes]], model_name: str | None) -> None:
    if not 1 <= len(images) <= MAX_IMAGES:
        raise PhotoInventoryError(f"Upload 1 to {MAX_IMAGES} images.")
    if not model_name:
        raise PhotoInventoryError("Choose a visual-capable model by configuring one LLM API key first.")
    for filename, media_type, content in images:
        if media_type.lower() not in IMAGE_TYPES:
            raise PhotoInventoryError("Upload JPEG, PNG, or WebP image files only.")
        if not content:
            raise PhotoInventoryError(f"{filename} is empty.")
        if len(content) > MAX_IMAGE_BYTES:
            raise PhotoInventoryError("Each image must be 8 MB or smaller.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as image:
                    if Image.MIME.get(image.format) != media_type.lower() or image.width * image.height > 20_000_000:
                        raise PhotoInventoryError("Use a matching JPEG, PNG, or WebP image below 20 megapixels.")
                    image.verify()
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise PhotoInventoryError("This image is damaged or cannot be decoded.") from exc


async def analyze(images: Sequence[tuple[str, str, bytes]], model_name: str | None) -> PhotoInventoryResult:
    """Extract editable item names from 1–10 in-memory images, then estimate in code."""
    _validate(images, model_name)
    items = await _run_vision(images, model_name)
    editable_text = "\n".join(f"{item.qty} {item.item}" if item.qty != 1 else item.item for item in items)
    return PhotoInventoryResult(items=items, editable_text=editable_text, estimate=estimate(editable_text))


async def _run_vision(images: Sequence[tuple[str, str, bytes]], model_name: str) -> list[DetectedItem]:
    """Use the same user-selected provider as the planning agent, without durable image storage."""
    from pydantic_ai import Agent, BinaryImage
    from pydantic_ai.usage import UsageLimits
    from .agent import build_model

    # Keep this model deliberately narrow: it identifies objects but cannot make
    # price, volume, weight, or booking claims.
    agent = Agent(
        build_model(model_name),
        output_type=DetectedInventory,
        instructions=(
            "Identify only clearly visible moveable household items in these photos. "
            "Return a conservative editable count for each item. Do not estimate cubic feet, weight, price, "
            "or anything not visible. Use simple names such as sofa, queen bed, desk, box, upright piano."
        ),
        defer_model_check=True,
    )
    content = [
        "Extract a conservative inventory from these photos. Items will be reviewed by the user before planning.",
        *[BinaryImage(data=data, media_type=media_type) for _, media_type, data in images],
    ]
    try:
        result = await asyncio.wait_for(agent.run(content, usage_limits=UsageLimits(request_limit=3)), timeout=60)
    except Exception as exc:
        raise PhotoInventoryError(f"Photo analysis is unavailable: {type(exc).__name__}") from exc
    return result.output.items
