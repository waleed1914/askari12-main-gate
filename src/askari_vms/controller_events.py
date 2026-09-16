"""Reconnectable live-event polling for the two-door web controller."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import json
import re
from threading import Event, Lock, Thread
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from askari_vms.gate_controller import GateControllerError, read_credentials
from askari_vms.settings import AppSettings

MAX_EVENT_BYTES = 256_000


@dataclass(frozen=True, slots=True)
class ControllerReading:
    event_id: int
    timestamp: datetime
    card: str
    door: int
    reader: int = 0
    event: str = ""
    note: str = ""


def _number(value: str, default: int = 0) -> int:
    digits = "".join(char for char in value if char.isdigit())
    return int(digits) if digits else default


def _timestamp(value: str) -> datetime:
    cleaned = value.strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(cleaned, pattern)
        except ValueError:
            pass
    return datetime.now().replace(microsecond=0)


def parse_event_xml(payload: bytes) -> list[ControllerReading]:
    """Parse XML fields or the controller's JSON object wrapped in ``<response>``."""
    try:
        text = payload.decode("utf-8", errors="replace").lstrip("\ufeff\x00 \r\n\t")
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise GateControllerError("Controller returned an unreadable event response.") from exc

    candidates = []
    for node in (root, *root.iter()):
        fields = {
            child.tag.rsplit("}", 1)[-1].casefold(): (child.text or "").strip()
            for child in list(node)
        }
        if "id" in fields and ("card" in fields or "door" in fields):
            candidates.append(fields)

    # Firmware V5 wraps its live record as JSON text after a ``#GEvent`` XML
    # comment instead of representing fields as XML children.
    embedded = "".join(root.itertext())
    for match in re.finditer(r"\{[^{}]+\}", embedded):
        try:
            record = json.loads(match.group(0))
        except (TypeError, ValueError):
            continue
        fields = {str(key).casefold(): str(value).strip() for key, value in record.items()}
        if "id" in fields and ("card" in fields or "door" in fields):
            candidates.append(fields)

    readings: list[ControllerReading] = []
    seen: set[int] = set()
    for fields in candidates:
        event_id = _number(fields.get("id", ""), -1)
        if event_id < 0 or event_id in seen:
            continue
        seen.add(event_id)
        readings.append(ControllerReading(
            event_id=event_id,
            timestamp=_timestamp(fields.get("time", "")),
            card=fields.get("card", "").strip(),
            door=_number(fields.get("door", "")),
            reader=_number(fields.get("reader", "")),
            event=fields.get("event", fields.get("output", "")),
            note=fields.get("note", ""),
        ))
    return readings


class ControllerEventClient:
    def __init__(self, key: str, address: str, port: int = 80, timeout: float = 2.0) -> None:
        self.key, self.address, self.port, self.timeout = key, address, port, timeout

    def fetch(self, last_event_id: int) -> bytes:
        try:
            username, password = read_credentials(self.key, self.address)
        except Exception:
            raise GateControllerError(
                "Controller credentials unavailable in Windows Credential Manager."
            ) from None
        query = urllib.parse.urlencode({"ID": last_event_id})
        url = f"http://{self.address}:{self.port}/GEvent.xml?{query}"
        token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.timeout) as response:
                payload = response.read(MAX_EVENT_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise GateControllerError(f"Controller event feed returned HTTP {exc.code}.") from None
        except Exception:
            raise GateControllerError("Controller event feed unavailable. Retrying automatically.") from None
        if len(payload) > MAX_EVENT_BYTES:
            raise GateControllerError("Controller event response was too large.")
        return payload


class ControllerEventFeed:
    def __init__(self, client: ControllerEventClient, last_event_id: int = 0,
                 label: str = "Entry") -> None:
        self.client = client
        self.label = label
        self._last_id = last_event_id
        self._stop = Event()
        self._lock = Lock()
        self._readings: list[ControllerReading] = []
        self._status = (False, "Connecting to Entry e-tag reader…")
        self._status = (False, f"Connecting to {label} e-tag reader…")
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = Thread(target=self._run, daemon=True, name=f"{self.label.casefold()}-controller-events")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def drain(self) -> tuple[list[ControllerReading], tuple[bool, str]]:
        with self._lock:
            readings, self._readings = self._readings, []
            return readings, self._status

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                readings = parse_event_xml(self.client.fetch(self._last_id))
                new = [item for item in readings if item.event_id > self._last_id]
                if readings:
                    self._last_id = max(self._last_id, *(item.event_id for item in readings))
                with self._lock:
                    self._readings.extend(new)
                    self._readings = self._readings[-500:]
                    self._status = (True, f"{self.label} e-tag reader connected")
                failures = 0
                delay = 0.5
            except Exception as exc:
                message = (str(exc) if isinstance(exc, GateControllerError) else
                           "Controller event feed unavailable. Retrying automatically.")
                with self._lock:
                    self._status = (False, message)
                failures += 1
                delay = min(10, 2 ** min(failures - 1, 4))
            self._stop.wait(delay)


def entry_event_feed(settings: AppSettings, last_event_id: int = 0) -> ControllerEventFeed | None:
    controller = next(
        (item for item in settings.controllers if item.key == "entry" and item.configured),
        None,
    )
    if controller is None:
        return None
    return ControllerEventFeed(
        ControllerEventClient(controller.key, controller.ip_address, controller.port),
        last_event_id,
    )


def exit_event_feed(settings: AppSettings, last_event_id: int = 0) -> ControllerEventFeed | None:
    controller = next(
        (item for item in settings.controllers if item.key == "exit" and item.configured), None,
    )
    if controller is None:
        return None
    return ControllerEventFeed(
        ControllerEventClient(controller.key, controller.ip_address, controller.port),
        last_event_id, "Exit",
    )
