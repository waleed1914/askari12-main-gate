from askari_vms.seed import counts, main, top_up
from askari_vms.storage import Store


def test_an_empty_database_gets_everything(tmp_path) -> None:
    store = Store(tmp_path / "a.sqlite3")
    try:
        added = top_up(store)
        assert added["etags"] > 40
        assert added["users"] >= 6
        assert added["visits"] > 100
        assert added["etag_events"] > 100
        after = counts(store)
        assert all(after[table] > 0 for table in ("etags", "users", "visits", "etag_events"))
        # Audit rows are never fabricated: inventing entries in a security record is
        # worse than inventing traffic, so this tool leaves that table alone.
        assert "audit_events" not in added
        assert after["audit_events"] == 0
    finally:
        store.close()


def test_only_the_empty_tables_are_filled(tmp_path) -> None:
    """The case that prompted this: a database seeded before e-tag events existed."""
    store = Store(tmp_path / "b.sqlite3")
    try:
        store.seed_demo_data()
        store.database.connection.execute("DELETE FROM etag_events")
        store.database.connection.commit()
        before = counts(store)

        added = top_up(store)
        assert set(added) == {"etag_events"}, "only the empty table should be touched"
        after = counts(store)
        assert after["etag_events"] > 100
        for table in ("etags", "users", "visits"):
            assert after[table] == before[table], f"{table} was modified"
    finally:
        store.close()


def test_a_full_database_is_left_alone(tmp_path) -> None:
    store = Store(tmp_path / "c.sqlite3")
    try:
        store.seed_demo_data()
        before = counts(store)
        assert top_up(store) == {}
        assert counts(store) == before
    finally:
        store.close()


def test_dry_run_changes_nothing(tmp_path) -> None:
    store = Store(tmp_path / "d.sqlite3")
    try:
        before = counts(store)
        added = top_up(store, dry_run=True)
        assert added["etags"] > 40, "it should still report what it would add"
        assert counts(store) == before, "a dry run must not write"
    finally:
        store.close()


def test_readings_reference_tags_that_exist(tmp_path) -> None:
    store = Store(tmp_path / "e.sqlite3")
    try:
        top_up(store)
        known = {record.rfid for record in store.etags.list()}
        events = store.etag_events.list()
        matched = [event for event in events if event.rfid in known]
        # A few deliberately unknown tags remain, to exercise the critical path.
        assert len(matched) > len(events) * 0.85
        assert any(event.rfid not in known for event in events)
    finally:
        store.close()


def test_the_command_line_reports_and_succeeds(tmp_path, capsys) -> None:
    path = tmp_path / "f.sqlite3"
    assert main(["--database", str(path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would add" in out
    assert Store(path).database.count("etags") == 0

    assert main(["--database", str(path)]) == 0
    assert "added" in capsys.readouterr().out
    store = Store(path)
    assert store.etags.count() > 40
    store.close()

    assert main(["--database", str(path)]) == 0
    assert "nothing to add" in capsys.readouterr().out
