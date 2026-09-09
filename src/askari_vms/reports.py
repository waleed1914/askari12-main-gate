"""Reports and analytics.

One combined view over visitor transactions and e-tag readings, so Admin can answer
"who came through this gate last week" without opening two pages.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from askari_vms.etag_events import ETagEvent
from askari_vms.visits import VisitRecord

ETAG = "E-Tag"
ALL_DOORS = "All Doors"
ALL_CATEGORIES = "All Categories"
NOT_APPLICABLE = "—"

GROUP_NONE = "No grouping"
GROUP_CATEGORY = "Vehicle Category"
GROUP_DOOR = "Door/Gate"
GROUP_DAY = "Day"
GROUP_TYPE = "Type"
GROUP_OPTIONS = (GROUP_NONE, GROUP_CATEGORY, GROUP_DOOR, GROUP_DAY, GROUP_TYPE)

CSV_HEADER = ("Type", "Date & Time", "Name", "CNIC", "Vehicle Number", "Entry Door", "Exit Door", "Captured By")


@dataclass(frozen=True, slots=True)
class ReportRow:
    kind: str            # "E-Tag", or the visitor's vehicle category
    timestamp: datetime
    name: str
    vehicle_number: str
    entry_door: str
    exit_door: str
    captured_by: str
    cnic: str = ""
    category: str = ""

    @property
    def is_etag(self) -> bool:
        return self.kind == ETAG

    def as_row(self) -> tuple[str, ...]:
        return (
            self.kind,
            self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            self.name or NOT_APPLICABLE,
            self.cnic or NOT_APPLICABLE,
            self.vehicle_number or NOT_APPLICABLE,
            self.entry_door or NOT_APPLICABLE,
            self.exit_door or NOT_APPLICABLE,
            self.captured_by or NOT_APPLICABLE,
        )


def build_rows(visits: Sequence[VisitRecord], etag_events: Sequence[ETagEvent]) -> list[ReportRow]:
    """Merge both record types into one timeline, newest first."""
    rows: list[ReportRow] = []
    for visit in visits:
        rows.append(ReportRow(
            kind=visit.vehicle_category or "Visitor",
            timestamp=visit.entry_time,
            name=visit.visitor_name,
            vehicle_number=visit.vehicle_number,
            entry_door=visit.entry_door,
            exit_door=visit.exit_door,
            captured_by=visit.entry_operator,
            cnic=visit.cnic,
            category=visit.vehicle_category,
        ))
    for event in etag_events:
        rows.append(ReportRow(
            kind=ETAG,
            timestamp=event.timestamp,
            name=event.resident_name,
            vehicle_number=event.vehicle_number,
            entry_door=event.door,
            exit_door="",
            # The controller opens e-tag gates itself, so no operator captured this.
            captured_by="",
            category=ETAG,
        ))
    rows.sort(key=lambda row: row.timestamp, reverse=True)
    return rows


def filter_rows(
    rows: Sequence[ReportRow],
    query: str = "",
    start: date | None = None,
    end: date | None = None,
    door: str = ALL_DOORS,
    category: str = ALL_CATEGORIES,
) -> list[ReportRow]:
    text = query.strip().casefold()
    out = []
    for row in rows:
        if text and text not in " ".join((row.name, row.vehicle_number, row.cnic, row.kind)).casefold():
            continue
        day = row.timestamp.date()
        if start is not None and day < start:
            continue
        if end is not None and day > end:      # end date is inclusive
            continue
        if door != ALL_DOORS and door not in (row.entry_door, row.exit_door):
            continue
        if category != ALL_CATEGORIES and row.kind != category:
            continue
        out.append(row)
    return out


def totals(rows: Sequence[ReportRow]) -> dict[str, int]:
    etag = sum(1 for row in rows if row.is_etag)
    return {"etag": etag, "visitor": len(rows) - etag, "total": len(rows)}


def doors(rows: Sequence[ReportRow]) -> list[str]:
    found = {row.entry_door for row in rows if row.entry_door}
    found |= {row.exit_door for row in rows if row.exit_door}
    return sorted(found)


def categories(rows: Sequence[ReportRow]) -> list[str]:
    return sorted({row.kind for row in rows if row.kind})


def group_counts(rows: Sequence[ReportRow], by: str) -> list[tuple[str, int]]:
    """Counts for a grouping, largest first — the shape the tiles need."""
    if by == GROUP_NONE:
        return []
    counts: dict[str, int] = {}
    for row in rows:
        if by == GROUP_CATEGORY:
            key = row.kind
        elif by == GROUP_DOOR:
            key = row.entry_door or NOT_APPLICABLE
        elif by == GROUP_DAY:
            key = row.timestamp.strftime("%Y-%m-%d")
        elif by == GROUP_TYPE:
            key = ETAG if row.is_etag else "Visitor / Commercial"
        else:
            continue
        counts[key] = counts.get(key, 0) + 1
    if by == GROUP_DAY:
        return sorted(counts.items(), reverse=True)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def to_csv(rows: Sequence[ReportRow]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    writer.writerows(row.as_row() for row in rows)
    return buffer.getvalue()
