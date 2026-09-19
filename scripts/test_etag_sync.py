"""Send one E-Tag to both physical controllers and verify it.

Run from the repository root with the virtual environment Python. This script uses
the existing Windows Credential Manager entries and never prints passwords.
"""

from __future__ import annotations

import argparse
from datetime import date

from askari_vms.etag_controller import HttpETagController
from askari_vms.etags import ETagRecord


CONTROLLERS = (("entry", "192.168.1.10"), ("exit", "192.168.1.11"))


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rfid", required=True, help="Numeric E-Tag/card number, max 10 digits")
    parser.add_argument("--vehicle", required=True, help="Vehicle number written into Name, max 8 characters")
    parser.add_argument("--expiry", required=True, type=date.fromisoformat, help="Expiry date: YYYY-MM-DD")
    parser.add_argument("--write", action="store_true", help="Required confirmation that permits controller writes")
    return parser.parse_args()


def record_for(args: argparse.Namespace) -> ETagRecord:
    return ETagRecord(
        user_id="SYNC-TEST", resident_name="Controller test", care_of="", street_no="",
        house_no="", mobile_no="", cnic="", gender="", vehicle_type="",
        vehicle_number=args.vehicle.strip().upper(), make="", position="", station_unit="",
        department="", challan_number="", rfid=args.rfid.strip(), status="Active",
        issue_date=date.today(), expiry_date=args.expiry,
        comments="Standalone controller synchronization test",
        allowed_controllers=("entry", "exit"),
    )


def main() -> int:
    args = arguments()
    if not args.rfid.isdigit() or len(args.rfid) > 10:
        raise SystemExit("--rfid must contain at most 10 digits")
    if not args.write:
        raise SystemExit("No cards changed. Add --write to confirm writes to both controllers.")

    record = record_for(args)
    failures = 0
    for key, address in CONTROLLERS:
        controller = HttpETagController(key, address)
        try:
            before = controller.find(record.rfid)
            written = controller.save(record)
            verified = controller.find(record.rfid)
            print(
                f"{key.upper()}: SUCCESS address={address} before={before} "
                f"written_slot={written.slot + 1} verified={verified is not None}"
            )
        except Exception as exc:
            failures += 1
            print(f"{key.upper()}: FAILED address={address} error={exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
