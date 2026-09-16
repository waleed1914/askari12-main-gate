"""Exit-PC client and durable retry worker for the Entry LAN service."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime
from enum import Enum
from getpass import getpass
import json
from pathlib import Path
from threading import Event, Lock, Thread
import time
import urllib.error
import urllib.request
from uuid import uuid4

from askari_vms.etag_events import ETagEvent, ETagEventKind
from askari_vms.storage import Store
from askari_vms.visits import VisitRecord

DEFAULT_SERVER = "192.168.1.40"
DEFAULT_PORT = 8765


class LanClientError(RuntimeError):
    pass


def credential_target(address: str = DEFAULT_SERVER) -> str:
    return f"AskariVMS/server/{address}"


def read_token(address: str = DEFAULT_SERVER) -> str:
    import win32cred
    record = win32cred.CredRead(credential_target(address), win32cred.CRED_TYPE_GENERIC)
    blob = record["CredentialBlob"]
    return blob.decode("utf-16-le") if isinstance(blob, bytes) else str(blob)


def write_token(token: str, address: str = DEFAULT_SERVER) -> None:
    import win32cred
    win32cred.CredWrite({
        "Type": win32cred.CRED_TYPE_GENERIC, "TargetName": credential_target(address),
        "UserName": "exit-client", "CredentialBlob": token,
        "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
    }, 0)


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(type(value).__name__)


def visit_from_dict(data: dict) -> VisitRecord:
    values = dict(data)
    values["entry_time"] = datetime.fromisoformat(values["entry_time"])
    values["exit_time"] = datetime.fromisoformat(values["exit_time"]) if values.get("exit_time") else None
    return VisitRecord(**values)


class LanClient:
    def __init__(self, address: str = DEFAULT_SERVER, port: int = DEFAULT_PORT,
                 token_provider=read_token, timeout: float = 2.0) -> None:
        self.address, self.port = address, port
        self.base = f"http://{address}:{port}"
        self._token_provider, self.timeout = token_provider, timeout

    def _request(self, path: str, payload: dict | None = None) -> dict:
        try:
            token = self._token_provider(self.address)
        except Exception:
            raise LanClientError("Entry server credential unavailable.") from None
        body = json.dumps(payload, default=_json_default).encode() if payload is not None else None
        request = urllib.request.Request(
            self.base + path, data=body, method="POST" if body is not None else "GET",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                request, timeout=self.timeout
            ) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise LanClientError(f"Entry server rejected request (HTTP {exc.code}).") from None
        except Exception:
            raise LanClientError("Entry server unavailable. Saved for automatic retry.") from None

    def open_visits(self) -> list[VisitRecord]:
        return [visit_from_dict(item) for item in self._request("/api/v1/visits/open")["visits"]]

    def download_entry_driver_image(self, visit_id: str) -> tuple[bytes, str]:
        return self._download_entry_image(visit_id, "entry-driver-image")

    def download_entry_anpr_image(self, visit_id: str) -> tuple[bytes, str]:
        return self._download_entry_image(visit_id, "entry-anpr-image")

    def _download_entry_image(self, visit_id: str, endpoint: str) -> tuple[bytes, str]:
        try:
            token = self._token_provider(self.address)
        except Exception:
            raise LanClientError("Entry server credential unavailable.") from None
        request = urllib.request.Request(
            self.base + f"/api/v1/visits/{visit_id}/{endpoint}",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                request, timeout=max(self.timeout, 5)
            ) as response:
                return response.read(), response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return b"", ""
            raise LanClientError(f"Entry server rejected image request (HTTP {exc.code}).") from None
        except Exception:
            raise LanClientError("Entry driver image unavailable. Retrying automatically.") from None

    def checkout(self, visit: VisitRecord) -> None:
        self._request(f"/api/v1/visits/{visit.visit_id}/checkout", {
            "exit_operator": visit.exit_operator, "driver_match": visit.driver_match,
            "exit_time": visit.exit_time, "receipt_lost": visit.receipt_lost,
            "exit_driver_image": visit.exit_driver_image,
            "exit_anpr_image": visit.exit_anpr_image,
            "exit_plate_image": visit.exit_plate_image,
        })

    def upload_exit_driver_image(self, visit_id: str, path: str) -> str:
        return self.upload_exit_image(visit_id, path, "exit-driver-image")

    def upload_exit_image(self, visit_id: str, path: str, endpoint: str) -> str:
        try:
            token = self._token_provider(self.address)
            data = Path(path).read_bytes()
        except Exception:
            raise LanClientError("Exit driver image unavailable. Saved for automatic retry.") from None
        request = urllib.request.Request(
            self.base + f"/api/v1/visits/{visit_id}/{endpoint}", data=data, method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"},
        )
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                request, timeout=max(self.timeout, 5)
            ) as response:
                return str(json.load(response)["path"])
        except Exception:
            raise LanClientError("Exit image upload failed. Saved for automatic retry.") from None

    def etag_event(self, event: ETagEvent) -> None:
        self._request("/api/v1/etag-events", asdict(event))


class ExitSyncService:
    """One background worker; the Qt thread only enqueues and drains snapshots."""
    def __init__(self, database_path: str | Path, client: LanClient | None = None) -> None:
        self.database_path = str(database_path)
        self.client = client or LanClient()
        self._stop = Event()
        self._wake = Event()
        self._lock = Lock()
        self._visits: list[VisitRecord] | None = None
        self._status = (False, "Connecting to Entry server…")
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = Thread(target=self._run, daemon=True, name="exit-entry-sync")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def latest(self) -> tuple[list[VisitRecord] | None, tuple[bool, str]]:
        with self._lock:
            visits, self._visits = self._visits, None
            return visits, self._status

    def queue_checkout(self, visit: VisitRecord) -> None:
        payload = json.dumps(asdict(visit), default=_json_default)
        store = Store(self.database_path)
        try:
            store.outbox.enqueue(uuid4().hex, "checkout", payload)
        finally:
            store.close()
        self._wake.set()

    def queue_etag_event(self, event: ETagEvent) -> None:
        payload = json.dumps(asdict(event), default=_json_default)
        store = Store(self.database_path)
        try:
            store.outbox.enqueue(event.event_id, "etag_event", payload)
        finally:
            store.close()
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            connected = False
            message = "Entry server unavailable — transactions will retry automatically."
            try:
                visits = self._cache_entry_images(self.client.open_visits())
                with self._lock:
                    self._visits = visits
                self._flush()
                connected, message = True, "Connected to Entry server"
            except LanClientError as exc:
                message = str(exc)
            with self._lock:
                self._status = (connected, message)
            self._wake.wait(2.0)
            self._wake.clear()

    def _cache_entry_images(self, visits: list[VisitRecord]) -> list[VisitRecord]:
        folder = Path(self.database_path).parent / "images" / "entry_from_server"
        result = []
        for visit in visits:
            existing = self._cache_one_entry_image(
                folder, visit.visit_id, "driver", visit.driver_image,
                self.client.download_entry_driver_image,
            )
            anpr = self._cache_one_entry_image(folder, visit.visit_id, "anpr", visit.entry_anpr_image,
                                               self.client.download_entry_anpr_image)
            result.append(replace(visit, driver_image=str(existing) if existing else "",
                                  entry_anpr_image=str(anpr) if anpr else ""))
        return result

    @staticmethod
    def _cache_one_entry_image(folder: Path, visit_id: str, label: str, remote_path: str, downloader):
        if not remote_path:
            return None
        existing = next((p for p in (folder / f"{visit_id}-{label}.jpg",
                                      folder / f"{visit_id}-{label}.png") if p.is_file()), None)
        if existing is None:
            data, content_type = downloader(visit_id)
            if data:
                folder.mkdir(parents=True, exist_ok=True)
                suffix = ".png" if content_type == "image/png" else ".jpg"
                existing = folder / f"{visit_id}-{label}{suffix}"
                existing.write_bytes(data)
        return existing

    def _flush(self) -> None:
        store = Store(self.database_path)
        try:
            pending = store.outbox.list()
        finally:
            store.close()
        for item in pending:
            try:
                data = json.loads(item.payload)
                if item.kind == "checkout":
                    visit = visit_from_dict(data)
                    if visit.exit_driver_image:
                        central_path = self.client.upload_exit_driver_image(
                            visit.visit_id, visit.exit_driver_image
                        )
                        visit = replace(visit, exit_driver_image=central_path)
                    if visit.exit_anpr_image:
                        central_path = self.client.upload_exit_image(
                            visit.visit_id, visit.exit_anpr_image, "exit-anpr-image"
                        )
                        visit = replace(visit, exit_anpr_image=central_path)
                    if visit.exit_plate_image:
                        central_path = self.client.upload_exit_image(
                            visit.visit_id, visit.exit_plate_image, "exit-plate-image"
                        )
                        visit = replace(visit, exit_plate_image=central_path)
                    self.client.checkout(visit)
                elif item.kind == "etag_event":
                    data["timestamp"] = datetime.fromisoformat(data["timestamp"])
                    data["kind"] = ETagEventKind(data["kind"])
                    self.client.etag_event(ETagEvent(**data))
                else:
                    raise ValueError("unknown outbox kind")
            except Exception as exc:
                store = Store(self.database_path)
                try:
                    store.outbox.failed(item.item_id, str(exc))
                finally:
                    store.close()
                continue
            store = Store(self.database_path)
            try:
                store.outbox.delete(item.item_id)
            finally:
                store.close()


def main() -> int:
    token = getpass("Paste EXIT_PC_TOKEN: ").strip()
    if not token:
        raise SystemExit("Token cannot be blank")
    write_token(token)
    print(f"Credential saved: {credential_target()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
