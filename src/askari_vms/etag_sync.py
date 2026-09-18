"""Persistent, non-blocking synchronization of E-Tags to gate controllers."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict
from datetime import date
import json
import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer

from askari_vms.audit import AuditLog, AuditSeverity
from askari_vms.etag_controller import HttpETagController
from askari_vms.etags import ETagRecord
from askari_vms.settings import AppSettings


class ETagSyncService(QObject):
    """Queues mutations in SQLite and performs one verified controller write at a time."""

    def __init__(self, settings: AppSettings, outbox, states, audit: AuditLog,
                 on_changed: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.settings, self.outbox, self.states, self.audit = settings, outbox, states, audit
        self.on_changed = on_changed
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="etag-sync")
        self._future: Future | None = None
        self._item = None
        self._retry_after: dict[str, float] = {}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)

    def update_settings(self, settings: AppSettings) -> None:
        self.settings = settings

    def save(self, record: ETagRecord) -> None:
        selected = set(record.allowed_controllers)
        for controller in self.settings.controllers:
            if not controller.configured:
                continue
            if controller.key in selected:
                self._enqueue("save", record.rfid, controller.key, self._record_json(record))
            else:
                self._enqueue("revoke", record.rfid, controller.key, json.dumps({"rfid": record.rfid}))

    def replace(self, previous: ETagRecord, updated: ETagRecord) -> None:
        if previous.rfid != updated.rfid:
            self.revoke(previous.rfid)
        self.save(updated)

    def revoke(self, rfid: str) -> None:
        for controller in self.settings.controllers:
            if controller.configured:
                self._enqueue("revoke", rfid, controller.key, json.dumps({"rfid": rfid}))

    def _enqueue(self, action: str, rfid: str, key: str, payload: str) -> None:
        item_id = f"etag:{key}:{rfid}"
        envelope = json.dumps({"action": action, "controller": key, "payload": payload})
        self.outbox.enqueue(item_id, "etag_controller", envelope)
        self.states.set(rfid, key, "Pending")
        self._retry_after.pop(item_id, None)
        if self.on_changed:
            self.on_changed()

    @staticmethod
    def _record_json(record: ETagRecord) -> str:
        data = asdict(record)
        data["issue_date"] = record.issue_date.isoformat()
        data["expiry_date"] = record.expiry_date.isoformat()
        return json.dumps(data)

    @staticmethod
    def _record(payload: str) -> ETagRecord:
        data = json.loads(payload)
        data["issue_date"] = date.fromisoformat(data["issue_date"])
        data["expiry_date"] = date.fromisoformat(data["expiry_date"])
        data["allowed_controllers"] = tuple(data["allowed_controllers"])
        return ETagRecord(**data)

    def _tick(self) -> None:
        if self._future is not None:
            if not self._future.done():
                return
            self._finish()
        now = time.monotonic()
        item = next((x for x in self.outbox.list()
                     if x.kind == "etag_controller" and self._retry_after.get(x.item_id, 0) <= now), None)
        if item is None:
            return
        self._item = item
        self._future = self._executor.submit(self._execute, item.payload)

    def _execute(self, envelope: str) -> tuple[str, str, str]:
        data = json.loads(envelope)
        key = data["controller"]
        configured = next(c for c in self.settings.controllers if c.key == key and c.configured)
        adapter = HttpETagController(key, configured.ip_address, configured.port)
        if data["action"] == "save":
            record = self._record(data["payload"])
            adapter.save(record)
            return record.rfid, key, "saved"
        rfid = json.loads(data["payload"])["rfid"]
        adapter.revoke(rfid)
        return rfid, key, "revoked"

    def _finish(self) -> None:
        item, future = self._item, self._future
        self._item = self._future = None
        try:
            rfid, key, action = future.result()
        except Exception as exc:
            error = str(exc)
            data = json.loads(item.payload)
            rfid, key = item.item_id.rsplit(":", 1)[-1], data["controller"]
            self.outbox.failed(item.item_id, error)
            self.states.set(rfid, key, "Failed", error)
            self._retry_after[item.item_id] = time.monotonic() + 30
            self.audit.record("Controller sync", f"E-Tag {rfid}",
                              f"{key.title()} controller update failed",
                              "The update remains queued and will retry automatically.", AuditSeverity.WARNING)
        else:
            self.outbox.delete(item.item_id)
            self.states.set(rfid, key, "Synced")
            self.audit.record("Controller sync", f"E-Tag {rfid}",
                              f"{key.title()} controller {action} and verified",
                              "The controller search endpoint confirmed the RFID after the update.")
        if self.on_changed:
            self.on_changed()

    def close(self) -> None:
        self._timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
