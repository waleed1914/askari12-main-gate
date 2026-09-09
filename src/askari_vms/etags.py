from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum


class ETagState(StrEnum):
    ACTIVE = "Active"
    EXPIRING = "Expiring soon"
    EXPIRED = "Expired"
    BLOCKED = "Blocked"


@dataclass(frozen=True, slots=True)
class ETagRecord:
    # A member and their e-tag are one record: registering a tag registers the member.
    user_id: str
    resident_name: str
    care_of: str
    street_no: str
    house_no: str
    mobile_no: str
    cnic: str
    gender: str
    vehicle_type: str
    vehicle_number: str
    make: str
    position: str
    station_unit: str
    department: str
    challan_number: str
    rfid: str
    status: str
    issue_date: date
    expiry_date: date
    comments: str = ""
    allowed_controllers: tuple[str, ...] = ()
    member_id: str = ""
    address: str = ""
    lesco_ref_no: str = ""
    model: str = ""
    vehicle_image: str = ""

    def normalized(self) -> "ETagRecord":
        return replace(
            self,
            user_id=self.user_id.strip(),
            resident_name=self.resident_name.strip(),
            vehicle_number=self.vehicle_number.strip().upper(),
            rfid=self.rfid.strip(),
            cnic=self.cnic.strip(),
            member_id=self.member_id.strip().upper(),
            address=" ".join(self.address.split()),
            lesco_ref_no=self.lesco_ref_no.strip(),
            model=" ".join(self.model.split()),
        )


def expiry_state(record: ETagRecord, today: date | None = None) -> ETagState:
    today = today or date.today()
    if record.status.casefold() == "blocked":
        return ETagState.BLOCKED
    if record.expiry_date < today or record.status.casefold() == "expired":
        return ETagState.EXPIRED
    if (record.expiry_date - today).days <= 10:
        return ETagState.EXPIRING
    return ETagState.ACTIVE


def validate_etag(record: ETagRecord) -> dict[str, str]:
    errors: dict[str, str] = {}
    required = {
        "user_id": ("User ID", record.user_id),
        "resident_name": ("Resident name", record.resident_name),
        "vehicle_type": ("Vehicle type", record.vehicle_type),
        "vehicle_number": ("Vehicle number", record.vehicle_number),
        "rfid": ("E-Tag RFID", record.rfid),
    }
    for key, (label, value) in required.items():
        if not value.strip():
            errors[key] = f"{label} is required."
    if record.expiry_date < record.issue_date:
        errors["expiry_date"] = "Expiry date cannot be before the issue date."
    if not record.allowed_controllers:
        errors["allowed_controllers"] = "Select at least one controller."
    return errors


def renew_for_one_year(record: ETagRecord, today: date | None = None) -> ETagRecord:
    """Extend from the later of today or the current expiry by one calendar year."""
    today = today or date.today()
    base = max(today, record.expiry_date)
    try:
        new_expiry = base.replace(year=base.year + 1)
    except ValueError:  # 29 February -> 28 February in a non-leap year
        new_expiry = base.replace(year=base.year + 1, day=28)
    return replace(record, status="Active", expiry_date=new_expiry)


def tags_for_resident(records: Sequence[ETagRecord], user_id: str) -> tuple[ETagRecord, ...]:
    """Every tag held by one resident. A resident may hold several, on different vehicles."""
    key = user_id.strip().casefold()
    return tuple(record for record in records if record.user_id.strip().casefold() == key)
