"""Inventory estimator: a room list or item list -> cubic feet and weight.

Volumes are moving-industry rules of thumb (the kind used on mover survey sheets);
weight is 7 lb per cubic foot unless an item is known to be much heavier.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

LBS_PER_CUFT = 7

# item: (cubic feet, pounds or None for the 7 lb/cu ft default)
ITEMS: dict[str, tuple[int, int | None]] = {
    "sofa": (35, None), "loveseat": (25, None), "sectional": (60, None), "armchair": (15, None),
    "king bed": (70, None), "queen bed": (60, None), "full bed": (50, None), "twin bed": (30, None), "crib": (10, None),
    "dresser": (30, None), "nightstand": (5, None), "wardrobe": (40, None),
    "desk": (20, None), "office chair": (8, None), "bookshelf": (20, None), "filing cabinet": (10, None),
    "dining table": (30, None), "dining chair": (5, None), "coffee table": (10, None), "side table": (5, None),
    "tv": (10, None), "tv stand": (10, None), "rug": (5, None), "lamp": (3, None), "mirror": (4, None),
    "refrigerator": (45, 250), "washer": (25, 170), "dryer": (25, 120), "microwave": (4, None),
    "piano": (70, 500), "upright piano": (70, 500), "grand piano": (90, 800), "safe": (10, 300),
    "treadmill": (25, 250), "bike": (10, None), "grill": (15, None), "plant": (3, None),
    "box": (3, None), "boxes": (3, None), "wardrobe box": (10, None), "suitcase": (4, None),
}
# Items movers usually need to know about in advance (special equipment or crew).
SPECIAL = {"piano", "upright piano", "grand piano", "safe", "treadmill"}

ROOMS: dict[str, dict[str, int]] = {
    "bedroom": {"queen bed": 1, "dresser": 1, "nightstand": 2, "lamp": 1, "box": 10},
    "master bedroom": {"king bed": 1, "dresser": 1, "nightstand": 2, "lamp": 2, "mirror": 1, "box": 12},
    "kids room": {"twin bed": 1, "dresser": 1, "bookshelf": 1, "box": 8},
    "living room": {"sofa": 1, "armchair": 1, "coffee table": 1, "tv stand": 1, "tv": 1, "bookshelf": 1, "rug": 1, "lamp": 1, "box": 8},
    "kitchen": {"box": 15, "microwave": 1},
    "dining room": {"dining table": 1, "dining chair": 4},
    "office": {"desk": 1, "office chair": 1, "bookshelf": 1, "filing cabinet": 1, "box": 6},
    "garage": {"bike": 2, "box": 10},
    "patio": {"grill": 1, "plant": 3},
}

_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "ten": 10, "twenty": 20}


class Line(BaseModel):
    item: str
    qty: int
    cuft: int
    lbs: int


class Inventory(BaseModel):
    lines: list[Line]
    total_cuft: int
    total_lbs: int
    special_items: list[str]
    unknown: list[str]
    basis: str = "rule-of-thumb volumes per item/room; 7 lb per cu ft unless the item is known heavier"


def _qty(text: str) -> tuple[int, str]:
    """'2 bedrooms', 'bedroom x2', 'twenty boxes', 'boxes: 20' -> (2, 'bedrooms')."""
    t = text.strip().lower()
    m = re.match(r"^(\d+)\s*x?\s+(.*)$", t) or re.match(r"^(.*?)\s*(?:x|:|\*)\s*(\d+)$", t)
    if m:
        a, b = m.groups()
        return (int(a), b) if a.isdigit() else (int(b), a)
    first, _, rest = t.partition(" ")
    if first in _WORDS and rest:
        return _WORDS[first], rest
    return 1, t


def _singular(name: str) -> str:
    name = name.strip().rstrip(".")
    for key in (name, name[:-1] if name.endswith("s") else name, name[:-2] if name.endswith("es") else name):
        if key in ROOMS or key in ITEMS:
            return key
    return name


def estimate(text: str) -> Inventory:
    """Parse one room or item per line (or comma-separated) and total it up."""
    counts: dict[str, int] = {}
    unknown: list[str] = []
    for raw in re.split(r"[\n,;]+", text):
        if not raw.strip():
            continue
        qty, name = _qty(raw)
        name = _singular(name)
        if name in ROOMS:
            for item, n in ROOMS[name].items():
                counts[item] = counts.get(item, 0) + n * qty
        elif name in ITEMS:
            counts[name] = counts.get(name, 0) + qty
        else:
            unknown.append(raw.strip())
    lines = []
    for item, qty in sorted(counts.items(), key=lambda kv: -ITEMS[kv[0]][0] * kv[1]):
        cuft, lbs = ITEMS[item]
        lines.append(Line(item=item, qty=qty, cuft=cuft * qty, lbs=(lbs or cuft * LBS_PER_CUFT) * qty))
    return Inventory(
        lines=lines,
        total_cuft=sum(l.cuft for l in lines),
        total_lbs=sum(l.lbs for l in lines),
        special_items=sorted({l.item for l in lines if l.item in SPECIAL}),
        unknown=unknown,
    )
