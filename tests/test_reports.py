from datetime import date, datetime, timedelta

from askari_vms.etag_events import IN, OUT, ETagEvent, ETagEventKind
from askari_vms.reports import (
    ALL_CATEGORIES,
    ALL_DOORS,
    CSV_HEADER,
    ETAG,
    GROUP_CATEGORY,
    GROUP_DAY,
    GROUP_DOOR,
    GROUP_NONE,
    GROUP_TYPE,
    build_rows,
    categories,
    doors,
    filter_rows,
    group_counts,
    to_csv,
    totals,
)
from askari_vms.visits import DriverMatch, VisitRecord, check_out

DAY = datetime(2026, 9, 2, 12, 0, 0)


def visit(visit_id="VIS-1", when=DAY, category="Car", plate="ABC-123", name="Ahmed Khan", **changes):
    values = dict(
        visit_id=visit_id, barcode="B1", entry_time=when, entry_operator="operator01",
        visitor_name=name, cnic="35202-1234567-1", vehicle_number=plate,
        vehicle_category=category, destination="House 44-B",
    )
    values.update(changes)
    return VisitRecord(**values)


def reading(rfid="900000001", when=DAY, door="E-tag Entry", name="Rizwan Rashid", plate="MNA-3996"):
    return ETagEvent("ETL-1", when, "Entry Controller", door, IN, rfid,
                     ETagEventKind.GRANTED, resident_name=name, vehicle_number=plate)


def test_both_record_types_merge_into_one_timeline() -> None:
    rows = build_rows(
        [visit("VIS-1", DAY - timedelta(hours=2))],
        [reading(when=DAY), reading(when=DAY - timedelta(hours=5))],
    )
    assert [r.timestamp for r in rows] == sorted((r.timestamp for r in rows), reverse=True)
    assert rows[0].kind == ETAG
    assert rows[0].is_etag and not rows[1].is_etag
    assert rows[1].kind == "Car"


def test_an_etag_reading_has_no_capturing_operator() -> None:
    """The controller opens e-tag gates itself, so no operator captured the event."""
    [row] = build_rows([], [reading()])
    assert row.captured_by == ""
    assert row.exit_door == ""
    assert row.as_row()[-1] == "—"


def test_totals_split_etag_from_visitor() -> None:
    rows = build_rows([visit("V1"), visit("V2")], [reading(), reading(), reading()])
    assert totals(rows) == {"etag": 3, "visitor": 2, "total": 5}
    assert totals([]) == {"etag": 0, "visitor": 0, "total": 0}


def test_search_matches_name_plate_cnic_and_type() -> None:
    rows = build_rows([visit(name="Sana Malik", plate="XYZ-887")], [reading()])
    assert len(filter_rows(rows, query="sana")) == 1
    assert len(filter_rows(rows, query="xyz-887")) == 1
    assert len(filter_rows(rows, query="35202")) == 1
    assert len(filter_rows(rows, query=ETAG)) == 1
    assert filter_rows(rows, query="nothing") == []


def test_the_date_range_is_inclusive_at_both_ends() -> None:
    rows = build_rows([
        visit("V1", datetime(2026, 9, 1, 23, 59)),
        visit("V2", datetime(2026, 9, 2, 0, 1)),
        visit("V3", datetime(2026, 9, 3, 23, 59)),
        visit("V4", datetime(2026, 9, 4, 0, 1)),
    ], [])
    kept = filter_rows(rows, start=date(2026, 9, 2), end=date(2026, 9, 3))
    assert {r.timestamp.day for r in kept} == {2, 3}
    assert len(filter_rows(rows, start=date(2026, 9, 4))) == 1
    assert len(filter_rows(rows, end=date(2026, 9, 1))) == 1


def test_door_filter_matches_entry_or_exit() -> None:
    closed = check_out(visit("V1"), "operator02", DriverMatch.MATCHED,
                       exit_door="Visitor Exit", exit_time=DAY + timedelta(hours=1))
    rows = build_rows([closed], [reading(door="E-tag Entry")])
    assert len(filter_rows(rows, door="Visitor Entry")) == 1
    assert len(filter_rows(rows, door="Visitor Exit")) == 1, "the exit door should match too"
    assert len(filter_rows(rows, door="E-tag Entry")) == 1
    assert len(filter_rows(rows, door=ALL_DOORS)) == 2


def test_category_filter_includes_etag_as_a_type() -> None:
    rows = build_rows([visit(category="Truck"), visit("V2", category="Car")], [reading()])
    assert len(filter_rows(rows, category="Truck")) == 1
    assert len(filter_rows(rows, category=ETAG)) == 1
    assert len(filter_rows(rows, category=ALL_CATEGORIES)) == 3


def test_filters_combine() -> None:
    rows = build_rows([
        visit("V1", datetime(2026, 9, 2, 9, 0), category="Truck", name="Ahmed Khan"),
        visit("V2", datetime(2026, 9, 2, 9, 0), category="Car", name="Ahmed Khan"),
        visit("V3", datetime(2026, 9, 5, 9, 0), category="Truck", name="Ahmed Khan"),
    ], [])
    kept = filter_rows(rows, query="ahmed", start=date(2026, 9, 1), end=date(2026, 9, 3), category="Truck")
    assert [r.timestamp.day for r in kept] == [2]


def test_grouping_counts_largest_first() -> None:
    rows = build_rows(
        [visit("V1", category="Truck"), visit("V2", category="Car"), visit("V3", category="Truck")],
        [reading()],
    )
    assert group_counts(rows, GROUP_CATEGORY)[0] == ("Truck", 2)
    assert dict(group_counts(rows, GROUP_TYPE)) == {"Visitor / Commercial": 3, ETAG: 1}
    assert group_counts(rows, GROUP_DOOR)[0][1] >= 1
    assert group_counts(rows, GROUP_NONE) == []


def test_grouping_by_day_is_newest_first() -> None:
    rows = build_rows([
        visit("V1", datetime(2026, 9, 1, 9, 0)),
        visit("V2", datetime(2026, 9, 3, 9, 0)),
        visit("V3", datetime(2026, 9, 3, 18, 0)),
    ], [])
    assert group_counts(rows, GROUP_DAY) == [("2026-09-03", 2), ("2026-09-01", 1)]


def test_filter_options_come_from_the_data() -> None:
    closed = check_out(visit("V1"), "operator02", exit_door="Visitor Exit", exit_time=DAY)
    rows = build_rows([closed], [reading(door="E-tag Entry")])
    assert doors(rows) == ["E-tag Entry", "Visitor Entry", "Visitor Exit"]
    assert categories(rows) == ["Car", ETAG], "options are alphabetical for a predictable dropdown"


def test_csv_has_a_header_and_one_line_per_row() -> None:
    rows = build_rows([visit(name="Sana Malik")], [reading()])
    text = to_csv(rows)
    lines = text.strip().splitlines()
    assert lines[0] == ",".join(CSV_HEADER)
    assert len(lines) == 3
    assert "Sana Malik" in text and "Rizwan Rashid" in text


def test_csv_quotes_a_value_containing_a_comma() -> None:
    rows = build_rows([visit(name="Khan, Ahmed")], [])
    assert '"Khan, Ahmed"' in to_csv(rows)


def test_csv_of_nothing_still_has_its_header() -> None:
    assert to_csv([]).strip() == ",".join(CSV_HEADER)
