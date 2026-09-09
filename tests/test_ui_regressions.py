"""Guards for defects that live in the UI/validation seam, not in pure functions."""

from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt

from askari_vms.audit import AuditLog
from askari_vms.controllers import DoorCommand
from askari_vms.ui.pages.audit_logs import AuditLogsPage
from askari_vms.ui.pages.door_controls import DoorControlsPage
from askari_vms.ui.pages.etags import ETagsPage
from askari_vms.ui.pages.users import UsersPage
from askari_vms.etags import ETagRecord
from askari_vms.users import UserAccount, UserRole, verify_password


def _fill_etag(form, **values: str) -> None:
    defaults = {"user_id": "A12-900", "resident_name": "Test Resident", "vehicle_number": "new-1", "rfid": "90000001"}
    defaults.update(values)
    for key, value in defaults.items():
        form._fields[key].setText(value)


def _check_first_row(page) -> None:
    page.table.item(0, 0).setCheckState(Qt.CheckState.Checked)


def test_unselected_vehicle_type_is_not_saved_as_a_value(qapp) -> None:
    page = _etags_page()
    page.open_add_dialog()
    form = page.stack.currentWidget()
    _fill_etag(form)  # deliberately leave the vehicle type combo on its prompt
    form._validate_and_save()
    assert page.stack.currentWidget() is form, "record saved without a vehicle type"
    assert len(page._records) == 2

    form._fields["vehicle_type"].setCurrentText("Car")
    form._validate_and_save()
    assert len(page._records) == 3
    saved = page._records[-1]
    assert saved.vehicle_type == "Car"
    assert saved.gender == "", "gender prompt was stored as a value"


def test_each_vehicle_may_hold_only_one_etag(qapp) -> None:
    page = _etags_page()
    existing = page._records[0]
    page.open_add_dialog()
    form = page.stack.currentWidget()
    _fill_etag(form, vehicle_number=existing.vehicle_number.lower(), rfid="90000002")
    form._fields["vehicle_type"].setCurrentText("Car")
    form._validate_and_save()
    assert page.stack.currentWidget() is form
    assert len(page._records) == 2


def test_one_resident_may_hold_several_etags(qapp) -> None:
    page = _etags_page()
    existing = page._records[0]
    page.open_add_dialog()
    form = page.stack.currentWidget()
    _fill_etag(form, user_id=existing.user_id, vehicle_number="second-1", rfid="90000003")
    form._fields["vehicle_type"].setCurrentText("Car")
    form._validate_and_save()
    assert len(page._records) == 3, "a resident must be allowed a second tag on another vehicle"


def test_new_accounts_are_not_admin_by_default(qapp) -> None:
    page = _users_page()
    page._show_add()
    form = page.stack.currentWidget()
    assert form.role.currentText() == UserRole.OPERATOR


def test_new_account_password_is_hashed_and_stored(qapp) -> None:
    page = _users_page()
    page._show_add()
    form = page.stack.currentWidget()
    form.employee_id.setText("OP-009")
    form.full_name.setText("Operator Nine")
    form.username.setText("operator09")
    form.password.setText("secret123")
    form.confirmation.setText("secret123")
    form.save_button.click()

    saved = page._records[-1]
    assert saved.username == "operator09"
    assert saved.password_hash and "secret123" not in saved.password_hash
    assert verify_password("secret123", saved.password_hash)
    assert not verify_password("wrong", saved.password_hash)


def test_editing_rejects_a_short_password_but_keeps_a_blank_one(qapp) -> None:
    page = _users_page()
    page.search.setText("operator01")
    _check_first_row(page)
    page._show_edit()
    form = page.stack.currentWidget()
    form.password.setText("123")
    form.confirmation.setText("123")
    form.save_button.click()
    assert page.stack.currentWidget() is form, "a 3-character password was accepted on edit"

    form.password.setText("")
    form.confirmation.setText("")
    form.full_name.setText("Operator One Renamed")
    form.save_button.click()
    assert page.stack.currentWidget() is page.list_page
    assert page._records[1].full_name == "Operator One Renamed"


def test_the_last_admin_cannot_be_deleted_or_deactivated(qapp) -> None:
    page = _users_page()
    page.search.setText("admin")
    assert page.table.rowCount() == 1
    _check_first_row(page)

    page._toggle_status()
    assert page._records[0].status == "Active", "last Admin was deactivated"

    _check_first_row(page)
    page._delete_selected()
    page._delete_selected()
    assert any(record.is_active_admin() for record in page._records), "last Admin was deleted"


def test_a_second_admin_frees_the_first(qapp) -> None:
    page = _users_page()
    page._records.append(UserAccount("ADMIN-02", "Second Admin", "admin2", UserRole.ADMIN))
    page.refresh()
    page.search.setText("ADMIN-01")
    _check_first_row(page)
    page._delete_selected()
    page._delete_selected()
    assert all(record.employee_id != "ADMIN-01" for record in page._records)


def test_gate_commands_reach_the_shared_audit_log(qapp) -> None:
    log = AuditLog()
    doors = DoorControlsPage(log)
    audit_page = AuditLogsPage(log)

    doors.issue_command(0, 0, DoorCommand.OPEN)

    events = log.events()
    assert len(events) == 1
    event = events[0]
    assert event.action == "Gate command"
    assert event.target == "Visitor Entry"
    assert event.operator and event.workstation
    assert audit_page.table.rowCount() == 1, "audit page did not pick up the new event"


def test_clearing_screen_history_leaves_audit_events(qapp) -> None:
    log = AuditLog()
    doors = DoorControlsPage(log)
    doors.issue_command(0, 0, DoorCommand.OPEN)
    doors.clear_history()

    assert doors.table.rowCount() == 0
    assert len(log.events()) == 1, "clearing the on-screen list must not erase audit records"


def test_settings_save_is_audited_and_rejects_a_bad_address(qapp) -> None:
    from askari_vms.ui.pages.settings import SettingsPage

    log = AuditLog()
    page = SettingsPage(log)

    page._controller_fields["exit"]["ip_address"].setText("192.168.0.999")
    assert page.save() is False, "an invalid IPv4 address was accepted"
    assert log.events() == ()

    page._controller_fields["exit"]["ip_address"].setText("192.168.0.91")
    page._controller_fields["exit"]["password"].setText("controller-secret")
    assert page.save() is True

    saved = page.settings.controllers[1]
    assert saved.ip_address == "192.168.0.91"
    assert saved.has_password, "typing a password should mark the controller as credentialled"
    assert "controller-secret" not in str(saved), "the secret must not live in the settings record"

    events = log.events()
    assert len(events) == 1
    assert events[0].action == "Record updated"
    assert events[0].target == "Settings"
    assert "controller-secret" not in events[0].details


