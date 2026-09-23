"""True cost of the move: moving plan + housing move-in costs + utilities setup + storage + vehicle.

Housing figures are estimates for San Francisco (the MVP's destination) unless the user gives their rent.
"""

from __future__ import annotations

from pydantic import BaseModel

from .models import HomeSize, Plan

# California AB 12 (effective July 1, 2024): most landlords may charge at most one month's rent as deposit.
DEPOSIT_MONTHS_CA = 1
DEPOSIT_SOURCE = "California AB 12 (2023): security deposit capped at 1 month's rent for most landlords"

# Rough monthly utilities for San Francisco by home size (electric + gas + water/trash share + internet). Estimates.
UTILITIES_SF = {HomeSize.studio: 170, HomeSize.one_bed: 210, HomeSize.two_bed: 270, HomeSize.three_bed: 330}
UTILITY_SETUP_FEES = 75  # activation/installation fees, rough
APPLICATION_FEE = 50     # per rental application, rough (CA screening fees are capped and indexed yearly)


class CostLine(BaseModel):
    item: str
    amount_usd: float
    basis: str
    kind: str  # "plan" (from quotes), "user" (user input), "rule" (law/fixed), "estimate"


class TrueCost(BaseModel):
    lines: list[CostLine]
    total_usd: float
    due_before_move_in_usd: float


def compute(plan: Plan | None, home_size: HomeSize, monthly_rent: int | None, applications: int = 3,
            vehicle_cost: float | None = None, vehicle_basis: str = "") -> TrueCost:
    lines: list[CostLine] = []
    if plan:
        for o in plan.offers:
            lines.append(CostLine(item=f"{o.provider} {o.title}", amount_usd=o.price_usd, basis=o.price_basis, kind="plan"))
    if vehicle_cost is not None:
        lines.append(CostLine(item="Vehicle", amount_usd=vehicle_cost, basis=vehicle_basis, kind="estimate"))
    if monthly_rent:
        lines.append(CostLine(item="First month's rent", amount_usd=monthly_rent, basis="your expected rent", kind="user"))
        lines.append(CostLine(item="Security deposit", amount_usd=monthly_rent * DEPOSIT_MONTHS_CA, basis=DEPOSIT_SOURCE, kind="rule"))
    if applications:
        lines.append(CostLine(item=f"Rental applications ({applications})", amount_usd=APPLICATION_FEE * applications,
                              basis=f"about ${APPLICATION_FEE} each", kind="estimate"))
    util = UTILITIES_SF[home_size]
    lines.append(CostLine(item="Utilities, first month", amount_usd=util,
                          basis=f"rough San Francisco average for a {home_size.value}: electric, gas, water share, internet", kind="estimate"))
    lines.append(CostLine(item="Utility and internet setup", amount_usd=UTILITY_SETUP_FEES, basis="activation and install fees, rough", kind="estimate"))
    total = round(sum(l.amount_usd for l in lines), 2)
    upfront = round(sum(l.amount_usd for l in lines if l.item in ("First month's rent", "Security deposit") or l.item.startswith("Rental applications")), 2)
    return TrueCost(lines=lines, total_usd=total, due_before_move_in_usd=upfront)
