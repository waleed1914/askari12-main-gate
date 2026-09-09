from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field, replace

from askari_vms.controllers import default_controllers

# Camera lanes. E-tag lanes deliberately have no cameras: the controller already
# identifies the holder, so only its events are consumed.
UNASSIGNED = "Unassigned"
VISITOR_ENTRY = "Visitor Entry"
VISITOR_EXIT = "Visitor Exit"
CAMERA_LANES = (UNASSIGNED, VISITOR_ENTRY, VISITOR_EXIT)

ANPR = "ANPR"
DRIVER = "Driver"

# Entry and Exit portals are pinned to their assigned PC; Admin shares the Entry PC.
WORKSTATION_ROLES = ("Admin + Entry", "Exit")

MINIMUM_PURGE_PERCENT = 50
MAXIMUM_PURGE_PERCENT = 95


def is_valid_ip(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value.strip())
    except ValueError:
        return False
    return True


def is_valid_port(value: int) -> bool:
    return 1 <= value <= 65535


@dataclass(frozen=True, slots=True)
class ControllerSettings:
    key: str
    name: str
    ip_address: str = ""
    port: int = 80
    username: str = "admin"
    # The password lives in the OS credential store, never in this record.
    has_password: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.ip_address)


@dataclass(frozen=True, slots=True)
class CameraSettings:
    key: str
    role: str
    model: str
    ip_address: str = ""
    port: int = 37777
    lane: str = UNASSIGNED
    http_port: int = 80
    snapshot_path: str = ""
    anpr_event_path: str = ""

    @property
    def assigned(self) -> bool:
        return self.lane != UNASSIGNED


@dataclass(frozen=True, slots=True)
class PeripheralSettings:
    id_card_camera_index: int = 0
    receipt_printer: str = ""


@dataclass(frozen=True, slots=True)
class StorageSettings:
    data_directory: str = r"C:\AskariVMS\data"
    purge_threshold_percent: int = 80


@dataclass(frozen=True, slots=True)
class BackupSettings:
    destination: str = ""


