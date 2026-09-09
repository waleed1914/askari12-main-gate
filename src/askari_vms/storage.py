"""Local SQLite store.

Everything runs on the Entry PC with no server, so a single file database is the right
shape. Repositories speak the domain dataclasses, so pages never see SQL and the domain
stays independent of storage.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import asdict, replace
from datetime import date, datetime
from pathlib import Path

from askari_vms.audit import AuditEvent, AuditSeverity
from askari_vms.categories import VehicleCategory
from askari_vms.etag_events import ETagEvent, ETagEventKind
from askari_vms.etags import ETagRecord
from askari_vms.settings import (
    AppSettings,
    BackupSettings,
    CameraSettings,
    ControllerSettings,
    PeripheralSettings,
    StorageSettings,
)
from askari_vms.users import UserAccount
from askari_vms.visits import VisitRecord

SCHEMA_VERSION = 4
MEMORY = ":memory:"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS etags (
    rfid TEXT PRIMARY KEY,
    user_id TEXT NOT NULL, resident_name TEXT NOT NULL, care_of TEXT NOT NULL,
    street_no TEXT NOT NULL, house_no TEXT NOT NULL, mobile_no TEXT NOT NULL,
    cnic TEXT NOT NULL, gender TEXT NOT NULL, vehicle_type TEXT NOT NULL,
    vehicle_number TEXT NOT NULL, make TEXT NOT NULL, position TEXT NOT NULL,
    station_unit TEXT NOT NULL, department TEXT NOT NULL, challan_number TEXT NOT NULL,
    status TEXT NOT NULL, issue_date TEXT NOT NULL, expiry_date TEXT NOT NULL,
    comments TEXT NOT NULL, allowed_controllers TEXT NOT NULL,
    member_id TEXT NOT NULL, address TEXT NOT NULL, lesco_ref_no TEXT NOT NULL,
    model TEXT NOT NULL, vehicle_image TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_etags_vehicle ON etags(vehicle_number);

CREATE TABLE IF NOT EXISTS users (
    employee_id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL, username TEXT NOT NULL UNIQUE, role TEXT NOT NULL,
    status TEXT NOT NULL, contact TEXT NOT NULL, cnic TEXT NOT NULL, email TEXT NOT NULL,
    department TEXT NOT NULL, designation TEXT NOT NULL, address TEXT NOT NULL,
    cnic_front TEXT NOT NULL, cnic_back TEXT NOT NULL, password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS visits (
    visit_id TEXT PRIMARY KEY,
    barcode TEXT NOT NULL, entry_time TEXT NOT NULL, entry_operator TEXT NOT NULL,
    entry_door TEXT NOT NULL, visitor_name TEXT NOT NULL, cnic TEXT NOT NULL,
    mobile TEXT NOT NULL, vehicle_number TEXT NOT NULL, vehicle_category TEXT NOT NULL,
    destination TEXT NOT NULL, exit_time TEXT, exit_operator TEXT NOT NULL,
    exit_door TEXT NOT NULL, driver_match TEXT NOT NULL, receipt_lost INTEGER NOT NULL,
    father_name TEXT NOT NULL DEFAULT '', date_of_birth TEXT NOT NULL DEFAULT '',
    cnic_issue_date TEXT NOT NULL DEFAULT '', cnic_expiry_date TEXT NOT NULL DEFAULT '',
    cnic_image TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_visits_entry ON visits(entry_time DESC);
CREATE INDEX IF NOT EXISTS idx_visits_open ON visits(exit_time);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL, operator TEXT NOT NULL, workstation TEXT NOT NULL,
    action TEXT NOT NULL, target TEXT NOT NULL, summary TEXT NOT NULL,
    details TEXT NOT NULL, severity TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_events(timestamp DESC);

CREATE TABLE IF NOT EXISTS etag_events (
    event_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL, controller_name TEXT NOT NULL, door TEXT NOT NULL,
    direction TEXT NOT NULL, rfid TEXT NOT NULL, kind TEXT NOT NULL,
    resident_name TEXT NOT NULL, vehicle_number TEXT NOT NULL, note TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_etag_events_time ON etag_events(timestamp DESC);

CREATE TABLE IF NOT EXISTS categories (
    name TEXT PRIMARY KEY,
    shortcut TEXT NOT NULL UNIQUE, description TEXT NOT NULL,
    price REAL NOT NULL, lost_receipt_price REAL NOT NULL, active INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


class Database:
    """Owns the connection and the schema. Audit rows are never deleted."""

    def __init__(self, path: str | Path = MEMORY) -> None:
        self.path = str(path)
        if self.path != MEMORY:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        # Survive an unclean shutdown, which a gate PC will eventually have.
        if self.path != MEMORY:
            self.connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    # Columns added after a database may already exist in the field. CREATE TABLE
    # IF NOT EXISTS will not add them, so they are applied by hand.
    _ADDED_COLUMNS = {
        "visits": (
            ("father_name", "TEXT NOT NULL DEFAULT \'\'"),
            ("date_of_birth", "TEXT NOT NULL DEFAULT \'\'"),
            ("cnic_issue_date", "TEXT NOT NULL DEFAULT \'\'"),
            ("cnic_expiry_date", "TEXT NOT NULL DEFAULT \'\'"),
            ("cnic_image", "TEXT NOT NULL DEFAULT \'\'"),
            ("driver_image", "TEXT NOT NULL DEFAULT \'\'"),
        ),
    }

    def _create_schema(self) -> None:
        with self.connection:
            self.connection.executescript(_SCHEMA)
            self._apply_added_columns()
            row = self.connection.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                self.connection.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            else:
                old_version = int(row["version"])
                if old_version < 3 and self.count("categories") == 0:
                    self._seed_category_migration()
                self.connection.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))

    def _seed_category_migration(self) -> None:
        """Give pre-category databases initial shortcuts exactly once."""
        from askari_vms.categories import default_categories

        self.connection.executemany(
            """INSERT INTO categories
               (name, shortcut, description, price, lost_receipt_price, active)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (item.name, item.shortcut, item.description, item.price,
                 item.lost_receipt_price, int(item.active))
                for item in default_categories()
            ],
        )

    def _apply_added_columns(self) -> None:
        """Additive migration: bring an older database up to the current columns."""
        for table, columns in self._ADDED_COLUMNS.items():
            existing = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns:
                if name not in existing:
                    self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    @property
    def version(self) -> int:
        return self.connection.execute("SELECT version FROM schema_version").fetchone()["version"]

    def count(self, table: str) -> int:
        return self.connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]

    def is_empty(self) -> bool:
        return all(self.count(table) == 0 for table in ("etags", "users", "visits"))

    def close(self) -> None:
        self.connection.close()


