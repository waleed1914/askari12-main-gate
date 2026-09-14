from datetime import datetime
import time

from askari_vms.controller_events import (
    ControllerEventFeed,
    ControllerReading,
    parse_event_xml,
)
from askari_vms.etag_events import ETagEventKind
from askari_vms.ui.pages.entry_portal import EntryPortalWindow


SINGLE = b"""<?xml version="1.0"?>
<Event>
  <ID>42</ID><Time>2026-09-14 20:54:40</Time><Card>14811188</Card>
  <Door>1</Door><Reader>0</Reader><Event>Invalid card</Event><Note>Denied</Note>
</Event>"""


def test_controller_event_xml_fields_are_parsed():
    [reading] = parse_event_xml(SINGLE)
    assert reading == ControllerReading(
        42, datetime(2026, 9, 14, 20, 54, 40), "14811188", 1, 0,
        "Invalid card", "Denied",
    )


def test_wrapped_events_are_parsed_and_deduplicated():
    payload = b"<Events>" + SINGLE.split(b"?>", 1)[1] + SINGLE.split(b"?>", 1)[1].replace(
        b"<ID>42</ID>", b"<ID>43</ID>"
    ) + b"</Events>"
    assert [item.event_id for item in parse_event_xml(payload)] == [42, 43]


class _Feed:
    def __init__(self, readings):
        self.readings = readings
        self.stopped = False

    def start(self):
        pass

    def stop(self):
        self.stopped = True

    def drain(self):
        readings, self.readings = self.readings, []
        return readings, (True, "Entry e-tag reader connected")


def test_entry_door_one_readings_are_classified_persisted_and_shown(qapp):
    saved = []
    feed = _Feed([ControllerReading(42, datetime(2026, 9, 14, 20, 54, 40), "14811188", 1,
                                          event="Invalid card")])
    page = EntryPortalWindow(controller_event_feed=feed, on_etag_event=saved.append)
    try:
        page._refresh_controller_event_feed()
        assert len(saved) == 1
        assert saved[0].event_id == "ETL-entry-42"
        assert saved[0].kind is ETagEventKind.UNKNOWN
        assert page.events_table.item(0, 1).text() == "14811188"
        assert page.events_status.text() == "Entry e-tag reader connected"
        assert any(event.severity.value == "Critical" for event in page._audit.events())
    finally:
        page.close()
    assert feed.stopped


def test_visitor_door_two_events_are_not_recorded_as_etags(qapp):
    saved = []
    feed = _Feed([ControllerReading(44, datetime.now(), "123", 2)])
    page = EntryPortalWindow(controller_event_feed=feed, on_etag_event=saved.append)
    try:
        page._refresh_controller_event_feed()
        assert saved == []
    finally:
        page.close()


def test_feed_advances_id_and_does_not_repeat():
    class Client:
        requested = []

        def fetch(self, last_id):
            self.requested.append(last_id)
            return SINGLE

    client = Client()
    feed = ControllerEventFeed(client)
    feed.start()
    deadline = time.monotonic() + 2
    readings = []
    try:
        while time.monotonic() < deadline and not readings:
            time.sleep(0.02)
            readings, _ = feed.drain()
        assert [item.event_id for item in readings] == [42]
        time.sleep(0.6)
        repeated, _ = feed.drain()
        assert repeated == []
        assert 42 in client.requested
    finally:
        feed.stop()
