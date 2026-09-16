from datetime import datetime
from http.server import ThreadingHTTPServer
import json
from threading import Thread
import urllib.error
import urllib.request

from askari_vms.etag_events import ETagEventKind
from askari_vms.lan_server import handler_factory
from askari_vms.storage import Store
from askari_vms.visits import VisitRecord


def request(base, path, token="test-token", method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=2) as response:
        return response.status, json.load(response)


def upload(base, path, data):
    req = urllib.request.Request(base + path, data=data, method="POST", headers={
        "Authorization": "Bearer test-token", "Content-Type": "image/jpeg",
    })
    with urllib.request.urlopen(req, timeout=2) as response:
        return response.status, json.load(response)


def download(base, path):
    req = urllib.request.Request(base + path, headers={"Authorization": "Bearer test-token"})
    with urllib.request.urlopen(req, timeout=2) as response:
        return response.status, response.read(), response.headers.get_content_type()


def running_server(path):
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), handler_factory(path, "test-token", {"127.0.0.1"})
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}"


def test_health_requires_the_shared_secret(tmp_path):
    server, thread, base = running_server(tmp_path / "server.sqlite3")
    try:
        assert request(base, "/api/v1/health")[1] == {"status": "ok", "role": "entry-server"}
        try:
            request(base, "/api/v1/health", token="wrong")
            assert False, "wrong token must not authenticate"
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_open_visits_and_checkout_write_the_entry_database(tmp_path):
    path = tmp_path / "server.sqlite3"
    store = Store(path)
    visit = VisitRecord("VIS-000001", "TOKEN1", datetime(2026, 9, 16, 10), "entry01",
                        vehicle_number="LEA-123")
    store.visits.save(visit)
    store.close()
    server, thread, base = running_server(path)
    try:
        status, body = request(base, "/api/v1/visits/open")
        assert status == 200 and body["visits"][0]["visit_id"] == visit.visit_id
        status, image = upload(base, f"/api/v1/visits/{visit.visit_id}/exit-driver-image",
                               b"\xff\xd8test-jpeg")
        assert status == 200 and (tmp_path / "images") in __import__("pathlib").Path(image["path"]).parents
        status, body = request(base, f"/api/v1/visits/{visit.visit_id}/checkout", method="POST", payload={
            "exit_operator": "operator02", "driver_match": "Matched",
            "exit_time": "2026-09-16T11:00:00", "receipt_lost": False,
            "exit_driver_image": image["path"],
        })
        assert status == 200 and body["visit"]["exit_operator"] == "operator02"
    finally:
        server.shutdown()
        thread.join(timeout=2)
    store = Store(path)
    try:
        [closed] = store.visits.list()
        assert closed.exit_time == datetime(2026, 9, 16, 11)
        assert closed.driver_match == "Matched"
        assert closed.exit_driver_image == image["path"]
        assert any(event.target == visit.visit_id for event in store.audit.list())
    finally:
        store.close()


def test_exit_can_download_entry_driver_image(tmp_path):
    path = tmp_path / "server.sqlite3"
    image_path = tmp_path / "entry.jpg"
    image_path.write_bytes(b"\xff\xd8entry-photo")
    store = Store(path)
    visit = VisitRecord("VIS-IMG", "TOKEN", datetime.now(), "entry01",
                        driver_image=str(image_path))
    store.visits.save(visit)
    store.close()
    server, thread, base = running_server(path)
    try:
        status, body, content_type = download(
            base, "/api/v1/visits/VIS-IMG/entry-driver-image"
        )
        assert status == 200 and body == image_path.read_bytes()
        assert content_type == "image/jpeg"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_exit_can_download_entry_anpr_image(tmp_path):
    path = tmp_path / "server.sqlite3"
    image_path = tmp_path / "anpr.jpg"
    image_path.write_bytes(b"\xff\xd8anpr-photo")
    store = Store(path)
    store.visits.save(VisitRecord("VIS-ANPR", "TOKEN", datetime.now(), "entry01",
                                  entry_anpr_image=str(image_path)))
    store.close()
    server, thread, base = running_server(path)
    try:
        status, body, _ = download(base, "/api/v1/visits/VIS-ANPR/entry-anpr-image")
        assert status == 200 and body == image_path.read_bytes()
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_exit_etag_event_is_stored_centrally(tmp_path):
    path = tmp_path / "server.sqlite3"
    Store(path).close()
    server, thread, base = running_server(path)
    try:
        status, body = request(base, "/api/v1/etag-events", method="POST", payload={
            "event_id": "ETL-exit-91", "timestamp": "2026-09-16T11:05:00",
            "controller_name": "Exit Controller", "door": "E-tag Exit",
            "direction": "Out", "rfid": "77076", "kind": "Unknown tag",
        })
        assert status == 200 and body["event_id"] == "ETL-exit-91"
    finally:
        server.shutdown()
        thread.join(timeout=2)
    store = Store(path)
    try:
        [event] = store.etag_events.list()
        assert event.kind is ETagEventKind.UNKNOWN and event.rfid == "77076"
    finally:
        store.close()