def test_settings_blocks_two_cameras_on_one_lane(qapp) -> None:
    from askari_vms.settings import VISITOR_ENTRY
    from askari_vms.ui.pages.settings import SettingsPage

    page = SettingsPage(AuditLog())
    page._camera_fields["anpr_entry"]["lane"].setCurrentText(VISITOR_ENTRY)
    assert page.save() is False
    assert "more than one ANPR" in page.message.text()


def test_settings_revert_restores_the_saved_values(qapp) -> None:
    from askari_vms.ui.pages.settings import SettingsPage

    page = SettingsPage(AuditLog())
    original = page._controller_fields["entry"]["ip_address"].text()
    page._controller_fields["entry"]["ip_address"].setText("10.0.0.5")
    page._revert()
    assert page._controller_fields["entry"]["ip_address"].text() == original


def test_etag_logs_colour_every_state_and_escalate_unknown_tags(qapp) -> None:
    from askari_vms.etag_events import ETagEventKind
    from askari_vms.ui.pages.etag_logs import ROW_COLOURS

    log = AuditLog()
    page = _logs_page(log)

    kinds = [event.kind for event in page.events()]
    assert set(kinds) == {
        ETagEventKind.GRANTED, ETagEventKind.EXPIRING,
        ETagEventKind.EXPIRED, ETagEventKind.UNKNOWN,
    }, "the seeded feed should demonstrate every colour rule"

    # Only the unknown tag raises an audit event, and it is critical.
    events = log.events()
    assert len(events) == 1
    assert events[0].severity.value == "Critical"
    assert events[0].action == "Hardware event"
    assert events[0].operator == "SYSTEM"

    # Every row is painted with its state's colour.
    assert page.table.rowCount() == 4
    for row in range(page.table.rowCount()):
        kind = page.events()[row].kind
        assert page.table.item(row, 0).background().color().name() == ROW_COLOURS[kind].name()


def test_etag_logs_filters_narrow_the_feed(qapp) -> None:
    from askari_vms.etag_events import ETagEventKind

    page = _logs_page()
    page.kind_filter.setCurrentText(ETagEventKind.UNKNOWN.value)
    assert page.table.rowCount() == 1
    assert "critical" in page.summary.text()

    page.kind_filter.setCurrentText("All events")
    page.direction_filter.setCurrentText("Out")
    assert page.table.rowCount() == 1

    page.direction_filter.setCurrentText("Both directions")
    page.search.setText("ICT-001")
    assert page.table.rowCount() == 1
    page.search.setText("nothing-matches")
    assert page.table.rowCount() == 0


def test_a_new_reading_is_classified_against_the_registry(qapp) -> None:
    from askari_vms.etag_events import IN, ETagEventKind

    page = _logs_page()
    before = len(page.events())

    event = page.record_reading("Entry Controller", "E-tag Entry", IN, "10000002")
    assert event.kind is ETagEventKind.EXPIRING
    assert event.resident_name == "Expiry Example"
    assert len(page.events()) == before + 1
    assert page.events()[0] is event, "newest reading must appear first"


def _etags_page(controllers=None):
    """Registry page with two known tags, independent of the demo seed."""
    from askari_vms.ui.pages.etags import ETagsPage

    today = date.today()
    records = [
        ETagRecord("A12-001", "Sample Resident", "", "12", "44-B", "03001234567", "", "Male",
                   "Car", "ICT-001", "Toyota", "", "", "", "CH-001", "10000001", "Active",
                   today - timedelta(days=90), today + timedelta(days=180),
                   allowed_controllers=("entry", "exit")),
        ETagRecord("A12-002", "Expiry Example", "", "8", "10-A", "03007654321", "", "Female",
                   "SUV", "ABC-786", "Honda", "", "", "", "CH-002", "10000002", "Active",
                   today - timedelta(days=360), today + timedelta(days=7),
                   allowed_controllers=("entry", "exit")),
    ]
    return ETagsPage(controllers, records)


def _etag_registry():
    """Three tags covering active, near-expiry and lapsed. Independent of demo data."""
    today = date.today()

    def tag(user_id, name, plate, rfid, expiry_offset, controllers=("entry", "exit")):
        return ETagRecord(
            user_id, name, "", "12", "44-B", "03001234567", "", "Male", "Car", plate,
            "Toyota", "", "", "", "CH-001", rfid, "Active",
            today - timedelta(days=90), today + timedelta(days=expiry_offset),
            allowed_controllers=controllers,
        )

    return [
        tag("A12-001", "Sample Resident", "ICT-001", "10000001", 180),
        tag("A12-002", "Expiry Example", "ABC-786", "10000002", 7),
        tag("A12-003", "Lapsed Example", "LEA-4410", "10000003", -5, ("entry",)),
    ]


def _logs_page(log=None, records=None):
    """A log page with a known registry and four readings, one of them an unknown tag."""
    from askari_vms.etag_events import IN, OUT
    from askari_vms.ui.pages.etag_logs import ETagLogsPage

    page = ETagLogsPage(log or AuditLog(), records if records is not None else _etag_registry())
    now = datetime.now().replace(microsecond=0)
    for minutes, controller, door, direction, rfid in (
        (34, "Entry Controller", "E-tag Entry", IN, "77777777"),
        (21, "Entry Controller", "E-tag Entry", IN, "10000003"),
        (9, "Exit Controller", "E-tag Exit", OUT, "10000002"),
        (2, "Entry Controller", "E-tag Entry", IN, "10000001"),
    ):
        page.record_reading(controller, door, direction, rfid,
                            timestamp=now - timedelta(minutes=minutes), refresh=False)
    page.refresh()
    return page


def test_clicking_a_reading_opens_the_resident_profile(qapp) -> None:
    from askari_vms.ui.pages.resident_profile import ResidentProfilePage

    page = _logs_page()
    page._open_profile(0, 0)

    profile = page.stack.currentWidget()
    assert isinstance(profile, ResidentProfilePage)
    assert profile is not page.list_page
    assert profile._tags[0].resident_name == "Sample Resident"
    assert profile._unknown_rfid == ""
    assert [event.rfid for event in profile._history] == ["10000001"]

    profile._history_card()  # the card builds without a crash for a populated history
    page._show_list()
    assert page.stack.currentWidget() is page.list_page
    assert page.stack.count() == 1, "the profile page should be cleaned up on Back"


def test_clicking_an_unknown_tag_opens_a_tag_profile(qapp) -> None:
    from askari_vms.ui.pages.resident_profile import ResidentProfilePage

    page = _logs_page()
    unknown_row = next(i for i, e in enumerate(page.events()) if e.kind.value == "Unknown tag")
    page._open_profile(unknown_row, 0)

    profile = page.stack.currentWidget()
    assert isinstance(profile, ResidentProfilePage)
    assert profile._unknown_rfid == "77777777"
    assert profile._tags == []
    assert len(profile._history) == 1


