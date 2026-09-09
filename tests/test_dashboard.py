from datetime import date, datetime, timedelta

from askari_vms.dashboard import (
    category_counts,
    entries_on,
    expired_tags,
    expiring_tags,
    in_society,
    metrics,
    unknown_tag_reads,
)
from askari_vms.etag_events import ETagEvent, ETagEventKind
from askari_vms.etags import ETagRecord
from askari_vms.visits import DriverMatch, VisitRecord, check_out

NOW = datetime(2026, 9, 4, 12, 0, 0)
TODAY = NOW.date()


def a_visit(visit_id="VIS-1", when=NOW, category="Car", **changes) -> VisitRecord:
    values = dict(
        visit_id=visit_id, barcode="B1", entry_time=when, entry_operator="operator01",
        visitor_name="Ahmed Khan", cnic="35202-1234567-1", vehicle_number="ABC-123",
        vehicle_category=category, destination="House 44-B",
    )
    values.update(changes)
    return VisitRecord(**values)


def a_tag(rfid="900001", expiry_offset=180, status="Active") -> ETagRecord:
    return ETagRecord(
        "A12-001", "Resident", "", "1", "1-A", "03001234567", "", "Male", "Car",
        f"P-{rfid}", "Toyota", "", "", "", "CH-1", rfid, status,
        TODAY - timedelta(days=30), TODAY + timedelta(days=expiry_offset),
        allowed_controllers=("entry",),
    )


def test_in_society_counts_only_open_visits() -> None:
    open_one = a_visit("VIS-1")
    closed = check_out(a_visit("VIS-2"), "operator02", exit_time=NOW)
    assert [v.visit_id for v in in_society([open_one, closed])] == ["VIS-1"]
    assert in_society([]) == []


def test_entries_today_ignores_other_days() -> None:
    visits = [
        a_visit("VIS-1", NOW),
        a_visit("VIS-2", NOW - timedelta(days=1)),
        a_visit("VIS-3", NOW.replace(hour=0, minute=1)),
    ]
    assert {v.visit_id for v in entries_on(visits, TODAY)} == {"VIS-1", "VIS-3"}


def test_category_counts_are_busiest_first() -> None:
    visits = [a_visit("1", category="Truck"), a_visit("2", category="Car"),
              a_visit("3", category="Truck"), a_visit("4", category="")]
    counts = category_counts(visits)
    assert counts[0] == ("Truck", 2)
    assert ("Uncategorised", 1) in counts, "a blank category still has to be counted"


def test_expiring_and_expired_tags_are_separated() -> None:
    tags = [a_tag("1", 200), a_tag("2", 5), a_tag("3", -10), a_tag("4", 200, "Blocked")]
    assert [t.rfid for t in expiring_tags(tags, TODAY)] == ["2"]
    assert [t.rfid for t in expired_tags(tags, TODAY)] == ["3"]


def test_unknown_reads_are_picked_out() -> None:
    events = [
        ETagEvent("E1", NOW, "Entry Controller", "E-tag Entry", "In", "900001", ETagEventKind.GRANTED),
        ETagEvent("E2", NOW, "Entry Controller", "E-tag Entry", "In", "77777777", ETagEventKind.UNKNOWN),
    ]
    assert [e.event_id for e in unknown_tag_reads(events)] == ["E2"]


def test_metrics_report_the_gate_state() -> None:
    visits = [
        a_visit("VIS-1", NOW),                                   # inside, today
        a_visit("VIS-2", NOW - timedelta(days=2)),               # inside, older
        check_out(a_visit("VIS-3", NOW), "operator02", exit_time=NOW),
        a_visit("VIS-4", NOW, cnic=""),                          # inside, incomplete
    ]
    tags = [a_tag("1", 200), a_tag("2", 5)]
    events = [ETagEvent("E1", NOW, "Entry Controller", "E-tag Entry", "In", "9", ETagEventKind.UNKNOWN)]

    values = {m.key: m.value for m in metrics(visits, tags, events, NOW)}
    assert values["inside"] == 3
    assert values["today"] == 3, "VIS-1, VIS-3 and VIS-4 entered today"
    assert values["etags"] == 1, "only the far-future tag counts as active"
    assert values["expiring"] == 1
    assert values["incomplete"] == 1
    assert values["unknown"] == 1


def test_metrics_survive_an_empty_gate() -> None:
    values = {m.key: m.value for m in metrics([], [], [], NOW)}
    assert set(values) == {"inside", "today", "etags", "expiring", "incomplete", "unknown"}
    assert all(value == 0 for value in values.values())
