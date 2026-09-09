from dataclasses import replace

from askari_vms.settings import (
    ANPR,
    UNASSIGNED,
    VISITOR_ENTRY,
    VISITOR_EXIT,
    AppSettings,
    CameraSettings,
    ControllerSettings,
    StorageSettings,
    default_settings,
    describe_controller,
    is_valid_ip,
    is_valid_port,
    unassigned_lanes,
    validate_settings,
    with_camera,
    with_controller,
)


def test_defaults_match_the_surveyed_hardware() -> None:
    settings = default_settings()
    entry, exit_controller = settings.controllers
    assert entry.ip_address == "192.168.0.90"
    assert entry.configured and not exit_controller.configured
    assert describe_controller(exit_controller) == "Not configured"

    assert len(settings.cameras) == 4
    assert {camera.role for camera in settings.cameras} == {ANPR, "Driver"}
    assert next(camera for camera in settings.cameras if camera.key == "driver_entry").snapshot_path == "/ISAPI/Streaming/channels/101/picture"
    assert all(camera.port == 37777 for camera in settings.cameras if camera.key != "driver_entry")
    # Every visitor lane is covered; e-tag lanes are deliberately absent.
    assert {camera.lane for camera in settings.cameras} == {VISITOR_ENTRY, VISITOR_EXIT, UNASSIGNED}
    assert not validate_settings(settings)
    assert unassigned_lanes(settings) == ("Visitor Exit has no ANPR camera",)


def test_ip_and_port_validation() -> None:
    assert is_valid_ip("192.168.0.90")
    assert is_valid_ip("  10.0.0.1  ")
    for bad in ("", "192.168.0", "192.168.0.256", "abc", "192.168.0.1/24"):
        assert not is_valid_ip(bad), bad
    assert is_valid_port(1) and is_valid_port(65535)
    assert not is_valid_port(0) and not is_valid_port(70000)


def test_unconfigured_controller_is_allowed_but_a_bad_address_is_not() -> None:
    settings = default_settings()
    assert not validate_settings(settings)

    broken = with_controller(settings, replace(settings.controllers[0], ip_address="192.168.0.999"))
    assert any("valid IPv4" in message for message in validate_settings(broken).values())

    nameless = with_controller(settings, replace(settings.controllers[0], username="  "))
    assert any("username" in message for message in validate_settings(nameless).values())


def test_two_devices_cannot_share_an_address() -> None:
    settings = default_settings()
    clash = with_camera(settings, replace(settings.cameras[0], ip_address="192.168.0.90"))
    errors = validate_settings(clash)
    assert any("used by both" in message for message in errors.values())


def test_a_lane_cannot_hold_two_cameras_of_the_same_role() -> None:
    settings = default_settings()
    doubled = with_camera(settings, replace(settings.cameras[0], lane=VISITOR_ENTRY))
    errors = validate_settings(doubled)
    assert any("more than one ANPR" in message for message in errors.values())


def test_assigned_camera_requires_an_address() -> None:
    settings = default_settings()
    blank = with_camera(settings, replace(settings.cameras[1], ip_address=""))
    assert any("IP address is required" in message for message in validate_settings(blank).values())

    # Unassigning it makes the blank address acceptable again.
    parked = with_camera(blank, replace(blank.cameras[1], lane=UNASSIGNED))
    assert not any("IP address is required" in message for message in validate_settings(parked).values())
    assert "Visitor Entry has no ANPR camera" in unassigned_lanes(parked)


def test_storage_and_workstation_bounds() -> None:
    settings = default_settings()
    assert any("data folder" in m.casefold() for m in validate_settings(replace(settings, storage=StorageSettings("   ", 80))).values())
    assert any("threshold" in m.casefold() for m in validate_settings(replace(settings, storage=StorageSettings("C:/data", 20))).values())
    assert any("threshold" in m.casefold() for m in validate_settings(replace(settings, storage=StorageSettings("C:/data", 99))).values())
    assert any("workstation" in m.casefold() for m in validate_settings(replace(settings, workstation_role="Kiosk")).values())


def test_empty_settings_do_not_crash_validation() -> None:
    assert isinstance(validate_settings(AppSettings()), dict)
    assert not validate_settings(AppSettings(
        controllers=(ControllerSettings("entry", "Entry Controller"),),
        cameras=(CameraSettings("c", ANPR, "model"),),
    ))
