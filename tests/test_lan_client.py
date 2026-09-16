from datetime import datetime
import time

from askari_vms.etag_events import ETagEvent, ETagEventKind, OUT
from askari_vms.lan_client import ExitSyncService, LanClient, LanClientError
from askari_vms.storage import Store
from askari_vms.visits import VisitRecord, check_out


class RecoveringClient:
    def __init__(self, visits=()):
        self.online = False
        self.visits = list(visits)
        self.checkouts = []
        self.events = []

    def open_visits(self):
        if not self.online:
            raise LanClientError("offline")
        return self.visits

    def checkout(self, visit):
        if not self.online:
            raise LanClientError("offline")
        self.checkouts.append(visit)

    def download_entry_driver_image(self, visit_id):
        return b"\xff\xd8photo", "image/jpeg"

    def etag_event(self, event):
        if not self.online:
            raise LanClientError("offline")
        self.events.append(event)


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def outbox_count(path):
    store = Store(path)
    try:
        return len(store.outbox.list())
    finally:
        store.close()


def test_offline_transactions_survive_and_flush_after_reconnect(tmp_path):
    path = tmp_path / "exit.sqlite3"
    Store(path).close()
    visit = VisitRecord("VIS-9", "TOKEN", datetime.now(), "entry01", vehicle_number="ABC-9")
    closed = check_out(visit, "operator02")
    event = ETagEvent("ETL-exit-9", datetime.now(), "Exit Controller", "E-tag Exit",
                      OUT, "77076", ETagEventKind.UNKNOWN)
    client = RecoveringClient([visit])
    sync = ExitSyncService(path, client)
    sync.queue_checkout(closed)
    sync.queue_etag_event(event)
    sync.start()
    try:
        assert wait_until(lambda: outbox_count(path) == 2)
        client.online = True
        sync._wake.set()
        assert wait_until(lambda: len(client.checkouts) == 1 and len(client.events) == 1)
        store = Store(path)
        try:
            assert store.outbox.list() == []
        finally:
            store.close()
        visits, status = sync.latest()
        assert status[0] is True
        assert visits and visits[0].visit_id == "VIS-9"
    finally:
        sync.stop()


def test_lan_client_never_uses_an_internet_proxy(monkeypatch):
    captured = []
    class Opener:
        def open(self, request, timeout):
            captured.append(request.full_url)
            raise OSError
    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: Opener())
    client = LanClient(token_provider=lambda address: "secret")
    try:
        client.open_visits()
    except LanClientError:
        pass
    assert captured == ["http://192.168.1.40:8765/api/v1/visits/open"]


def test_sync_caches_entry_photo_for_exit_ui(tmp_path):
    path = tmp_path / "exit.sqlite3"
    Store(path).close()
    visit = VisitRecord("VIS-IMG", "TOKEN", datetime.now(), "entry01",
                        driver_image=r"C:\AskariVMS\data\images\driver_entry\remote.jpg")
    client = RecoveringClient([visit])
    client.online = True
    sync = ExitSyncService(path, client)
    [cached_visit] = sync._cache_entry_images([visit])
    cached = __import__("pathlib").Path(cached_visit.driver_image)
    assert cached.read_bytes() == b"\xff\xd8photo"