def test_profile_gathers_every_tag_and_reading_for_one_resident(qapp) -> None:
    from askari_vms.etag_events import IN, OUT

    today = date.today()

    def record(rfid: str, plate: str) -> ETagRecord:
        return ETagRecord(
            "A12-777", "Multi Vehicle", "", "5", "9-D", "03001112222", "", "Male", "Car",
            plate, "Toyota", "", "", "", "CH-777", rfid, "Active",
            today - timedelta(days=30), today + timedelta(days=200), allowed_controllers=("entry", "exit"),
        )

    page = _logs_page(records=[record("20000001", "AAA-111"), record("20000002", "BBB-222")])
    page.record_reading("Entry Controller", "E-tag Entry", IN, "20000001")
    page.record_reading("Exit Controller", "E-tag Exit", OUT, "20000002")
    page.record_reading("Entry Controller", "E-tag Entry", IN, "20000001")

    page._open_profile(0, 0)
    profile = page.stack.currentWidget()
    assert len(profile._tags) == 2, "both of the resident's tags should be listed"
    assert len(profile._history) == 3, "history must merge readings across the resident's tags"


def test_clicking_maps_to_the_filtered_row_not_the_raw_index(qapp) -> None:
    page = _logs_page()
    page.search.setText("LEA-4410")
    assert page.table.rowCount() == 1

    page._open_profile(0, 0)
    profile = page.stack.currentWidget()
    assert profile._tags[0].resident_name == "Lapsed Example", "clicked the wrong record while filtered"


def test_etag_log_columns_match_the_agreed_header(qapp) -> None:
    from askari_vms.ui.pages.etag_logs import ACTION_COLUMN, COLUMNS

    page = _logs_page()
    assert COLUMNS == ("Sr #", "ETag ID", "Full Name", "Entry Time", "Door", "Status", "Action")
    headers = [page.table.horizontalHeaderItem(i).text() for i in range(page.table.columnCount())]
    assert headers == list(COLUMNS)

    # Sr # numbers the rows on screen, starting at 1.
    assert [page.table.item(r, 0).text() for r in range(page.table.rowCount())] == ["1", "2", "3", "4"]
    first = page.events()[0]
    assert page.table.item(0, 1).text() == first.rfid
    assert page.table.item(0, 2).text() == first.resident_name
    assert page.table.item(0, 4).text() == first.door
    assert page.table.item(0, 5).text() == first.kind.value
    assert page.table.cellWidget(0, ACTION_COLUMN) is not None, "Action column needs its button"


def test_action_button_opens_the_profile_for_its_own_row(qapp) -> None:
    from askari_vms.ui.pages.etag_logs import ACTION_COLUMN
    from askari_vms.ui.pages.resident_profile import ResidentProfilePage

    page = _logs_page()
    page.search.setText("LEA-4410")
    assert page.table.rowCount() == 1
    assert page.table.item(0, 0).text() == "1", "Sr # restarts at 1 for a filtered view"

    page.table.cellWidget(0, ACTION_COLUMN).click()
    profile = page.stack.currentWidget()
    assert isinstance(profile, ResidentProfilePage)
    assert profile._tags[0].resident_name == "Lapsed Example"


def test_action_buttons_are_rebuilt_when_the_filter_changes(qapp) -> None:
    from askari_vms.ui.pages.etag_logs import ACTION_COLUMN
    from askari_vms.ui.pages.resident_profile import ResidentProfilePage

    page = _logs_page()
    page.kind_filter.setCurrentText("Unknown tag")
    assert page.table.rowCount() == 1

    page.table.cellWidget(0, ACTION_COLUMN).click()
    profile = page.stack.currentWidget()
    assert isinstance(profile, ResidentProfilePage)
    assert profile._unknown_rfid == "77777777", "a stale button opened the wrong record"


def test_etag_form_fields_match_the_reference_screen(qapp) -> None:
    from askari_vms.ui.pages.etags import ETagsPage

    # Matches the live /addetag screen. Member and e-tag are one record, so the
    # reference's duplicated Member name/contact/CNIC fields are deliberately not copied.
    reference = {
        "member_id", "user_id", "resident_name", "care_of", "street_no", "house_no",
        "mobile_no", "cnic", "address", "lesco_ref_no", "gender", "vehicle_type",
        "vehicle_number", "make", "model", "position", "station_unit", "department",
        "challan_number", "rfid", "status", "issue_date", "expiry_date", "comments",
    }
    page = _etags_page()
    page.open_add_dialog()
    form = page.stack.currentWidget()
    assert set(form._fields) == reference, "the Add E-Tag form drifted from the reference screen"
    assert set(form._controller_checks) == {"entry", "exit"}
    assert hasattr(form, "vehicle_image_label"), "the vehicle image picker is missing"
    assert form.vehicle_image_label.text() == "No file chosen"


def test_allowed_controllers_follow_the_configured_controllers(qapp) -> None:
    from askari_vms.settings import controller_choices, default_settings
    from askari_vms.ui.pages.etags import ETagsPage

    settings = default_settings()
    page = _etags_page(controller_choices(settings))
    page.open_add_dialog()
    labels = [cb.text() for cb in page.stack.currentWidget()._controller_checks.values()]
    assert "192.168.0.90" in labels[0], "a configured controller should show its address"
    assert "E-tag Entry" in labels[0], "the picker must name the e-tag door, not the visitor door"
    assert "not configured" in labels[1]
    page._show_list()

    # Give the Exit controller an address; the picker must follow.
    from askari_vms.settings import with_controller
    from dataclasses import replace as dc_replace
    updated = with_controller(settings, dc_replace(settings.controllers[1], ip_address="192.168.0.91"))
    page.set_controllers(controller_choices(updated))
    page.open_add_dialog()
    labels = [cb.text() for cb in page.stack.currentWidget()._controller_checks.values()]
    assert "192.168.0.91" in labels[1]
    assert "E-tag Exit" in labels[1]


def test_saving_settings_updates_the_etag_picker(qapp) -> None:
    from askari_vms.ui.admin_window import AdminWindow
    from askari_vms.ui.pages.settings import SettingsPage

    window = _window()
    settings_page = next(
        window.pages.widget(i) for i in range(window.pages.count())
        if isinstance(window.pages.widget(i), SettingsPage)
    )
    settings_page._controller_fields["exit"]["ip_address"].setText("192.168.0.91")
    assert settings_page.save() is True

    window.etags_page.open_add_dialog()
    labels = [cb.text() for cb in window.etags_page.stack.currentWidget()._controller_checks.values()]
    assert "192.168.0.91" in labels[1], "the E-Tag picker did not follow the saved settings"


