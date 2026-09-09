"""Entry portal — the screen the gate operator works on all shift.

Keyboard-first and arranged to match the operator's existing screen: the fields run in
one column in the order they are filled, the CNIC capture sits beside them, and the live
controller feed runs along the bottom.

Nothing here blocks the operator. Every capture step may fail, every field may be left
blank, and the gate still opens. What is missing is recorded, not prevented.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QBrush, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices, QVideoFrame, QVideoSink
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from askari_vms.audit import AuditLog, AuditSeverity
from askari_vms.auth import Session
from askari_vms.ui.brand import circular_logo
from askari_vms.categories import VehicleCategory, by_shortcut
from askari_vms.cnic_ocr import CNICCapture, CNICReader, CNICReadError
from askari_vms.etag_events import ETagEvent
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


class StreamPanel(QFrame):
    """A live camera view. The feed arrives when the camera adapter is enabled."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("capturePanel")
        self.setMinimumHeight(150)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        name = QLabel(title)
        name.setObjectName("captureTitle")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state = QLabel("No feed")
        self.state.setProperty("muted", "true")
        self.state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state.setWordWrap(True)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(90)
        self.preview.hide()
        layout.addWidget(name)
        layout.addStretch()
        # A fixed-size preview would sit hard against the left edge: a QVBoxLayout
        # left-aligns any item it cannot stretch. Centre it in its own row instead.
        preview_row = QHBoxLayout()
        preview_row.setContentsMargins(0, 0, 0, 0)
        preview_row.addStretch()
        preview_row.addWidget(self.preview)
        preview_row.addStretch()
        layout.addLayout(preview_row, 1)
        layout.addWidget(self.state)
        layout.addStretch()

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
        self.preview.setPixmap(pixmap.scaled(
            self.preview.width(), self.preview.height(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
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
        self._selected: VehicleCategory | None = None
        self._captured: dict[str, bool] = {"cnic": False, "anpr": False, "driver": False, "plate": False}
        self._cnic_image = ""
        self._reusable: VisitRecord | None = None
        self._dictating = False
        self._capture_thread: QThread | None = None
        self._capture_worker: _CNICCaptureWorker | None = None
        self._id_camera: QCamera | None = None
        self._id_camera_session: QMediaCaptureSession | None = None
        self._id_video_sink: QVideoSink | None = None
        self._latest_id_image = QImage()
        self._preview_frame_count = 0

        self.setWindowTitle("Askari VMS — Entry Portal")
        self.setObjectName("appRoot")
        self.setMinimumSize(1180, 760)
        self._build()
        self._start_id_camera()
        self._install_shortcuts()
        self.clear_form()

    # ---------- construction ----------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)
        root.addWidget(self._header())

        columns = QHBoxLayout()
        columns.setSpacing(12)
        columns.addWidget(self._shortcut_card(), 0)
        columns.addWidget(self._form_card(), 3)
        columns.addWidget(self._capture_card(), 3)
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

        mode = "ID OCR READY — OTHER HARDWARE SIMULATED" if self._cnic_reader else "HARDWARE DISABLED — SIMULATION MODE"
        self.simulation = QLabel(mode)
        self.simulation.setProperty("status", "warning")
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
        card.setMaximumWidth(210)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        heading = QLabel("Shortcut Keys")
        heading.setObjectName("shortcutHeading")
        layout.addWidget(heading)

        lines = [f"<b>{c.shortcut}</b>: {c.name}" for c in self._categories]
        lines += [
            "",
            f"<b>{DICTATE_KEY}</b>: Destination",
            f"<b>{CAPTURE_KEY}</b>: Capture CNIC",
            f"<b>{SUBMIT_KEY}</b>: Submit",
            f"<b>{CLEAR_KEY}</b>: New record",
        ]
        keys = QLabel("<br>".join(lines) or "No categories configured")
        keys.setObjectName("shortcutPanel")
        keys.setWordWrap(True)
        layout.addWidget(keys)
        layout.addStretch()
        return card

    def _form_card(self) -> QFrame:
        card = QFrame()
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(8)

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
            editor.setMinimumHeight(34)
            self.fields[key] = editor
            layout.addWidget(caption)
            layout.addWidget(editor)

        layout.addWidget(QLabel("Vehicle Type"))
        self.vehicle_type = QComboBox()
        self.vehicle_type.setMinimumHeight(34)
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
        heading = QLabel("CNIC")
        heading.setProperty("section", "true")
        top.addWidget(heading)
        top.addStretch()
        self.capture_button = QPushButton("Capture")
        self.capture_button.setObjectName("primaryButton")
        self.capture_button.clicked.connect(self.capture)
        top.addWidget(self.capture_button)
        layout.addLayout(top)

        self.cnic_panel = StreamPanel("ID card")
        self.cnic_panel.setMinimumHeight(360)
        self.cnic_panel.preview.setFixedSize(300, 300)
        layout.addWidget(self.cnic_panel)

        self._streams: dict[str, StreamPanel] = {}
        for key, title in STREAMS:
            panel = StreamPanel(title)
            self._streams[key] = panel
            layout.addWidget(panel)

        self.choose_button = QPushButton("Choose ID card image…")
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
            shortcut.activated.connect(lambda c=category: self.choose_category(c))
        for key, slot in ((SUBMIT_KEY, self.submit), ("Ctrl+Enter", self.submit),
                          (CLEAR_KEY, self.clear_form), (DICTATE_KEY, self.toggle_dictation),
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
        self._dictating = not self._dictating
        if self._dictating:
            self.fields["destination"].setFocus()
            self.status.setText("Listening for destination… (speech adapter not enabled — type instead)")
        else:
            self.status.setText("Dictation stopped.")

    def capture(self) -> None:
        """Capture every source. A camera failure never blocks the transaction."""
        if self._cnic_reader is None:
            self._captured["cnic"] = True
            self.cnic_panel.set_state("ID card captured (simulated)", live=True)
        else:
            self.start_cnic_capture()
        for key in ("anpr", "driver", "plate"):
            self._captured[key] = True
        for key, title in STREAMS:
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
        """Start a persistent Windows/Qt live feed so focus settles before capture."""
        if self._cnic_reader is None or not hasattr(self._cnic_reader, "camera_index"):
            return False
        devices = QMediaDevices.videoInputs()
        index = int(getattr(self._cnic_reader, "camera_index", 0))
        if not 0 <= index < len(devices):
            self.cnic_panel.set_state(f"ID camera {index} not found")
            return False
        self._id_camera = QCamera(devices[index], self)
        self._id_camera_session = QMediaCaptureSession(self)
        self._id_video_sink = QVideoSink(self)
        self._id_camera_session.setCamera(self._id_camera)
        self._id_camera_session.setVideoSink(self._id_video_sink)
        self._id_video_sink.videoFrameChanged.connect(self._id_frame_changed)
        self._id_camera.errorOccurred.connect(
            lambda _error, text: self.cnic_panel.set_state(f"Camera error — {text}")
        )
        self.cnic_panel.set_state("Starting live camera…")
        self._id_camera.start()
        return True

    @Slot(QVideoFrame)
    def _id_frame_changed(self, frame: QVideoFrame) -> None:
        image = frame.toImage()
        if image.isNull():
            return
        self._latest_id_image = image.copy()
        self._preview_frame_count += 1
        if self._preview_frame_count % 3:
            return
        pixmap = QPixmap.fromImage(image).scaled(
            self.cnic_panel.preview.width(), self.cnic_panel.preview.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.cnic_panel.preview.setPixmap(pixmap)
        self.cnic_panel.preview.show()
        self.cnic_panel.set_state("Live — place ID card flat, then press Capture", live=True)

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
        fit_height_to_rows(self.events_table)
        self._event_columns.apply()

    # ---------- submit ----------

    def clear_form(self) -> None:
        for editor in self.fields.values():
            editor.clear()
            editor.setProperty("ocrConfidence", "")
            editor.style().unpolish(editor)
            editor.style().polish(editor)
        self._selected = None
        self._dictating = False
        self._cnic_image = ""
        self._captured = {key: False for key in self._captured}
        self.cnic_panel.set_state("No feed")
        self.cnic_panel.clear_image()
        if self._id_camera is not None:
            self._latest_id_image = QImage()
            self.cnic_panel.set_state("Starting live camera…")
            self._id_camera.start()
        for panel in self._streams.values():
            panel.set_state("No feed")
        self.notice.hide()
        self.warning.hide()
        self.reuse_button.hide()
        self.refresh_events()
        # ANPR fills the plate, so the operator's first action is the destination.
        self.fields["destination"].setFocus()

    def closeEvent(self, event) -> None:
        if self._id_camera is not None:
            self._id_camera.stop()
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
        ).normalized()

    def submit(self) -> VisitRecord:
        """Record the visit, print the receipt, open the gate. Never refuses."""
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
        details.append("Receipt printing and the gate command are simulated until the adapters are enabled.")

        self._audit.record(
            action="Visitor decision",
            target=visit.visit_id,
            summary=f"Visitor entry {visit.vehicle_number or 'no plate'} to {visit.destination or 'no destination'}",
            details=" ".join(details),
            severity=AuditSeverity.WARNING if (gaps or uncaptured) else AuditSeverity.INFO,
        )
        self._audit.record(
            action="Gate command",
            target="Visitor Entry",
            summary=f"Visitor Entry opened for {visit.visit_id}",
            details=(
                f"Receipt printed for {visit.visit_id} and Visitor Entry opened automatically. "
                "Simulated: no physical command was transmitted."
            ),
        )

        if self._on_submit is not None:
            self._on_submit(visit)
        self.submitted.emit(visit)

        message = f"{visit.visit_id} submitted. Receipt {visit.barcode} printed, Visitor Entry opened."
        if gaps:
            message += "  Flagged as missing: " + ", ".join(gaps) + "."
        self.clear_form()
        self.status.setText(message)
        return visit