class _Repository:
    table = ""

    def __init__(self, database: Database) -> None:
        self.db = database

    def _write(self, sql: str, parameters: Iterable) -> None:
        with self.db.connection:
            self.db.connection.execute(sql, tuple(parameters))

    def _write_many(self, sql: str, rows: Iterable[Sequence]) -> None:
        with self.db.connection:
            self.db.connection.executemany(sql, [tuple(row) for row in rows])

    @staticmethod
    def _upsert_sql(table: str, columns: Sequence[str], key: str) -> str:
        """Update on the primary key, but still raise on any other unique constraint.

        `INSERT OR REPLACE` would instead delete the conflicting row, which for e-tags
        means silently discarding another vehicle's tag.
        """
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{c}=excluded.{c}" for c in columns if c != key)
        return (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT({key}) DO UPDATE SET {updates}"
        )

    def count(self) -> int:
        return self.db.count(self.table)


class ETagRepository(_Repository):
    table = "etags"
    _COLUMNS = (
        "rfid", "user_id", "resident_name", "care_of", "street_no", "house_no", "mobile_no",
        "cnic", "gender", "vehicle_type", "vehicle_number", "make", "position", "station_unit",
        "department", "challan_number", "status", "issue_date", "expiry_date", "comments",
        "allowed_controllers", "member_id", "address", "lesco_ref_no", "model", "vehicle_image",
    )

    @staticmethod
    def _to_row(record: ETagRecord) -> tuple:
        data = asdict(record)
        data["issue_date"] = record.issue_date.isoformat()
        data["expiry_date"] = record.expiry_date.isoformat()
        data["allowed_controllers"] = ",".join(record.allowed_controllers)
        return tuple(data[column] for column in ETagRepository._COLUMNS)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ETagRecord:
        data = dict(row)
        data["issue_date"] = date.fromisoformat(data["issue_date"])
        data["expiry_date"] = date.fromisoformat(data["expiry_date"])
        controllers = data["allowed_controllers"]
        data["allowed_controllers"] = tuple(c for c in controllers.split(",") if c)
        return ETagRecord(**data)

    def list(self) -> list[ETagRecord]:
        rows = self.db.connection.execute("SELECT * FROM etags ORDER BY user_id, rfid").fetchall()
        return [self._from_row(row) for row in rows]

    def save(self, record: ETagRecord) -> None:
        self._write(self._upsert_sql("etags", self._COLUMNS, "rfid"), self._to_row(record))

    def save_all(self, records: Iterable[ETagRecord]) -> None:
        self._write_many(
            self._upsert_sql("etags", self._COLUMNS, "rfid"),
            [self._to_row(record) for record in records],
        )

    def delete(self, rfid: str) -> None:
        self._write("DELETE FROM etags WHERE rfid = ?", (rfid,))


