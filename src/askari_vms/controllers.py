from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class DoorState(StrEnum):
    CLOSED = "Closed"
    OPEN = "Open"
    LOCKED = "Locked"
    UNLOCKED = "Unlocked"


class DoorCommand(StrEnum):
    OPEN = "Open"
    CLOSE = "Close"
    LOCK = "Lock"
    UNLOCK = "Unlock"


@dataclass(frozen=True, slots=True)
class Door:
    controller_key: str
    number: int
    name: str
    lane_type: str
    state: DoorState = DoorState.CLOSED


@dataclass(frozen=True, slots=True)
class Controller:
    key: str
    name: str
    ip_address: str
    connected: bool
    doors: tuple[Door, Door]


def apply_simulated_command(door: Door, command: DoorCommand) -> Door:
    states = {
        DoorCommand.OPEN: DoorState.OPEN,
        DoorCommand.CLOSE: DoorState.CLOSED,
        DoorCommand.LOCK: DoorState.LOCKED,
        DoorCommand.UNLOCK: DoorState.UNLOCKED,
    }
    return replace(door, state=states[command])


def default_controllers() -> tuple[Controller, Controller]:
    return (
        Controller(
            "entry", "Entry Controller", "Not configured", False,
            (Door("entry", 1, "Visitor Entry", "Visitor"), Door("entry", 2, "E-tag Entry", "E-Tag")),
        ),
        Controller(
            "exit", "Exit Controller", "Not configured", False,
            (Door("exit", 1, "Visitor Exit", "Visitor"), Door("exit", 2, "E-tag Exit", "E-Tag")),
        ),
    )

