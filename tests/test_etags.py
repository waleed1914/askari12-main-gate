from datetime import date, timedelta

from askari_vms.etags import ETagRecord, ETagState, expiry_state, renew_for_one_year, validate_etag


def make_record(**changes: object) -> ETagRecord:
    today = date.today()
    values = {
        "user_id": "A12-1", "resident_name": "Resident", "care_of": "", "street_no": "",
        "house_no": "", "mobile_no": "", "cnic": "", "gender": "Male", "vehicle_type": "Car",
        "vehicle_number": "abc-1", "make": "", "position": "", "station_unit": "",
        "department": "", "challan_number": "", "rfid": "123", "status": "Active",
        "issue_date": today, "expiry_date": today + timedelta(days=30),
        "allowed_controllers": ("entry", "exit"),
    }
    values.update(changes)
    return ETagRecord(**values)


def test_expiry_states_use_fixed_ten_day_warning() -> None:
    today = date.today()
    assert expiry_state(make_record(expiry_date=today + timedelta(days=11)), today) is ETagState.ACTIVE
    assert expiry_state(make_record(expiry_date=today + timedelta(days=10)), today) is ETagState.EXPIRING
    assert expiry_state(make_record(expiry_date=today - timedelta(days=1)), today) is ETagState.EXPIRED
    assert expiry_state(make_record(status="Blocked"), today) is ETagState.BLOCKED


def test_etag_validation_and_normalization() -> None:
    record = make_record(user_id=" ", vehicle_number=" abc-1 ", allowed_controllers=())
    errors = validate_etag(record)
    assert "user_id" in errors
    assert "allowed_controllers" in errors
    assert record.normalized().vehicle_number == "ABC-1"


def test_renewal_extends_from_current_expiry_and_reactivates() -> None:
    today = date(2026, 9, 2)
    renewed = renew_for_one_year(make_record(status="Expired", expiry_date=date(2026, 8, 1)), today)
    assert renewed.expiry_date == date(2027, 9, 2)
    assert renewed.status == "Active"

    future = renew_for_one_year(make_record(expiry_date=date(2027, 1, 5)), today)
    assert future.expiry_date == date(2028, 1, 5)
