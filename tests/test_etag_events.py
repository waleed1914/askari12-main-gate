from datetime import date, datetime, timedelta

from askari_vms.audit import AuditSeverity
from askari_vms.etag_events import IN, ETagEventKind, build_event, classify, days_until_expiry
from askari_vms.etags import ETagRecord


def make_record(rfid: str, **changes: object) -> ETagRecord:
    today = date.today()
    values = {
        "user_id": "A12-1", "resident_name": "Resident", "care_of": "", "street_no": "",
        "house_no": "", "mobile_no": "", "cnic": "", "gender": "Male", "vehicle_type": "Car",
        "vehicle_number": "ABC-1", "make": "", "position": "", "station_unit": "",
        "department": "", "challan_number": "", "rfid": rfid, "status": "Active",
        "issue_date": today, "expiry_date": today + timedelta(days=30),
        "allowed_controllers": ("entry", "exit"),
    }
    values.update(changes)
    return ETagRecord(**values)


def test_classification_follows_the_registry() -> None:
    today = date(2026, 9, 2)
    records = [
        make_record("1001", expiry_date=today + timedelta(days=90)),
        make_record("1002", expiry_date=today + timedelta(days=7)),
        make_record("1003", expiry_date=today - timedelta(days=1)),
        make_record("1004", status="Blocked"),
    ]
    assert classify("1001", records, today)[0] is ETagEventKind.GRANTED
    assert classify("1002", records, today)[0] is ETagEventKind.EXPIRING
    assert classify("1003", records, today)[0] is ETagEventKind.EXPIRED
    assert classify("1004", records, today)[0] is ETagEventKind.BLOCKED

    kind, match = classify("9999", records, today)
    assert kind is ETagEventKind.UNKNOWN
    assert match is None


def test_the_ten_day_boundary_is_inclusive() -> None:
    today = date(2026, 9, 2)
    records = [make_record("1001", expiry_date=today + timedelta(days=10))]
    assert classify("1001", records, today)[0] is ETagEventKind.EXPIRING
    records = [make_record("1001", expiry_date=today + timedelta(days=11))]
    assert classify("1001", records, today)[0] is ETagEventKind.GRANTED


def test_only_an_unknown_tag_is_critical() -> None:
    today = date(2026, 9, 2)
    records = [make_record("1001"), make_record("1003", expiry_date=today - timedelta(days=1))]
    when = datetime(2026, 9, 2, 10, 30)

    granted = build_event("E1", when, "Entry Controller", "E-tag Entry", IN, "1001", records, today)
    assert granted.severity is AuditSeverity.INFO
    assert not granted.is_critical
    assert granted.resident_name == "Resident"

    expired = build_event("E2", when, "Entry Controller", "E-tag Entry", IN, "1003", records, today)
    assert expired.severity is AuditSeverity.WARNING
    assert not expired.is_critical

    unknown = build_event("E3", when, "Entry Controller", "E-tag Entry", IN, "  9999 ", records, today)
    assert unknown.is_critical
    assert unknown.severity is AuditSeverity.CRITICAL
    assert unknown.rfid == "9999", "the raw controller reading should be trimmed"
    assert unknown.resident_name == "" and unknown.vehicle_number == ""
    assert "Unregistered tag" in unknown.describe()


def test_days_until_expiry() -> None:
    today = date(2026, 9, 2)
    assert days_until_expiry(make_record("1", expiry_date=date(2026, 9, 12)), today) == 10
    assert days_until_expiry(make_record("1", expiry_date=date(2026, 9, 1)), today) == -1
