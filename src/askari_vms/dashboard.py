"""Dashboard figures.

Pure functions over the stored records so the numbers can be checked without a screen.
"Currently in society" is the one the gate actually runs on: it is the count of visits
that were let in and have not been checked out.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from askari_vms.etag_events import ETagEvent, ETagEventKind
from askari_vms.etags import ETagRecord, ETagState, expiry_state
from askari_vms.visits import VisitRecord, is_incomplete


@dataclass(frozen=True, slots=True)
class Metric:
    key: str
    caption: str
    value: int
    note: str = ""
    accent: str = "#17633f"


def in_society(visits: Sequence[VisitRecord]) -> list[VisitRecord]:
    """Visits that entered and have not exited — who is behind the barrier right now."""
    return [visit for visit in visits if visit.is_inside]


def entries_on(visits: Sequence[VisitRecord], day: date) -> list[VisitRecord]:
    return [visit for visit in visits if visit.entry_time.date() == day]


def category_counts(visits: Sequence[VisitRecord]) -> list[tuple[str, int]]:
    """Per-category totals, busiest first — the tiles along the dashboard."""
    counts: dict[str, int] = {}
    for visit in visits:
        name = visit.vehicle_category or "Uncategorised"
        counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def expiring_tags(tags: Sequence[ETagRecord], today: date | None = None) -> list[ETagRecord]:
    return [tag for tag in tags if expiry_state(tag, today) is ETagState.EXPIRING]


def expired_tags(tags: Sequence[ETagRecord], today: date | None = None) -> list[ETagRecord]:
    return [tag for tag in tags if expiry_state(tag, today) is ETagState.EXPIRED]


def unknown_tag_reads(events: Sequence[ETagEvent]) -> list[ETagEvent]:
    """Readings the controller opened for but we cannot identify — always critical."""
    return [event for event in events if event.kind is ETagEventKind.UNKNOWN]


def metrics(
    visits: Sequence[VisitRecord],
    tags: Sequence[ETagRecord],
    events: Sequence[ETagEvent] = (),
    now: datetime | None = None,
) -> list[Metric]:
    now = now or datetime.now()
    today = now.date()
    inside = in_society(visits)
    today_entries = entries_on(visits, today)
    incomplete = [visit for visit in visits if is_incomplete(visit)]
    expiring = expiring_tags(tags, today)
    unknown = unknown_tag_reads(events)

    return [
        Metric("inside", "Currently in society", len(inside),
               f"{len(today_entries)} entered today"),
        Metric("today", "Entries today", len(today_entries),
               f"{len(visits)} recorded in total"),
        Metric("etags", "Active e-tags", sum(1 for tag in tags if expiry_state(tag, today) is ETagState.ACTIVE),
               f"{len(tags)} registered", "#2e8b57"),
        Metric("expiring", "E-tags expiring", len(expiring),
               "within 10 days", "#24558c"),
        Metric("incomplete", "Missing data", len(incomplete),
               "visits submitted incomplete", "#b8860b"),
        Metric("unknown", "Unknown tag reads", len(unknown),
               "critical — check controller cards", "#a3282f"),
    ]
