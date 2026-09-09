import io
import time

import pytest

from askari_vms.anpr import (
    ANPRFeed, PlateDeduplicator, PlateReading, entry_anpr_feed,
    multipart_parts, normalize_plate, parse_plate_events,
)
from askari_vms.ip_camera import CameraError
from askari_vms.settings import default_settings
from askari_vms.ui.pages.entry_portal import EntryPortalWindow


def test_snapshot_event_batch_and_event_manager_json():
    body = b'''Events[0].EventBaseInfo.Code=TrafficJunction
Events[0].EventBaseInfo.Action=Pulse
Events[0].TrafficCar.PlateNumber=abc-123
Events[0].GroupID=41
Events[1].Code=TrafficJunction
Events[1].TrafficCar.PlateNumber=XYZ 789
Events[2].Code=VideoMotion
Events[2].TrafficCar.PlateNumber=IGNORE
Events[3].Code=TrafficJunction
Events[3].TrafficCar.PlateNumber=Unknown
'''
    readings = parse_plate_events(body, now=10)
    assert [r.plate for r in readings] == ["ABC-123", "XYZ 789"]
    assert readings[0].event_id == "41:"
    event = b'Code=TrafficJunction;action=Pulse;index=0;data={"TrafficCar":{"PlateNumber":"abc-123"},"UTC":123}'
    assert parse_plate_events(event, now=11) == [PlateReading("ABC-123", 11, ":123")]
    assert parse_plate_events(event.replace(b"Pulse", b"Stop")) == []
    assert parse_plate_events(b'Code=TrafficJunction;action=Pulse;data={broken') == []
    assert parse_plate_events(b'Heartbeat') == []
    assert normalize_plate("No Plate") == ""
    assert normalize_plate("<unknown>") == ""


def test_multipart_lengths_keep_binary_images_out_of_the_parser():
    jpeg = b'\xff\xd8\r\n--myboundary\r\nEvents[0].TrafficCar.PlateNumber=WRONG'
    body = b'Events[0].Code=TrafficJunction\nEvents[0].TrafficCar.PlateNumber=ABC-123'
    wire = b''
    for kind, data in [(b'image/jpeg', jpeg), (b'text/plain', body)]:
        wire += b'\r\n--myboundary\r\nContent-Type: ' + kind + b'\r\nContent-Length: ' + str(len(data)).encode() + b'\r\n\r\n' + data
    parts = list(multipart_parts(io.BytesIO(wire)))
    assert parts == [(b'image/jpeg', jpeg), (b'text/plain', body)]
    with pytest.raises(CameraError):
        list(multipart_parts(io.BytesIO(b'--b\r\nContent-Length: 10000001\r\n\r\n')))
    with pytest.raises(CameraError):
        list(multipart_parts(io.BytesIO(b'--b\r\nContent-Length: 10\r\n\r\nabc')))


def test_duplicate_bursts_replays_and_same_plate_returning():
    dedup = PlateDeduplicator()
    assert dedup.accept(PlateReading("ABC-123", 10, "group1"))
    assert not dedup.accept(PlateReading("ABC-123", 11, "group2"))
    assert not dedup.accept(PlateReading("ABC-123", 20, "group1"))
    assert dedup.accept(PlateReading("ABC-123", 30, "new-visit"))
    assert dedup.accept(PlateReading("XYZ-789", 31))


def test_plate_autofill_preserves_focus_corrections_and_never_submits(qapp):
    page = EntryPortalWindow()
    submissions = []
    page.submitted.connect(submissions.append)
    page.show()
    page.fields["destination"].setText("House 12")
    page.fields["destination"].setFocus()
    qapp.processEvents()
    try:
        assert page.read_plate(" abc-123 ")
        assert page.fields["vehicle_number"].text() == "ABC-123"
        assert page.fields["destination"].hasFocus()
        page.fields["vehicle_number"].setText("ABC-128")
        assert not page.read_plate("ABC-123")
        assert page.fields["vehicle_number"].text() == "ABC-128"
        assert page.read_plate("XYZ-789")
        assert page.fields["vehicle_number"].text() == "XYZ-789"
        assert page.fields["destination"].text() == "House 12"
        assert not submissions
        assert not any(e.action == "Gate command" for e in page._audit.events())
    finally:
        page.close()


def test_queued_old_detection_does_not_fill_next_visitor(qapp):
    class Feed:
        stopped = False
        readings = []
        def start(self): pass
        def stop(self): self.stopped = True
        def drain(self):
            batch, self.readings = self.readings, []
            return batch, (True, "ANPR connected")

    feed = Feed()
    page = EntryPortalWindow(anpr_feed=feed)
    try:
        feed.readings = [PlateReading("OLD-123", time.monotonic() - 1)]
        page.clear_form()
        page._refresh_anpr()
        assert page.fields["vehicle_number"].text() == ""
        time.sleep(0.02)  # Move beyond Windows' monotonic clock tick.
        feed.readings = [PlateReading("NEW-456", time.monotonic())]
        page._refresh_anpr()
        assert page.fields["vehicle_number"].text() == "NEW-456"
        page.capture()
        assert not page._captured["anpr"] and not page._captured["plate"]
    finally:
        page.close()
    assert feed.stopped


def test_assignment_follows_lane_not_legacy_camera_key():
    feed = entry_anpr_feed(default_settings())
    assert feed is not None and feed.address == "192.168.1.13"


def test_subscription_recovers_and_errors_do_not_expose_credentials(monkeypatch):
    feed = ANPRFeed("test", "127.0.0.1", 80, "/events")
    calls = []
    class Response(io.BytesIO):
        headers = {"Content-Type": "multipart/x-mixed-replace"}
    body = b'Events[0].Code=TrafficJunction\nEvents[0].TrafficCar.PlateNumber=ABC-123'
    wire = b'--b\r\nContent-Type: text/plain\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body
    def connect():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("private credential detail")
        return Response(wire)
    monkeypatch.setattr(feed, "_open", connect)
    feed.start()
    deadline = time.monotonic() + 4
    result = []
    try:
        while time.monotonic() < deadline:
            readings, (_, message) = feed.drain()
            assert "private" not in message
            result.extend(readings)
            if result: break
            time.sleep(0.02)
        assert result and result[0].plate == "ABC-123"
        assert len(calls) >= 2
    finally:
        feed.stop()
        feed._thread.join(timeout=1)
    assert not feed._thread.is_alive()
