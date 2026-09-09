"""Dahua plate event subscription. Network I/O and parsing stay outside Qt.

Protocol: Dahua's Access Control Products Integration Instruction, real-time
snapshot subscription (snapManager / TrafficJunction / TrafficCar.PlateNumber).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import re
from threading import Event, Lock, Thread
import time
import urllib.error
import urllib.request

from askari_vms.ip_camera import CameraError, read_credentials
from askari_vms.settings import ANPR, VISITOR_ENTRY, AppSettings

DAHUA_EVENT_PATH = "/cgi-bin/snapManager.cgi?action=attachFileProc&Flags%5B0%5D=Event&Events=%5BTrafficJunction%5D&heartbeat=5"
MAX_PART = 10_000_000


def normalize_plate(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = " ".join(value.strip().strip('"').split()).upper()
    if value.replace(" ", "").replace("-", "") in {"UNKNOWN", "NOPLATE", "UNRECOGNIZED", "无牌", "未知"}:
        return ""
    if not 2 <= len(value) <= 32 or not any(c.isalnum() for c in value):
        return ""
    return value if all(c.isalnum() or c in " -./" for c in value) else ""


@dataclass(frozen=True)
class PlateReading:
    plate: str
    received_at: float
    event_id: str = ""


def parse_plate_events(body: bytes, now: float | None = None) -> list[PlateReading]:
    """Read both snapManager key/value events and eventManager JSON payloads."""
    text = body.decode("utf-8", errors="replace").strip()
    stamp = time.monotonic() if now is None else now
    groups: list[dict] = []
    if text.startswith("Code=") and ";data=" in text:
        header, payload = text.split(";data=", 1)
        attributes = dict(part.split("=", 1) for part in header.split(";") if "=" in part)
        if attributes.get("Code") != "TrafficJunction" or attributes.get("action") == "Stop":
            return []
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return []
        if not isinstance(data, dict):
            return []
        car, obj = data.get("TrafficCar", {}), data.get("Object", {})
        groups.append({
            "Code": "TrafficJunction",
            "Plate": car.get("PlateNumber", "") if isinstance(car, dict) else "",
            "Fallback": obj.get("Text", "") if isinstance(obj, dict) else "",
            "ID": str(data.get("GroupID", data.get("EventID", ""))),
            "Time": str(data.get("UTC", data.get("PTS", ""))),
        })
    else:
        events: dict[str, dict[str, str]] = {}
        for line in text.splitlines():
            match = re.match(r"Events\[(\d+)\]\.(.*?)=(.*)$", line.strip())
            if match:
                index, key, value = match.groups()
                events.setdefault(index, {})[key] = value.strip()
        for data in events.values():
            if data.get("EventBaseInfo.Action", data.get("Action")) == "Stop":
                continue
            groups.append({
                "Code": data.get("EventBaseInfo.Code", data.get("Code")),
                "Plate": data.get("TrafficCar.PlateNumber", data.get("Data.TrafficCar.PlateNumber", "")),
                "Fallback": data.get("Object.Text", ""),
                "ID": data.get("GroupID", ""),
                "Time": data.get("UTC", data.get("PTS", data.get("Data.PTS", ""))),
            })
    result = []
    for data in groups:
        if data["Code"] != "TrafficJunction":
            continue
        plate = normalize_plate(data["Plate"]) or normalize_plate(data["Fallback"])
        if plate:
            identity = f"{data['ID']}:{data['Time']}" if data["ID"] or data["Time"] else ""
            result.append(PlateReading(plate, stamp, identity))
    return result


def multipart_parts(response):
    """Length-delimited parts; JPEGs may contain newlines and boundary-like bytes."""
    while True:
        line = response.readline(4097)
        if not line:
            return
        if len(line) > 4096:
            raise CameraError("Invalid ANPR event stream.")
        if not line.strip().startswith(b"--"):
            continue
        headers = {}
        for _ in range(32):
            line = response.readline(4097)
            if not line:
                return
            if line in (b"\r\n", b"\n"):
                break
            if len(line) > 4096 or b":" not in line:
                raise CameraError("Invalid ANPR event headers.")
            key, value = line.split(b":", 1)
            headers[key.strip().lower()] = value.strip()
        else:
            raise CameraError("Invalid ANPR event headers.")
        try:
            length = int(headers[b"content-length"])
        except (KeyError, ValueError):
            raise CameraError("ANPR event length missing.") from None
        if not 0 <= length <= MAX_PART:
            raise CameraError("ANPR event exceeds the size limit.")
        body = response.read(length)
        if len(body) != length:
            raise CameraError("ANPR event stream interrupted.")
        yield headers.get(b"content-type", b""), body


class PlateDeduplicator:
    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], float] = {}
        self._last_plate = ""
        self._last_at = float("-inf")

    def accept(self, reading: PlateReading) -> bool:
        now = reading.received_at
        self._seen = {key: stamp for key, stamp in self._seen.items() if now - stamp < 60}
        key = (reading.plate, reading.event_id)
        duplicate = bool(reading.event_id and key in self._seen)
        duplicate |= reading.plate == self._last_plate and now - self._last_at < 5
        self._last_plate, self._last_at = reading.plate, now
        if reading.event_id:
            self._seen[key] = now
        return not duplicate


class ANPRFeed:
    def __init__(self, key: str, address: str, port: int, path: str) -> None:
        self.key, self.address = key, address
        self.base = f"http://{address}:{port}"
        self.path = path
        self._stop = Event()
        self._lock = Lock()
        self._pending: deque[PlateReading] = deque(maxlen=64)
        self._status = (False, "Connecting to ANPR…")
        self._thread: Thread | None = None
        self._duplicates = PlateDeduplicator()

    def start(self) -> None:
        if self._thread is None:
            self._thread = Thread(target=self._run, daemon=True, name="entry-anpr")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()  # read timeout bounds shutdown without blocking Qt

    def drain(self) -> tuple[list[PlateReading], tuple[bool, str]]:
        with self._lock:
            readings = list(self._pending)
            self._pending.clear()
            return readings, self._status

    def _set_status(self, connected: bool, message: str) -> None:
        with self._lock:
            self._status = (connected, message)

    def _open(self):
        try:
            username, password = read_credentials(self.key, self.address)
        except Exception:
            raise CameraError("ANPR credentials unavailable. Type the plate manually.") from None
        manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, self.base, username, password)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                             urllib.request.HTTPDigestAuthHandler(manager))
        return opener.open(self.base + self.path, timeout=8)

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                with self._open() as response:
                    if "multipart/" not in response.headers.get("Content-Type", ""):
                        raise CameraError("ANPR event stream unavailable. Type the plate manually.")
                    self._set_status(True, "ANPR connected — waiting for a plate")
                    for content_type, body in multipart_parts(response):
                        if self._stop.is_set():
                            return
                        failures = 0
                        if b"text" in content_type or b"json" in content_type:
                            for reading in parse_plate_events(body):
                                if self._duplicates.accept(reading):
                                    with self._lock:
                                        self._pending.append(reading)
                    raise CameraError("ANPR disconnected. Retrying; manual entry is available.")
            except Exception as exc:
                message = str(exc) if isinstance(exc, CameraError) else "ANPR unavailable. Retrying; type the plate manually."
                self._set_status(False, message)
                failures += 1
                self._stop.wait(min(10, 2 ** min(failures - 1, 4)))


def entry_anpr_feed(settings: AppSettings) -> ANPRFeed | None:
    camera = next((c for c in settings.cameras if c.role == ANPR and c.lane == VISITOR_ENTRY
                   and c.ip_address and c.anpr_event_path), None)
    return ANPRFeed(camera.key, camera.ip_address, camera.http_port, camera.anpr_event_path) if camera else None
