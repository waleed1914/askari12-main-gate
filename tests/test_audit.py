from datetime import datetime

from askari_vms.audit import AUDITED_ACTIONS, AuditLog, AuditSeverity, sample_audit_events


def test_required_audit_actions_are_defined() -> None:
    assert "Login" in AUDITED_ACTIONS
    assert "Gate command" in AUDITED_ACTIONS
    assert "Visitor decision" in AUDITED_ACTIONS
    assert "Printer override" in AUDITED_ACTIONS
    assert "Hardware event" in AUDITED_ACTIONS
    assert tuple(item.value for item in AuditSeverity) == ("Info", "Warning", "Critical")


def test_recorded_events_are_newest_first_with_sequential_ids() -> None:
    log = AuditLog()
    first = log.record("Gate command", "Visitor Entry", "Opened", "details")
    second = log.record("Gate command", "Visitor Exit", "Opened", "details")

    assert first.event_id == "AUD-000001"
    assert second.event_id == "AUD-000002"
    assert log.events() == (second, first)
    assert first.severity is AuditSeverity.INFO


def test_seeded_log_continues_the_id_sequence() -> None:
    log = AuditLog(sample_audit_events())
    assert log.record("Gate command", "Visitor Entry", "Opened", "details").event_id == "AUD-000005"


def test_listeners_are_notified_of_every_event() -> None:
    log = AuditLog()
    seen = []
    log.add_listener(seen.append)
    log.record("Backup", "USB", "Backup written", "details", AuditSeverity.WARNING)

    assert len(seen) == 1
    assert seen[0].severity is AuditSeverity.WARNING


def test_events_cannot_be_mutated_through_the_returned_tuple() -> None:
    log = AuditLog()
    log.record("Login", "Admin portal", "Signed in", "details", timestamp=datetime(2026, 9, 2, 10, 0, 0))
    snapshot = log.events()
    log.record("Logout", "Admin portal", "Signed out", "details")

    assert len(snapshot) == 1, "events() must return a snapshot, not a live view"
    assert len(log.events()) == 2