class UserRepository(_Repository):
    table = "users"
    _COLUMNS = (
        "employee_id", "full_name", "username", "role", "status", "contact", "cnic", "email",
        "department", "designation", "address", "cnic_front", "cnic_back", "password_hash",
    )

    def list(self) -> list[UserAccount]:
        rows = self.db.connection.execute("SELECT * FROM users ORDER BY employee_id").fetchall()
        return [UserAccount(**dict(row)) for row in rows]

    def find(self, username: str) -> UserAccount | None:
        row = self.db.connection.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip().lower(),)
        ).fetchone()
        return UserAccount(**dict(row)) if row else None

    def save(self, account: UserAccount) -> None:
        data = asdict(account)
        self._write(
            self._upsert_sql("users", self._COLUMNS, "employee_id"),
            tuple(data[column] for column in self._COLUMNS),
        )

    def save_all(self, accounts: Iterable[UserAccount]) -> None:
        for account in accounts:
            self.save(account)

    def delete(self, employee_id: str) -> None:
        self._write("DELETE FROM users WHERE employee_id = ?", (employee_id,))


class VisitRepository(_Repository):
    table = "visits"
    _COLUMNS = (
        "visit_id", "barcode", "entry_time", "entry_operator", "entry_door", "visitor_name",
        "cnic", "mobile", "vehicle_number", "vehicle_category", "destination", "exit_time",
        "exit_operator", "exit_door", "driver_match", "receipt_lost",
        "father_name", "date_of_birth", "cnic_issue_date", "cnic_expiry_date", "cnic_image", "driver_image",
    )

    @staticmethod
    def _to_row(visit: VisitRecord) -> tuple:
        data = asdict(visit)
        data["entry_time"] = visit.entry_time.isoformat()
        data["exit_time"] = _iso(visit.exit_time)
        data["receipt_lost"] = int(visit.receipt_lost)
        return tuple(data[column] for column in VisitRepository._COLUMNS)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> VisitRecord:
        data = dict(row)
        data["entry_time"] = datetime.fromisoformat(data["entry_time"])
        data["exit_time"] = datetime.fromisoformat(data["exit_time"]) if data["exit_time"] else None
        data["receipt_lost"] = bool(data["receipt_lost"])
        return VisitRecord(**data)

    def list(self) -> list[VisitRecord]:
        rows = self.db.connection.execute("SELECT * FROM visits ORDER BY entry_time DESC").fetchall()
        return [self._from_row(row) for row in rows]

    def open_visits(self) -> list[VisitRecord]:
        rows = self.db.connection.execute(
            "SELECT * FROM visits WHERE exit_time IS NULL ORDER BY entry_time DESC"
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def save(self, visit: VisitRecord) -> None:
        self._write(self._upsert_sql("visits", self._COLUMNS, "visit_id"), self._to_row(visit))

    def save_all(self, visits: Iterable[VisitRecord]) -> None:
        self._write_many(
            self._upsert_sql("visits", self._COLUMNS, "visit_id"),
            [self._to_row(visit) for visit in visits],
        )


class AuditRepository(_Repository):
    table = "audit_events"

    def list(self) -> list[AuditEvent]:
        rows = self.db.connection.execute(
            "SELECT * FROM audit_events ORDER BY timestamp DESC, event_id DESC"
        ).fetchall()
        return [
            AuditEvent(
                event_id=row["event_id"], timestamp=datetime.fromisoformat(row["timestamp"]),
                operator=row["operator"], workstation=row["workstation"], action=row["action"],
                target=row["target"], summary=row["summary"], details=row["details"],
                severity=AuditSeverity(row["severity"]),
            )
            for row in rows
        ]

    def append(self, event: AuditEvent) -> None:
        """Audit rows are insert-only: there is no update or delete on this table."""
        self._write(
            "INSERT OR IGNORE INTO audit_events (event_id, timestamp, operator, workstation,"
            " action, target, summary, details, severity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event.event_id, event.timestamp.isoformat(), event.operator, event.workstation,
             event.action, event.target, event.summary, event.details, event.severity.value),
        )

    def append_all(self, events: Iterable[AuditEvent]) -> None:
        for event in events:
            self.append(event)


