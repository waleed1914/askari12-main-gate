from datetime import datetime, timedelta

from askari_vms.visits import (
    DriverMatch,
    VisitRecord,
    VisitState,
    active_visits_for_vehicle,
    check_out,
    counts,
    describe_duration,
    is_incomplete,
    missing_fields,
    visits_for_vehicle,
)

NOW = datetime(2026, 9, 2, 12, 0, 0)


def visit(visit_id: str = "VIS-1", **changes: object) -> VisitRecord:
    values = {
        "visit_id": visit_id, "barcode": "ABC123", "entry_time": NOW - timedelta(hours=1),
        "entry_operator": "operator01", "visitor_name": "Ahmed Khan", "cnic": "35202-1234567-1",
        "vehicle_number": "ABC-123", "vehicle_category": "Car", "destination": "House 44-B",
    }
    values.update(changes)
    return VisitRecord(**values)


def test_a_complete_visit_reports_no_gaps() -> None:
    assert missing_fields(visit()) == ()
    assert not is_incomplete(visit())


def test_every_required_field_is_reported_when_blank() -> None:
    empty = visit(visitor_name="  ", cnic="", vehicle_number="", destination="", vehicle_category="")
    assert missing_fields(empty) == ("Visitor name", "CNIC", "Vehicle number", "Destination", "Category")
    assert is_incomplete(empty)

    # A partially filled record names only what is actually missing.
    partial = visit(cnic="", destination="")
    assert missing_fields(partial) == ("CNIC", "Destination")


def test_state_follows_the_exit_time() -> None:
    active = visit()
    assert active.state is VisitState.INSIDE and active.is_inside

    closed = check_out(active, "operator02", exit_time=NOW)
    assert closed.state is VisitState.EXITED
    assert not closed.is_inside
    assert closed.exit_operator == "operator02"
    assert closed.exit_door == "Visitor Exit"
    assert closed.driver_match == DriverMatch.MATCHED


def test_a_mismatch_is_recorded_and_never_blocks() -> None:
    closed = check_out(visit(), "operator02", DriverMatch.MISMATCHED, exit_time=NOW)
    assert closed.mismatched
    assert closed.exit_time == NOW, "a mismatch must still close the visit"


def test_receipt_lost_survives_checkout() -> None:
    assert check_out(visit(receipt_lost=True), "operator02", exit_time=NOW).receipt_lost
    assert check_out(visit(), "operator02", exit_time=NOW, receipt_lost=True).receipt_lost


def test_duration_uses_the_exit_time_once_closed() -> None:
    assert describe_duration(visit(), NOW) == "1h 0m"
    assert describe_duration(visit(entry_time=NOW - timedelta(minutes=25)), NOW) == "25m"
    closed = check_out(visit(), "operator02", exit_time=NOW - timedelta(minutes=30))
    assert describe_duration(closed, NOW) == "30m", "a closed visit must not keep counting"


def test_duplicate_active_visits_are_detected_not_blocked() -> None:
    first = visit("VIS-1")
    second = visit("VIS-2")
    closed = check_out(visit("VIS-3"), "operator02", exit_time=NOW)
    records = [first, second, closed]

    active = active_visits_for_vehicle(records, "abc-123")
    assert {v.visit_id for v in active} == {"VIS-1", "VIS-2"}, "matching is case-insensitive"
    assert len(visits_for_vehicle(records, "ABC-123")) == 3, "history includes closed visits"
    assert active_visits_for_vehicle(records, "  ") == ()


def test_normalisation_tidies_operator_typing() -> None:
    tidied = visit(visitor_name="  Ahmed   Khan ", vehicle_number=" abc-123 ", destination=" House   44-B ").normalized()
    assert tidied.visitor_name == "Ahmed Khan"
    assert tidied.vehicle_number == "ABC-123"
    assert tidied.destination == "House 44-B"


def test_counts_summarise_the_day() -> None:
    records = [
        visit("VIS-1"),
        visit("VIS-2", cnic=""),
        check_out(visit("VIS-3"), "operator02", DriverMatch.MISMATCHED, exit_time=NOW),
    ]
    assert counts(records) == {"total": 3, "inside": 2, "exited": 1, "incomplete": 1, "mismatched": 1}
