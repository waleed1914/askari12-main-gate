"""Physical two-door controller adapter for the isolated gate LAN."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Protocol
import urllib.error
import urllib.parse
import urllib.request

from askari_vms.controllers import DoorCommand
from askari_vms.settings import AppSettings


class GateControllerError(RuntimeError):
    """A safe hardware error that never contains controller credentials."""


class GateController(Protocol):
    def command(self, door_index: int, command: DoorCommand) -> None: ...


COMMAND_CODES = {
    DoorCommand.OPEN: 1,
    DoorCommand.CLOSE: 0,
    DoorCommand.LOCK: 6,
    DoorCommand.UNLOCK: 7,
}


def credential_target(key: str, address: str) -> str:
    return f"AskariVMS/controller/{key}/{address}"


def read_credentials(key: str, address: str) -> tuple[str, str]:
    import win32cred

    record = win32cred.CredRead(credential_target(key, address), win32cred.CRED_TYPE_GENERIC)
    blob = record["CredentialBlob"]
    password = blob.decode("utf-16-le") if isinstance(blob, bytes) else blob
    return record["UserName"], password


@dataclass(frozen=True, slots=True)
class HttpGateController:
    key: str
    address: str
    port: int = 80
    timeout: float = 2.0

    def command(self, door_index: int, command: DoorCommand) -> None:
        if door_index not in (0, 1):
            raise ValueError("controller door index must be 0 or 1")
        try:
            username, password = read_credentials(self.key, self.address)
        except Exception:
            raise GateControllerError(
                "Controller credentials unavailable in Windows Credential Manager."
            ) from None

        query = urllib.parse.urlencode({"open": COMMAND_CODES[command], "door": door_index})
        url = f"http://{self.address}:{self.port}/cdor.cgi?{query}"
        token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.timeout) as response:
                response.read(4096)
        except urllib.error.HTTPError as exc:
            raise GateControllerError(f"Controller rejected the command (HTTP {exc.code}).") from None
        except Exception:
            raise GateControllerError("Controller unavailable. Open the gate manually.") from None


def entry_gate_controller(settings: AppSettings) -> HttpGateController | None:
    configured = next(
        (item for item in settings.controllers if item.key == "entry" and item.configured),
        None,
    )
    if configured is None:
        return None
    return HttpGateController(configured.key, configured.ip_address, configured.port)


def exit_gate_controller(settings: AppSettings) -> HttpGateController | None:
    configured = next(
        (item for item in settings.controllers if item.key == "exit" and item.configured),
        None,
    )
    if configured is None:
        return None
    return HttpGateController(configured.key, configured.ip_address, configured.port)
