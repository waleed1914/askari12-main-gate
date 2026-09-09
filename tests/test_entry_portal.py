import time
from datetime import datetime, timedelta

import pytest

from askari_vms.audit import AuditLog
from askari_vms.auth import start_session
from askari_vms.categories import default_categories
from askari_vms.users import UserAccount, UserRole
from askari_vms.visits import (
    DriverMatch,
    VisitRecord,
    check_out,
    new_barcode,
    next_visit_id,
    previous_visit,
)

NOW = datetime(2026, 9, 3, 10, 0, 0)


def a_visit(visit_id="VIS-000007", plate="ABC-123", **changes) -> VisitRecord:
    values = dict(
        visit_id=visit_id, barcode="B1", entry_time=NOW - timedelta(hours=2),
        entry_operator="operator01", visitor_name="Ahmed Khan", cnic="35202-1234567-1",
        mobile="03001234567", vehicle_number=plate, vehicle_category="Car",
        destination="House 44-B",
    )
    values.update(changes)
    return VisitRecord(**values)


# ---------------- domain ----------------

def test_visit_ids_continue_past_what_is_stored() -> None:
    assert next_visit_id([]) == "VIS-000001"
    assert next_visit_id([a_visit("VIS-000007"), a_visit("VIS-000003")]) == "VIS-000008"
    # A stray id that does not fit the pattern must not break the sequence.
    assert next_visit_id([a_visit("legacy"), a_visit("VIS-000002")]) == "VIS-000003"


def test_barcodes_are_random_per_visit() -> None:
    tokens = {new_barcode() for _ in range(200)}
    assert len(tokens) == 200, "a receipt token must not be guessable from another"
    assert all(len(token) == 8 and token.isalnum() for token in tokens)


def test_previous_visit_matches_plate_or_cnic_and_takes_the_latest() -> None:
    old = a_visit("VIS-1", entry_time=NOW - timedelta(days=10))
    recent = a_visit("VIS-2", entry_time=NOW - timedelta(days=1))
    other = a_visit("VIS-3", plate="ZZZ-999", cnic="61101-0000000-1")
    visits = [old, recent, other]

    assert previous_visit(visits, "abc-123").visit_id == "VIS-2"
    assert previous_visit(visits, "", "61101-0000000-1").visit_id == "VIS-3"
    assert previous_visit(visits, "NOPE-1") is None
    assert previous_visit(visits, "", "") is None


# ---------------- portal ----------------

@pytest.fixture()
def portal(qapp):
    from askari_vms.ui.pages.entry_portal import EntryPortalWindow

    account = UserAccount("OP-001", "Operator One", "operator01", UserRole.OPERATOR)
    return EntryPortalWindow(
        audit_log=AuditLog(),
        categories=default_categories(),
        visits=[a_visit()],
        session=start_session(account, "Admin + Entry", NOW),
    )


def fill(portal, name="Sana Malik", cnic="35202-7654321-9", plate="XYZ-887", destination="House 12"):
    portal.fields["visitor_name"].setText(name)
    portal.fields["cnic"].setText(cnic)
    portal.fields["vehicle_number"].setText(plate)
    portal.fields["destination"].setText(destination)


def test_a_category_shortcut_selects_its_category(portal) -> None:
    assert portal.choose_shortcut("F2") is True
    assert portal._selected.name == "Truck"
    assert portal.choose_shortcut("F11") is False, "no category is bound to F11"
    assert portal._selected.name == "Truck", "a miss must not clear the selection"


def test_submitting_records_the_visit_and_opens_the_gate(portal) -> None:
    fill(portal)
    portal.choose_shortcut("F1")
    portal.capture()

    visit = portal.submit()
    assert visit.visit_id == "VIS-000008", "continues past the stored visit"
    assert visit.vehicle_number == "XYZ-887", "plate is normalised to upper case"
    assert visit.vehicle_category == "Car"
    assert visit.entry_operator == "operator01"
    assert visit.barcode and len(visit.barcode) == 8

    actions = [event.action for event in portal._audit.events()]
    assert "Visitor decision" in actions
    assert "Gate command" in actions, "the gate opens on print, and that is its own event"
    gate = next(e for e in portal._audit.events() if e.action == "Gate command")
    assert gate.target == "Visitor Entry"

    decision = next(e for e in portal._audit.events() if e.action == "Visitor decision")
    assert decision.severity.value == "Info", "a complete, fully captured visit is routine"


