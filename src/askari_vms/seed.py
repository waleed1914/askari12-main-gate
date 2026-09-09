"""Add demonstration records to an existing database, on purpose.

Automatic seeding only runs on a brand-new database, and deliberately so: on a real
gate an empty e-tag-event table is the normal state on day one, and quietly filling it
would invent traffic in a security log. This tool exists so demo data is only ever
added by someone asking for it.

    python -m askari_vms.seed                 # show what is there, add what is missing
    python -m askari_vms.seed --dry-run       # report only
    python -m askari_vms.seed --database PATH
"""

from __future__ import annotations

import argparse
from pathlib import Path

from askari_vms.settings import default_settings
from askari_vms.storage import Store, default_database_path

TABLES = ("etags", "users", "visits", "etag_events", "audit_events")


def counts(store: Store) -> dict[str, int]:
    return {table: store.database.count(table) for table in TABLES}


def top_up(store: Store, dry_run: bool = False) -> dict[str, int]:
    """Fill only the tables that are empty. Never touches one that holds records."""
    from askari_vms.demo_data import etag_event_records, etag_records, user_accounts, visit_records

    added: dict[str, int] = {}
    before = counts(store)

    if not before["etags"]:
        records = etag_records()
        seen: set[str] = set()
        unique = [r for r in records if not (r.vehicle_number in seen or seen.add(r.vehicle_number))]
        if not dry_run:
            store.etags.save_all(unique)
        added["etags"] = len(unique)

    if not before["users"]:
        accounts = user_accounts()
        if not dry_run:
            store.users.save_all(accounts)
        added["users"] = len(accounts)

    if not before["visits"]:
        visits = visit_records()
        if not dry_run:
            store.visits.save_all(visits)
        added["visits"] = len(visits)

    # audit_events is deliberately absent: fabricating rows in a security record is
    # worse than fabricating traffic, and the table is insert-only by design.
    if not before["etag_events"]:
        # Readings must reference tags that actually exist, so read the registry back.
        registry = store.etags.list()
        events = etag_event_records(registry) if registry else []
        if not dry_run:
            store.etag_events.append_all(events)
        added["etag_events"] = len(events)

    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add demonstration records to an Askari VMS database.")
    parser.add_argument("--database", type=Path, default=None, help="database file (default: the configured data folder)")
    parser.add_argument("--dry-run", action="store_true", help="report what would be added and change nothing")
    args = parser.parse_args(argv)

    path = args.database or default_database_path(default_settings())
    print(f"database: {path}")
    store = Store(path)
    try:
        stored = store.settings.load()
        if stored is not None and args.database is None:
            configured = default_database_path(stored)
            if configured != path:
                print(f"note: settings point at {configured}")

        before = counts(store)
        print("before:  " + "  ".join(f"{table}={count}" for table, count in before.items()))

        added = top_up(store, dry_run=args.dry_run)
        if not added:
            print("nothing to add — every table already holds records.")
            return 0

        verb = "would add" if args.dry_run else "added"
        for table, count in added.items():
            print(f"  {verb} {count} {table}")
        if not args.dry_run:
            print("after:   " + "  ".join(f"{t}={c}" for t, c in counts(store).items()))
            print("restart the app to see them.")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
