"""Moving timeline planned backward from the move date (and the current lease end)."""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel


class Task(BaseModel):
    due: date
    title: str
    detail: str = ""
    source: str = ""
    overdue: bool = False
    key: str = ""      # stable id, so an errand can attach its document to this task
    errand: str = ""   # id of the errand (see errands.py) whose document belongs to this task


def build(move_date: date, lease_end: date | None = None, today: date | None = None, has_vehicle: bool = False,
          selling: bool = False, needs_storage: bool = False, same_state: bool = True, special_items: list[str] | None = None) -> list[Task]:
    today = today or date.today()
    d = lambda days: move_date + timedelta(days=days)
    tasks = [
        Task(due=d(-42), title="Start the housing search and compare moving options"),
        Task(due=d(-35), title="Get quotes from movers, trucks and storage", detail="Use each provider's own quote page; compare them against your plan."),
        Task(due=d(-28), title="Book movers or truck", detail="Check the mover's USDOT/MC registration first."),
        Task(due=d(-21), title="Schedule electricity, gas and internet at the new address", key="utilities_start", detail="Start service the day before you arrive."),
        Task(due=d(-14), title="Pack things you won't need for two weeks"),
        Task(due=d(-7), title="Set up USPS mail forwarding", key="usps", source="https://moversguide.usps.com"),
        Task(due=d(-3), title="Confirm bookings, times and addresses with every provider"),
        Task(due=d(-1), title="Pack a first-night box; defrost the fridge"),
        Task(due=move_date, title="Move day", detail="Walk through the old place and take photos for your deposit."),
        Task(due=d(1), title="Stop utilities at the old address", key="utilities_stop"),
    ]
    if lease_end:
        tasks.append(Task(due=lease_end - timedelta(days=30), title="Give written notice to your current landlord", key="landlord_notice",
                          detail="Most leases need 30 days; month-to-month tenants of 1+ year may need 60. Check your lease."))
        tasks.append(Task(due=lease_end, title="Current lease ends", detail="Return keys and request your deposit back in writing."))
        if lease_end < move_date:
            tasks.append(Task(due=lease_end, title="Gap between lease end and move date",
                              detail=f"{(move_date - lease_end).days} day(s) without housing: plan storage or a short stay."))
    if selling:
        tasks.append(Task(due=d(-21), title="Post resale listings", detail="Leave two weeks for buyers; donate what's left the week before."))
    if needs_storage:
        tasks.append(Task(due=d(-14), title="Reserve a storage unit"))
    if special_items:
        tasks.append(Task(due=d(-35), title=f"Tell movers about special items: {', '.join(special_items)}", detail="They may need extra crew or equipment."))
    if has_vehicle:
        if same_state:
            tasks.append(Task(due=d(10), title="Update your address with the California DMV (within 10 days)", key="dmv",
                              source="https://www.dmv.ca.gov/portal/dmv-virtual-office/change-of-address/"))
        else:
            tasks.append(Task(due=d(30), title="Register your vehicle in the new state", detail="Deadlines vary by state; often 30 days."))
    tasks.sort(key=lambda t: t.due)
    for t in tasks:
        t.overdue = t.due < today
    return tasks
