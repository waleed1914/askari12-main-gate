from askari_vms.controllers import DoorCommand, DoorState, apply_simulated_command, default_controllers


def test_default_door_mapping_is_exact() -> None:
    entry, exit_controller = default_controllers()
    assert tuple(door.name for door in entry.doors) == ("E-tag Entry", "Visitor Entry")
    assert tuple(door.name for door in exit_controller.doors) == ("Visitor Exit", "E-tag Exit")
    assert entry.doors[0].lane_type == "E-Tag" and entry.doors[1].lane_type == "Visitor"
    assert exit_controller.doors[0].lane_type == "Visitor" and exit_controller.doors[1].lane_type == "E-Tag"


def test_simulated_commands_update_door_state() -> None:
    door = default_controllers()[0].doors[0]
    assert apply_simulated_command(door, DoorCommand.OPEN).state is DoorState.OPEN
    assert apply_simulated_command(door, DoorCommand.CLOSE).state is DoorState.CLOSED
    assert apply_simulated_command(door, DoorCommand.LOCK).state is DoorState.LOCKED
    assert apply_simulated_command(door, DoorCommand.UNLOCK).state is DoorState.UNLOCKED