@dataclass(frozen=True, slots=True)
class AppSettings:
    controllers: tuple[ControllerSettings, ...] = ()
    cameras: tuple[CameraSettings, ...] = ()
    peripherals: PeripheralSettings = field(default_factory=PeripheralSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    backup: BackupSettings = field(default_factory=BackupSettings)
    workstation_role: str = WORKSTATION_ROLES[0]


def default_settings() -> AppSettings:
    """Seeded from the surveyed hardware. Only the Entry controller is known so far."""
    return AppSettings(
        controllers=(
            ControllerSettings("entry", "Entry Controller", "192.168.0.90", 80, "admin", True),
            ControllerSettings("exit", "Exit Controller"),
        ),
        cameras=(
            CameraSettings("anpr_entry", ANPR, "ITC413-PW4D-Z3", "192.168.1.12", 37777, UNASSIGNED),
            CameraSettings("anpr_exit", ANPR, "ITC413-PW4D-Z3", "192.168.1.13", 37777, VISITOR_ENTRY,
                           anpr_event_path="/cgi-bin/snapManager.cgi?action=attachFileProc&Flags%5B0%5D=Event&Events=%5BTrafficJunction%5D&heartbeat=5"),
            CameraSettings("driver_entry", DRIVER, "DS-2CD1653G0-IZS", "192.168.1.16", 8000, VISITOR_ENTRY,
                           snapshot_path="/ISAPI/Streaming/channels/101/picture"),
            CameraSettings("driver_exit", DRIVER, "DH-IPC-HFW2441", "192.168.1.15", 37777, VISITOR_EXIT),
        ),
    )


def validate_settings(settings: AppSettings) -> dict[str, str]:
    """Collect every problem at once so the operator can fix them in one pass."""
    errors: dict[str, str] = {}

    seen_addresses: dict[str, str] = {}
    for controller in settings.controllers:
        if not controller.configured:
            continue  # An unconfigured controller is allowed; it simply stays offline.
        if not is_valid_ip(controller.ip_address):
            errors[f"controller_{controller.key}_ip"] = f"{controller.name}: enter a valid IPv4 address."
        if not is_valid_port(controller.port):
            errors[f"controller_{controller.key}_port"] = f"{controller.name}: port must be between 1 and 65535."
        if not controller.username.strip():
            errors[f"controller_{controller.key}_user"] = f"{controller.name}: a username is required."

    for camera in settings.cameras:
        if camera.anpr_event_path and (not camera.anpr_event_path.startswith("/") or camera.anpr_event_path.startswith("//")):
            errors[f"camera_{camera.key}_anpr_event_path"] = "ANPR event path must be an absolute path on the device."
        if not is_valid_port(camera.http_port):
            errors[f"camera_{camera.key}_http_port"] = "Camera HTTP port must be between 1 and 65535."
        if camera.snapshot_path and (not camera.snapshot_path.startswith("/") or camera.snapshot_path.startswith("//")):
            errors[f"camera_{camera.key}_snapshot_path"] = "Camera snapshot path must be an absolute path on the device."
        if not camera.ip_address:
            if camera.assigned:
                errors[f"camera_{camera.key}_ip"] = f"{camera.model} ({camera.lane}): an IP address is required."
            continue
        if not is_valid_ip(camera.ip_address):
            errors[f"camera_{camera.key}_ip"] = f"{camera.model}: enter a valid IPv4 address."
        if not is_valid_port(camera.port):
            errors[f"camera_{camera.key}_port"] = f"{camera.model}: port must be between 1 and 65535."

    for device in (*settings.controllers, *settings.cameras):
        address = getattr(device, "ip_address", "")
        if not address or not is_valid_ip(address):
            continue
        label = getattr(device, "name", None) or getattr(device, "model", "device")
        if address in seen_addresses:
            errors[f"duplicate_{address}"] = f"{address} is used by both {seen_addresses[address]} and {label}."
        else:
            seen_addresses[address] = label

    # Each visitor lane needs exactly one ANPR and one driver camera.
    for lane in (VISITOR_ENTRY, VISITOR_EXIT):
        for role in (ANPR, DRIVER):
            matches = [c for c in settings.cameras if c.lane == lane and c.role == role]
            if len(matches) > 1:
                errors[f"lane_{lane}_{role}"] = f"{lane} has more than one {role} camera assigned."

    if not settings.storage.data_directory.strip():
        errors["data_directory"] = "A data folder is required."
    if not MINIMUM_PURGE_PERCENT <= settings.storage.purge_threshold_percent <= MAXIMUM_PURGE_PERCENT:
        errors["purge_threshold"] = f"Disk threshold must be between {MINIMUM_PURGE_PERCENT}% and {MAXIMUM_PURGE_PERCENT}%."
    if settings.workstation_role not in WORKSTATION_ROLES:
        errors["workstation_role"] = "Choose a workstation role."
    return errors


def unassigned_lanes(settings: AppSettings) -> tuple[str, ...]:
    """Visitor lanes still missing an ANPR or driver camera, for a non-blocking warning."""
    missing: list[str] = []
    for lane in (VISITOR_ENTRY, VISITOR_EXIT):
        roles = {camera.role for camera in settings.cameras if camera.lane == lane and camera.ip_address}
        for role in (ANPR, DRIVER):
            if role not in roles:
                missing.append(f"{lane} has no {role} camera")
    return tuple(missing)


def describe_controller(controller: ControllerSettings) -> str:
    if not controller.configured:
        return "Not configured"
    return f"{controller.ip_address}:{controller.port}"


def with_controller(settings: AppSettings, updated: ControllerSettings) -> AppSettings:
    controllers = tuple(updated if c.key == updated.key else c for c in settings.controllers)
    return replace(settings, controllers=controllers)


def with_camera(settings: AppSettings, updated: CameraSettings) -> AppSettings:
    cameras = tuple(updated if c.key == updated.key else c for c in settings.cameras)
    return replace(settings, cameras=cameras)


def etag_door_name(key: str) -> str:
    """Door 2 is always the e-tag lane on both controllers."""
    for controller in default_controllers():
        if controller.key == key:
            return controller.doors[1].name
    return "E-Tag door"


def controller_choices(settings: AppSettings) -> tuple[tuple[str, str], ...]:
    """(key, label) pairs for the Allowed Controllers picker on the E-Tag form."""
    choices = []
    for controller in settings.controllers:
        address = controller.ip_address if controller.configured else "not configured"
        choices.append((controller.key, f"{controller.name} — {address} ({etag_door_name(controller.key)})"))
    return tuple(choices)