def test_an_empty_form_still_submits_and_is_flagged(portal) -> None:
    """The operator is never blocked; what is missing is recorded."""
    visit = portal.submit()
    assert visit.visit_id == "VIS-000008"

    decision = next(e for e in portal._audit.events() if e.action == "Visitor decision")
    assert decision.severity.value == "Warning"
    for label in ("Visitor name", "CNIC", "Vehicle number", "Destination", "Category"):
        assert label in decision.details
    assert "No image captured from" in decision.details
    assert any(e.action == "Gate command" for e in portal._audit.events()), "the gate still opens"


def test_a_missed_camera_is_recorded_but_does_not_block(portal) -> None:
    fill(portal)
    portal.choose_shortcut("F1")
    # No capture() call: every camera failed.
    portal.submit()
    decision = next(e for e in portal._audit.events() if e.action == "Visitor decision")
    assert "Driver camera" in decision.details and "ANPR overview" in decision.details
    assert decision.severity.value == "Warning"


def test_a_duplicate_active_visit_warns_but_allows(portal) -> None:
    portal.fields["vehicle_number"].setText("abc-123")     # already inside
    assert portal.warning.isVisibleTo(portal)
    assert "already has an open visit" in portal.warning.text()

    portal.choose_shortcut("F1")
    portal.fields["destination"].setText("House 9")
    assert portal.submit() is not None, "a duplicate must never be blocked"


def test_a_returning_visitor_is_offered_but_never_auto_filled(portal) -> None:
    closed = check_out(a_visit("VIS-000007"), "operator02", DriverMatch.MATCHED, exit_time=NOW)
    portal._visits = [closed]

    portal.fields["vehicle_number"].setText("ABC-123")
    assert portal.notice.isVisibleTo(portal)
    assert "Seen before" in portal.notice.text()
    assert portal.fields["visitor_name"].text() == "", "prior details must not fill themselves in"

    assert portal.reuse_previous() is True
    assert portal.fields["visitor_name"].text() == "Ahmed Khan"
    assert portal.fields["destination"].text() == "House 44-B"
    assert portal._selected.name == "Car"


def test_capture_marks_every_source(portal) -> None:
    assert all(not captured for captured in portal._captured.values())
    portal.capture()
    assert all(portal._captured.values())
    assert portal._streams["driver"].state.text() == "Captured (simulated)"
    assert portal.cnic_panel.state.text() == "ID card captured (simulated)"


def test_clearing_resets_everything_for_the_next_visitor(portal) -> None:
    fill(portal)
    portal.choose_shortcut("F3")
    portal.capture()

    portal.clear_form()
    assert portal.fields["visitor_name"].text() == "" and portal.fields["destination"].text() == ""
    assert portal._selected is None
    assert not any(portal._captured.values())
    assert portal._streams["driver"].state.text() == "No feed"
    assert portal.cnic_panel.state.text() == "No feed"


def test_submitting_clears_the_form_for_the_next_visitor(portal) -> None:
    fill(portal)
    portal.choose_shortcut("F1")
    portal.submit()
    assert portal.fields["visitor_name"].text() == ""
    assert portal._selected is None
    assert "submitted" in portal.status.text()


def test_the_portal_saves_through_the_admin_window(qapp, tmp_path) -> None:
    """End to end: submitting in the portal must land in the database."""
    from askari_vms.storage import Store
    from askari_vms.ui.admin_window import AdminWindow

    path = tmp_path / "askari.sqlite3"
    window = AdminWindow(Store(path))
    try:
        before = window.store.visits.count()
        portal = window.open_entry_portal()
        assert portal.isVisible()
        assert len(portal._categories) >= 3, "categories should come from the database"

        portal.fields["visitor_name"].setText("Persisted Visitor")
        portal.fields["vehicle_number"].setText("new-9")
        portal.fields["destination"].setText("House 77")
        portal.choose_shortcut("F1")
        portal.capture()
        visit = portal.submit()

        assert window.store.visits.count() == before + 1
        saved = next(v for v in window.store.visits.list() if v.visit_id == visit.visit_id)
        assert saved.visitor_name == "Persisted Visitor"
        assert saved.vehicle_number == "NEW-9"
        assert saved.is_inside, "a new entry has not exited yet"

        # Both audit events reached the shared, persisted log.
        stored = [e.action for e in window.store.audit.list()]
        assert "Visitor decision" in stored and "Gate command" in stored
    finally:
        window.store.close()