def test_employee_form_matches_the_reference_screen(qapp) -> None:
    from askari_vms.ui.pages.users import SHORTCUTS, UsersPage

    page = _users_page()
    page._show_add()
    form = page.stack.currentWidget()

    for attribute in ("employee_id", "full_name", "status", "contact", "address", "email",
                      "department", "designation", "role", "username", "password",
                      "confirmation", "cnic"):
        assert hasattr(form, attribute), f"missing field: {attribute}"
    assert set(form._cnic_paths) == {"cnic_front", "cnic_back"}
    assert set(form._cnic_labels) == {"cnic_front", "cnic_back"}
    assert form._cnic_labels["cnic_front"].text() == "No file chosen"

    # Role (job title) and Employee Type (permission level) are separate.
    assert [form.role.itemText(i) for i in range(form.role.count())] == ["Admin", "Operator", "Reporter"]
    assert form.designation.text() == ""
    assert len(SHORTCUTS) == 9


def test_employee_shortcuts_focus_their_fields_and_submit(qapp) -> None:
    from PySide6.QtGui import QKeySequence, QShortcut
    from askari_vms.ui.pages.users import SHORTCUTS, UsersPage

    page = _users_page()
    page._show_add()
    form = page.stack.currentWidget()

    bound = {s.key().toString() for s in form.findChildren(QShortcut)}
    for key, _what, _attribute in SHORTCUTS:
        assert key in bound, f"{key} is not bound"
    assert "Ctrl+Return" in bound or "Ctrl+Enter" in bound

    # Every shortcut points at a real widget on the form.
    for _key, _what, attribute in SHORTCUTS:
        assert getattr(form, attribute) is not None


def test_employee_extra_fields_round_trip(qapp) -> None:
    from askari_vms.ui.pages.users import UsersPage

    page = _users_page()
    page._show_add()
    form = page.stack.currentWidget()
    form.employee_id.setText("EMP-050")
    form.full_name.setText("Gate Supervisor")
    form.username.setText("emp050")
    form.address.setPlainText("  House 5, Askari Greens  ")
    form.designation.setText("  Shift   Supervisor ")
    form.department.setText("Security")
    form._cnic_paths["cnic_front"] = r"C:\scans\front.png"
    form.password.setText("secret123")
    form.confirmation.setText("secret123")
    form.save_button.click()

    saved = page._records[-1]
    assert saved.address == "House 5, Askari Greens"
    assert saved.designation == "Shift Supervisor", "whitespace should collapse like other names"
    assert saved.cnic_front == r"C:\scans\front.png"
    assert saved.role == "Operator"

    # Editing the record shows those values again.
    page.search.setText("emp050")
    _check_first_row(page)
    page._show_edit()
    editing = page.stack.currentWidget()
    assert editing.address.toPlainText() == "House 5, Askari Greens"
    assert editing.designation.text() == "Shift Supervisor"
    assert editing._cnic_labels["cnic_front"].text() == "front.png"
    assert editing._cnic_labels["cnic_back"].text() == "No file chosen"


def _demo_visits():
    from askari_vms.visits import DriverMatch, VisitRecord

    now = datetime.now().replace(microsecond=0)
    return [
        VisitRecord("VIS-000021", "8F2A19C4", now - timedelta(minutes=18), "operator01",
                    visitor_name="Ahmed Khan", cnic="35202-1234567-1", mobile="03001234567",
                    vehicle_number="ABC-123", vehicle_category="Car", destination="House 44-B"),
        VisitRecord("VIS-000020", "1B77E3D0", now - timedelta(hours=1), "operator01",
                    visitor_name="Delivery Rider", vehicle_number="LEB-9921",
                    vehicle_category="Motorcycle", destination="Block C"),
        VisitRecord("VIS-000019", "C40D8821", now - timedelta(hours=3), "operator01",
                    visitor_name="Sana Malik", cnic="35202-7654321-9", vehicle_number="XYZ-887",
                    vehicle_category="Car", destination="House 12",
                    exit_time=now - timedelta(hours=1, minutes=40), exit_operator="operator02",
                    exit_door="Visitor Exit", driver_match=DriverMatch.MATCHED),
        VisitRecord("VIS-000018", "9AA10F55", now - timedelta(hours=5), "operator01",
                    visitor_name="Contractor Van", cnic="35202-1112223-3",
                    vehicle_number="TRK-4410", vehicle_category="Truck", destination="Site office",
                    exit_time=now - timedelta(hours=2), exit_operator="operator02",
                    exit_door="Visitor Exit", driver_match=DriverMatch.MISMATCHED, receipt_lost=True),
    ]


def _vms_page(log=None):
    from askari_vms.ui.pages.vms_operations import VmsOperationsPage
    return VmsOperationsPage(log or AuditLog(), _demo_visits())


def _window():
    """AdminWindow on an in-memory database, so tests never touch the real data folder."""
    from askari_vms.storage import Store
    from askari_vms.ui.admin_window import AdminWindow

    return AdminWindow(Store())


def _users_page():
    """One Admin plus two Operators, so the last-admin guard is exercised."""
    from askari_vms.ui.pages.users import UsersPage

    return UsersPage([
        UserAccount("ADMIN-01", "Administrator", "admin", UserRole.ADMIN),
        UserAccount("OP-001", "Operator One", "operator01", UserRole.OPERATOR),
        UserAccount("OP-002", "Operator Two", "operator02", UserRole.OPERATOR),
    ])


def test_missing_data_filter_finds_incomplete_visits(qapp) -> None:
    page = _vms_page()
    assert page.table.rowCount() == 4
    assert "1 with missing data" in page.summary.text()
    assert "1 mismatched driver" in page.summary.text()

    page.missing_only.setChecked(True)
    assert page.table.rowCount() == 1
    assert page.table.item(0, 1).text() == "VIS-000020", "the record submitted without CNIC"

    page.missing_only.setChecked(False)
    page.mismatched_only.setChecked(True)
    assert page.table.rowCount() == 1
    assert page.table.item(0, 1).text() == "VIS-000018"


def test_status_and_category_filters(qapp) -> None:
    from askari_vms.visits import VisitState

    page = _vms_page()
    page.status_filter.setCurrentText(VisitState.INSIDE.value)
    assert page.table.rowCount() == 2
    page.status_filter.setCurrentText(VisitState.EXITED.value)
    assert page.table.rowCount() == 2

    page.status_filter.setCurrentText("All statuses")
    page.category_filter.setCurrentText("Truck")
    assert page.table.rowCount() == 1
    assert page.table.item(0, 5).text() == "Truck"


def test_incomplete_and_mismatched_rows_are_coloured(qapp) -> None:
    from askari_vms.ui.pages.vms_operations import (
        INCOMPLETE_COLOUR, INSIDE_COLOUR, MISMATCH_COLOUR, row_colour,
    )

    page = _vms_page()
    by_id = {v.visit_id: v for v in page._visits}
    assert row_colour(by_id["VIS-000021"]).name() == INSIDE_COLOUR.name()
    assert row_colour(by_id["VIS-000020"]).name() == INCOMPLETE_COLOUR.name()
    assert row_colour(by_id["VIS-000018"]).name() == MISMATCH_COLOUR.name()


