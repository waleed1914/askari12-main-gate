from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from askari_vms.audit import AuditSeverity
from askari_vms.etags import ETagRecord, ETagState, expiry_state

IN = "In"
OUT = "Out"


class ETagEventKind(StrEnum):
    GRANTED = "Granted"
    EXPIRING = "Expiring soon"
    EXPIRED = "Expired"
    BLOCKED = "Blocked"
    UNKNOWN = "Unknown tag"


# The controller opens e-tag gates on its own; these are the states we report on the
# events it hands back. Blue warns near expiry, red marks an expired tag, and an
# unrecognised tag is critical because the controller should never have opened for it.
_FROM_STATE = {
    ETagState.ACTIVE: ETagEventKind.GRANTED,
    ETagState.EXPIRING: ETagEventKind.EXPIRING,
    ETagState.EXPIRED: ETagEventKind.EXPIRED,
    ETagState.BLOCKED: ETagEventKind.BLOCKED,
}

_SEVERITY = {
    ETagEventKind.GRANTED: AuditSeverity.INFO,
    ETagEventKind.EXPIRING: AuditSeverity.INFO,
    ETagEventKind.EXPIRED: AuditSeverity.WARNING,
    ETagEventKind.BLOCKED: AuditSeverity.WARNING,
    ETagEventKind.UNKNOWN: AuditSeverity.CRITICAL,
}


@dataclass(frozen=True, slots=True)
class ETagEvent:
    event_id: str
    timestamp: datetime
    controller_name: str
    door: str
    direction: str
    rfid: str
    kind: ETagEventKind
    resident_name: str = ""
    vehicle_number: str = ""
    note: str = ""

    @property
    def severity(self) -> AuditSeverity:
        return _SEVERITY[self.kind]

    @property
    def is_critical(self) -> bool:
        return self.kind is ETagEventKind.UNKNOWN

    def describe(self) -> str:
        who = self.resident_name or "Unregistered tag"
        return f"{who} — {self.kind.value} at {self.door} ({self.direction})"


def classify(rfid: str, records: Sequence[ETagRecord], today: date | None = None) -> tuple[ETagEventKind, ETagRecord | None]:
    """Decide how a controller reading should be reported against the registry."""
    tag = rfid.strip()
    match = next((record for record in records if record.rfid == tag), None)
    if match is None:
        return ETagEventKind.UNKNOWN, None
    return _FROM_STATE[expiry_state(match, today)], match


def build_event(
    event_id: str,
    timestamp: datetime,
    controller_name: str,
    door: str,
    direction: str,
    rfid: str,
    records: Sequence[ETagRecord],
    today: date | None = None,
    note: str = "",
) -> ETagEvent:
    """Turn one raw controller reading into a classified event."""
    kind, match = classify(rfid, records, today)
    return ETagEvent(
        event_id=event_id,
        timestamp=timestamp,
        controller_name=controller_name,
        door=door,
        direction=direction,
        rfid=rfid.strip(),
        kind=kind,
        resident_name=match.resident_name if match else "",
        vehicle_number=match.vehicle_number if match else "",
        note=note,
    )


def days_until_expiry(record: ETagRecord, today: date | None = None) -> int:
    return (record.expiry_date - (today or date.today())).days


def history_for(events: Sequence[ETagEvent], rfids: Collection[str]) -> tuple[ETagEvent, ...]:
    """Every reading for a set of tags, newest first (the feed is already ordered)."""
    wanted = {rfid.strip() for rfid in rfids}
    return tuple(event for event in events if event.rfid in wanted)
