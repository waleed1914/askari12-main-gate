import time

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from askari_vms.ip_camera import CameraError, CameraFrame, SnapshotClient, SnapshotFeed
from askari_vms.storage import Store
from askari_vms.ui.pages.entry_portal import EntryPortalWindow
from askari_vms.ui.pages.exit_portal import ExitPortalWindow


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
        exit_portal.clear()
        assert exit_portal.evidence.pixmap().isNull()
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
