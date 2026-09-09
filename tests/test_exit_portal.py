from datetime import datetime, timedelta

import pytest

from askari_vms.audit import AuditLog
from askari_vms.auth import start_session
from askari_vms.users import UserAccount, UserRole
from askari_vms.visits import (
    DriverMatch,
    VisitRecord,
    check_out,
    find_open_visit,
    search_open_visits,
)

NOW = datetime(2026, 9, 4, 15, 0, 0)


def a_visit(visit_id="VIS-000141", plate="LEA-4410", barcode="8F2A19C4", **changes) -> VisitRecord:
    values = dict(
        visit_id=visit_id, barcode=barcode, entry_time=NOW - timedelta(hours=2),
        entry_operator="operator01", visitor_name="Ahmed Khan", cnic="35202-1234567-1",
        mobile="03001234567", vehicle_number=plate, vehicle_category="Car",
        destination="House 44-B",
    )
    values.update(changes)
    return VisitRecord(**values)


# ---------------- lookup ----------------

def test_a_scanned_receipt_finds_the_open_visit() -> None:
    visits = [a_visit()]
    assert find_open_visit(visits, "8F2A19C4").visit_id == "VIS-000141"
    assert find_open_visit(visits, " 8f2a19c4 ").visit_id == "VIS-000141", "scans may vary in case"


def test_plate_cnic_and_visit_number_all_resolve() -> None:
    visits = [a_visit()]
    for token in ("LEA-4410", "lea-4410", "35202-1234567-1", "VIS-000141"):
        assert find_open_visit(visits, token) is not None, token


def test_a_closed_visit_is_never_returned() -> None:
    """A vehicle that already left must not be exited twice."""
    closed = check_out(a_visit(), "operator02", exit_time=NOW)
    assert find_open_visit([closed], "8F2A19C4") is None
    assert find_open_visit([closed], "LEA-4410") is None


def test_a_repeat_plate_resolves_to_the_current_visit() -> None:
    old = a_visit("VIS-1", entry_time=NOW - timedelta(days=2), barcode="AAAA1111")
    current = a_visit("VIS-2", entry_time=NOW - timedelta(minutes=20), barcode="BBBB2222")
    assert find_open_visit([old, current], "LEA-4410").visit_id == "VIS-2"


def test_nothing_matches_nothing() -> None:
    assert find_open_visit([a_visit()], "") is None
    assert find_open_visit([a_visit()], "   ") is None
    assert find_open_visit([], "8F2A19C4") is None


def test_partial_search_supports_a_lost_receipt() -> None:
    visits = [a_visit("VIS-1", "LEA-4410"), a_visit("VIS-2", "LEB-9921", barcode="C1")]
    assert len(search_open_visits(visits, "LE")) == 2
    assert [v.visit_id for v in search_open_visits(visits, "9921")] == ["VIS-2"]
    assert search_open_visits(visits, "") == []


# ---------------- portal ----------------

@pytest.fixture()
def portal(qapp):
    from askari_vms.ui.pages.exit_portal import ExitPortalWindow

    account = UserAccount("OP-002", "Operator Two", "operator02", UserRole.OPERATOR)
    return ExitPortalWindow(
        audit_log=AuditLog(),
        visits=[a_visit(), a_visit("VIS-000142", "LEB-9921", "C40D8821", visitor_name="Sana Malik")],
        session=start_session(account, "Exit", NOW),
    )


def test_scanning_a_receipt_opens_the_visit(portal) -> None:
    portal.search.setText("8F2A19C4")
    visit = portal.lookup()
    assert visit is not None and visit.visit_id == "VIS-000141"
    assert "Ahmed Khan" in portal.visit_heading.text()
    assert portal._visit.visit_id == "VIS-000141"


def test_anpr_opens_the_visit_without_the_operator(portal) -> None:
    visit = portal.read_plate("LEB-9921")
    assert visit is not None and visit.visit_id == "VIS-000142"
    assert "automatically" in portal.status.text()


def test_anpr_with_no_active_entry_says_so_and_does_not_select(portal) -> None:
    assert portal.read_plate("ZZZ-0000") is None
    assert portal._visit is None
    assert "No active entry" in portal.message.text()


def test_a_partial_search_offers_the_candidates(portal) -> None:
    portal.search.setText("LE")
    assert portal.lookup() is None, "an ambiguous search must not pick for the operator"
    assert portal.results.isVisibleTo(portal)
    assert portal.results.rowCount() == 2

    portal._choose_result(0)
    assert portal._visit is not None
    assert not portal.results.isVisibleTo(portal)


def test_submitting_without_a_decision_is_refused(portal) -> None:
    """Matched/Mismatched is mandatory — it is the one thing that gates submit."""
    portal.search.setText("8F2A19C4")
    portal.lookup()
    assert portal.submit() is None
    assert "Matched or Mismatched" in portal.status.text()
    assert portal._visit is not None, "the visit stays selected so the operator can decide"


def test_a_matched_exit_closes_the_visit_and_opens_the_gate(portal) -> None:
    portal.search.setText("8F2A19C4")
    portal.lookup()
    portal.capture()
    portal.set_decision(DriverMatch.MATCHED)

    closed = portal.submit()
    assert closed is not None and not closed.is_inside
    assert closed.driver_match == DriverMatch.MATCHED
    assert closed.exit_operator == "operator02"
    assert closed.exit_door == "Visitor Exit"

    actions = [e.action for e in portal._audit.events()]
    assert "Visitor decision" in actions and "Gate command" in actions
    decision = next(e for e in portal._audit.events() if e.action == "Visitor decision")
    assert decision.severity.value == "Info"


