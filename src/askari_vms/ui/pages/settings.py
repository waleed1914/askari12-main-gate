from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from askari_vms.audit import AuditLog
from askari_vms.settings import (
    CAMERA_LANES,
    MAXIMUM_PURGE_PERCENT,
    MINIMUM_PURGE_PERCENT,
    WORKSTATION_ROLES,
    AppSettings,
    BackupSettings,
    PeripheralSettings,
    StorageSettings,
    default_settings,
    describe_controller,
    unassigned_lanes,
    validate_settings,
)


class SettingsPage(QScrollArea):
    """Gates, controllers, cameras, peripherals, storage, backup and workstation role."""

    def __init__(self, audit_log: AuditLog | None = None, settings: AppSettings | None = None,
                 on_saved: Callable[[AppSettings], None] | None = None,
                 repository: object | None = None) -> None:
        super().__init__()
        self._audit = audit_log if audit_log is not None else AuditLog()
        self._on_saved = on_saved
        self._repository = repository
        self.settings = settings if settings is not None else default_settings()
        self._controller_fields: dict[str, dict[str, QWidget]] = {}
        self._camera_fields: dict[str, dict[str, QWidget]] = {}

        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)

        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("Settings")
        title.setProperty("section", "true")
        subtitle = QLabel("Gates, controllers, cameras, peripherals, storage, backup, and workstation role")
        subtitle.setProperty("muted", "true")
        header.addWidget(title)
        header.addWidget(subtitle)
        layout.addLayout(header)

        self.banner = QLabel(
            "Entry driver preview and ANPR plate detection connect using their configured endpoints "
            "and Windows credentials. Controller commands remain simulated. "
            "Restart the portal after saving camera changes."
        )
        self.banner.setObjectName("infoBanner")
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)

        layout.addWidget(self._controllers_card())
        layout.addWidget(self._cameras_card())
        layout.addWidget(self._peripherals_card())
        layout.addWidget(self._storage_card())
        layout.addWidget(self._workstation_card())

        footer = QHBoxLayout()
        self.message = QLabel()
        self.message.setObjectName("formError")
        self.message.setWordWrap(True)
        self.message.hide()
        footer.addWidget(self.message, 1)
        revert = QPushButton("Revert changes")
        revert.clicked.connect(self._revert)
        footer.addWidget(revert)
        self.save_button = QPushButton("Save settings")
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self.save)
        footer.addWidget(self.save_button)
        layout.addLayout(footer)
        layout.addStretch()

        self.setWidget(body)
        self._populate()

    # ---------- construction helpers ----------

    @staticmethod
    def _card(title: str, hint: str = "") -> tuple[QFrame, QGridLayout]:
        card = QFrame()
        card.setProperty("card", True)
        grid = QGridLayout(card)
        grid.setContentsMargins(20, 18, 20, 20)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)
        for column in range(4):
            grid.setColumnStretch(column, 1)
        heading = QLabel(title)
        heading.setProperty("section", "true")
        grid.addWidget(heading, 0, 0, 1, 4)
        if hint:
            note = QLabel(hint)
            note.setProperty("muted", "true")
            note.setWordWrap(True)
            grid.addWidget(note, 1, 0, 1, 4)
        return card, grid

    @staticmethod
    def _field(layout: QGridLayout, row: int, column: int, label: str, widget: QWidget) -> None:
        box = QVBoxLayout()
        box.setSpacing(4)
        caption = QLabel(label)
        box.addWidget(caption)
        box.addWidget(widget)
        layout.addLayout(box, row, column)

    @staticmethod
    def _line(placeholder: str = "") -> QLineEdit:
        widget = QLineEdit()
        widget.setPlaceholderText(placeholder)
        widget.setMinimumHeight(36)
        return widget

    @staticmethod
    def _port() -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(1, 65535)
        widget.setMinimumHeight(36)
        return widget

    # ---------- cards ----------

    def _controllers_card(self) -> QFrame:
        card, grid = self._card(
            "Door controllers",
            "Two two-door controllers. Door 1 is the visitor lane and Door 2 the e-tag lane on each. "
            "Credentials are sent over plain HTTP, so keep controllers on an isolated LAN.",
        )
        row = 2
        for controller in self.settings.controllers:
            name = QLabel(controller.name)
            name.setProperty("section", "true")
            grid.addWidget(name, row, 0, 1, 2)
            status = QLabel(describe_controller(controller))
            status.setProperty("status", "online" if controller.configured else "warning")
            grid.addWidget(status, row, 3, alignment=Qt.AlignmentFlag.AlignRight)

            fields = {
                "ip_address": self._line("192.168.0.90"),
                "port": self._port(),
                "username": self._line("admin"),
                "password": self._line("Stored in Windows Credential Manager"),
            }
            fields["password"].setEchoMode(QLineEdit.EchoMode.Password)
            self._field(grid, row + 1, 0, "IP Address", fields["ip_address"])
            self._field(grid, row + 1, 1, "Port", fields["port"])
            self._field(grid, row + 1, 2, "Username", fields["username"])
            self._field(grid, row + 1, 3, "Password", fields["password"])

            doors = QLabel(f"Door 1: Visitor  •  Door 2: E-Tag  —  {controller.name.split()[0]} side")
            doors.setProperty("muted", "true")
            grid.addWidget(doors, row + 2, 0, 1, 3)
            test = QPushButton("Test connection")
            test.clicked.connect(lambda _=False, key=controller.key: self._test_connection(key))
            grid.addWidget(test, row + 2, 3)

            fields["status"] = status
            self._controller_fields[controller.key] = fields
            row += 3
        return card

    def _cameras_card(self) -> QFrame:
        card, grid = self._card(
            "Cameras",
            "ANPR and driver cameras serve the visitor lanes only. E-tag lanes need no cameras — "
            "the controller already identifies the tag holder.",
        )
        row = 2
        for camera in self.settings.cameras:
            label = QLabel(f"{camera.role} — {camera.model}")
            label.setProperty("section", "true")
            grid.addWidget(label, row, 0, 1, 4)

            fields = {
                "ip_address": self._line("192.168.1.12"),
                "port": self._port(),
                "lane": QComboBox(),
                "http_port": self._port(),
                "snapshot_path": self._line("Snapshot path (blank disables preview)"),
                "anpr_event_path": self._line("ANPR event path (blank disables detection)"),
            }
            fields["lane"].addItems(CAMERA_LANES)
            fields["lane"].setMinimumHeight(36)
            self._field(grid, row + 1, 0, "IP Address", fields["ip_address"])
            self._field(grid, row + 1, 1, "SDK port", fields["port"])
            self._field(grid, row + 1, 2, "Assigned lane", fields["lane"])
            self._field(grid, row + 2, 0, "HTTP port", fields["http_port"])
            self._field(grid, row + 2, 1, "Snapshot path", fields["snapshot_path"])
            self._field(grid, row + 2, 2, "ANPR event path", fields["anpr_event_path"])
            self._camera_fields[camera.key] = fields
            row += 3
        return card

    def _peripherals_card(self) -> QFrame:
        card, grid = self._card(
            "Peripherals",
            "The barcode scanner emulates a keyboard and needs no configuration. "
            "Exit has no receipt printer by design.",
        )
        self.id_camera_index = QSpinBox()
        self.id_camera_index.setRange(0, 9)
        self.id_camera_index.setMinimumHeight(36)
        self.printer = self._line("Receipt printer name (Entry only)")
        self._field(grid, 2, 0, "ID card camera (USB device index)", self.id_camera_index)
        self._field(grid, 2, 1, "Receipt printer", self.printer)
        return card

    def _storage_card(self) -> QFrame:
        card, grid = self._card(
            "Storage and backup",
            "All records live on the Entry PC. When the disk passes the threshold the oldest "
            "images are removed first; database records and audit logs are always kept.",
        )
        self.data_directory = self._line(r"C:\AskariVMS\data")
        self.purge_threshold = QSpinBox()
        self.purge_threshold.setRange(MINIMUM_PURGE_PERCENT, MAXIMUM_PURGE_PERCENT)
        self.purge_threshold.setSuffix(" %")
        self.purge_threshold.setMinimumHeight(36)
        self.backup_destination = self._line("USB drive or LAN path")
        self._field(grid, 2, 0, "Data folder", self.data_directory)
        self._field(grid, 2, 1, "Purge images above", self.purge_threshold)
        self._field(grid, 2, 2, "Backup destination", self.backup_destination)
        return card

    def _workstation_card(self) -> QFrame:
        card, grid = self._card(
            "Workstation",
            "Entry and Exit portals are restricted to their assigned PC. The portal an operator "
            "gets is decided by this setting, not by their account.",
        )
        self.workstation_role = QComboBox()
        self.workstation_role.addItems(WORKSTATION_ROLES)
        self.workstation_role.setMinimumHeight(36)
        self._field(grid, 2, 0, "This computer", self.workstation_role)
        return card

    # ---------- state ----------

    def _populate(self) -> None:
        for controller in self.settings.controllers:
            fields = self._controller_fields[controller.key]
            fields["ip_address"].setText(controller.ip_address)
            fields["port"].setValue(controller.port)
            fields["username"].setText(controller.username)
            fields["password"].setText("")
            fields["password"].setPlaceholderText(
                "Unchanged — leave blank to keep" if controller.has_password else "Not set"
            )
        for camera in self.settings.cameras:
            fields = self._camera_fields[camera.key]
            fields["ip_address"].setText(camera.ip_address)
            fields["port"].setValue(camera.port)
            fields["lane"].setCurrentText(camera.lane)
            fields["http_port"].setValue(camera.http_port)
            fields["snapshot_path"].setText(camera.snapshot_path)
            fields["anpr_event_path"].setText(camera.anpr_event_path)
        self.id_camera_index.setValue(self.settings.peripherals.id_card_camera_index)
        self.printer.setText(self.settings.peripherals.receipt_printer)
        self.data_directory.setText(self.settings.storage.data_directory)
        self.purge_threshold.setValue(self.settings.storage.purge_threshold_percent)
        self.backup_destination.setText(self.settings.backup.destination)
        self.workstation_role.setCurrentText(self.settings.workstation_role)

    def value(self) -> AppSettings:
        controllers = []
        for controller in self.settings.controllers:
            fields = self._controller_fields[controller.key]
            typed = fields["password"].text()
            controllers.append(replace(
                controller,
                ip_address=fields["ip_address"].text().strip(),
                port=fields["port"].value(),
                username=fields["username"].text().strip(),
                has_password=controller.has_password or bool(typed),
            ))
        cameras = []
        for camera in self.settings.cameras:
            fields = self._camera_fields[camera.key]
            cameras.append(replace(
                camera,
                ip_address=fields["ip_address"].text().strip(),
                port=fields["port"].value(),
                lane=fields["lane"].currentText(),
                http_port=fields["http_port"].value(),
                snapshot_path=fields["snapshot_path"].text().strip(),
                anpr_event_path=fields["anpr_event_path"].text().strip(),
            ))
        return replace(
            self.settings,
            controllers=tuple(controllers),
            cameras=tuple(cameras),
            peripherals=PeripheralSettings(self.id_camera_index.value(), self.printer.text().strip()),
            storage=StorageSettings(self.data_directory.text().strip(), self.purge_threshold.value()),
            backup=BackupSettings(self.backup_destination.text().strip()),
            workstation_role=self.workstation_role.currentText(),
        )

    def save(self) -> bool:
        candidate = self.value()
        errors = validate_settings(candidate)
        if errors:
            self._show_message("  •  ".join(errors.values()), error=True)
            return False
        self.settings = candidate
        if self._repository is not None:
            self._repository.save(candidate)
        self._audit.record(
            action="Record updated",
            target="Settings",
            summary="System settings saved",
            details=(
                "Controllers: "
                + ", ".join(f"{c.name} {describe_controller(c)}" for c in candidate.controllers)
                + f". Workstation role: {candidate.workstation_role}. "
                + f"Images purged above {candidate.storage.purge_threshold_percent}%."
            ),
        )
        for controller in self.settings.controllers:
            status = self._controller_fields[controller.key]["status"]
            status.setText(describe_controller(controller))
            status.setProperty("status", "online" if controller.configured else "warning")
            status.style().unpolish(status)
            status.style().polish(status)
        self._populate()
        warnings = unassigned_lanes(candidate)
        note = "Settings saved."
        if warnings:
            note += "  Still to configure: " + "; ".join(warnings) + "."
        self._show_message(note, error=False)
        if self._on_saved is not None:
            self._on_saved(self.settings)
        return True

    def _revert(self) -> None:
        self._populate()
        self._show_message("Reverted to the last saved settings.", error=False)

    def _test_connection(self, key: str) -> None:
        controller = next(c for c in self.settings.controllers if c.key == key)
        fields = self._controller_fields[key]
        address = fields["ip_address"].text().strip()
        if not address:
            self._show_message(f"{controller.name}: enter an IP address before testing.", error=True)
            return
        self._show_message(
            f"{controller.name} at {address} — simulated check only. Reachability testing "
            "starts working when the controller adapter is enabled.",
            error=False,
        )

    def _show_message(self, text: str, error: bool) -> None:
        self.message.setText(text)
        self.message.setObjectName("formError" if error else "infoBanner")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.show()
