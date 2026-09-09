from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class AuditSeverity(StrEnum):
    INFO = "Info"
    WARNING = "Warning"
    CRITICAL = "Critical"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    timestamp: datetime
    operator: str
    workstation: str
    action: str
    target: str
    summary: str
    details: str
    severity: AuditSeverity = AuditSeverity.INFO


AUDITED_ACTIONS = (
    "Login",
    "Logout",
    "Gate command",
    "Visitor decision",
    "Record created",
    "Record updated",
    "Record deleted",
    "Bulk action",
    "Printer override",
    "Hardware event",
    "Backup",
)

# Fallback identity, used only before anyone has signed in. AuditLog.set_operator()
# replaces it with the real operator for the session.
DEFAULT_OPERATOR = "system"
DEFAULT_WORKSTATION = "Admin / Entry PC"


class AuditLog:
    """Shared sink every page records to, so one page's actions appear in Audit Logs.

    Events are held newest-first. Given an AuditRepository they are also written to the
    database, which is insert-only: nothing in the app deletes an audit row.
    """

    def __init__(self, events: Iterable[AuditEvent] = (), repository: object | None = None) -> None:
        self._events: list[AuditEvent] = list(events)
        self._listeners: list[Callable[[AuditEvent], None]] = []
        # Optional AuditRepository. Events are appended to it and never deleted.
        self._repository = repository
        self._operator = DEFAULT_OPERATOR
        self._workstation = DEFAULT_WORKSTATION

    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)

    def add_listener(self, listener: Callable[[AuditEvent], None]) -> None:
        self._listeners.append(listener)

    def set_operator(self, operator: str, workstation: str) -> None:
        """Stamp every later event with the signed-in operator and their workstation."""
        self._operator = operator
        self._workstation = workstation

    def _next_sequence(self) -> int:
        """Continue past whatever is already stored, so ids stay unique across restarts."""
        highest = 0
        for event in self._events:
            _, _, digits = event.event_id.partition("AUD-")
            if digits.isdigit():
                highest = max(highest, int(digits))
        return highest + 1

    def record(
        self,
        action: str,
        target: str,
        summary: str,
        details: str,
        severity: AuditSeverity = AuditSeverity.INFO,
        operator: str | None = None,
        workstation: str | None = None,
        timestamp: datetime | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=f"AUD-{self._next_sequence():06d}",
            timestamp=timestamp or datetime.now().replace(microsecond=0),
            operator=operator or self._operator,
            workstation=workstation or self._workstation,
            action=action,
            target=target,
            summary=summary,
            details=details,
            severity=severity,
        )
        self._events.insert(0, event)
        if self._repository is not None:
            self._repository.append(event)
        for listener in self._listeners:
            listener(event)
        return event


def sample_audit_events(now: datetime | None = None) -> tuple[AuditEvent, ...]:
    """Demonstration events for the milestone-1 shell."""
    now = now or datetime.now().replace(microsecond=0)
    return (
        AuditEvent("AUD-000004", now, "admin", "Admin / Entry PC", "Login", "Admin portal", "Administrator signed in", "Successful local login from the configured Admin workstation."),
        AuditEvent("AUD-000003", now - timedelta(minutes=12), "operator01", "Admin / Entry PC", "Gate command", "Visitor Entry", "Visitor gate opened", "Gate opened after visitor record VIS-000021 was submitted and its receipt printed."),
        AuditEvent("AUD-000002", now - timedelta(minutes=28), "operator02", "Exit PC", "Visitor decision", "VIS-000020", "Driver marked mismatched", "Exit operator selected Mismatched, captured exit evidence, submitted the transaction, and opened Visitor Exit.", AuditSeverity.WARNING),
        AuditEvent("AUD-000001", now - timedelta(hours=1), "SYSTEM", "Admin / Entry PC", "Hardware event", "Entry printer", "Receipt printer unavailable", "Printing failed. The operator confirmed manual continuation before the visitor gate was opened.", AuditSeverity.CRITICAL),
    )