def test_a_mismatch_is_recorded_and_still_opens_the_gate(portal) -> None:
    portal.search.setText("8F2A19C4")
    portal.lookup()
    portal.capture()
    portal.set_decision(DriverMatch.MISMATCHED)
    assert "MISMATCHED" in portal.decision_note.text()

    closed = portal.submit()
    assert closed is not None and closed.mismatched
    assert not closed.is_inside, "a mismatch must never trap the vehicle inside"
    decision = next(e for e in portal._audit.events() if e.action == "Visitor decision")
    assert decision.severity.value == "Warning"
    assert any(e.action == "Gate command" for e in portal._audit.events())


def test_a_lost_receipt_is_recorded_on_the_visit(portal) -> None:
    portal.search.setText("LEA-4410")
    portal.lookup()
    portal.receipt_lost.setChecked(True)
    portal.set_decision(DriverMatch.MATCHED)

    closed = portal.submit()
    assert closed.receipt_lost is True
    decision = next(e for e in portal._audit.events() if e.action == "Visitor decision")
    assert "lost" in decision.details.lower()
    assert decision.severity.value == "Warning"


def test_a_missed_exit_camera_is_recorded_but_does_not_block(portal) -> None:
    portal.search.setText("8F2A19C4")
    portal.lookup()
    portal.set_decision(DriverMatch.MATCHED)
    closed = portal.submit()          # no capture() call
    assert closed is not None
    decision = next(e for e in portal._audit.events() if e.action == "Visitor decision")
    assert "No exit image captured" in decision.details


def test_the_gate_can_be_opened_with_no_visit_at_all(portal) -> None:
    portal.open_gate_manually()
    event = next(e for e in portal._audit.events() if e.action == "Gate command")
    assert event.severity.value == "Warning", "a manual open is an exception worth flagging"
    assert "no visit" in event.details


def test_submitting_returns_focus_to_the_scanner(portal) -> None:
    portal.show()
    portal.search.setText("8F2A19C4")
    portal.lookup()
    portal.set_decision(DriverMatch.MATCHED)
    portal.submit()
    assert portal.focusWidget() is portal.search, "the wedge types wherever focus is"
    assert portal._visit is None and portal.search.text() == ""


def test_an_exited_visit_cannot_be_exited_again(portal) -> None:
    portal.search.setText("8F2A19C4")
    portal.lookup()
    portal.set_decision(DriverMatch.MATCHED)
    portal.submit()

    portal.search.setText("8F2A19C4")
    assert portal.lookup() is None
    assert "No active entry" in portal.message.text()


def test_the_exit_pc_routes_operators_to_the_exit_portal(qapp, tmp_path) -> None:
    from dataclasses import replace
    from askari_vms.auth import authenticate
    from askari_vms.routing import Portal, portal_for
    from askari_vms.settings import default_settings
    from askari_vms.storage import Store

    store = Store(tmp_path / "exit.sqlite3")
    try:
        store.seed_if_empty()
        store.settings.save(replace(default_settings(), workstation_role="Exit"))
        operator, _ = authenticate(store.users.list(), "operator01", "change-me-123")
        assert portal_for(operator.role, "Exit") is Portal.EXIT
    finally:
        store.close()


def test_an_exit_persists_through_the_admin_window(qapp, tmp_path) -> None:
    """End to end: the vehicle leaves and the database says so after a restart."""
    from dataclasses import replace
    from askari_vms.settings import default_settings
    from askari_vms.storage import Store
    from askari_vms.ui.admin_window import AdminWindow

    path = tmp_path / "exit-e2e.sqlite3"
    store = Store(path)
    store.seed_if_empty()
    store.settings.save(replace(default_settings(), workstation_role="Exit"))
    store.close()

    window = AdminWindow(Store(path))
    try:
        assert window.exit_portal_button.isVisibleTo(window), "the Exit PC offers the Exit portal"
        portal = window.open_exit_portal()
        open_visit = next(v for v in window.store.visits.list() if v.is_inside)

        portal.search.setText(open_visit.barcode)
        assert portal.lookup() is not None
        portal.capture()
        portal.set_decision(DriverMatch.MATCHED)
        closed = portal.submit()
        assert closed is not None
    finally:
        window.store.close()

    reopened = Store(path)
    try:
        stored = next(v for v in reopened.visits.list() if v.visit_id == closed.visit_id)
        assert not stored.is_inside, "the exit must have persisted"
        assert stored.driver_match == DriverMatch.MATCHED
        assert any(e.target == stored.visit_id for e in reopened.audit.list())
    finally:
        reopened.close()


def test_the_emblem_appears_on_the_exit_header(portal) -> None:
    from PySide6.QtWidgets import QLabel
    from askari_vms.ui.pages.exit_portal import HEADER_LOGO_SIZE

    assert portal.emblem_shown
    emblem = portal.findChild(QLabel, "brandLogo")
    assert emblem is not None and not emblem.pixmap().isNull()
    assert emblem.width() == HEADER_LOGO_SIZE