def test_manual_checkout_closes_the_visit_and_is_audited(qapp) -> None:
    from askari_vms.ui.pages.vms_operations import VisitDetailPage

    log = AuditLog()
    page = _vms_page(log)
    page._open_detail(0, 0)
    detail = page.stack.currentWidget()
    assert isinstance(detail, VisitDetailPage)
    assert hasattr(detail, "checkout_button"), "an active visit must offer manual checkout"

    detail.checkout_button.click()
    closed = next(v for v in page._visits if v.visit_id == "VIS-000021")
    assert not closed.is_inside
    assert closed.exit_operator == "admin"
    assert page.stack.currentWidget() is page.list_page

    events = log.events()
    assert len(events) == 1
    assert events[0].action == "Record updated"
    assert events[0].severity.value == "Warning", "bypassing the Exit portal deserves a warning"
    assert "VIS-000021" in events[0].target


def test_a_closed_visit_offers_no_checkout_button(qapp) -> None:
    page = _vms_page()
    page.status_filter.setCurrentText("Exited")
    page._open_detail(0, 0)
    detail = page.stack.currentWidget()
    assert not hasattr(detail, "checkout_button")


def test_detail_opens_the_filtered_row_and_shows_vehicle_history(qapp) -> None:
    page = _vms_page()
    page.search.setText("TRK-4410")
    assert page.table.rowCount() == 1
    page._open_detail(0, 0)
    detail = page.stack.currentWidget()
    assert detail.visit.visit_id == "VIS-000018", "opened the wrong record while filtered"
    assert "Mismatched" in detail.visit.driver_match


def _assert_no_dead_strip(table) -> None:
    """A fixed-height table must be exactly header + rows, or a doubled line appears."""
    header = table.horizontalHeader().sizeHint().height()
    rows = sum(table.rowHeight(row) for row in range(table.rowCount()))
    expected = header + rows + 2 * table.frameWidth()
    assert table.height() == expected, (
        f"table is {table.height() - expected:+d}px off its content "
        f"(header {header} + rows {rows} + frame {2 * table.frameWidth()})"
    )


def test_profile_tables_have_no_dead_strip(qapp) -> None:
    from PySide6.QtWidgets import QTableWidget

    page = _logs_page()
    page._open_profile(0, 0)
    profile = page.stack.currentWidget()
    tables = profile.findChildren(QTableWidget)
    assert tables, "the profile should render its tag and history tables"
    for table in tables:
        if table.rowCount() <= 10:
            _assert_no_dead_strip(table)


def test_visit_history_table_has_no_dead_strip(qapp) -> None:
    from PySide6.QtWidgets import QTableWidget

    page = _vms_page()
    page.search.setText("TRK-4410")
    page._open_detail(0, 0)
    detail = page.stack.currentWidget()
    for table in detail.findChildren(QTableWidget):
        if table.rowCount() <= 8:
            _assert_no_dead_strip(table)


def test_new_etag_fields_round_trip(qapp) -> None:
    from askari_vms.ui.pages.etags import ETagsPage

    page = _etags_page()
    page.open_add_dialog()
    form = page.stack.currentWidget()
    _fill_etag(form, rfid="90000900", vehicle_number="mem-1")
    form._fields["vehicle_type"].setCurrentText("Car")
    form._fields["member_id"].setText(" mem-0041 ")
    form._fields["address"].setText("  House 123,   Phase 3  ")
    form._fields["lesco_ref_no"].setText(" 68508697-289450 ")
    form._fields["model"].setText("  Picanto  ")
    form._vehicle_image = r"C:\scans\car.png"
    form._validate_and_save()

    saved = page._records[-1]
    assert saved.member_id == "MEM-0041"
    assert saved.address == "House 123, Phase 3"
    assert saved.lesco_ref_no == "68508697-289450"
    assert saved.model == "Picanto"
    assert saved.vehicle_image == r"C:\scans\car.png"

    # Editing shows them again.
    page.search.setText("90000900")
    page.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    page.open_edit_dialog()
    editing = page.stack.currentWidget()
    assert editing._fields["member_id"].text() == "MEM-0041"
    assert editing._fields["model"].text() == "Picanto"
    assert editing.vehicle_image_label.text() == "car.png"


def test_sidebar_fits_the_minimum_window_height(qapp) -> None:
    """Over-constrain the sidebar and Qt overlaps the brand block instead of scrolling."""
    from PySide6.QtWidgets import QFrame
    window = _window()
    window.show()
    sidebar = window.findChild(QFrame, "sidebar")
    needed = sidebar.minimumSizeHint().height()
    allowed = window.minimumHeight()
    assert needed <= allowed, (
        f"sidebar needs {needed}px but the window minimum is {allowed}px; "
        "the brand block or nav list has outgrown it"
    )


