from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from askari_vms.audit import AuditEvent, AuditSeverity
from askari_vms.etag_events import ETagEvent, ETagEventKind
from askari_vms.etags import ETagRecord
from askari_vms.settings import default_settings, with_controller
from askari_vms.storage import SCHEMA_VERSION, Store, default_database_path
from askari_vms.users import UserAccount, UserRole, verify_password
from askari_vms.visits import DriverMatch, VisitRecord, check_out

TODAY = date(2026, 9, 3)
NOW = datetime(2026, 9, 3, 10, 30, 0)


@pytest.fixture()
def store():
    store = Store()
    yield store
    store.close()


def a_tag(rfid="10000001", plate="ABC-123", **changes) -> ETagRecord:
    values = dict(
        user_id="A12-001", resident_name="Sample Resident", care_of="Father", street_no="12",
        house_no="44-B", mobile_no="03001234567", cnic="35202-1234567-1", gender="Male",
        vehicle_type="Car", vehicle_number=plate, make="Toyota", position="Officer",
        station_unit="Unit 4", department="Maintenance", challan_number="CH-001", rfid=rfid,
        status="Active", issue_date=TODAY - timedelta(days=90), expiry_date=TODAY + timedelta(days=180),
        comments="Note", allowed_controllers=("entry", "exit"), member_id="MEM-0001",
        address="House 123, Phase 3", lesco_ref_no="68508697-289450", model="Corolla",
        vehicle_image=r"C:\scans\car.png",
    )
    values.update(changes)
    return ETagRecord(**values)


def a_visit(visit_id="VIS-000001", **changes) -> VisitRecord:
    values = dict(
        visit_id=visit_id, barcode="8F2A19C4", entry_time=NOW - timedelta(hours=2),
        entry_operator="operator01", visitor_name="Ahmed Khan", cnic="35202-1234567-1",
        mobile="03001234567", vehicle_number="ABC-123", vehicle_category="Car",
        destination="House 44-B",
    )
    values.update(changes)
    return VisitRecord(**values)


def test_a_new_database_has_the_schema_and_is_empty(store) -> None:
    assert store.database.version == SCHEMA_VERSION
    assert store.database.is_empty()
    assert store.etags.list() == []
    assert store.users.list() == []


def test_an_etag_round_trips_with_every_field(store) -> None:
    original = a_tag()
    store.etags.save(original)
    [loaded] = store.etags.list()
    assert loaded == original, "a saved e-tag must come back byte-for-byte"
    assert loaded.allowed_controllers == ("entry", "exit")
    assert isinstance(loaded.issue_date, date) and isinstance(loaded.expiry_date, date)
    assert loaded.member_id == "MEM-0001" and loaded.lesco_ref_no == "68508697-289450"
    assert loaded.model == "Corolla" and loaded.vehicle_image == r"C:\scans\car.png"


def test_saving_the_same_rfid_updates_rather_than_duplicates(store) -> None:
    store.etags.save(a_tag())
    store.etags.save(a_tag(resident_name="Renamed Resident"))
    records = store.etags.list()
    assert len(records) == 1
    assert records[0].resident_name == "Renamed Resident"


def test_one_vehicle_cannot_hold_two_tags(store) -> None:
    import sqlite3

    store.etags.save(a_tag(rfid="10000001", plate="ABC-123"))
    with pytest.raises(sqlite3.IntegrityError):
        store.etags.save(a_tag(rfid="10000002", plate="ABC-123"))


def test_a_single_controller_and_no_comments_survive(store) -> None:
    store.etags.save(a_tag(allowed_controllers=("entry",), comments=""))
    [loaded] = store.etags.list()
    assert loaded.allowed_controllers == ("entry",)
    assert loaded.comments == ""


def test_deleting_an_etag_removes_only_that_one(store) -> None:
    store.etags.save_all([a_tag("10000001", "ABC-123"), a_tag("10000002", "XYZ-887")])
    store.etags.delete("10000001")
    assert [r.rfid for r in store.etags.list()] == ["10000002"]


def test_users_round_trip_and_keep_their_hash(store) -> None:
    from askari_vms.users import hash_password

    account = UserAccount(
        "ADMIN-01", "Administrator", "admin", UserRole.ADMIN, contact="03001234567",
        cnic="35202-1234567-1", email="admin@askari12.local", designation="Gate Administrator",
        address="House 5", password_hash=hash_password("secret123"),
    )
    store.users.save(account)
    [loaded] = store.users.list()
    assert loaded == account
    assert verify_password("secret123", loaded.password_hash)
    assert store.users.find("admin") == account
    assert store.users.find("ADMIN") == account, "lookup should be case-insensitive"
    assert store.users.find("nobody") is None


def test_an_open_visit_keeps_a_null_exit_time(store) -> None:
    store.visits.save(a_visit())
    [loaded] = store.visits.list()
    assert loaded.exit_time is None
    assert loaded.is_inside
    assert store.visits.open_visits() == [loaded]


def test_a_closed_visit_round_trips_its_exit_details(store) -> None:
    closed = check_out(a_visit(), "operator02", DriverMatch.MISMATCHED, exit_time=NOW, receipt_lost=True)
    store.visits.save(closed)
    [loaded] = store.visits.list()
    assert loaded == closed
    assert loaded.exit_time == NOW
    assert loaded.mismatched and loaded.receipt_lost is True
    assert store.visits.open_visits() == []


