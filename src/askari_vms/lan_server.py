"""Authenticated LAN API hosted only by the Entry PC.

The Exit PC never opens SQLite across a network share. Each request gets a short-lived
local Store handle on Entry, which is safe alongside the desktop process in WAL mode.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import unquote

from askari_vms.audit import AuditLog, AuditSeverity
from askari_vms.etag_events import ETagEvent, ETagEventKind
from askari_vms.settings import default_settings
from askari_vms.storage import Store, default_database_path
from askari_vms.visits import DriverMatch, check_out

DEFAULT_BIND = "192.168.1.40"
DEFAULT_PORT = 8765
DEFAULT_EXIT_CLIENT = "192.168.1.34"
MAX_BODY = 1_000_000


def credential_target(address: str = DEFAULT_BIND) -> str:
    return f"AskariVMS/server/{address}"


def read_server_token(address: str = DEFAULT_BIND) -> str:
    import win32cred
    record = win32cred.CredRead(credential_target(address), win32cred.CRED_TYPE_GENERIC)
    blob = record["CredentialBlob"]
    return blob.decode("utf-16-le") if isinstance(blob, bytes) else str(blob)


def write_server_token(token: str, address: str = DEFAULT_BIND) -> None:
    import win32cred
    win32cred.CredWrite({
        "Type": win32cred.CRED_TYPE_GENERIC,
        "TargetName": credential_target(address),
        "UserName": "exit-client",
        "CredentialBlob": token,
        "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
    }, 0)


def _json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(type(value).__name__)


def _visit_dict(visit) -> dict:
    return json.loads(json.dumps(asdict(visit), default=_json_value))


def handler_factory(database_path: str | Path, token: str, allowed_clients: set[str]):
    database_path = str(database_path)

    class Handler(BaseHTTPRequestHandler):
        server_version = "AskariVMS-LAN/1"

        def log_message(self, _format, *_args) -> None:
            pass  # Never put CNICs, tokens or request paths into console logs.

        def _reply(self, status: int, payload: dict | list) -> None:
            body = json.dumps(payload, default=_json_value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if self.client_address[0] not in allowed_clients:
                self._reply(403, {"error": "client_not_allowed"})
                return False
            supplied = self.headers.get("Authorization", "")
            expected = f"Bearer {token}"
            if not compare_digest(supplied, expected):
                self._reply(401, {"error": "authentication_required"})
                return False
            return True

        def _body(self) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self._reply(413, {"error": "invalid_body_size"})
                return None
            try:
                value = json.loads(self.rfile.read(length) or b"{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._reply(400, {"error": "invalid_json"})
                return None
            if not isinstance(value, dict):
                self._reply(400, {"error": "json_object_required"})
                return None
            return value

        def do_GET(self) -> None:
            if not self._authorized():
                return
            if self.path == "/api/v1/health":
                self._reply(200, {"status": "ok", "role": "entry-server"})
                return
            if self.path == "/api/v1/visits/open":
                store = Store(database_path)
                try:
                    rows = [_visit_dict(visit) for visit in store.visits.open_visits()]
                finally:
                    store.close()
                self._reply(200, {"visits": rows})
                return
            self._reply(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            body = self._body()
            if body is None:
                return
            if self.path.startswith("/api/v1/visits/") and self.path.endswith("/checkout"):
                visit_id = unquote(self.path[len("/api/v1/visits/"):-len("/checkout")]).strip("/")
                self._checkout(visit_id, body)
                return
            if self.path == "/api/v1/etag-events":
                self._etag_event(body)
                return
            self._reply(404, {"error": "not_found"})

        def _checkout(self, visit_id: str, body: dict) -> None:
            if body.get("driver_match") not in (DriverMatch.MATCHED, DriverMatch.MISMATCHED):
                self._reply(400, {"error": "driver_match_required"})
                return
            store = Store(database_path)
            try:
                visit = next((item for item in store.visits.list() if item.visit_id == visit_id), None)
                if visit is None:
                    self._reply(404, {"error": "visit_not_found"})
                    return
                if not visit.is_inside:
                    self._reply(409, {"error": "visit_already_closed"})
                    return
                try:
                    stamp = datetime.fromisoformat(body["exit_time"]) if body.get("exit_time") else None
                except (TypeError, ValueError):
                    self._reply(400, {"error": "invalid_exit_time"})
                    return
                closed = check_out(
                    visit, operator=str(body.get("exit_operator", "Exit operator")),
                    driver_match=body["driver_match"], exit_time=stamp,
                    receipt_lost=bool(body.get("receipt_lost", False)),
                )
                store.visits.save(closed)
                AuditLog(store.audit.list(), repository=store.audit).record(
                    action="Visitor decision", target=closed.visit_id,
                    summary=f"Exit {closed.vehicle_number or 'no plate'} — driver {closed.driver_match}",
                    details="Checkout received from Exit PC over the authenticated LAN service.",
                    severity=AuditSeverity.WARNING if closed.mismatched else AuditSeverity.INFO,
                    operator=closed.exit_operator,
                )
                self._reply(200, {"visit": _visit_dict(closed)})
            finally:
                store.close()

        def _etag_event(self, body: dict) -> None:
            required = ("event_id", "timestamp", "controller_name", "door", "direction", "rfid", "kind")
            if any(not body.get(key) for key in required):
                self._reply(400, {"error": "missing_event_fields"})
                return
            try:
                event = ETagEvent(
                    event_id=str(body["event_id"]), timestamp=datetime.fromisoformat(body["timestamp"]),
                    controller_name=str(body["controller_name"]), door=str(body["door"]),
                    direction=str(body["direction"]), rfid=str(body["rfid"]),
                    kind=ETagEventKind(body["kind"]), resident_name=str(body.get("resident_name", "")),
                    vehicle_number=str(body.get("vehicle_number", "")), note=str(body.get("note", "")),
                )
            except (TypeError, ValueError):
                self._reply(400, {"error": "invalid_event"})
                return
            store = Store(database_path)
            try:
                store.etag_events.append(event)
            finally:
                store.close()
            self._reply(200, {"event_id": event.event_id})

    return Handler


def serve(database_path: str | Path, token: str, bind: str = DEFAULT_BIND,
          port: int = DEFAULT_PORT, allowed_client: str = DEFAULT_EXIT_CLIENT) -> None:
    server = ThreadingHTTPServer((bind, port), handler_factory(database_path, token, {allowed_client, "127.0.0.1"}))
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Askari VMS Entry-PC LAN service")
    parser.add_argument("--bind", default=DEFAULT_BIND)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--allow", default=DEFAULT_EXIT_CLIENT)
    parser.add_argument("--database", default=str(default_database_path(default_settings())))
    parser.add_argument("--provision", action="store_true",
                        help="create the LAN secret in Windows Credential Manager")
    args = parser.parse_args()
    if args.provision:
        token = secrets.token_urlsafe(32)
        write_server_token(token, args.bind)
        print(f"Credential created: {credential_target(args.bind)}")
        print("EXIT_PC_TOKEN=" + token)
        print("Copy this token once to the Exit PC, then clear this console.")
        return 0
    try:
        token = read_server_token(args.bind)
    except Exception:
        parser.error(f"Add Windows Generic Credential {credential_target(args.bind)} before starting")
    serve(args.database, token, args.bind, args.port, args.allow)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