def test_the_entry_button_follows_the_workstation_role(qapp) -> None:
    from dataclasses import replace
    from askari_vms.storage import Store
    from askari_vms.ui.admin_window import AdminWindow

    window = AdminWindow(Store())
    try:
        assert window.settings.workstation_role == "Admin + Entry"
        assert window.entry_portal_button.isVisibleTo(window)
    finally:
        window.store.close()

    exit_store = Store()
    exit_store.settings.save(replace(exit_store.settings.load() or __import__(
        "askari_vms.settings", fromlist=["default_settings"]).default_settings(),
        workstation_role="Exit"))
    window = AdminWindow(exit_store)
    try:
        assert window.settings.workstation_role == "Exit"
        assert not window.entry_portal_button.isVisibleTo(window), (
            "Entry is restricted to its assigned PC"
        )
    finally:
        window.store.close()


def test_categories_persist_and_reach_the_portal(qapp, tmp_path) -> None:
    from askari_vms.categories import VehicleCategory
    from askari_vms.storage import Store
    from askari_vms.ui.admin_window import AdminWindow

    path = tmp_path / "cats.sqlite3"
    window = AdminWindow(Store(path))
    try:
        window.store.categories.save(VehicleCategory("Ambulance", "F9", "Emergency vehicle"))
        portal = window.open_entry_portal()
        assert portal.choose_shortcut("F9") is True
        assert portal._selected.name == "Ambulance"
    finally:
        window.store.close()

    reopened = Store(path)
    try:
        assert any(c.shortcut == "F9" for c in reopened.categories.list()), "categories must persist"
    finally:
        reopened.close()


def test_the_shortcut_and_the_dropdown_stay_in_step(portal) -> None:
    portal.choose_shortcut("F2")
    assert portal.vehicle_type.currentText() == "Truck"
    assert portal._selected.name == "Truck"

    # Choosing from the dropdown selects the category too.
    portal.vehicle_type.setCurrentText("Motorcycle")
    assert portal._selected.name == "Motorcycle"


def test_the_fields_match_the_operator_screen(portal) -> None:
    from askari_vms.ui.pages.entry_portal import FIELDS

    assert [label for _key, label in FIELDS] == [
        "Vehicle Number", "Destination", "Full Name", "Father/Husband Name", "CNIC",
        "DoB", "CNIC Issue Date", "CNIC Expiry Date", "Contact",
    ]
    assert set(portal.fields) == {key for key, _ in FIELDS}
    assert portal.vehicle_type.count() >= 3, "Vehicle Type is a dropdown of the categories"


def test_focus_returns_to_destination_after_submitting(portal) -> None:
    """ANPR fills the plate, so destination is the operator's first real action."""
    portal.show()
    fill(portal)
    portal.choose_shortcut("F1")
    portal.submit()
    # focusWidget() rather than hasFocus(): the window need not be activated for this.
    assert portal.focusWidget() is portal.fields["destination"], "focus should land on Destination"

    portal.fields["visitor_name"].setFocus()
    assert portal.focusWidget() is portal.fields["visitor_name"]
    portal.clear_form()
    assert portal.focusWidget() is portal.fields["destination"]


def test_the_cnic_fields_reach_the_record(portal) -> None:
    fill(portal)
    portal.fields["father_name"].setText("  Khan   Sahib ")
    portal.fields["date_of_birth"].setText("1990-01-01")
    portal.fields["cnic_issue_date"].setText("2015-06-01")
    portal.fields["cnic_expiry_date"].setText("2025-06-01")
    portal.choose_shortcut("F1")

    visit = portal.submit()
    assert visit.father_name == "Khan Sahib"
    assert visit.date_of_birth == "1990-01-01"
    assert visit.cnic_issue_date == "2015-06-01"
    assert visit.cnic_expiry_date == "2025-06-01"


def test_the_live_event_feed_shows_controller_readings(portal) -> None:
    from datetime import datetime as dt
    from askari_vms.etag_events import IN, OUT, ETagEvent, ETagEventKind

    portal.set_events([
        ETagEvent("ETL-1", dt(2026, 9, 2, 21, 36, 59), "Entry Controller", "E-tag Entry", IN,
                  "927394048", ETagEventKind.GRANTED, "Rizwan Rashid", "MNA-3996"),
        ETagEvent("ETL-2", dt(2026, 9, 2, 18, 59, 26), "Exit Controller", "E-tag Exit", OUT,
                  "940479717", ETagEventKind.EXPIRED, "Kamran Malik", "AFR-2879"),
    ])
    assert portal.events_table.rowCount() == 2
    assert portal.events_table.item(0, 0).text() == "Entry"
    assert portal.events_table.item(1, 0).text() == "Exit"
    assert portal.events_table.item(0, 1).text() == "927394048"
    assert portal.events_table.item(0, 2).text() == "Rizwan Rashid"
    assert portal.events_table.item(0, 3).text() == "MNA-3996"


