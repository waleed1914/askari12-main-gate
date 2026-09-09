"""Deterministic demonstration records.

Used to fill the UI before hardware and persistence exist, and reused to seed a fresh
database. Seeded from a fixed value so every run produces the same data.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from askari_vms.etag_events import IN, OUT, build_event
from askari_vms.etags import ETagRecord
from askari_vms.users import UserAccount, UserRole, hash_password
from askari_vms.visits import DriverMatch, VisitRecord, check_out

FIRST_NAMES = (
    "Ahmed", "Bilal", "Rizwan", "Usman", "Hamza", "Sana", "Ayesha", "Fatima", "Imran",
    "Kashif", "Nadia", "Owais", "Saad", "Tariq", "Zainab", "Danish", "Farhan", "Hina",
)
LAST_NAMES = (
    "Khan", "Malik", "Rashid", "Hashmi", "Mahmood", "Ahmed", "Iqbal", "Siddiqui",
    "Chaudhry", "Butt", "Qureshi", "Shah", "Javed", "Aslam",
)
PLATE_PREFIXES = ("LEA", "LEB", "LEF", "LEG", "MNA", "AJV", "BGH", "ICT", "LZQ", "TRK")
VEHICLE_TYPES = ("Car", "SUV", "Van", "Truck", "Motorcycle")
MAKES = {
    "Car": (("Toyota", "Corolla"), ("Honda", "City"), ("Suzuki", "Cultus"), ("Kia", "Picanto")),
    "SUV": (("Toyota", "Fortuner"), ("Honda", "BR-V"), ("Kia", "Sportage")),
    "Van": (("Suzuki", "Bolan"), ("Toyota", "Hiace")),
    "Truck": (("Hino", "500"), ("Isuzu", "NPR")),
    "Motorcycle": (("Honda", "CD-70"), ("Yamaha", "YBR")),
}
CATEGORIES = ("Car", "Truck", "Motorcycle", "Delivery Van", "Water Tanker", "Rickshaw")
DESTINATIONS = (
    "House 44-B", "House 12, Block A", "Block C", "Society Office", "Jamia Masjid",
    "Construction Plot 34", "House 210, Phase 3", "House 88, Sector C", "Site office",
)
DEPARTMENTS = ("Maintenance", "Security", "Administration", "Engineering", "Medical")


def _rng(seed: int) -> random.Random:
    return random.Random(seed)


def _name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def _plate(rng: random.Random) -> str:
    return f"{rng.choice(PLATE_PREFIXES)}-{rng.randrange(1000, 9999)}"


def _cnic(rng: random.Random) -> str:
    return f"{rng.choice((35202, 35301, 42101, 61101))}-{rng.randrange(1000000, 9999999)}-{rng.randrange(1, 9)}"


def _mobile(rng: random.Random) -> str:
    return f"030{rng.randrange(0, 9)}{rng.randrange(1000000, 9999999)}"


def etag_records(count: int = 60, today: date | None = None, seed: int = 20260903) -> list[ETagRecord]:
    """A registry with a realistic spread of active, near-expiry, expired and blocked tags."""
    rng = _rng(seed)
    today = today or date.today()
    records: list[ETagRecord] = []
    for index in range(1, count + 1):
        vehicle_type = rng.choice(VEHICLE_TYPES)
        make, model = rng.choice(MAKES[vehicle_type])
        name = _name(rng)
        # Spread expiry so the 10-day warning and the expired state both appear.
        bucket = rng.random()
        if bucket < 0.10:
            expiry = today + timedelta(days=rng.randrange(0, 11))     # expiring soon
        elif bucket < 0.20:
            expiry = today - timedelta(days=rng.randrange(1, 120))    # expired
        else:
            expiry = today + timedelta(days=rng.randrange(30, 700))
        status = "Blocked" if rng.random() < 0.06 else "Active"
        records.append(ETagRecord(
            user_id=f"A12-{index:03d}",
            resident_name=name,
            care_of=_name(rng),
            street_no=str(rng.randrange(1, 40)),
            house_no=f"{rng.randrange(1, 400)}-{rng.choice('ABCD')}",
            mobile_no=_mobile(rng),
            cnic=_cnic(rng),
            gender=rng.choice(("Male", "Female")),
            vehicle_type=vehicle_type,
            vehicle_number=_plate(rng),
            make=make,
            position=rng.choice(("", "Supervisor", "Officer", "Clerk")),
            station_unit=rng.choice(("", "Unit 4", "Unit 9", "HQ")),
            department=rng.choice(DEPARTMENTS),
            challan_number=f"CH-{rng.randrange(100000, 999999)}",
            rfid=str(rng.randrange(900000000, 999999999)),
            status=status,
            issue_date=expiry - timedelta(days=rng.choice((365, 730))),
            expiry_date=expiry,
            comments=rng.choice(("", "", "Second vehicle for the same household.", "Renewed at the society office.")),
            allowed_controllers=rng.choice((("entry", "exit"), ("entry", "exit"), ("entry",))),
            member_id=f"MEM-{index:04d}",
            address=f"House {rng.randrange(1, 400)}, Phase {rng.randrange(1, 6)}, Lahore",
            lesco_ref_no=f"{rng.randrange(10000000, 99999999)}-{rng.randrange(100000, 999999)}",
            model=model,
        ))
    # One resident deliberately holds two tags on different vehicles.
    first = records[0]
    records.append(replace_tag(first, rng))
    return records


def replace_tag(record: ETagRecord, rng: random.Random) -> ETagRecord:
    from dataclasses import replace

    vehicle_type = rng.choice(VEHICLE_TYPES)
    make, model = rng.choice(MAKES[vehicle_type])
    return replace(
        record,
        vehicle_type=vehicle_type,
        vehicle_number=_plate(rng),
        make=make,
        model=model,
        rfid=str(rng.randrange(900000000, 999999999)),
        comments="Second vehicle for the same member.",
    )


def visit_records(count: int = 140, now: datetime | None = None, seed: int = 20260903) -> list[VisitRecord]:
    """Visitor transactions: mostly closed, some still inside, a few incomplete."""
    rng = _rng(seed + 1)
    now = (now or datetime.now()).replace(microsecond=0)
    visits: list[VisitRecord] = []
    for index in range(count, 0, -1):
        entered = now - timedelta(minutes=rng.randrange(5, 60 * 24 * 6))
        visit = VisitRecord(
            visit_id=f"VIS-{index:06d}",
            barcode=f"{rng.randrange(0x10000000, 0xFFFFFFFF):08X}",
            entry_time=entered,
            entry_operator=rng.choice(("operator01", "operator02")),
            visitor_name=_name(rng),
            cnic=_cnic(rng),
            mobile=_mobile(rng),
            vehicle_number=_plate(rng),
            vehicle_category=rng.choice(CATEGORIES),
            destination=rng.choice(DESTINATIONS),
        )
        # 1 in 9 was submitted with something missing; the operator is never blocked.
        if rng.random() < 0.11:
            from dataclasses import replace
            gap = rng.choice(("cnic", "destination", "visitor_name", "vehicle_category"))
            visit = replace(visit, **{gap: ""})
        if rng.random() < 0.75:
            match = DriverMatch.MISMATCHED if rng.random() < 0.08 else DriverMatch.MATCHED
            visit = check_out(
                visit,
                operator=rng.choice(("operator02", "operator03")),
                driver_match=match,
                exit_time=entered + timedelta(minutes=rng.randrange(10, 400)),
                receipt_lost=rng.random() < 0.07,
            )
        visits.append(visit)
    visits.sort(key=lambda v: v.entry_time, reverse=True)
    return visits


def user_accounts(seed: int = 20260903) -> list[UserAccount]:
    rng = _rng(seed + 2)
    people = [
        ("ADMIN-01", "Administrator", "admin", UserRole.ADMIN, "Gate Administrator"),
        ("OP-001", "Operator One", "operator01", UserRole.OPERATOR, "Entry Operator"),
        ("OP-002", "Operator Two", "operator02", UserRole.OPERATOR, "Exit Operator"),
        ("OP-003", "Operator Three", "operator03", UserRole.OPERATOR, "Relief Operator"),
        ("SUP-001", "Shift Supervisor", "supervisor", UserRole.ADMIN, "Shift Supervisor"),
        ("REP-001", "Report Viewer", "reporter", UserRole.REPORTER, "Reporting Officer"),
    ]
    accounts = []
    for employee_id, full_name, username, role, designation in people:
        accounts.append(UserAccount(
            employee_id=employee_id, full_name=full_name, username=username, role=role,
            contact=_mobile(rng), cnic=_cnic(rng), email=f"{username}@askari12.local",
            department=rng.choice(DEPARTMENTS), designation=designation,
            address=f"House {rng.randrange(1, 200)}, Askari Greens, Lahore",
            password_hash=hash_password("change-me-123"),
        ))
    return accounts


def etag_event_records(records, count: int = 180, now: datetime | None = None, seed: int = 20260903):
    """Controller readings drawn from the registry, plus a few tags it does not know."""
    rng = _rng(seed + 3)
    now = (now or datetime.now()).replace(microsecond=0)
    pool = [record.rfid for record in records]
    if not pool:
        return []
    events = []
    minutes = sorted(rng.sample(range(2, 60 * 24 * 5), min(count, 60 * 24 * 5 - 2)), reverse=True)
    for index, offset in enumerate(reversed(minutes), start=1):
        rfid = rng.choice(pool) if rng.random() > 0.04 else str(rng.randrange(10000000, 99999999))
        inbound = rng.random() < 0.5
        events.append(build_event(
            event_id=f"ETL-{index:06d}",
            timestamp=now - timedelta(minutes=offset),
            controller_name="Entry Controller" if inbound else "Exit Controller",
            door="E-tag Entry" if inbound else "E-tag Exit",
            direction=IN if inbound else OUT,
            rfid=rfid,
            records=records,
        ))
    events.reverse()
    return events