class ETagEventRepository(_Repository):
    table = "etag_events"

    def list(self) -> list[ETagEvent]:
        rows = self.db.connection.execute(
            "SELECT * FROM etag_events ORDER BY timestamp DESC, event_id DESC"
        ).fetchall()
        return [
            ETagEvent(
                event_id=row["event_id"], timestamp=datetime.fromisoformat(row["timestamp"]),
                controller_name=row["controller_name"], door=row["door"],
                direction=row["direction"], rfid=row["rfid"], kind=ETagEventKind(row["kind"]),
                resident_name=row["resident_name"], vehicle_number=row["vehicle_number"],
                note=row["note"],
            )
            for row in rows
        ]

    def append(self, event: ETagEvent) -> None:
        self._write(
            "INSERT OR REPLACE INTO etag_events (event_id, timestamp, controller_name, door,"
            " direction, rfid, kind, resident_name, vehicle_number, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event.event_id, event.timestamp.isoformat(), event.controller_name, event.door,
             event.direction, event.rfid, event.kind.value, event.resident_name,
             event.vehicle_number, event.note),
        )

    def append_all(self, events: Iterable[ETagEvent]) -> None:
        for event in events:
            self.append(event)


class CategoryRepository(_Repository):
    table = "categories"
    _COLUMNS = ("name", "shortcut", "description", "price", "lost_receipt_price", "active")

    def list(self) -> list[VehicleCategory]:
        rows = self.db.connection.execute("SELECT * FROM categories ORDER BY shortcut").fetchall()
        return [
            VehicleCategory(
                name=row["name"], shortcut=row["shortcut"], description=row["description"],
                price=row["price"], lost_receipt_price=row["lost_receipt_price"],
                active=bool(row["active"]),
            )
            for row in rows
        ]

    def active(self) -> list[VehicleCategory]:
        """Only these appear in the Entry portal."""
        return [category for category in self.list() if category.active]

    def save(self, category: VehicleCategory) -> None:
        data = asdict(category)
        data["active"] = int(category.active)
        self._write(
            self._upsert_sql("categories", self._COLUMNS, "name"),
            tuple(data[column] for column in self._COLUMNS),
        )

    def save_all(self, categories: Iterable[VehicleCategory]) -> None:
        for category in categories:
            self.save(category)

    def delete(self, name: str) -> None:
        self._write("DELETE FROM categories WHERE name = ?", (name,))


