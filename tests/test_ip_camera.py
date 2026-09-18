import time
from datetime import datetime

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from askari_vms.ip_camera import (
    CameraError, CameraFrame, SnapshotClient, SnapshotFeed, entry_anpr_snapshot_feed,
    exit_anpr_snapshot_feed, exit_driver_feed,
)
from askari_vms.settings import default_settings
from askari_vms.storage import Store
from askari_vms.ui.pages.entry_portal import EntryPortalWindow
from askari_vms.ui.pages.exit_portal import ExitPortalWindow
from askari_vms.visits import VisitRecord


def jpeg():
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(0xff338855)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "JPG")
    return bytes(data)


class FakeFeed:
    def __init__(self):
        self.frame = CameraFrame()
        self.stopped = False

    def start(self):
        pass

    def stop(self):
        self.stopped = True

    def latest(self):
        return self.frame


def test_entry_anpr_live_view_uses_assigned_camera_and_dahua_snapshot_path():
    feed = entry_anpr_snapshot_feed(default_settings())
    assert feed is not None
    assert feed.client.key == "anpr_exit"  # durable key; .13 is assigned to Entry
    assert feed.client.url == "http://192.168.1.13:80/cgi-bin/snapshot.cgi"


def test_exit_driver_uses_confirmed_hikvision_camera():
    feed = exit_driver_feed(default_settings())
    assert feed is not None
    assert feed.client.key == "driver_exit"
    assert feed.client.url == "http://192.168.1.17:80/ISAPI/Streaming/channels/101/picture"


def test_exit_anpr_live_view_uses_confirmed_http_port():
    feed = exit_anpr_snapshot_feed(default_settings())
    assert feed is not None
    assert feed.client.url == "http://192.168.1.12:80/cgi-bin/snapshot.cgi"


def test_exit_driver_live_view_and_capture_are_persisted(qapp, tmp_path):
    feed = FakeFeed()
    store = Store(tmp_path / "exit.sqlite3")
    visit = VisitRecord("V-EXIT-CAM", "TOKEN", datetime.now(), "entry01")
    store.visits.save(visit)
    portal = ExitPortalWindow(
        visits=[visit], driver_camera=feed, image_directory=str(tmp_path),
        on_checkout=store.visits.save,
    )
    try:
        portal.select(visit)
        portal.set_decision("Matched")
        time.sleep(0.02)
        feed.frame = CameraFrame(jpeg(), time.monotonic(), "Live")
        portal._refresh_driver()
        assert not portal._panels["driver"].pixmap().isNull()
        closed = portal.submit()
        assert closed is not None and closed.exit_driver_image
        assert not QImage(closed.exit_driver_image).isNull()
        assert store.visits.list()[0].exit_driver_image == closed.exit_driver_image
    finally:
        portal.close()
        store.close()


def test_exit_anpr_overview_and_available_plate_crop_are_persisted(qapp, tmp_path):
    camera = FakeFeed()
    class PlateFeed:
        def start(self): pass
        def stop(self): pass
        def drain(self): return [], (True, "connected")
        def latest_plate_image(self): return jpeg()

    visit = VisitRecord("V-EXIT-EVIDENCE", "TOKEN", datetime.now(), "entry01")
    portal = ExitPortalWindow(visits=[visit], anpr_camera=camera, anpr_feed=PlateFeed(),
                              image_directory=str(tmp_path))
    try:
        portal.select(visit)
        portal.set_decision("Matched")
        camera.frame = CameraFrame(jpeg(), time.monotonic(), "Live")
        closed = portal.submit()
        assert closed.exit_anpr_image and not QImage(closed.exit_anpr_image).isNull()
        assert closed.exit_plate_image and not QImage(closed.exit_plate_image).isNull()
    finally:
        portal.close()


def test_entry_portal_displays_the_anpr_live_snapshot(qapp):
    feed = FakeFeed()
    portal = EntryPortalWindow(anpr_camera=feed)
    try:
        feed.frame = CameraFrame(jpeg(), time.monotonic(), "Live ANPR camera")
        portal._refresh_anpr_camera()
        assert not portal._streams["anpr"].preview.pixmap().isNull()
        assert portal.cnic_panel.state.minimumHeight() == 30
    finally:
        portal.close()
    assert feed.stopped


