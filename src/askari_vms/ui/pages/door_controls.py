from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from askari_vms.audit import AuditLog
from askari_vms.controllers import Controller, Door, DoorCommand, DoorState, apply_simulated_command, default_controllers


class DoorPanel(QFrame):
    def __init__(self, controller_index: int, door_index: int, door: Door, command_handler: callable) -> None:
        super().__init__()
        self.setObjectName("doorPanel")
        self.controller_index = controller_index
        self.door_index = door_index
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        heading = QHBoxLayout()
        name_box = QVBoxLayout()
        name = QLabel(door.name)
        name.setProperty("section", "true")
        detail = QLabel(f"Door {door.number}  •  {door.lane_type} lane")
        detail.setProperty("muted", "true")
        name_box.addWidget(name)
        name_box.addWidget(detail)
        heading.addLayout(name_box)
        heading.addStretch()
        self.state = QLabel(door.state.value)
        self._set_state(door.state)
        heading.addWidget(self.state)
        layout.addLayout(heading)

        actions = QHBoxLayout()
        for command in DoorCommand:
            button = QPushButton(command.value)
            button.setMinimumHeight(36)
            if command is DoorCommand.OPEN:
                button.setObjectName("primaryButton")
            button.clicked.connect(lambda checked=False, cmd=command: command_handler(controller_index, door_index, cmd))
            actions.addWidget(button)
        layout.addLayout(actions)

    def _set_state(self, state: DoorState) -> None:
        self.state.setText(state.value)
        self.state.setProperty("doorState", state.value.casefold())
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)

    def update_door(self, door: Door) -> None:
        self._set_state(door.state)


class ControllerPanel(QFrame):
    def __init__(self, controller_index: int, controller: Controller, command_handler: callable) -> None:
        super().__init__()
        self.setProperty("card", True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        names = QVBoxLayout()
        title = QLabel(controller.name)
        title.setProperty("section", "true")
        address = QLabel(f"IP: {controller.ip_address}")
        address.setProperty("muted", "true")
        names.addWidget(title)
        names.addWidget(address)
        heading.addLayout(names)
        heading.addStretch()
        status = QLabel("Online" if controller.connected else "Simulation")
        status.setProperty("status", "online" if controller.connected else "warning")
        heading.addWidget(status)
        layout.addLayout(heading)
        self.door_panels: list[DoorPanel] = []
        for door_index, door in enumerate(controller.doors):
            panel = DoorPanel(controller_index, door_index, door, command_handler)
            self.door_panels.append(panel)
            layout.addWidget(panel)


class DoorControlsPage(QWidget):
    def __init__(self, audit_log: AuditLog | None = None) -> None:
        super().__init__()
        self._audit = audit_log if audit_log is not None else AuditLog()
        self.controllers = list(default_controllers())
        self.history: list[tuple[datetime, str, str, str, str]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)

        title_row = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Door Controls")
        title.setProperty("section", "true")
        subtitle = QLabel("Manual control and live state for both two-door controllers")
        subtitle.setProperty("muted", "true")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        title_row.addLayout(titles)
        title_row.addStretch()
        simulation = QLabel("HARDWARE DISABLED — SIMULATION MODE")
        simulation.setProperty("status", "warning")
        title_row.addWidget(simulation)
        layout.addLayout(title_row)

        self.feedback = QLabel("Commands made here are simulated and will not operate physical gates.")
        self.feedback.setObjectName("infoBanner")
        layout.addWidget(self.feedback)

        controller_grid = QGridLayout()
        controller_grid.setHorizontalSpacing(14)
        self.controller_panels: list[ControllerPanel] = []
        for index, controller in enumerate(self.controllers):
            panel = ControllerPanel(index, controller, self.issue_command)
            self.controller_panels.append(panel)
            controller_grid.addWidget(panel, 0, index)
        controller_grid.setColumnStretch(0, 1)
        controller_grid.setColumnStretch(1, 1)
        layout.addLayout(controller_grid)

        activity_title = QHBoxLayout()
        activity = QLabel("Command activity")
        activity.setProperty("section", "true")
        activity_title.addWidget(activity)
        activity_title.addStretch()
        clear = QPushButton("Clear screen history")
        clear.clicked.connect(self.clear_history)
        activity_title.addWidget(clear)
        layout.addLayout(activity_title)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("Time", "Controller", "Door", "Command", "Result"))
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

    def issue_command(self, controller_index: int, door_index: int, command: DoorCommand) -> None:
        controller = self.controllers[controller_index]
        door = controller.doors[door_index]
        updated_door = apply_simulated_command(door, command)
        doors = list(controller.doors)
        doors[door_index] = updated_door
        self.controllers[controller_index] = replace(controller, doors=tuple(doors))
        self.controller_panels[controller_index].door_panels[door_index].update_door(updated_door)
        timestamp = datetime.now().replace(microsecond=0)
        self.history.insert(0, (timestamp, controller.name, door.name, command.value, "Simulated successfully"))
        # Every manual gate command is audited, even in simulation.
        self._audit.record(
            action="Gate command",
            target=door.name,
            summary=f"{command.value} sent to {door.name}",
            details=(
                f"Manual {command.value.casefold()} command issued from Door Controls for "
                f"{door.name} (Door {door.number}, {door.lane_type} lane) on {controller.name}. "
                "Hardware is in simulation mode, so no physical command was transmitted."
            ),
            timestamp=timestamp,
        )
        self.feedback.setText(f"{timestamp:%H:%M:%S} — {command.value} sent to {door.name}. No physical command was transmitted.")
        self._refresh_history()

    def _refresh_history(self) -> None:
        self.table.setRowCount(len(self.history))
        for row, (timestamp, controller, door, command, result) in enumerate(self.history):
            for column, value in enumerate((timestamp.strftime("%d %b %Y  %H:%M:%S"), controller, door, command, result)):
                self.table.setItem(row, column, QTableWidgetItem(value))

    def clear_history(self) -> None:
        self.history.clear()
        self._refresh_history()
        self.feedback.setText("On-screen simulation history cleared. Permanent audit records will never be cleared here.")

