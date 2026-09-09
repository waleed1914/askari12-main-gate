from askari_vms.controllers import DoorCommand, DoorState, apply_simulated_command, default_controllers


def test_default_door_mapping_is_exact() -> None:
    entry, exit_controller = default_controllers()
    assert tuple(door.name for door in entry.doors) == ("Visitor Entry", "E-tag Entry")
    assert tuple(door.name for door in exit_controller.doors) == ("Visitor Exit", "E-tag Exit")
    assert all(controller.doors[0].lane_type == "Visitor" for controller in (entry, exit_controller))
    assert all(controller.doors[1].lane_type == "E-Tag" for controller in (entry, exit_controller))


def test_simulated_commands_update_door_state() -> None:
    door = default_controllers()[0].doors[0]
    assert apply_simulated_command(door, DoorCommand.OPEN).state is DoorState.OPEN
    assert apply_simulated_command(door, DoorCommand.CLOSE).state is DoorState.CLOSED
    assert apply_simulated_command(door, DoorCommand.LOCK).state is DoorState.LOCKED
    assert apply_simulated_command(door, DoorCommand.UNLOCK).state is DoorState.UNLOCKED