class SettingsRepository(_Repository):
    table = "app_settings"
    _KEY = "app"

    def load(self) -> AppSettings | None:
        row = self.db.connection.execute(
            "SELECT value FROM app_settings WHERE key = ?", (self._KEY,)
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row["value"])
        return AppSettings(
            controllers=tuple(ControllerSettings(**c) for c in data["controllers"]),
            cameras=tuple(CameraSettings(**c) for c in data["cameras"]),
            peripherals=PeripheralSettings(**data["peripherals"]),
            storage=StorageSettings(**data["storage"]),
            backup=BackupSettings(**data["backup"]),
            workstation_role=data["workstation_role"],
        )

    def save(self, settings: AppSettings) -> None:
        # Controller passwords are deliberately absent from these records; only the
        # has_password flag is stored. Secrets belong in the OS credential store.
        self._write(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (self._KEY, json.dumps(asdict(settings))),
        )


class Store:
    """One handle onto every repository."""

    def __init__(self, path: str | Path = MEMORY) -> None:
        self.database = Database(path)
        self.etags = ETagRepository(self.database)
        self.users = UserRepository(self.database)
        self.visits = VisitRepository(self.database)
        self.audit = AuditRepository(self.database)
        self.etag_events = ETagEventRepository(self.database)
        self.categories = CategoryRepository(self.database)
        self.settings = SettingsRepository(self.database)

    def close(self) -> None:
        self.database.close()

    def seed_accounts_if_empty(self) -> bool:
        """Provision initial logins before displaying the sign-in screen.

        Never reset existing accounts or add demonstration gate traffic. Settings
        and audit rows may already exist from hardware setup before the first login.
        """
        if self.users.count():
            return False
        from askari_vms.audit import AuditLog
        from askari_vms.demo_data import user_accounts

        accounts = user_accounts()
        columns = self.users._COLUMNS
        self.users._write_many(
            self.users._upsert_sql("users", columns, "employee_id"),
            [tuple(asdict(account)[column] for column in columns) for account in accounts],
        )
        AuditLog(self.audit.list(), repository=self.audit).record(
            action="Record created", target="Initial user accounts",
            summary="Initial sign-in accounts provisioned",
            details="The user table was empty. Initial accounts were created; change their initial passwords through Users before live use.",
            severity=AuditSeverity.WARNING, operator="SYSTEM",
        )
        return True

    def seed_demo_data(self) -> None:
        """Fill an empty database so a fresh install is explorable."""
        from askari_vms.categories import default_categories
        from askari_vms.demo_data import etag_event_records, etag_records, user_accounts, visit_records

        records = etag_records()
        # The unique index on vehicle_number mirrors the rule that one vehicle holds one
        # tag, so drop any generated collision rather than fail the whole seed.
        seen: set[str] = set()
        unique = []
        for record in records:
            if record.vehicle_number in seen:
                continue
            seen.add(record.vehicle_number)
            unique.append(record)
        self.etags.save_all(unique)
        self.users.save_all(user_accounts())
        self.visits.save_all(visit_records())
        self.etag_events.append_all(etag_event_records(unique))
        self.categories.save_all(default_categories())

    def seed_if_empty(self) -> bool:
        if not self.database.is_empty():
            return False
        self.seed_demo_data()
        return True


def default_database_path(settings: AppSettings) -> Path:
    return Path(settings.storage.data_directory) / "askari_vms.sqlite3"