def test_entry_submit_persists_anpr_overview(qapp, tmp_path):
    feed = FakeFeed()
    portal = EntryPortalWindow(anpr_camera=feed, image_directory=str(tmp_path))
    try:
        time.sleep(0.02)
        feed.frame = CameraFrame(jpeg(), time.monotonic(), "Live ANPR camera")
        visit = portal.submit()
        assert visit.entry_anpr_image
        assert not QImage(visit.entry_anpr_image).isNull()
    finally:
        portal.close()


def test_capture_persists_and_next_visitor_cannot_reuse_frame(qapp, tmp_path):
    feed = FakeFeed()
    store = Store(tmp_path / "visits.sqlite3")
    portal = EntryPortalWindow(driver_camera=feed, image_directory=str(tmp_path), on_submit=store.visits.save)
    try:
        time.sleep(0.02)
        feed.frame = CameraFrame(jpeg(), time.monotonic(), "Live")
        portal._refresh_driver()
        assert not portal._streams["driver"].preview.pixmap().isNull()
        first = portal.submit()
        assert first.driver_image
        assert not QImage(first.driver_image).isNull()
        assert store.visits.list()[0].driver_image == first.driver_image
        # Submit before a new frame arrives: old visitor's image must not transfer.
        second = portal.submit()
        assert not second.driver_image
        time.sleep(0.02)  # Windows monotonic clock can share a tick with clear_form.
        feed.frame = CameraFrame(jpeg(), time.monotonic(), "Live")
        third = portal.submit()
        assert third.driver_image and third.driver_image != first.driver_image
        exit_portal = ExitPortalWindow(visits=[first])
        exit_portal.select(first)
        assert not exit_portal.evidence.pixmap().isNull()
        assert exit_portal.evidence.minimumHeight() == 180
        exit_portal.clear()
        assert exit_portal.evidence.pixmap().isNull()
        assert exit_portal.evidence.minimumHeight() == 180
        exit_portal.close()
    finally:
        portal.close()
        store.close()
    assert feed.stopped


def test_stale_image_and_disk_failure_never_block_submit(qapp, tmp_path):
    feed = FakeFeed()
    blocked = tmp_path / "file-not-folder"
    blocked.write_text("occupied")
    portal = EntryPortalWindow(driver_camera=feed, image_directory=str(blocked))
    try:
        feed.frame = CameraFrame(jpeg(), time.monotonic() - 10, "Live")
        assert not portal.submit().driver_image
        time.sleep(0.02)
        feed.frame = CameraFrame(jpeg(), time.monotonic(), "Live")
        assert not portal.submit().driver_image
        assert any("Driver camera" in event.details for event in portal._audit.events())
    finally:
        portal.close()


def test_missing_credentials_are_redacted(monkeypatch):
    def fail(*args):
        raise RuntimeError("private credential detail")
    monkeypatch.setattr("askari_vms.ip_camera.read_credentials", fail)
    client = SnapshotClient("driver_entry", "127.0.0.1", 80, "/picture")
    import pytest
    with pytest.raises(CameraError, match="credentials unavailable") as error:
        client.fetch()
    assert "private" not in str(error.value)


def test_feed_recovers_after_failure_and_stops():
    class Client:
        calls = 0

        def fetch(self):
            self.calls += 1
            if self.calls == 1:
                raise CameraError("Camera unavailable")
            return b"image"

    client = Client()
    feed = SnapshotFeed(client)
    feed.start()
    deadline = time.monotonic() + 4
    try:
        while not feed.latest().jpeg and time.monotonic() < deadline:
            time.sleep(0.02)
        assert feed.latest().jpeg == b"image"
        assert client.calls >= 2
    finally:
        feed.stop()
        feed._thread.join(timeout=1)
    assert not feed._thread.is_alive()