def test_an_operator_signing_in_reaches_the_entry_portal(qapp, tmp_path) -> None:
    """The whole point of routing: operator credentials open the operator screen."""
    from askari_vms.auth import authenticate, start_session
    from askari_vms.routing import Portal, portal_for
    from askari_vms.storage import Store
    from askari_vms.ui.pages.entry_portal import EntryPortalWindow

    store = Store(tmp_path / "route.sqlite3")
    try:
        store.seed_if_empty()
        accounts = store.users.list()

        operator, problem = authenticate(accounts, "operator01", "change-me-123")
        assert problem == "" and operator is not None
        assert portal_for(operator.role, "Admin + Entry") is Portal.ENTRY

        session = start_session(operator, "Admin + Entry")
        portal = EntryPortalWindow(
            categories=store.categories.active(),
            visits=store.visits.list(),
            session=session,
            on_submit=store.visits.save,
            events=store.etag_events.list(),
        )
        portal.refresh_events()
        assert "operator01" in portal._session.operator
        assert portal.events_table.rowCount() > 0, "the live feed should be populated"
        assert len(portal._categories) >= 3

        admin, _ = authenticate(accounts, "admin", "change-me-123")
        assert portal_for(admin.role, "Admin + Entry") is Portal.ADMIN
    finally:
        store.close()


def test_the_entry_portal_logout_is_audited(qapp) -> None:
    from askari_vms.audit import AuditLog
    from askari_vms.auth import start_session
    from askari_vms.categories import default_categories
    from askari_vms.ui.pages.entry_portal import EntryPortalWindow

    log = AuditLog()
    account = UserAccount("OP-001", "Operator One", "operator01", UserRole.OPERATOR)
    portal = EntryPortalWindow(
        audit_log=log, categories=default_categories(),
        session=start_session(account, "Admin + Entry", NOW),
    )
    portal.sign_out()
    logout = [e for e in log.events() if e.action == "Logout"]
    assert logout and logout[0].target == "Entry portal"
    assert portal._session is None


def test_no_field_is_clipped_at_the_minimum_window_height(qapp) -> None:
    """The last field sat 1px from the card edge; an extra field would have hidden it."""
    from askari_vms.categories import default_categories
    from askari_vms.ui.pages.entry_portal import EntryPortalWindow

    page = EntryPortalWindow(categories=default_categories())
    page.resize(page.minimumWidth(), page.minimumHeight())
    page.show()
    qapp.processEvents()

    card = page.vehicle_type.parentWidget()
    widgets = [*page.fields.values(), page.vehicle_type]
    for widget in widgets:
        bottom = widget.mapTo(page, widget.rect().bottomLeft()).y()
        limit = card.mapTo(page, card.rect().bottomLeft()).y()
        assert bottom <= limit, f"{widget.objectName() or 'field'} is clipped by {bottom - limit}px"


def test_spare_height_goes_to_the_form_not_the_event_table(qapp) -> None:
    from askari_vms.categories import default_categories
    from askari_vms.ui.pages.entry_portal import EntryPortalWindow

    page = EntryPortalWindow(categories=default_categories())
    page.show()

    page.resize(1500, 780)
    qapp.processEvents()
    short = page.vehicle_type.parentWidget().height()

    page.resize(1500, 1200)
    qapp.processEvents()
    tall = page.vehicle_type.parentWidget().height()
    assert tall > short, "the form card should take the extra height"


def test_camera_panels_never_overlap_and_preview_grows(qapp):
    from PySide6.QtGui import QPixmap
    from askari_vms.ui.pages.entry_portal import EntryPortalWindow
    from askari_vms.ui.theme import APP_STYLESHEET
    from askari_vms.demo_data import etag_records, etag_event_records

    page = EntryPortalWindow(categories=default_categories(), events=etag_event_records(etag_records()))
    # Exercise the real theme as padding affects minimum layout sizes.
    page.setStyleSheet(APP_STYLESHEET + page.styleSheet())
    photo = QPixmap(1280, 720)
    photo.fill()
    for panel in (page.cnic_panel, page._streams["driver"]):
        panel.preview.setPixmap(photo)
        panel.preview.show()
    page.show()
    try:
        heights = []
        for width, height in ((1180, 760), (1600, 1000)):
            page.resize(width, height)
            qapp.processEvents()
            panels = [page.cnic_panel, *page._streams.values()]
            for index, panel in enumerate(panels):
                assert panel.parentWidget().rect().contains(panel.geometry())
                for other in panels[index + 1:]:
                    assert not panel.geometry().intersects(other.geometry())
            preview = page._streams["driver"].preview
            assert preview.pixmap().width() <= preview.width()
            assert preview.pixmap().height() <= preview.height()
            heights.append(preview.pixmap().height())
        assert heights[1] > heights[0]
        assert heights[1] > 200
        page.form_scroll.ensureWidgetVisible(page.vehicle_type)
        qapp.processEvents()
        viewport = page.form_scroll.viewport()
        assert viewport.rect().contains(page.vehicle_type.mapTo(viewport, page.vehicle_type.rect().center()))
        assert page.events_table.rowCount() == 8  # additional events remain scrollable
    finally:
        page.close()


