"""Authenticated LAN snapshot adapter, independent of Qt.

The worker owns network I/O. Consumers poll one latest frame, so a slow UI never
accumulates images. Credentials are scoped to the camera address in Windows.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock, Thread
import time
import urllib.error
import urllib.request

from askari_vms.settings import AppSettings, DRIVER, VISITOR_ENTRY

MAX_IMAGE_BYTES = 10_000_000
MAX_FRAME_AGE = 3.0


def credential_target(key: str, address: str) -> str:
    return f"AskariVMS/camera/{key}/{address}"


def read_credentials(key: str, address: str) -> tuple[str, str]:
    import win32cred

    record = win32cred.CredRead(credential_target(key, address), win32cred.CRED_TYPE_GENERIC)
    blob = record["CredentialBlob"]
    return record["UserName"], blob.decode("utf-16-le") if isinstance(blob, bytes) else blob


class CameraError(Exception):
    """Safe operator message; never includes authentication data."""


class SnapshotClient:
    def __init__(self, key: str, address: str, port: int, path: str) -> None:
        self.key, self.address = key, address
        self.base = f"http://{address}:{port}"
        self.url = self.base + path

    def fetch(self) -> bytes:
        try:
            username, password = read_credentials(self.key, self.address)
        except Exception:
            raise CameraError("Camera credentials unavailable in Windows Credential Manager.") from None
        manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, self.base, username, password)
        # LAN requests must not be sent through a workstation's internet proxy.
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPDigestAuthHandler(manager),
            urllib.request.HTTPBasicAuthHandler(manager),
        )
        try:
            with opener.open(self.url, timeout=2) as response:
                data = response.read(MAX_IMAGE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise CameraError(f"Camera returned HTTP {exc.code}.") from None
        except Exception:
            raise CameraError("Camera unavailable. Retrying automatically.") from None
        if len(data) > MAX_IMAGE_BYTES or not data.startswith(b"\xff\xd8"):
            raise CameraError("Camera did not return a JPEG image.")
        return data


@dataclass(frozen=True)
class CameraFrame:
    jpeg: bytes = b""
    received_at: float = 0.0
    message: str = "Connecting to driver camera…"

    def fresh(self, after: float = 0.0) -> bool:
        return bool(self.jpeg) and self.received_at > after and time.monotonic() - self.received_at <= MAX_FRAME_AGE


class SnapshotFeed:
    def __init__(self, client: SnapshotClient) -> None:
        self.client = client
        self._stop = Event()
        self._lock = Lock()
        self._frame = CameraFrame()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = Thread(target=self._run, daemon=True, name="driver-camera")
            self._thread.start()

    def stop(self) -> None:
        # No GUI-thread join: network requests have a bounded timeout. This worker
        # holds no Qt objects and cannot call a destroyed window.
        self._stop.set()

    def latest(self) -> CameraFrame:
        with self._lock:
            return self._frame

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                jpeg = self.client.fetch()
                frame = CameraFrame(jpeg, time.monotonic(), "Live driver camera — refreshing snapshots")
                failures = 0
                delay = max(0.0, 0.5 - (time.monotonic() - started))
            except Exception as exc:
                # An unexpected adapter failure must not leak URLs or credentials.
                message = str(exc) if isinstance(exc, CameraError) else "Camera unavailable. Retrying automatically."
                frame = CameraFrame(message=message)
                failures += 1
                delay = min(10, 2 ** min(failures - 1, 4))
            with self._lock:
                self._frame = frame
            self._stop.wait(delay)


def entry_driver_feed(settings: AppSettings) -> SnapshotFeed | None:
    camera = next((c for c in settings.cameras if c.role == DRIVER and c.lane == VISITOR_ENTRY
                   and c.ip_address and c.snapshot_path), None)
    if camera is None:
        return None
    return SnapshotFeed(SnapshotClient(camera.key, camera.ip_address, camera.http_port, camera.snapshot_path))
