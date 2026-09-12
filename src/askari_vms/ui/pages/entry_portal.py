"""Entry portal — the screen the gate operator works on all shift.

Keyboard-first and arranged to match the operator's existing screen: the fields run in
one column in the order they are filled, the CNIC capture sits beside them, and the live
controller feed runs along the bottom.

Nothing here blocks the operator. Every capture step may fail, every field may be left
blank, and the gate still opens. What is missing is recorded, not prevented.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QEvent, QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QBrush, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtMultimedia import (
    QCamera, QCameraDevice, QMediaCaptureSession, QMediaDevices, QVideoFrame, QVideoSink,
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from askari_vms.audit import AuditLog, AuditSeverity
from askari_vms.auth import Session
from askari_vms.camera_link import NO_DEVICE, UNPLUGGED, CameraLink, LinkState
from askari_vms.ui.brand import circular_logo
from askari_vms.categories import VehicleCategory, by_shortcut
from askari_vms.cnic_ocr import CNICCapture, CNICReader, CNICReadError
from askari_vms.etag_events import ETagEvent
from askari_vms.ip_camera import SnapshotFeed
from askari_vms.printing import NullPrinter, PrinterError, ReceiptPrinter, print_receipt
from askari_vms.speech import OfflineDictation, SpeechError
from askari_vms.anpr import ANPRFeed, normalize_plate
from askari_vms.ui.event_styles import ROW_COLOURS, TEXT_COLOURS
from askari_vms.ui.tables import ProportionalColumns, fit_height_to_rows
from askari_vms.visits import (
    VisitRecord,
    active_visits_for_vehicle,
    missing_fields,
    new_barcode,
    next_visit_id,
    previous_visit,
)

DICTATE_KEY = "Ctrl+D"
SUBMIT_KEY = "Ctrl+Return"
CLEAR_KEY = "Ctrl+N"
CAPTURE_KEY = "Ctrl+K"

# How often the camera watchdog checks for a stalled feed. Twice a second is well below
# the stall timeout and costs nothing.
CAMERA_TICK_MS = 500
LIVE_PROMPT = "Live — place ID card flat, then press Capture"

# In the operator's order of use: ANPR fills the plate, so the first thing they type or
# speak is the destination, then the OCR result is corrected downwards.
FIELDS = (
    ("vehicle_number", "Vehicle Number"),
    ("destination", "Destination"),
    ("visitor_name", "Full Name"),
    ("father_name", "Father/Husband Name"),
    ("cnic", "CNIC"),
    ("date_of_birth", "DoB"),
    ("cnic_issue_date", "CNIC Issue Date"),
    ("cnic_expiry_date", "CNIC Expiry Date"),
    ("mobile", "Contact"),
)

HEADER_LOGO_SIZE = 40

STREAMS = (
    ("anpr", "ANPR — Visitor Entry"),
    ("driver", "Driver camera — Visitor Entry"),
)
# Readable names for the audit trail; the dict keys are not for humans.
CAPTURE_LABELS = {
    "cnic": "ID card",
    "anpr": "ANPR overview",
    "plate": "Plate crop",
    "driver": "Driver camera",
}
EVENT_COLUMNS = ("Type", "Etag", "Owner Name", "Car Number", "Time", "Expiry Date", "Status")
EVENT_WEIGHTS = (8, 14, 20, 15, 20, 14, 9)


class _CNICCaptureWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, reader: CNICReader, image_path: str = "") -> None:
        super().__init__()
        self.reader = reader
        self.image_path = image_path

    @Slot()
    def run(self) -> None:
        try:
            if self.image_path and hasattr(self.reader, "read_image"):
                self.completed.emit(self.reader.read_image(self.image_path))
            else:
                self.completed.emit(self.reader.capture_and_read())
        except Exception as exc:  # hardware errors must return control to the operator
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class _DictationWorker(QObject):
    completed = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, adapter: OfflineDictation, audio: bytes) -> None:
        super().__init__()
        self.adapter, self.audio = adapter, audio

    @Slot()
    def run(self) -> None:
        try:
            self.completed.emit(self.adapter.transcribe(self.audio))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class CameraPreview(QLabel):
    """Keep the original image and fit it to the available space without cropping."""

    def __init__(self) -> None:
        super().__init__()
        self._source = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(80, 90)

    def setPixmap(self, pixmap: QPixmap) -> None:
        self._source = pixmap
        self._fit()

    def _fit(self) -> None:
        if not self._source.isNull():
            super().setPixmap(self._source.scaled(
                self.contentsRect().size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def clear(self) -> None:
        self._source = QPixmap()
        super().clear()


class StreamPanel(QFrame):
    """A live camera view. The feed arrives when the camera adapter is enabled."""

    def __init__(self, title: str, compact: bool = False) -> None:
        super().__init__()
        self.setObjectName("capturePanel")
        self.setMinimumHeight(0 if compact else 150)
        layout = QHBoxLayout(self) if compact else QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        name = QLabel(title)
        name.setObjectName("captureTitle")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state = QLabel("No feed")
        self.state.setProperty("muted", "true")
        self.state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state.setWordWrap(True)
        self.preview = CameraPreview()
        self.preview.hide()
        layout.addWidget(name)
        layout.addWidget(self.preview, 1)
        layout.addWidget(self.state)

    def set_state(self, text: str, live: bool = False) -> None:
        self.state.setText(text)
        self.setProperty("captured", "true" if live else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_image(self, path: str) -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.preview.hide()
            return
        self.preview.setPixmap(pixmap)
        self.preview.show()

    def clear_image(self) -> None:
        self.preview.clear()
        self.preview.hide()


class EntryPortalWindow(QWidget):
    """One visitor transaction at a time, with the controller feed underneath."""

    submitted = Signal(object)
    signed_out = Signal()

    def __init__(
        self,
        audit_log: AuditLog | None = None,
        categories: Sequence[VehicleCategory] | None = None,
        visits: Sequence[VisitRecord] | None = None,
        session: Session | None = None,
        on_submit: Callable[[VisitRecord], None] | None = None,
        events: Sequence[ETagEvent] | None = None,
        gate: str = "C1 - Entry",
        cnic_reader: CNICReader | None = None,
        driver_camera: SnapshotFeed | None = None,
        image_directory: str = "",
        anpr_feed: ANPRFeed | None = None,
        printer: ReceiptPrinter | None = None,
        dictation: OfflineDictation | None = None,
    ) -> None:
        super().__init__()
        self._audit = audit_log if audit_log is not None else AuditLog()
        self._categories = list(categories or [])
        self._visits = list(visits or [])
        self._session = session
        self._on_submit = on_submit
        self._events = list(events or [])
        self._gate = gate
        self._cnic_reader = cnic_reader
        self._driver_camera = driver_camera
        self._anpr_feed = anpr_feed
        self._printer = printer if printer is not None else NullPrinter()
        self._dictation = dictation
        self._dictation_thread: QThread | None = None
        self._dictation_worker: _DictationWorker | None = None
        self._anpr_timer: QTimer | None = None
        self._anpr_connected: bool | None = None
        self._anpr_after = time.monotonic()
        self._last_anpr_plate = ""
        self._image_directory = image_directory
        self._driver_image = ""
        self._driver_after = time.monotonic()
        self._driver_displayed_at = 0.0
        self._driver_available: bool | None = None
        self._driver_timer: QTimer | None = None
        self._selected: VehicleCategory | None = None
        self._captured: dict[str, bool] = {"cnic": False, "anpr": False, "driver": False, "plate": False}
        self._cnic_image = ""
        self._reusable: VisitRecord | None = None
        self._dictating = False
        self._category_workflow_pending = False
        self._capture_thread: QThread | None = None
        self._capture_worker: _CNICCaptureWorker | None = None
        self._id_camera: QCamera | None = None
        self._id_camera_session: QMediaCaptureSession | None = None
        self._id_video_sink: QVideoSink | None = None
        self._latest_id_image = QImage()
        self._preview_frame_count = 0
        self._link = CameraLink()
        self._id_device_name = ""
        self._media_devices: QMediaDevices | None = None
        self._camera_timer: QTimer | None = None

        self.setWindowTitle("Askari VMS — Entry Portal")
        self.setObjectName("appRoot")
        self.setStyleSheet("""
            QLabel { font-size: 15px; }
            QLineEdit, QComboBox { font-size: 17px; }
            QPushButton { font-size: 15px; min-height: 36px; }
            QLabel#pageTitle { font-size: 25px; }
            QLabel#captureTitle { font-size: 17px; font-weight: 600; }
            QLabel#entryShortcuts { font-size: 14px; color: #315b43; }
            QScrollArea { border: none; background: transparent; }
        """)
        self.setMinimumSize(1180, 760)
        self._build()
        self._start_id_camera()
        self._install_shortcuts()
        QApplication.instance().installEventFilter(self)
        self.clear_form()
        if self._driver_camera is not None:
            self._driver_camera.start()
            self._driver_timer = QTimer(self)
            self._driver_timer.timeout.connect(self._refresh_driver)
            self._driver_timer.start(250)
        if self._anpr_feed is not None:
            self._anpr_feed.start()
            self._anpr_timer = QTimer(self)
            self._anpr_timer.timeout.connect(self._refresh_anpr)
            self._anpr_timer.start(200)

    def read_plate(self, plate: str) -> bool:
        """Adapter entry point: fill only the plate; leave confirmation to the operator."""
        plate = normalize_plate(plate)
        if not plate or plate == self._last_anpr_plate:
            return False
        self._last_anpr_plate = plate
        self.fields["vehicle_number"].setText(plate)
        self.status.setText(f"ANPR read {plate}. Check the plate before submitting.")
        self._audit.record(
            action="Hardware event", target="Visitor Entry ANPR",
            summary=f"ANPR detected {plate}",
            details="Vehicle Number filled from the camera. Operator confirmation is still required; no visit submitted or gate command sent.",
        )
        return True

    def _refresh_anpr(self) -> None:
        readings, (connected, message) = self._anpr_feed.drain()
        self._streams["anpr"].set_state(message)
        if connected != self._anpr_connected:
            self._audit.record(
                action="Hardware event", target="Visitor Entry ANPR",
                summary="ANPR connected" if connected else "ANPR unavailable",
                details="Plate detection feed. Manual entry remains available.",
                severity=AuditSeverity.INFO if connected else AuditSeverity.WARNING,
            )
            self._anpr_connected = connected
        for reading in readings:
            if reading.received_at > self._anpr_after and time.monotonic() - reading.received_at <= 10:
                self.read_plate(reading.plate)

    def _refresh_driver(self) -> None:
        frame = self._driver_camera.latest()
        panel = self._streams["driver"]
        available = frame.fresh()
        if available and frame.received_at != self._driver_displayed_at:
            pixmap = QPixmap()
            available = pixmap.loadFromData(frame.jpeg, "JPG")
            if available:
                panel.preview.setPixmap(pixmap)
                panel.preview.show()
                self._driver_displayed_at = frame.received_at
        if not available:
            panel.clear_image()
        panel.set_state(frame.message if available or not frame.jpeg else "Driver camera stalled — reconnecting.")
        if available != self._driver_available:
            # Do not audit the normal initial connecting state as an outage.
            if available or self._driver_available is not None:
                self._audit.record(
                    action="Hardware event", target="Visitor Entry driver camera",
                    summary="Driver camera connected" if available else "Driver camera unavailable",
                    details="Automatic snapshot feed. Manual visitor entry remains available.",
                    severity=AuditSeverity.INFO if available else AuditSeverity.WARNING,
                )
            self._driver_available = available

    def _capture_driver(self) -> None:
        if self._driver_camera is None or self._driver_image:
            return
        frame = self._driver_camera.latest()
        if not frame.fresh(self._driver_after):
            self._captured["driver"] = False
            self._streams["driver"].set_state("No fresh driver photo. You may still submit.")
            return
        image = QImage.fromData(frame.jpeg, "JPG")
        try:
            if image.isNull() or not self._image_directory:
                raise OSError("No image or data directory")
            folder = Path(self._image_directory) / "images" / "driver_entry" / datetime.now().strftime("%Y-%m-%d")
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{uuid4().hex}.jpg"
            if not image.save(str(path), "JPG", 95):
                raise OSError("Image write failed")
        except OSError:
            self._captured["driver"] = False
            self._streams["driver"].set_state("Driver photo could not be saved. You may still submit.")
            return
        self._driver_image = str(path)
        self._captured["driver"] = True
        self._streams["driver"].set_state("Driver photo saved for this visitor", live=True)

    # ---------- construction ----------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 10, 20, 10)
        root.setSpacing(10)
        root.addWidget(self._header())
        root.addWidget(self._shortcut_card())

        columns = QHBoxLayout()
        columns.setSpacing(12)
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.form_scroll.setWidget(self._form_card())
        columns.addWidget(self.form_scroll, 2)
        self.capture_scroll = QScrollArea()
        self.capture_scroll.setWidgetResizable(True)
        self.capture_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.capture_scroll.setWidget(self._capture_card())
        columns.addWidget(self.capture_scroll, 3)
        # Spare height goes to the form and the camera views. The events table sizes
        # itself to its rows, so giving it the stretch only padded it with blank space
        # while the form sat 1px from clipping its last field.
        root.addLayout(columns, 1)

        root.addWidget(self._events_card(), 0)
        root.addWidget(self._footer())

    def _header(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(18, 10, 18, 10)

        badge = circular_logo(HEADER_LOGO_SIZE)
        if not badge.isNull():
            emblem = QLabel()
            emblem.setObjectName("brandLogo")
            emblem.setPixmap(badge)
            emblem.setFixedSize(HEADER_LOGO_SIZE, HEADER_LOGO_SIZE)
            row.addWidget(emblem)
            row.addSpacing(12)
        self.emblem_shown = not badge.isNull()

        titles = QVBoxLayout()
        titles.setSpacing(0)
        who = self._session.display_name if self._session else "Not signed in"
        welcome = QLabel(f"Welcome {who}")
        welcome.setObjectName("userName")
        gate = QLabel(f"Gate: {self._gate}")
        gate.setProperty("muted", "true")
        titles.addWidget(welcome)
        titles.addWidget(gate)
        row.addLayout(titles)

        heading = QLabel("New Visitor Record")
        heading.setObjectName("pageTitle")
        row.addSpacing(24)
        row.addWidget(heading)
        row.addStretch()

        mode = "ANPR auto-fill enabled · Gate simulated" if self._anpr_feed else (
            "Driver preview enabled · Gate simulated" if self._driver_camera else "Gate and IP cameras simulated")
        self.simulation = QLabel(mode)
        self.simulation.setProperty("status", "warning")
        self.simulation.setWordWrap(True)
        row.addWidget(self.simulation)

        self.refresh_button = QPushButton("Refresh Cameras")
        self.refresh_button.clicked.connect(self.refresh_cameras)
        row.addWidget(self.refresh_button)

        self.sign_out_button = QPushButton("Logout")
        self.sign_out_button.setObjectName("dangerButton")
        self.sign_out_button.clicked.connect(self.sign_out)
        row.addWidget(self.sign_out_button)
        return bar

    def _shortcut_card(self) -> QFrame:
        card = QFrame()
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(4)
        lines = [f"<b>{c.shortcut}</b> {c.name}" for c in self._categories]
        if lines:
            categories = QLabel(" &nbsp; · &nbsp; ".join(lines))
            categories.setObjectName("entryShortcuts")
            categories.setWordWrap(True)
            layout.addWidget(categories)
        keys = QLabel(" &nbsp; · &nbsp; ".join((
            "<b>Ctrl+D</b> Destination", "<b>Ctrl+K</b> Capture",
            "<b>Ctrl+Enter</b> Submit", "<b>Ctrl+N</b> Next visitor",
        )))
        keys.setObjectName("entryShortcuts")
        keys.setWordWrap(True)
        layout.addWidget(keys)
        return card

    def _form_card(self) -> QFrame:
        card = QFrame()
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(4)

        self.fields: dict[str, QLineEdit] = {}
        placeholders = {
            "vehicle_number": "Filled by ANPR — confirm before submitting",
            "destination": f"Hold {DICTATE_KEY} and speak, or type",
            "cnic": "00000-0000000-0",
        }
        for key, label in FIELDS:
            caption = QLabel(label)
            editor = QLineEdit()
            editor.setPlaceholderText(placeholders.get(key, ""))
            editor.setMinimumHeight(40)
            self.fields[key] = editor
            layout.addWidget(caption)
            layout.addWidget(editor)
            layout.addSpacing(5)

        layout.addWidget(QLabel("Vehicle Type"))
        self.vehicle_type = QComboBox()
        self.vehicle_type.setMinimumHeight(40)
        self.vehicle_type.addItems([c.name for c in self._categories] or ["No categories configured"])
        self.vehicle_type.currentTextChanged.connect(self._vehicle_type_changed)
        layout.addWidget(self.vehicle_type)

        self.notice = QLabel()
        self.notice.setObjectName("infoBanner")
        self.notice.setWordWrap(True)
        self.notice.hide()
        layout.addWidget(self.notice)

        self.warning = QLabel()
        self.warning.setObjectName("formError")
        self.warning.setWordWrap(True)
        self.warning.hide()
        layout.addWidget(self.warning)
        layout.addStretch()

        self.fields["vehicle_number"].textChanged.connect(self._check_vehicle)
        self.fields["cnic"].textChanged.connect(self._check_vehicle)
        return card

    def _capture_card(self) -> QFrame:
        card = QFrame()
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        heading = QLabel("Visitor cameras")
        heading.setProperty("section", "true")
        top.addWidget(heading)
        top.addStretch()
        self.capture_button = QPushButton("Capture")
        self.capture_button.setObjectName("primaryButton")
        self.capture_button.setStyleSheet("min-height: 28px; padding: 6px 16px;")
        self.capture_button.clicked.connect(self.capture)
        top.addWidget(self.capture_button)
        layout.addLayout(top)

        self.cnic_panel = StreamPanel("ID card")
        views = QGridLayout()
        views.setSpacing(12)
        views.addWidget(self.cnic_panel, 0, 0)
        self._streams: dict[str, StreamPanel] = {}
        for key, title in STREAMS:
            panel = StreamPanel(title, compact=key == "anpr")
            self._streams[key] = panel
        views.addWidget(self._streams["driver"], 0, 1)
        views.addWidget(self._streams["anpr"], 1, 0, 1, 2)
        views.setColumnStretch(0, 1)
        views.setColumnStretch(1, 1)
        views.setRowStretch(0, 4)
        views.setRowStretch(1, 0)
        layout.addLayout(views, 1)

        self.choose_button = QPushButton("Choose ID card image…")
        self.choose_button.setStyleSheet("min-height: 28px; padding: 6px 16px;")
        self.choose_button.clicked.connect(self.choose_cnic_image)
        layout.addWidget(self.choose_button)
        return card

    def _events_card(self) -> QFrame:
        card = QFrame()
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)
        heading = QLabel("Live controller events")
        heading.setProperty("section", "true")
        layout.addWidget(heading)

        self.events_table = QTableWidget(0, len(EVENT_COLUMNS))
        self.events_table.setHorizontalHeaderLabels(EVENT_COLUMNS)
        self.events_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.events_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.events_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.events_table.verticalHeader().setVisible(False)
        self.events_table.verticalHeader().setDefaultSectionSize(34)
        self._event_columns = ProportionalColumns(self.events_table, EVENT_WEIGHTS)
        layout.addWidget(self.events_table)
        return card

    def _footer(self) -> QFrame:
        bar = QFrame()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel()
        self.status.setProperty("muted", "true")
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)

        self.reuse_button = QPushButton("Use previous details")
        self.reuse_button.clicked.connect(self.reuse_previous)
        self.reuse_button.hide()
        row.addWidget(self.reuse_button)

        self.submit_button = QPushButton("Submit and open gate")
        self.submit_button.setObjectName("primaryButton")
        self.submit_button.setMinimumHeight(42)
        self.submit_button.setMinimumWidth(220)
        self.submit_button.clicked.connect(self.submit)
        row.addWidget(self.submit_button)
        return bar

    def _install_shortcuts(self) -> None:
        for category in self._categories:
            shortcut = QShortcut(QKeySequence(category.shortcut), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda c=category: self.run_category_workflow(c))
        for key, slot in ((SUBMIT_KEY, self.submit), ("Ctrl+Enter", self.submit),
                          (CLEAR_KEY, self.clear_form),
                          (CAPTURE_KEY, self.capture)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(slot)

    # ---------- operator actions ----------

    def choose_category(self, category: VehicleCategory) -> None:
        self._selected = category
        if self.vehicle_type.currentText() != category.name:
            self.vehicle_type.blockSignals(True)
            self.vehicle_type.setCurrentText(category.name)
            self.vehicle_type.blockSignals(False)
        self.status.setText(f"Vehicle type set to {category.name} ({category.shortcut}).")

    def run_category_workflow(self, category: VehicleCategory) -> bool:
        """The category key is the operator's final confirmation for Entry."""
        if self._category_workflow_pending or self._capture_thread is not None:
            self.status.setText("Visitor capture is already in progress…")
            return False
        self.choose_category(category)
        self._category_workflow_pending = True
        self.capture()
        # Real CNIC OCR finishes asynchronously. Simulation or a camera that could not
        # start has no worker to wait for, so continue immediately and record gaps.
        if self._capture_thread is None:
            self._finish_category_workflow()
        return True

    def _finish_category_workflow(self) -> None:
        if not self._category_workflow_pending:
            return
        self._category_workflow_pending = False
        self.submit()

    def _vehicle_type_changed(self, name: str) -> None:
        match = next((c for c in self._categories if c.name == name), None)
        if match is not None:
            self.choose_category(match)

    def choose_shortcut(self, shortcut: str) -> bool:
        category = by_shortcut(self._categories, shortcut)
        if category is None:
            return False
        self.choose_category(category)
        return True

    def toggle_dictation(self) -> None:
        if self._dictating:
            self.stop_dictation()
        else:
            self.start_dictation()

    def eventFilter(self, watched, event) -> bool:
        key_event = event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease)
        if key_event and self.isVisible() and QApplication.activeWindow() is self and event.key() == Qt.Key.Key_D:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                if event.isAutoRepeat():
                    return True
                if event.type() == QEvent.Type.KeyPress:
                    self.start_dictation()
                    return True
                if event.type() == QEvent.Type.KeyRelease:
                    self.stop_dictation()
                    return True
        return super().eventFilter(watched, event)

    def start_dictation(self) -> bool:
        self.fields["destination"].setFocus()
        if self._dictation is None:
            self.status.setText("Destination microphone is not configured — type manually.")
            return False
        if self._dictating or self._dictation_thread is not None:
            return False
        try:
            self._dictation.start()
        except SpeechError as exc:
            self.status.setText(f"Microphone unavailable: {exc}. Type the destination manually.")
            return False
        self._dictating = True
        self.status.setText("Listening… keep holding Ctrl+D and speak the destination.")
        return True

    def stop_dictation(self) -> bool:
        if not self._dictating or self._dictation is None:
            return False
        self._dictating = False
        audio = self._dictation.stop()
        self.status.setText("Recognizing destination offline…")
        thread = QThread(self)
        worker = _DictationWorker(self._dictation, audio)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._dictation_complete)
        worker.failed.connect(self._dictation_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._dictation_finished)
        self._dictation_thread, self._dictation_worker = thread, worker
        thread.start()
        return True

    @Slot(str)
    def _dictation_complete(self, text: str) -> None:
        if text:
            self.fields["destination"].setText(text)
            self.status.setText(f"Destination heard: {text}. Check it before pressing the category key.")
        else:
            self.status.setText("No speech recognized. Hold Ctrl+D and try again, or type manually.")

    @Slot(str)
    def _dictation_failed(self, message: str) -> None:
        self.status.setText(f"Destination dictation failed: {message}. Type manually.")

    @Slot()
    def _dictation_finished(self) -> None:
        self._dictation_thread = None
        self._dictation_worker = None

    def capture(self) -> None:
        """Capture every source. A camera failure never blocks the transaction."""
        if self._cnic_reader is None:
            self._captured["cnic"] = True
            self.cnic_panel.set_state("ID card captured (simulated)", live=True)
        else:
            self.start_cnic_capture()
        for key in ("anpr", "driver", "plate"):
            if key in ("anpr", "plate") and self._anpr_feed is not None:
                continue  # Plate events are not captured overview/crop photographs.
            if key == "driver" and self._driver_camera is not None:
                self._driver_image = ""
                self._capture_driver()
                continue
            self._captured[key] = True
        for key, title in STREAMS:
            if key == "anpr" and self._anpr_feed is not None:
                continue
            if key == "driver" and self._driver_camera is not None:
                continue
            self._streams[key].set_state("Captured (simulated)", live=True)
        if self._cnic_reader is None:
            self.status.setText("Captured ID card, ANPR overview, plate crop and driver image (simulated).")

    def capture_cnic(self) -> bool:
        """Synchronous adapter hook retained for tests and non-UI callers."""
        if self._cnic_reader is None:
            return False
        try:
            capture = self._cnic_reader.capture_and_read()
        except CNICReadError as exc:
            self._cnic_failed(str(exc))
            return False
        self._apply_cnic_capture(capture)
        return True

    def start_cnic_capture(self) -> bool:
        """Run camera capture and OCR away from Qt's GUI thread."""
        if self._cnic_reader is None or self._capture_thread is not None:
            return False
        image_path = ""
        if self._id_camera is not None:
            if self._latest_id_image.isNull():
                self.status.setText("ID camera is starting — wait for the live picture, then capture again.")
                return False
            try:
                path = self._cnic_reader.new_image_path()
                if not self._latest_id_image.save(str(path), "JPG", 95):
                    raise OSError("Qt could not encode the image")
                image_path = str(path)
                # Ours, not a fault: tell the watchdog before the frames stop.
                self._link.stopped()
                self._id_camera.stop()
            except (AttributeError, OSError) as exc:
                self._cnic_failed(f"ID card image could not be saved: {exc}")
                return False
        self.capture_button.setEnabled(False)
        self.status.setText("ID card captured — reading text…")
        thread = QThread(self)
        worker = _CNICCaptureWorker(self._cnic_reader, image_path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._apply_cnic_capture)
        worker.failed.connect(self._cnic_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._capture_finished)
        self._capture_thread = thread
        self._capture_worker = worker
        thread.start()
        return True

    def _start_id_camera(self) -> bool:
        """Start a persistent Windows/Qt live feed so focus settles before capture.

        The feed is then kept up for the rest of the shift by the watchdog below: an
        operator should never have to restart the application because a cable was
        knocked, and a camera that is missing at start-up may well be plugged in a
        minute later.
        """
        if self._cnic_reader is None or not hasattr(self._cnic_reader, "camera_index"):
            return False
        # Qt reports plug and unplug, which turns most reconnects into an immediate
        # retry rather than a wait for the backoff to expire.
        self._media_devices = QMediaDevices(self)
        self._media_devices.videoInputsChanged.connect(self._id_devices_changed)
        self._camera_timer = QTimer(self)
        self._camera_timer.setInterval(CAMERA_TICK_MS)
        self._camera_timer.timeout.connect(self._camera_tick)
        self._camera_timer.start()
        return self._open_id_camera()

    def _pick_id_device(self) -> QCameraDevice | None:
        """Prefer the camera we were already using, by name.

        `camera_index` is a position in Qt's device list, and that list reorders when a
        device is unplugged and returns. After a reconnect index 0 can easily be the
        laptop's built-in webcam rather than the card-box camera, so the remembered
        description wins once we have one.
        """
        devices = QMediaDevices.videoInputs()
        if not devices:
            return None
        if self._id_device_name:
            for device in devices:
                if device.description() == self._id_device_name:
                    return device
        index = int(getattr(self._cnic_reader, "camera_index", 0))
        return devices[index] if 0 <= index < len(devices) else None

    def _open_id_camera(self) -> bool:
        """Build a brand-new camera and start it. Safe to call repeatedly."""
        self._teardown_id_camera()
        now = time.monotonic()
        # Declare the intent before looking for hardware. Otherwise a camera that is
        # missing at start-up leaves the link still "stopped", the failure is taken for
        # a deliberate one, and no retry is ever scheduled.
        self._link.starting(now)
        device = self._pick_id_device()
        if device is None:
            self._link.failed(now, NO_DEVICE)
            self._show_camera_state()
            return False

        self._id_device_name = device.description()
        self._id_camera = QCamera(device, self)
        self._id_camera_session = QMediaCaptureSession(self)
        self._id_video_sink = QVideoSink(self)
        self._id_camera_session.setCamera(self._id_camera)
        self._id_camera_session.setVideoSink(self._id_video_sink)
        self._id_video_sink.videoFrameChanged.connect(self._id_frame_changed)
        self._id_camera.errorOccurred.connect(self._id_camera_error)
        self.cnic_panel.set_state("Starting live camera…")
        self._id_camera.start()
        return True

    def _teardown_id_camera(self) -> None:
        """Drop the camera objects completely rather than reusing them.

        A handle to a device that has been unplugged is dead — restarting it fails —
        and keeping it open can stop Windows from handing the device back when it
        returns. So every reconnect gets fresh objects.
        """
        for signal, slot in (
            (getattr(self._id_video_sink, "videoFrameChanged", None), self._id_frame_changed),
            (getattr(self._id_camera, "errorOccurred", None), self._id_camera_error),
        ):
            if signal is not None:
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass  # Already gone; nothing to detach.
        if self._id_camera is not None:
            self._id_camera.stop()
        if self._id_camera_session is not None:
            self._id_camera_session.setCamera(None)
            self._id_camera_session.setVideoSink(None)
        for obj in (self._id_camera, self._id_camera_session, self._id_video_sink):
            if obj is not None:
                obj.deleteLater()
        self._id_camera = None
        self._id_camera_session = None
        self._id_video_sink = None

    @Slot()
    def _camera_tick(self) -> None:
        """Watchdog. Declares a silent feed lost, and reopens when a retry falls due."""
        now = time.monotonic()
        if self._link.poll(now):
            self._open_id_camera()
        elif self._link.state is LinkState.LOST:
            self._show_camera_state()  # keep the countdown moving

    @Slot()
    def _id_devices_changed(self) -> None:
        """A camera was plugged in or pulled out."""
        now = time.monotonic()
        if self._link.state in (LinkState.STARTING, LinkState.LIVE) and self._pick_id_device() is None:
            self._link.failed(now, UNPLUGGED)
            self._teardown_id_camera()
            self._show_camera_state()
            return
        self._link.devices_changed(now)

    @Slot(object, str)
    def _id_camera_error(self, _error: object, text: str) -> None:
        self._link.failed(time.monotonic(), text or "camera error")
        self._show_camera_state()

    def _show_camera_state(self) -> None:
        self.cnic_panel.set_state(self._link.describe(time.monotonic()))

    @Slot(QVideoFrame)
    def _id_frame_changed(self, frame: QVideoFrame) -> None:
        image = frame.toImage()
        if image.isNull():
            return
        self._link.frame(time.monotonic())
        self._latest_id_image = image.copy()
        self._preview_frame_count += 1
        if self._preview_frame_count % 3:
            return
        pixmap = QPixmap.fromImage(image)
        self.cnic_panel.preview.setPixmap(pixmap)
        self.cnic_panel.preview.show()
        self.cnic_panel.set_state(LIVE_PROMPT, live=True)

    @Slot()
    def _capture_finished(self) -> None:
        self._capture_thread = None
        self._capture_worker = None
        self.capture_button.setEnabled(True)

    @Slot(str)
    def _cnic_failed(self, message: str) -> None:
        self._captured["cnic"] = False
        self.cnic_panel.set_state(f"Capture failed — {message}")
        self.status.setText(f"ID card capture failed: {message}. You may type the details manually.")
        self._finish_category_workflow()

    @Slot(object)
    def _apply_cnic_capture(self, capture: CNICCapture) -> None:

        self._cnic_image = capture.image_path
        self._captured["cnic"] = True
        self.cnic_panel.set_image(capture.image_path)
        for key, value in capture.fields.items():
            editor = self.fields.get(key)
            if editor is None:
                continue
            editor.setText(value.value)
            editor.setProperty("ocrConfidence", "low" if value.confidence < 0.75 else "good")
            editor.style().unpolish(editor)
            editor.style().polish(editor)
        count = len(capture.fields)
        if count:
            self.cnic_panel.set_state(f"Captured — OCR filled {count} field(s)", live=True)
            self.status.setText("ID card captured. Check highlighted low-confidence fields before submitting.")
        else:
            self.cnic_panel.set_state("Captured — no readable text", live=True)
            self.status.setText("Image saved, but OCR found no readable text. Adjust focus/light or type manually.")
        self._finish_category_workflow()

    def choose_cnic_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select ID card image", "", "Images (*.png *.jpg *.jpeg *.bmp);;All files (*)"
        )
        if not path:
            return
        self._cnic_image = path
        self._captured["cnic"] = True
        self.cnic_panel.set_image(path)
        self.cnic_panel.set_state(Path(path).name, live=True)

    def refresh_cameras(self) -> None:
        if self._anpr_feed is not None:
            self._refresh_anpr()
        if self._driver_camera is not None:
            self._refresh_driver()
            self.status.setText("Driver preview refreshed. Unavailable cameras reconnect automatically.")
            return
        if self._anpr_feed is not None:
            self.status.setText("ANPR status refreshed. Disconnections reconnect automatically.")
            return
        for panel in (self.cnic_panel, *self._streams.values()):
            if not panel.property("captured") == "true":
                panel.set_state("No feed — camera adapter not enabled")
        self.status.setText("Camera feeds refreshed. Adapters are not enabled yet, so no stream is live.")

    def sign_out(self) -> None:
        if self._session is not None:
            self._audit.record(
                action="Logout",
                target="Entry portal",
                summary=f"{self._session.display_name} signed out",
                details=f"{self._session.describe()}. Signed in at {self._session.signed_in_at:%d %b %Y %H:%M:%S}.",
            )
            self._session = None
        self.signed_out.emit()
        self.close()

    # ---------- checks ----------

    def _check_vehicle(self) -> None:
        plate = self.fields["vehicle_number"].text()
        active = active_visits_for_vehicle(self._visits, plate)
        if active:
            self.warning.setText(
                f"{active[0].vehicle_number} already has an open visit ({active[0].visit_id}, "
                f"entered {active[0].entry_time:%H:%M}). You may still continue."
            )
            self.warning.show()
        else:
            self.warning.hide()

        earlier = previous_visit(self._visits, plate, self.fields["cnic"].text())
        self._reusable = earlier if (earlier is not None and not active) else None
        if self._reusable is not None:
            self.notice.setText(
                f"Seen before: {earlier.visitor_name or 'unnamed'} — "
                f"{earlier.destination or 'no destination'} on {earlier.entry_time:%d %b %Y}."
            )
            self.notice.show()
            self.reuse_button.show()
        else:
            self.notice.hide()
            self.reuse_button.hide()

    def reuse_previous(self) -> bool:
        """Fill from the offered earlier visit. Never automatic — the operator asks."""
        earlier = self._reusable
        if earlier is None:
            return False
        for key in ("visitor_name", "cnic", "mobile", "destination", "father_name",
                    "date_of_birth", "cnic_issue_date", "cnic_expiry_date"):
            self.fields[key].setText(getattr(earlier, key))
        if earlier.vehicle_category:
            match = next((c for c in self._categories if c.name == earlier.vehicle_category), None)
            if match is not None:
                self.choose_category(match)
        self.status.setText(f"Reused details from {earlier.visit_id}. Photographs are always taken fresh.")
        return True

    # ---------- events feed ----------

    def set_events(self, events: Sequence[ETagEvent]) -> None:
        self._events = list(events)
        self.refresh_events()

    def refresh_events(self, limit: int = 8) -> None:
        visible = self._events[:limit]
        self.events_table.setRowCount(len(visible))
        for row, event in enumerate(visible):
            values = (
                "Entry" if event.direction == "In" else "Exit",
                event.rfid,
                event.resident_name or "—",
                event.vehicle_number or "—",
                event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "",
                event.kind.value,
            )
            background = QBrush(ROW_COLOURS[event.kind])
            foreground = TEXT_COLOURS.get(event.kind)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setBackground(background)
                if foreground is not None:
                    item.setForeground(QBrush(foreground))
                self.events_table.setItem(row, column, item)
        fit_height_to_rows(self.events_table, maximum_rows=2)
        self.events_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._event_columns.apply()

    # ---------- submit ----------

    def clear_form(self) -> None:
        self._anpr_after = time.monotonic()
        self._last_anpr_plate = ""
        for editor in self.fields.values():
            editor.clear()
            editor.setProperty("ocrConfidence", "")
            editor.style().unpolish(editor)
            editor.style().polish(editor)
        self._selected = None
        self._dictating = False
        self._cnic_image = ""
        self._driver_image = ""
        self._driver_after = time.monotonic()
        self._captured = {key: False for key in self._captured}
        self.cnic_panel.set_state("No feed")
        self.cnic_panel.clear_image()
        if self._camera_timer is not None:
            self._latest_id_image = QImage()
            self.cnic_panel.set_state("Starting live camera…")
            if self._id_camera is not None:
                self._link.starting(time.monotonic())
                self._id_camera.start()
            else:
                # Lost while the last visitor was being processed. Take it back now.
                self._open_id_camera()
        for panel in self._streams.values():
            panel.set_state("No feed")
        self.notice.hide()
        self.warning.hide()
        self.reuse_button.hide()
        self.refresh_events()
        # ANPR fills the plate, so the operator's first action is the destination.
        self.fields["destination"].setFocus()

    def closeEvent(self, event) -> None:
        if self._anpr_timer is not None:
            self._anpr_timer.stop()
        if self._anpr_feed is not None:
            self._anpr_feed.stop()
        if self._driver_timer is not None:
            self._driver_timer.stop()
        if self._driver_camera is not None:
            self._driver_camera.stop()
        # Stop the watchdog first, or it reopens the camera we are shutting down.
        if self._camera_timer is not None:
            self._camera_timer.stop()
        self._link.stopped()
        self._teardown_id_camera()
        QApplication.instance().removeEventFilter(self)
        if self._dictation is not None and self._dictating:
            self._dictation.stop()
            self._dictating = False
        super().closeEvent(event)

    def build_visit(self) -> VisitRecord:
        return VisitRecord(
            visit_id=next_visit_id(self._visits),
            barcode=new_barcode(),
            entry_time=datetime.now().replace(microsecond=0),
            entry_operator=self._session.operator if self._session else "unknown",
            visitor_name=self.fields["visitor_name"].text(),
            cnic=self.fields["cnic"].text(),
            mobile=self.fields["mobile"].text().strip(),
            vehicle_number=self.fields["vehicle_number"].text(),
            vehicle_category=self._selected.name if self._selected else "",
            destination=self.fields["destination"].text(),
            father_name=self.fields["father_name"].text(),
            date_of_birth=self.fields["date_of_birth"].text().strip(),
            cnic_issue_date=self.fields["cnic_issue_date"].text().strip(),
            cnic_expiry_date=self.fields["cnic_expiry_date"].text().strip(),
            cnic_image=self._cnic_image,
            driver_image=self._driver_image,
        ).normalized()

    def submit(self) -> VisitRecord:
        """Record the visit, print the receipt, open the gate. Never refuses."""
        self._capture_driver()
        visit = self.build_visit()
        self._visits.insert(0, visit)

        gaps = missing_fields(visit)
        uncaptured = [CAPTURE_LABELS[key] for key in CAPTURE_LABELS if not self._captured[key]]

        details = [
            f"Visit {visit.visit_id} recorded at Visitor Entry by {visit.entry_operator}. "
            f"Receipt barcode {visit.barcode}."
        ]
        if gaps:
            details.append("Submitted with missing data: " + ", ".join(gaps) + ".")
        if uncaptured:
            details.append("No image captured from: " + ", ".join(uncaptured) + ".")
        print_errors: list[PrinterError] = []
        printed = print_receipt(self._printer, visit, on_error=print_errors.append)
        details.append(
            "Receipt printed successfully." if printed else
            "Receipt printing failed; operator continuation remains available."
        )
        details.append("Visitor Entry gate command is simulated until the controller adapter is enabled.")

        self._audit.record(
            action="Visitor decision",
            target=visit.visit_id,
            summary=f"Visitor entry {visit.vehicle_number or 'no plate'} to {visit.destination or 'no destination'}",
            details=" ".join(details),
            severity=AuditSeverity.WARNING if (gaps or uncaptured) else AuditSeverity.INFO,
        )
        if print_errors:
            self._audit.record(
                action="Hardware event",
                target="Entry printer",
                summary="Receipt printer unavailable",
                details=f"Visit {visit.visit_id}: {print_errors[0]}. Gate workflow was not blocked.",
                severity=AuditSeverity.CRITICAL,
            )
        self._audit.record(
            action="Gate command",
            target="Visitor Entry",
            summary=f"Visitor Entry opened for {visit.visit_id}",
            details=(
                (f"Receipt printed for {visit.visit_id}. " if printed else
                 f"Receipt failed for {visit.visit_id}; operator continuation allowed. ")
                + "Visitor Entry open is simulated: no physical command was transmitted."
            ),
        )

        if self._on_submit is not None:
            self._on_submit(visit)
        self.submitted.emit(visit)

        receipt_state = f"Receipt {visit.barcode} printed" if printed else "Receipt failed — check printer"
        message = f"{visit.visit_id} submitted. {receipt_state}; Visitor Entry open is currently simulated."
        if gaps:
            message += "  Flagged as missing: " + ", ".join(gaps) + "."
        self.clear_form()
        self.status.setText(message)
        return visit