def test_visits_come_back_newest_first(store) -> None:
    store.visits.save_all([
        a_visit("VIS-1", entry_time=NOW - timedelta(hours=5)),
        a_visit("VIS-2", entry_time=NOW - timedelta(hours=1)),
        a_visit("VIS-3", entry_time=NOW - timedelta(hours=3)),
    ])
    assert [v.visit_id for v in store.visits.list()] == ["VIS-2", "VIS-3", "VIS-1"]


def test_audit_events_are_insert_only_and_keep_severity(store) -> None:
    event = AuditEvent("AUD-000001", NOW, "admin", "Admin / Entry PC", "Gate command",
                       "Visitor Entry", "Opened", "details", AuditSeverity.CRITICAL)
    store.audit.append(event)
    store.audit.append(event)  # replaying the same event must not duplicate it
    [loaded] = store.audit.list()
    assert loaded == event
    assert loaded.severity is AuditSeverity.CRITICAL
    assert store.audit.count() == 1


def test_etag_events_round_trip_their_kind(store) -> None:
    event = ETagEvent("ETL-000001", NOW, "Entry Controller", "E-tag Entry", "In", "77777777",
                      ETagEventKind.UNKNOWN)
    store.etag_events.append(event)
    [loaded] = store.etag_events.list()
    assert loaded == event
    assert loaded.is_critical


def test_settings_round_trip_without_storing_a_password(store) -> None:
    settings = with_controller(
        default_settings(),
        replace(default_settings().controllers[1], ip_address="192.168.0.91", has_password=True),
    )
    store.settings.save(settings)
    loaded = store.settings.load()
    assert loaded == settings
    assert loaded.controllers[1].ip_address == "192.168.0.91"
    assert loaded.cameras[0].model == "ITC413-PW4D-Z3"

    raw = store.database.connection.execute("SELECT value FROM app_settings").fetchone()["value"]
    assert "password" not in raw.replace("has_password", ""), "no secret may reach the database"


def test_missing_settings_returns_none(store) -> None:
    assert store.settings.load() is None


def test_seeding_fills_an_empty_database_once(store) -> None:
    assert store.seed_if_empty() is True
    tags, users, visits = store.etags.count(), store.users.count(), store.visits.count()
    assert tags > 40 and users >= 6 and visits > 100

    assert store.seed_if_empty() is False, "a populated database must not be re-seeded"
    assert (store.etags.count(), store.users.count(), store.visits.count()) == (tags, users, visits)


def test_data_survives_reopening_the_file(tmp_path) -> None:
    path = tmp_path / "data" / "askari_vms.sqlite3"
    first = Store(path)
    first.etags.save(a_tag())
    first.audit.append(AuditEvent("AUD-000001", NOW, "admin", "PC", "Login", "Admin portal",
                                  "Signed in", "details"))
    first.close()

    second = Store(path)
    assert [r.rfid for r in second.etags.list()] == ["10000001"]
    assert [e.event_id for e in second.audit.list()] == ["AUD-000001"]
    assert second.database.version == SCHEMA_VERSION
    second.close()


def test_database_path_follows_the_configured_data_folder() -> None:
    settings = default_settings()
    assert default_database_path(settings).name == "askari_vms.sqlite3"
    assert str(default_database_path(settings)).startswith(settings.storage.data_directory)


def test_an_older_database_gains_the_new_visit_columns(tmp_path) -> None:
    """Their live database already holds visits, so the change must migrate, not recreate."""
    import sqlite3

    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (1);
        CREATE TABLE visits (
            visit_id TEXT PRIMARY KEY, barcode TEXT NOT NULL, entry_time TEXT NOT NULL,
            entry_operator TEXT NOT NULL, entry_door TEXT NOT NULL, visitor_name TEXT NOT NULL,
            cnic TEXT NOT NULL, mobile TEXT NOT NULL, vehicle_number TEXT NOT NULL,
            vehicle_category TEXT NOT NULL, destination TEXT NOT NULL, exit_time TEXT,
            exit_operator TEXT NOT NULL, exit_door TEXT NOT NULL, driver_match TEXT NOT NULL,
            receipt_lost INTEGER NOT NULL
        );
        INSERT INTO visits VALUES ('VIS-000001','B1','2026-09-02T10:00:00','operator01',
            'Visitor Entry','Ahmed Khan','35202-1234567-1','03001234567','ABC-123','Car',
            'House 44-B',NULL,'','','',0);
        """
    )
    old.commit()
    old.close()

    store = Store(path)
    try:
        assert store.database.version == SCHEMA_VERSION
        [visit] = store.visits.list()
        assert visit.visitor_name == "Ahmed Khan", "the existing row must survive"
        assert visit.father_name == "" and visit.cnic_issue_date == ""
        assert [(item.name, item.shortcut) for item in store.categories.active()][:3] == [
            ("Car", "F1"), ("Truck", "F2"), ("Motorcycle", "F3"),
        ], "an older populated database must gain the category shortcuts once"

        # And the new columns are writable.
        store.visits.save(replace(visit, father_name="Khan Sahib", date_of_birth="1990-01-01"))
        [updated] = store.visits.list()
        assert updated.father_name == "Khan Sahib"
        assert updated.date_of_birth == "1990-01-01"
    finally:
        store.close()


def test_the_cnic_fields_round_trip(store) -> None:
    visit = replace(
        a_visit(),
        father_name="Khan Sahib", date_of_birth="1990-01-01",
        cnic_issue_date="2015-06-01", cnic_expiry_date="2025-06-01",
        cnic_image=r"C:\scans\cnic.png",
    )
    store.visits.save(visit)
    [loaded] = store.visits.list()
    assert loaded == visit