def test_brand_shows_the_installed_emblem(qapp) -> None:
    from PySide6.QtWidgets import QLabel
    from askari_vms.ui.admin_window import AdminWindow
    from askari_vms.ui.brand import circular_logo, logo_path

    assert logo_path() is not None, "no logo file installed in ui/assets"
    badge = circular_logo(46)
    assert not badge.isNull()
    # Masked to a circle, so a corner is transparent while the centre is not.
    image = badge.toImage()
    assert image.pixelColor(1, 1).alpha() == 0, "logo corners should be masked away"
    assert image.pixelColor(image.width() // 2, image.height() // 2).alpha() == 255

    window = _window()
    mark = window.findChild(QLabel, "brandLogo")
    assert mark is not None and not mark.pixmap().isNull()


def test_dark_border_is_trimmed_from_the_logo(qapp) -> None:
    from PySide6.QtGui import QColor, QImage
    from askari_vms.ui.brand import trim_dark_border

    image = QImage(40, 40, QImage.Format.Format_RGB32)
    image.fill(QColor("#f7f7f7"))
    for y in range(6):                      # a black band along the top, as the JPEG has
        for x in range(40):
            image.setPixelColor(x, y, QColor("#000000"))

    trimmed = trim_dark_border(image)
    assert trimmed.height() == 34, f"expected the 6px band removed, got {40 - trimmed.height()}px"
    assert trimmed.width() == 40
    assert trimmed.pixelColor(0, 0).name() == "#f7f7f7"


def test_records_survive_closing_and_reopening_the_app(qapp, tmp_path) -> None:
    """The whole point of the store: a restart must not lose anything."""
    from askari_vms.storage import Store
    from askari_vms.ui.admin_window import AdminWindow
    from askari_vms.ui.pages.vms_operations import VmsOperationsPage

    path = tmp_path / "data" / "askari_vms.sqlite3"

    first = AdminWindow(Store(path))
    assert first.store.etags.count() > 40, "a fresh database should seed itself"
    tags_before = first.store.etags.count()

    # Add an e-tag through the UI.
    first.etags_page.open_add_dialog()
    form = first.etags_page.stack.currentWidget()
    for key, value in (("user_id", "A12-999"), ("resident_name", "Persisted Resident"),
                       ("vehicle_number", "PER-001"), ("rfid", "919191919")):
        form._fields[key].setText(value)
    form._fields["vehicle_type"].setCurrentText("Car")
    form._validate_and_save()

    # Close a visit, which writes both the visit and an audit event.
    vms = next(first.pages.widget(i) for i in range(first.pages.count())
               if isinstance(first.pages.widget(i), VmsOperationsPage))
    open_visit = next(v for v in vms._visits if v.is_inside)
    vms._manual_checkout(open_visit)
    audit_before = len(first.store.audit.list())
    first.store.close()

    second = AdminWindow(Store(path))
    try:
        assert second.store.etags.count() == tags_before + 1
        saved = next(r for r in second.store.etags.list() if r.rfid == "919191919")
        assert saved.resident_name == "Persisted Resident"
        assert saved.vehicle_number == "PER-001"

        reopened = next(v for v in second.store.visits.list() if v.visit_id == open_visit.visit_id)
        assert not reopened.is_inside, "the manual checkout should have persisted"

        events = second.store.audit.list()
        assert len(events) == audit_before
        assert any(e.target == open_visit.visit_id for e in events)
        assert second.audit_log.events(), "the window should load stored audit events"
    finally:
        second.store.close()


def test_audit_ids_continue_after_a_restart(qapp, tmp_path) -> None:
    from askari_vms.audit import AuditLog
    from askari_vms.storage import Store

    path = tmp_path / "askari.sqlite3"
    store = Store(path)
    log = AuditLog(repository=store.audit)
    first = log.record("Login", "Admin portal", "Signed in", "details")
    second = log.record("Logout", "Admin portal", "Signed out", "details")
    assert (first.event_id, second.event_id) == ("AUD-000001", "AUD-000002")
    store.close()

    store = Store(path)
    reopened = AuditLog(store.audit.list(), repository=store.audit)
    third = reopened.record("Login", "Admin portal", "Signed in again", "details")
    assert third.event_id == "AUD-000003", "ids must not restart and collide"
    assert len(store.audit.list()) == 3
    store.close()


def _store_with_accounts():
    from dataclasses import replace
    from askari_vms.storage import Store
    from askari_vms.users import hash_password

    store = Store()
    store.seed_demo_data()
    # Give one account a known password and deactivate another.
    accounts = store.users.list()
    admin = next(a for a in accounts if a.username == "admin")
    store.users.save(replace(admin, password_hash=hash_password("adminpass1")))
    operator = next(a for a in accounts if a.username == "operator01")
    store.users.save(replace(operator, status="Deactivated",
                             password_hash=hash_password("operatorpass1")))
    return store


def test_login_screen_refuses_bad_credentials_and_deactivated_accounts(qapp) -> None:
    from askari_vms.auth import BAD_CREDENTIALS, DEACTIVATED
    from askari_vms.ui.pages.login import LoginWindow

    store = _store_with_accounts()
    try:
        login = LoginWindow(store)
        login.username.setText("admin")
        login.password.setText("wrong")
        assert login.sign_in() is None
        assert login.error.text() == BAD_CREDENTIALS
        assert login.password.text() == "", "the password field should clear after a failure"

        login.username.setText("operator01")
        login.password.setText("operatorpass1")
        assert login.sign_in() is None
        assert login.error.text() == DEACTIVATED

        login.username.setText("admin")
        login.password.setText("adminpass1")
        session = login.sign_in()
        assert session is not None and session.operator == "admin"
    finally:
        store.close()


def test_signing_in_and_out_is_audited_with_the_real_operator(qapp) -> None:
    from askari_vms.auth import start_session
    from askari_vms.ui.admin_window import AdminWindow

    store = _store_with_accounts()
    try:
        admin = store.users.find("admin")
        session = start_session(admin, "Admin + Entry")
        window = AdminWindow(store, session)

        login_events = [e for e in window.audit_log.events() if e.action == "Login"
                        and e.operator == "admin" and "signed in" in e.summary]
        assert login_events, "signing in must be audited"
        assert login_events[0].workstation == "Admin + Entry"

        # Anything recorded now carries the signed-in operator, not a placeholder.
        recorded = window.audit_log.record("Gate command", "Visitor Entry", "Opened", "details")
        assert recorded.operator == "admin"
        assert recorded.workstation == "Admin + Entry"

        window.sign_out()
        logout = [e for e in store.audit.list() if e.action == "Logout"]
        assert logout and logout[0].operator == "admin"
        assert window.session is None
    finally:
        store.close()


def test_without_a_session_the_portal_hides_sign_out(qapp) -> None:
    window = _window()
    assert window.session is None
    assert not window.sign_out_button.isVisible()
    # Only the seeded demonstration rows exist; no Login was recorded for this window.
    seeded_operators = {"admin", "operator01", "operator02", "SYSTEM"}
    assert all(event.operator in seeded_operators for event in window.audit_log.events())


def _reports_page(log=None):
    from askari_vms.etag_events import IN, ETagEvent, ETagEventKind
    from askari_vms.ui.pages.reports import ReportsPage

    now = datetime.now().replace(microsecond=0)
    events = [
        ETagEvent("ETL-1", now - timedelta(minutes=5), "Entry Controller", "E-tag Entry", IN,
                  "900000001", ETagEventKind.GRANTED, "Rizwan Rashid", "MNA-3996"),
        ETagEvent("ETL-2", now - timedelta(days=3), "Entry Controller", "E-tag Entry", IN,
                  "900000002", ETagEventKind.EXPIRED, "Imran Iqbal", "BGH-5388"),
    ]
    return ReportsPage(log or AuditLog(), _demo_visits(), events)


def test_reports_show_every_record_before_any_filter_is_applied(qapp) -> None:
    """A QDateEdit clamps an unset date to its minimum, which once hid every row."""
    page = _reports_page()
    assert len(page._rows) == 6
    assert page._total_tiles["total"].text() == "6"
    assert page._total_tiles["etag"].text() == "2"
    assert page._total_tiles["visitor"].text() == "4"
    assert page.table.rowCount() == 6


def test_reports_merge_visits_and_etag_readings_newest_first(qapp) -> None:
    page = _reports_page()
    stamps = [row.timestamp for row in page._rows]
    assert stamps == sorted(stamps, reverse=True)
    assert page.table.item(0, 0).text() == "E-Tag", "the newest record is the e-tag reading"


def test_reports_filters_narrow_the_records(qapp) -> None:
    from askari_vms.reports import ETAG

    page = _reports_page()
    page.search.setText("MNA-3996")
    page.refresh()
    assert page.table.rowCount() == 1

    page.search.clear()
    page.category_filter.setCurrentText("Truck")
    page.refresh()
    assert page.table.rowCount() == 1
    assert page._total_tiles["etag"].text() == "0"

    page.category_filter.setCurrentText(ETAG)
    page.refresh()
    assert page.table.rowCount() == 2

    page.reset_filters()
    assert page.table.rowCount() == 6, "Reset must bring every record back"


def test_reports_date_range_filters(qapp) -> None:
    """Fixed timestamps: a wall-clock-relative fixture flips meaning around midnight."""
    from PySide6.QtCore import QDate
    from askari_vms.etag_events import IN, ETagEvent, ETagEventKind
    from askari_vms.ui.pages.reports import ReportsPage
    from askari_vms.visits import VisitRecord

    def at(day, hour=12):
        return datetime(2026, 9, day, hour, 0, 0)

    visits = [
        VisitRecord(f"VIS-{day}", "B", at(day), "operator01", visitor_name="Visitor",
                    vehicle_number=f"AAA-{day:03d}", vehicle_category="Car", destination="House 1")
        for day in (1, 2, 3, 4)
    ]
    events = [ETagEvent("ETL-1", at(2, 9), "Entry Controller", "E-tag Entry", IN,
                        "900000001", ETagEventKind.GRANTED, "Resident", "MNA-1")]
    page = ReportsPage(AuditLog(), visits, events)
    assert page.table.rowCount() == 5

    page.start_date.setDate(QDate(2026, 9, 2))
    page.end_date.setDate(QDate(2026, 9, 3))
    page.refresh()
    assert page.table.rowCount() == 3, "2 Sep and 3 Sep inclusive, e-tag reading included"

    page.reset_filters()
    assert page.table.rowCount() == 5


def test_reports_breakdown_is_always_shown_and_follows_group_by(qapp) -> None:
    from askari_vms.reports import GROUP_DOOR

    page = _reports_page()
    assert page._category_row.count() > 1, "the category breakdown should never be empty"
    assert "Vehicle Category" in page._category_heading.text()

    page.group_by.setCurrentText(GROUP_DOOR)
    assert GROUP_DOOR in page._category_heading.text()


def test_report_export_writes_csv_and_is_audited(qapp, tmp_path, monkeypatch) -> None:
    from PySide6.QtWidgets import QFileDialog
    from askari_vms.reports import CSV_HEADER

    log = AuditLog()
    page = _reports_page(log)
    target = tmp_path / "report.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))

    assert page.export_csv() == str(target)
    text = target.read_text(encoding="utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0] == ",".join(CSV_HEADER)
    assert len(lines) == 7, "header plus every visible row"

    events = log.events()
    assert len(events) == 1
    assert events[0].action == "Bulk action"
    assert "6 report row" in events[0].summary


def test_cancelling_the_export_writes_nothing(qapp, monkeypatch) -> None:
    from PySide6.QtWidgets import QFileDialog

    log = AuditLog()
    page = _reports_page(log)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    assert page.export_csv() is None
    assert log.events() == (), "a cancelled export must not be audited"


def test_reports_table_grows_instead_of_scrolling(qapp) -> None:
    """The page scrolls, so the table must show every row of the page it is on."""
    from PySide6.QtCore import Qt

    page = _reports_page()
    page.resize(1400, 900)
    qapp.processEvents()

    assert page.table.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert page.table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    _assert_no_dead_strip(page.table)
    assert page.table.rowCount() == 6

    # Every row is actually inside the widget, not clipped below its edge.
    last = page.table.rowViewportPosition(page.table.rowCount() - 1) + page.table.rowHeight(0)
    assert last <= page.table.viewport().height() + 1


def test_reports_table_keeps_growing_with_a_bigger_page_size(qapp) -> None:
    from askari_vms.ui.pages.reports import ReportsPage
    from askari_vms.visits import VisitRecord

    visits = [
        VisitRecord(f"VIS-{i}", "B", datetime(2026, 9, 2, 12, 0) - timedelta(minutes=i),
                    "operator01", visitor_name=f"Visitor {i}", vehicle_number=f"AAA-{i:03d}",
                    vehicle_category="Car", destination="House 1")
        for i in range(60)
    ]
    page = ReportsPage(AuditLog(), visits, [])
    page.resize(1400, 900)
    qapp.processEvents()

    short = page.table.height()
    page.pager.size_box.setCurrentText("50")
    qapp.processEvents()
    assert page.table.rowCount() == 50
    assert page.table.height() > short, "the table should grow with the page size"
    _assert_no_dead_strip(page.table)


def test_report_columns_share_the_width(qapp) -> None:
    """Stretching one column made Name enormous and pushed the rest off screen."""
    page = _reports_page()
    page.resize(1500, 900)
    qapp.processEvents()
    page._columns.apply()

    widths = [page.table.columnWidth(c) for c in range(page.table.columnCount())]
    assert all(width > 0 for width in widths)
    assert sum(widths) <= page.table.viewport().width(), "columns must not overflow"
    assert max(widths) < page.table.viewport().width() * 0.4, "no column should dominate"


def test_breakdown_tiles_share_the_full_width(qapp) -> None:
    """They used to bunch up on the left with all the space wasted to the right."""
    page = _reports_page()
    page.resize(1400, 900)
    qapp.processEvents()

    tiles = [page._category_row.itemAt(i).widget() for i in range(page._category_row.count())]
    tiles = [tile for tile in tiles if tile is not None]
    assert len(tiles) >= 2, "the fixture should produce several categories"

    card_width = page._category_card.width()
    used = sum(tile.width() for tile in tiles)
    assert used > card_width * 0.8, (
        f"tiles use only {used}px of {card_width}px — they are bunched to one side"
    )
    # Equal stretch, so no tile is wildly larger than another.
    assert max(t.width() for t in tiles) - min(t.width() for t in tiles) <= 2
    assert all(tile.height() >= 60 for tile in tiles)


def test_a_single_group_tile_fills_the_row(qapp) -> None:
    page = _reports_page()
    page.resize(1400, 900)
    page.category_filter.setCurrentText("Truck")
    page.refresh()
    qapp.processEvents()

    tiles = [page._category_row.itemAt(i).widget() for i in range(page._category_row.count())]
    tiles = [tile for tile in tiles if tile is not None]
    assert len(tiles) == 1
    assert tiles[0].width() > page._category_card.width() * 0.8


def _dashboard(visits=None, tags=None, events=None, **kwargs):
    from askari_vms.ui.pages.dashboard import DashboardPage
    return DashboardPage(visits or _demo_visits(), tags or [], events or [], **kwargs)


def test_dashboard_shows_who_is_inside_now(qapp) -> None:
    page = _dashboard()
    inside = [v for v in _demo_visits() if v.is_inside]
    assert page._cards["inside"].value.text() == str(len(inside))
    assert page.table.rowCount() == len(inside), "the table lists exactly who is inside"
    assert "inside now" in page.summary.text()


def test_a_closed_visit_leaves_the_dashboard(qapp) -> None:
    from askari_vms.visits import check_out

    visits = _demo_visits()
    page = _dashboard(visits)
    before = page.table.rowCount()

    open_visit = next(v for v in visits if v.is_inside)
    closed = [check_out(v, "operator02") if v.visit_id == open_visit.visit_id else v for v in visits]
    page.set_data(closed)
    assert page.table.rowCount() == before - 1
    assert page._cards["inside"].value.text() == str(before - 1)


def test_dashboard_filters_narrow_the_live_table(qapp) -> None:
    page = _dashboard()
    page.category_filter.setCurrentText("Motorcycle")
    assert page.table.rowCount() == 1

    page.category_filter.setCurrentIndex(0)
    page.search.setText("ABC-123")
    assert page.table.rowCount() == 1
    page.search.setText("nothing-here")
    assert page.table.rowCount() == 0
    assert "0 shown" in page.summary.text()


def test_dashboard_numbers_the_rows_across_pages(qapp) -> None:
    from datetime import datetime as dt
    from askari_vms.visits import VisitRecord

    visits = [
        VisitRecord(f"VIS-{i:03d}", "B", dt(2026, 9, 4, 9, 0) - timedelta(minutes=i),
                    "operator01", visitor_name=f"Visitor {i}", vehicle_number=f"AAA-{i:03d}",
                    vehicle_category="Car", destination="House 1")
        for i in range(25)
    ]
    page = _dashboard(visits)
    assert page.table.item(0, 0).text() == "1"
    page.pager.set_page(2)
    assert page.table.item(0, 0).text() == "11", "Sr # continues across pages"


def test_the_entry_button_only_appears_when_it_can_act(qapp) -> None:
    plain = _dashboard()
    assert not plain.entry_button.isVisibleTo(plain)

    opened: list[str] = []
    wired = _dashboard(on_open_entry=lambda: opened.append("entry"))
    assert wired.entry_button.isVisibleTo(wired)
    wired.entry_button.click()
    assert opened == ["entry"]


def test_the_dashboard_refreshes_after_an_entry_is_recorded(qapp, tmp_path) -> None:
    from askari_vms.storage import Store
    from askari_vms.ui.admin_window import AdminWindow

    window = AdminWindow(Store(tmp_path / "dash.sqlite3"))
    try:
        before = window.dashboard_page._cards["inside"].value.text()
        portal = window.open_entry_portal()
        portal.fields["vehicle_number"].setText("NEW-1")
        portal.fields["destination"].setText("House 9")
        portal.choose_shortcut("F1")
        portal.submit()

        after = window.dashboard_page._cards["inside"].value.text()
        assert int(after.replace(",", "")) == int(before.replace(",", "")) + 1, (
            "the dashboard must not go stale after an entry"
        )
    finally:
        window.store.close()


def test_changing_the_workstation_lane_updates_the_portal_buttons(qapp, tmp_path) -> None:
    """Saving Settings must switch the lane at once, not only after a restart."""
    from askari_vms.storage import Store
    from askari_vms.ui.admin_window import AdminWindow
    from askari_vms.ui.pages.settings import SettingsPage

    window = AdminWindow(Store(tmp_path / "lane.sqlite3"))
    try:
        assert window.entry_portal_button.isVisibleTo(window)
        assert not window.exit_portal_button.isVisibleTo(window)

        settings_page = next(
            window.pages.widget(i) for i in range(window.pages.count())
            if isinstance(window.pages.widget(i), SettingsPage)
        )
        settings_page.workstation_role.setCurrentText("Exit")
        assert settings_page.save() is True

        assert not window.entry_portal_button.isVisibleTo(window), "Entry is not this PC's lane now"
        assert window.exit_portal_button.isVisibleTo(window)
        assert not window.dashboard_page.entry_button.isVisibleTo(window.dashboard_page)

        settings_page.workstation_role.setCurrentText("Admin + Entry")
        assert settings_page.save() is True
        assert window.entry_portal_button.isVisibleTo(window)
        assert not window.exit_portal_button.isVisibleTo(window)
    finally:
        window.store.close()


def test_the_saved_lane_is_honoured_on_the_next_start(qapp, tmp_path) -> None:
    from dataclasses import replace
    from askari_vms.settings import default_settings
    from askari_vms.storage import Store
    from askari_vms.ui.admin_window import AdminWindow

    path = tmp_path / "lane2.sqlite3"
    store = Store(path)
    store.settings.save(replace(default_settings(), workstation_role="Exit"))
    store.close()

    window = AdminWindow(Store(path))
    try:
        assert window.settings.workstation_role == "Exit"
        assert window.exit_portal_button.isVisibleTo(window)
        assert not window.entry_portal_button.isVisibleTo(window)
    finally:
        window.store.close()


def test_sign_in_routes_on_the_current_lane_not_the_startup_one(qapp, tmp_path) -> None:
    """Settings can change while the app runs; the next sign-in must follow it."""
    from dataclasses import replace
    from askari_vms.auth import authenticate, start_session
    from askari_vms.routing import Portal, portal_for
    from askari_vms.settings import default_settings
    from askari_vms.storage import Store

    store = Store(tmp_path / "lane3.sqlite3")
    try:
        store.seed_if_empty()

        def current():
            return store.settings.load() or default_settings()

        operator, _ = authenticate(store.users.list(), "operator01", "change-me-123")
        assert portal_for(operator.role, current().workstation_role) is Portal.ENTRY

        store.settings.save(replace(default_settings(), workstation_role="Exit"))
        assert portal_for(operator.role, current().workstation_role) is Portal.EXIT

        store.settings.save(replace(default_settings(), workstation_role="Admin + Entry"))
        assert portal_for(operator.role, current().workstation_role) is Portal.ENTRY, (
            "switching back to Entry must take effect without a restart"
        )
    finally:
        store.close()


def test_the_login_screen_follows_the_lane(qapp, tmp_path) -> None:
    from askari_vms.storage import Store
    from askari_vms.ui.pages.login import LoginWindow

    store = Store(tmp_path / "loginlane.sqlite3")
    try:
        login = LoginWindow(store, workstation="Admin + Entry")
        assert "Admin + Entry" in login.subtitle.text()
        login.set_workstation("Exit")
        assert "Exit" in login.subtitle.text()
    finally:
        store.close()


def test_the_window_icon_is_the_emblem(qapp) -> None:
    """The title bar, taskbar and Alt-Tab all read this icon."""
    from askari_vms.ui.brand import ICON_SIZES, app_icon, logo_path

    assert logo_path() is not None, "no logo installed in ui/assets"
    icon = app_icon()
    assert not icon.isNull()

    available = {size.width() for size in icon.availableSizes()}
    assert set(ICON_SIZES) <= available, f"missing icon sizes: {set(ICON_SIZES) - available}"

    # Masked to a circle like the sidebar emblem, so the corners stay transparent.
    image = icon.pixmap(64, 64).toImage()
    assert image.pixelColor(1, 1).alpha() == 0
    assert image.pixelColor(32, 32).alpha() == 255
