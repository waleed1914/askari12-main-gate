from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum


class VisitState(StrEnum):
    INSIDE = "Inside"
    EXITED = "Exited"


class DriverMatch(StrEnum):
    MATCHED = "Matched"
    MISMATCHED = "Mismatched"


# A visit may be submitted with any of these blank — the operator is never blocked —
# but it is then flagged so Admin can chase it. See the "missing data" filter.
REQUIRED_FOR_COMPLETE = (
    ("visitor_name", "Visitor name"),
    ("cnic", "CNIC"),
    ("vehicle_number", "Vehicle number"),
    ("destination", "Destination"),
    ("vehicle_category", "Category"),
)


@dataclass(frozen=True, slots=True)
class VisitRecord:
    visit_id: str
    barcode: str
    entry_time: datetime
    entry_operator: str
    entry_door: str = "Visitor Entry"
    visitor_name: str = ""
    cnic: str = ""
    mobile: str = ""
    vehicle_number: str = ""
    vehicle_category: str = ""
    destination: str = ""
    # Read from the front of the CNIC or licence. Kept as free text: OCR returns
    # partial and malformed dates, and losing a partial read helps nobody.
    father_name: str = ""
    date_of_birth: str = ""
    cnic_issue_date: str = ""
    cnic_expiry_date: str = ""
    cnic_image: str = ""
    driver_image: str = ""
    exit_time: datetime | None = None
    exit_operator: str = ""
    exit_door: str = ""
    driver_match: str = ""
    receipt_lost: bool = False

    def normalized(self) -> "VisitRecord":
        return replace(
            self,
            visitor_name=" ".join(self.visitor_name.split()),
            vehicle_number=self.vehicle_number.strip().upper(),
            cnic=self.cnic.strip(),
            destination=" ".join(self.destination.split()),
            father_name=" ".join(self.father_name.split()),
        )

    @property
    def state(self) -> VisitState:
        return VisitState.EXITED if self.exit_time is not None else VisitState.INSIDE

    @property
    def is_inside(self) -> bool:
        return self.exit_time is None

    @property
    def mismatched(self) -> bool:
        return self.driver_match == DriverMatch.MISMATCHED


def missing_fields(visit: VisitRecord) -> tuple[str, ...]:
    """Human-readable names of the fields left blank at submission."""
    return tuple(label for attribute, label in REQUIRED_FOR_COMPLETE if not getattr(visit, attribute).strip())


def is_incomplete(visit: VisitRecord) -> bool:
    return bool(missing_fields(visit))


def duration(visit: VisitRecord, now: datetime | None = None) -> timedelta:
    end = visit.exit_time if visit.exit_time is not None else (now or datetime.now())
    return end - visit.entry_time


def describe_duration(visit: VisitRecord, now: datetime | None = None) -> str:
    total = int(duration(visit, now).total_seconds())
    if total < 0:
        return "—"
    hours, remainder = divmod(total, 3600)
    return f"{hours}h {remainder // 60}m" if hours else f"{remainder // 60}m"


def check_out(
    visit: VisitRecord,
    operator: str,
    driver_match: str = DriverMatch.MATCHED,
    exit_door: str = "Visitor Exit",
    exit_time: datetime | None = None,
    receipt_lost: bool = False,
) -> VisitRecord:
    """Close an active visit. Mismatched never blocks — it is recorded, not enforced."""
    return replace(
        visit,
        exit_time=exit_time or datetime.now().replace(microsecond=0),
        exit_operator=operator,
        exit_door=exit_door,
        driver_match=driver_match,
        receipt_lost=visit.receipt_lost or receipt_lost,
    )


def active_visits_for_vehicle(visits: Sequence[VisitRecord], vehicle_number: str) -> tuple[VisitRecord, ...]:
    """Used to warn — never block — when a plate already has an open visit."""
    plate = vehicle_number.strip().upper()
    if not plate:
        return ()
    return tuple(visit for visit in visits if visit.is_inside and visit.vehicle_number == plate)


def visits_for_vehicle(visits: Sequence[VisitRecord], vehicle_number: str) -> tuple[VisitRecord, ...]:
    plate = vehicle_number.strip().upper()
    return tuple(visit for visit in visits if visit.vehicle_number == plate)


def counts(visits: Sequence[VisitRecord]) -> dict[str, int]:
    return {
        "total": len(visits),
        "inside": sum(1 for visit in visits if visit.is_inside),
        "exited": sum(1 for visit in visits if not visit.is_inside),
        "incomplete": sum(1 for visit in visits if is_incomplete(visit)),
        "mismatched": sum(1 for visit in visits if visit.mismatched),
    }


def next_visit_id(existing: Sequence[VisitRecord]) -> str:
    """Continue the human-readable sequence past whatever is already stored."""
    highest = 0
    for visit in existing:
        _, _, digits = visit.visit_id.partition("VIS-")
        if digits.isdigit():
            highest = max(highest, int(digits))
    return f"VIS-{highest + 1:06d}"


def new_barcode() -> str:
    """A random token per visit, so a receipt cannot be guessed from another."""
    return secrets.token_hex(4).upper()


def previous_visit(
    visits: Sequence[VisitRecord], vehicle_number: str = "", cnic: str = ""
) -> VisitRecord | None:
    """The most recent visit by this vehicle or CNIC, offered for reuse — never auto-filled."""
    plate = vehicle_number.strip().upper()
    identity = cnic.strip()
    matches = [
        visit for visit in visits
        if (plate and visit.vehicle_number == plate) or (identity and visit.cnic == identity)
    ]
    return max(matches, key=lambda visit: visit.entry_time) if matches else None


def find_open_visit(visits: Sequence[VisitRecord], token: str) -> VisitRecord | None:
    """Resolve a scan or a typed search to the visit still inside.

    The scanner types the receipt barcode, but the operator may also key a plate, a
    CNIC or the visit number when a receipt is lost. Only open visits can be exited.
    """
    value = token.strip()
    if not value:
        return None
    upper = value.upper()
    open_visits = [visit for visit in visits if visit.is_inside]
    for match in (
        lambda v: v.barcode.upper() == upper,
        lambda v: v.visit_id.upper() == upper,
        lambda v: v.vehicle_number == upper,
        lambda v: v.cnic == value,
    ):
        found = [visit for visit in open_visits if match(visit)]
        if found:
            # Newest first, so a repeat plate resolves to the current visit.
            return max(found, key=lambda visit: visit.entry_time)
    return None


def search_open_visits(visits: Sequence[VisitRecord], query: str) -> list[VisitRecord]:
    """Partial matches for the manual lost-receipt search."""
    text = query.strip().casefold()
    if not text:
        return []
    return [
        visit for visit in visits
        if visit.is_inside and text in " ".join((
            visit.visit_id, visit.barcode, visit.vehicle_number, visit.cnic, visit.visitor_name,
        )).casefold()
    ]