def test_the_emblem_appears_on_the_entry_header(qapp) -> None:
    from PySide6.QtWidgets import QLabel
    from askari_vms.categories import default_categories
    from askari_vms.ui.brand import logo_path
    from askari_vms.ui.pages.entry_portal import HEADER_LOGO_SIZE, EntryPortalWindow

    assert logo_path() is not None, "no logo installed in ui/assets"
    page = EntryPortalWindow(categories=default_categories())
    assert page.emblem_shown
    emblem = page.findChild(QLabel, "brandLogo")
    assert emblem is not None and not emblem.pixmap().isNull()
    assert emblem.width() == HEADER_LOGO_SIZE


# ---------------- ID camera auto-reconnect ----------------

class _CameraReader:
    """A CNIC reader that claims a camera, so the portal starts its watchdog."""

    camera_index = 0

    def capture_and_read(self):  # pragma: no cover - never reached in these tests
        raise AssertionError("these tests never capture")


def _portal_without_a_camera(monkeypatch):
    """Build an entry portal on a machine that reports no cameras at all.

    Patched before construction for two reasons: the result must not depend on whatever
    webcam this developer machine happens to have, and a test must never switch on a
    real camera as a side effect.
    """
    from askari_vms.ui.pages.entry_portal import EntryPortalWindow

    monkeypatch.setattr(
        "askari_vms.ui.pages.entry_portal.QMediaDevices.videoInputs",
        staticmethod(lambda: []),
    )
    return EntryPortalWindow(cnic_reader=_CameraReader())


def test_a_missing_id_camera_keeps_retrying_instead_of_giving_up(qapp, monkeypatch) -> None:
    """A camera absent at start-up must still be picked up when it is plugged in."""
    from askari_vms.camera_link import LinkState

    portal = _portal_without_a_camera(monkeypatch)

    assert portal._camera_timer is not None and portal._camera_timer.isActive()
    assert portal._link.state is LinkState.LOST
    assert "no camera found" in portal.cnic_panel.state.text()

    # Someone plugs a camera in: Qt reports the device list changed, and the next tick
    # of the watchdog tries again rather than waiting out the backoff.
    portal._id_devices_changed()
    portal._camera_tick()
    assert portal._link.attempts == 1
    assert portal._link.state is LinkState.LOST  # still nothing there, so still trying


def test_a_camera_error_is_shown_and_scheduled_for_reconnect(qapp, monkeypatch) -> None:
    from askari_vms.camera_link import LinkState

    portal = _portal_without_a_camera(monkeypatch)
    portal._link.starting(time.monotonic())

    portal._id_camera_error(None, "Camera not ready")
    assert portal._link.state is LinkState.LOST
    assert "Camera not ready" in portal.cnic_panel.state.text()


def test_capturing_a_card_is_not_mistaken_for_a_lost_camera(qapp, monkeypatch) -> None:
    """The portal stops the feed itself while it OCRs, and must not reconnect over it."""
    from askari_vms.camera_link import LinkState

    portal = _portal_without_a_camera(monkeypatch)
    portal._link.frame(time.monotonic())
    portal._link.stopped()

    # Long enough that a stall would have been declared, had this been a fault.
    portal._camera_tick()
    assert portal._link.state is LinkState.STOPPED

    # A stop can itself raise an error from the driver. Still not a fault.
    portal._id_camera_error(None, "device stopped")
    assert portal._link.state is LinkState.STOPPED


def test_closing_the_portal_stops_the_watchdog(qapp, monkeypatch) -> None:
    """Otherwise the watchdog reopens the very camera the window is shutting down."""
    from askari_vms.camera_link import LinkState

    portal = _portal_without_a_camera(monkeypatch)
    portal.close()

    assert not portal._camera_timer.isActive()
    assert portal._link.state is LinkState.STOPPED
