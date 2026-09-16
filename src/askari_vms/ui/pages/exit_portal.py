"""Exit portal — the screen at the outbound barrier.

ANPR reads continuously and opens the active visit on its own. When it fails, the
operator scans the receipt (the Honeywell Orbit types it straight into the search box)
or searches by plate, CNIC or visit number for a lost receipt.

The operator must mark the driver Matched or Mismatched before submitting, but a
mismatch never blocks: it is recorded, and the gate still opens. The operator may also
open the barrier at any time without a visit at all — that is audited as an override.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import time
from uuid import uuid4

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QImage, QKeySequence, QShortcut, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from askari_vms.audit import AuditLog, AuditSeverity
from askari_vms.auth import Session
from askari_vms.anpr import ANPRFeed
from askari_vms.controller_events import ControllerEventFeed, ControllerReading
from askari_vms.etag_events import ETagEvent, OUT, REPEAT_PASSAGE_SECONDS, build_event, collapse_repeated_passages
from askari_vms.etags import ETagRecord
from askari_vms.ip_camera import SnapshotFeed
from askari_vms.lan_client import ExitSyncService
from askari_vms.ui.brand import circular_logo
from askari_vms.ui.tables import ProportionalColumns, fit_height_to_rows
from askari_vms.visits import (
    DriverMatch,
    VisitRecord,
    check_out,
    describe_duration,
    find_open_visit,
    search_open_visits,
)

SUBMIT_KEY = "Ctrl+Return"
CLEAR_KEY = "Ctrl+N"
MATCH_KEY = "Ctrl+M"
MISMATCH_KEY = "Ctrl+X"

HEADER_LOGO_SIZE = 40

STREAMS = (
    ("anpr", "ANPR — Visitor Exit"),
    ("driver", "Driver camera — Visitor Exit"),
)
CAPTURE_LABELS = {"anpr": "ANPR overview", "plate": "Plate crop", "driver": "Driver camera"}
RESULT_COLUMNS = ("Visit", "Vehicle", "Visitor", "CNIC", "Entry", "Inside", "Destination")
RESULT_WEIGHTS = (12, 12, 18, 16, 15, 9, 18)

NO_ACTIVE_ENTRY = "No active entry found. Search by plate, CNIC or visit number, or open the gate manually."


class ExitPortalWindow(QWidget):
    """One departing vehicle at a time."""

    closed_visit = Signal(object)
    signed_out = Signal()

    def __init__(
        self,
        audit_log: AuditLog | None = None,
        visits: Sequence[VisitRecord] | None = None,
        session: Session | None = None,
        on_checkout: Callable[[VisitRecord], None] | None = None,
        gate: str = "C2 - Exit",
        driver_camera: SnapshotFeed | None = None,
        anpr_camera: SnapshotFeed | None = None,
        anpr_feed: ANPRFeed | None = None,
        image_directory: str = "",
        events: Sequence[ETagEvent] | None = None,
        etags: Sequence[ETagRecord] | None = None,
        controller_event_feed: ControllerEventFeed | None = None,
        on_etag_event: Callable[[ETagEvent], None] | None = None,
        central_sync: ExitSyncService | None = None,
    ) -> None:
        super().__init__()
        self._audit = audit_log if audit_log is not None else AuditLog()
        self._visits = list(visits or [])
        self._session = session
        self._on_checkout = on_checkout
        self._gate = gate
        self._driver_camera = driver_camera
        self._anpr_camera = anpr_camera
        self._anpr_feed = anpr_feed
        self._image_directory = image_directory
        self._events = collapse_repeated_passages(list(events or []))
        self._etags = list(etags or [])
        self._controller_event_feed = controller_event_feed
        self._on_etag_event = on_etag_event
        self._central_sync = central_sync
        self._driver_image = ""
        self._driver_after = time.monotonic()
        self._driver_displayed_at = 0.0
        self._driver_available: bool | None = None
        self._visit: VisitRecord | None = None
        self._decision: str = ""
        self._captured: dict[str, bool] = {key: False for key in CAPTURE_LABELS}
        self._matches: list[VisitRecord] = []

        self.setWindowTitle("Askari VMS — Exit Portal")
        self.setObjectName("appRoot")
        self.setMinimumSize(1180, 760)
        self._build()
        self._install_shortcuts()
        self.clear()
        self._driver_timer: QTimer | None = None
        if self._driver_camera is not None:
            self._driver_camera.start()
            self._driver_timer = QTimer(self)
            self._driver_timer.timeout.connect(self._refresh_driver)
            self._driver_timer.start(250)
        self._anpr_camera_timer: QTimer | None = None
        if self._anpr_camera is not None:
            self._anpr_camera.start()
            self._anpr_camera_timer = QTimer(self)
            self._anpr_camera_timer.timeout.connect(self._refresh_anpr_camera)
            self._anpr_camera_timer.start(250)
        self._anpr_timer: QTimer | None = None
        if self._anpr_feed is not None:
            self._anpr_feed.start()
            self._anpr_timer = QTimer(self)
            self._anpr_timer.timeout.connect(self._refresh_anpr)
            self._anpr_timer.start(250)
        self._controller_event_timer: QTimer | None = None
        if self._controller_event_feed is not None:
            self._controller_event_feed.start()
            self._controller_event_timer = QTimer(self)
            self._controller_event_timer.timeout.connect(self._refresh_controller_events)
            self._controller_event_timer.start(250)
        self._central_sync_timer: QTimer | None = None
        if self._central_sync is not None:
            self._central_sync.start()
            self._central_sync_timer = QTimer(self)
            self._central_sync_timer.timeout.connect(self._refresh_central_sync)
            self._central_sync_timer.start(500)

    # ---------- construction ----------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)
        root.addWidget(self._header())
        root.addWidget(self._search_card())

        columns = QHBoxLayout()
        columns.setSpacing(12)
        columns.addWidget(self._visit_card(), 3)
        columns.addWidget(self._capture_card(), 2)
        root.addLayout(columns, 1)
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

        heading = QLabel("Visitor Exit")
        heading.setObjectName("pageTitle")
        row.addSpacing(24)
        row.addWidget(heading)
        row.addStretch()

        self.simulation = QLabel("HARDWARE DISABLED — SIMULATION MODE")
        self.simulation.setProperty("status", "warning")
        row.addWidget(self.simulation)

        self.open_gate_button = QPushButton("Open gate")
        self.open_gate_button.clicked.connect(self.open_gate_manually)
        row.addWidget(self.open_gate_button)

        self.sign_out_button = QPushButton("Logout")
        self.sign_out_button.setObjectName("dangerButton")
        self.sign_out_button.clicked.connect(self.sign_out)
        row.addWidget(self.sign_out_button)
        return bar

    def _search_card(self) -> QFrame:
        card = QFrame()
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(8)

        row = QHBoxLayout()
        caption = QLabel("Scan the receipt, or search by plate, CNIC or visit number")
        caption.setProperty("section", "true")
        row.addWidget(caption)
        row.addStretch()
        self.receipt_lost = QCheckBox("Receipt lost")
        row.addWidget(self.receipt_lost)
        layout.addLayout(row)

        entry = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("The scanner types here — or key a plate, CNIC or visit number")
        self.search.setMinimumHeight(40)
        # The Orbit is a keyboard wedge and ends its scan with Enter.
        self.search.returnPressed.connect(self.lookup)
        entry.addWidget(self.search, 1)
        find = QPushButton("Find")
        find.setObjectName("primaryButton")
        find.setMinimumHeight(40)
        find.clicked.connect(self.lookup)
        entry.addWidget(find)
        layout.addLayout(entry)

        self.message = QLabel()
        self.message.setObjectName("infoBanner")
        self.message.setWordWrap(True)
        self.message.hide()
        layout.addWidget(self.message)

        self.results = QTableWidget(0, len(RESULT_COLUMNS))
        self.results.setHorizontalHeaderLabels(RESULT_COLUMNS)
        self.results.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.results.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.results.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results.verticalHeader().setVisible(False)
        self.results.verticalHeader().setDefaultSectionSize(38)
        self.results.setCursor(Qt.CursorShape.PointingHandCursor)
        self.results.cellClicked.connect(self._choose_result)
        self._result_columns = ProportionalColumns(self.results, RESULT_WEIGHTS)
        self.results.hide()
        layout.addWidget(self.results)
        return card

    def _visit_card(self) -> QFrame:
        card = QFrame()
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(10)

        self.visit_heading = QLabel("No visit selected")
        self.visit_heading.setProperty("section", "true")
        layout.addWidget(self.visit_heading)

        self.detail_grid = QGridLayout()
        self.detail_grid.setHorizontalSpacing(28)
        self.detail_grid.setVerticalSpacing(12)
        for column in range(3):
            self.detail_grid.setColumnStretch(column, 1)
        layout.addLayout(self.detail_grid)

        self.evidence = QLabel(
            "Entry photographs (driver, ANPR overview, plate crop, ID card) appear here "
            "once the camera adapters are enabled, so the operator can compare faces."
        )
        self.evidence.setProperty("muted", "true")
        self.evidence.setWordWrap(True)
        self.evidence.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.anpr_evidence = QLabel("Entry ANPR photograph unavailable.")
        self.anpr_evidence.setProperty("muted", "true")
        self.anpr_evidence.setWordWrap(True)
        self.anpr_evidence.setAlignment(Qt.AlignmentFlag.AlignCenter)
        evidence_row = QHBoxLayout()
        evidence_row.addWidget(self.evidence, 1)
        evidence_row.addWidget(self.anpr_evidence, 1)
        layout.addLayout(evidence_row)
        layout.addStretch()

        decision_row = QHBoxLayout()
        decision_row.setSpacing(10)
        caption = QLabel("Driver check")
        caption.setProperty("section", "true")
        decision_row.addWidget(caption)
        self.matched_button = QPushButton(f"Matched  ({MATCH_KEY})")
        self.matched_button.setMinimumHeight(44)
        self.matched_button.clicked.connect(lambda: self.set_decision(DriverMatch.MATCHED))
        self.mismatched_button = QPushButton(f"Mismatched  ({MISMATCH_KEY})")
        self.mismatched_button.setMinimumHeight(44)
        self.mismatched_button.setObjectName("dangerButton")
        self.mismatched_button.clicked.connect(lambda: self.set_decision(DriverMatch.MISMATCHED))
        decision_row.addWidget(self.matched_button, 1)
        decision_row.addWidget(self.mismatched_button, 1)
        layout.addLayout(decision_row)

        self.decision_note = QLabel()
        self.decision_note.setWordWrap(True)
        layout.addWidget(self.decision_note)
        return card

    def _capture_card(self) -> QFrame:
        card = QFrame()
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        heading = QLabel("Exit capture")
        heading.setProperty("section", "true")
        layout.addWidget(heading)

        self._panels: dict[str, QLabel] = {}
        for key, title in STREAMS:
            panel = QFrame()
            panel.setObjectName("capturePanel")
            panel.setMinimumHeight(140)
            box = QVBoxLayout(panel)
            box.setContentsMargins(12, 10, 12, 10)
            name = QLabel(title)
            name.setObjectName("captureTitle")
            name.setAlignment(Qt.AlignmentFlag.AlignCenter)
            state = QLabel("No feed")
            state.setProperty("muted", "true")
            state.setAlignment(Qt.AlignmentFlag.AlignCenter)
            state.setMinimumHeight(180)
            box.addWidget(name)
            box.addStretch()
            box.addWidget(state)
            box.addStretch()
            self._panels[key] = state
            layout.addWidget(panel)

        self.capture_button = QPushButton("Capture exit evidence")
        self.capture_button.setMinimumHeight(38)
        self.capture_button.clicked.connect(self.capture)
        layout.addWidget(self.capture_button)

        note = QLabel(
            "Exit takes fresh driver, ANPR and plate images so entry and exit can be "
            "compared later. A camera failure never stops the vehicle leaving."
        )
        note.setProperty("muted", "true")
        note.setWordWrap(True)
        layout.addWidget(note)
        return card

    def _footer(self) -> QFrame:
        bar = QFrame()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel()
        self.status.setProperty("muted", "true")
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)

        shortcuts = QLabel(
            f"<b>{MATCH_KEY}</b> matched   <b>{MISMATCH_KEY}</b> mismatched   "
            f"<b>{SUBMIT_KEY}</b> submit and open   <b>{CLEAR_KEY}</b> next vehicle"
        )
        shortcuts.setObjectName("shortcutPanel")
        row.addWidget(shortcuts)

        self.submit_button = QPushButton("Submit and open gate")
        self.submit_button.setObjectName("primaryButton")
        self.submit_button.setMinimumHeight(44)
        self.submit_button.setMinimumWidth(220)
        self.submit_button.clicked.connect(self.submit)
        row.addWidget(self.submit_button)
        return bar

    def _install_shortcuts(self) -> None:
        for key, slot in (
            (SUBMIT_KEY, self.submit), ("Ctrl+Enter", self.submit),
            (CLEAR_KEY, self.clear),
            (MATCH_KEY, lambda: self.set_decision(DriverMatch.MATCHED)),
            (MISMATCH_KEY, lambda: self.set_decision(DriverMatch.MISMATCHED)),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(slot)

    # ---------- finding the visit ----------

    def read_plate(self, plate: str) -> VisitRecord | None:
        """An ANPR reading. A hit opens the active visit without the operator acting."""
        visit = find_open_visit(self._visits, plate)
        if visit is None:
            self._show_message(f"ANPR read {plate}. {NO_ACTIVE_ENTRY}", error=True)
            return None
        self.select(visit)
        self.status.setText(f"ANPR read {plate} — opened {visit.visit_id} automatically.")
        return visit

    def lookup(self) -> VisitRecord | None:
        """A scan or a typed search."""
        token = self.search.text()
        visit = find_open_visit(self._visits, token)
        if visit is not None:
            self.select(visit)
            self.results.hide()
            return visit

        # Fall back to partial matches so a half-remembered plate still finds the visit.
        self._matches = search_open_visits(self._visits, token)
        if self._matches:
            self._show_results()
            self._show_message(f"{len(self._matches)} open visit(s) match — choose one.", error=False)
        else:
            self.results.hide()
            self._show_message(NO_ACTIVE_ENTRY, error=True)
        return None

    def _show_results(self) -> None:
        self.results.setRowCount(len(self._matches))
        for row, visit in enumerate(self._matches):
            values = (
                visit.visit_id, visit.vehicle_number or "—", visit.visitor_name or "—",
                visit.cnic or "—", visit.entry_time.strftime("%d %b  %H:%M"),
                describe_duration(visit), visit.destination or "—",
            )
            for column, value in enumerate(values):
                self.results.setItem(row, column, QTableWidgetItem(value))
        fit_height_to_rows(self.results, 6)
        self._result_columns.apply()
        self.results.show()

    def _choose_result(self, row: int, _column: int = 0) -> None:
        if 0 <= row < len(self._matches):
            self.select(self._matches[row])
            self.results.hide()

    def select(self, visit: VisitRecord) -> None:
        self.evidence.clear()
        photo = QPixmap(visit.driver_image) if visit.driver_image else QPixmap()
        if not photo.isNull():
            # Keep the entry evidence visible when the live-camera column asks
            # for more vertical space than the window currently has.
            self.evidence.setMinimumHeight(180)
            self.evidence.setPixmap(photo.scaled(360, 180, Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation))
            self.evidence.setToolTip("Driver photographed at entry")
        else:
            self.evidence.setMinimumHeight(0)
            self.evidence.setText("Entry driver photo unavailable. Compare manually and record your decision.")
        anpr = QPixmap(visit.entry_anpr_image) if visit.entry_anpr_image else QPixmap()
        if not anpr.isNull():
            self.anpr_evidence.setMinimumHeight(180)
            self.anpr_evidence.setPixmap(anpr.scaled(
                360, 180, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
            self.anpr_evidence.setToolTip("ANPR overview photographed at entry")
        else:
            self.anpr_evidence.setMinimumHeight(0)
            self.anpr_evidence.setText("Entry ANPR photograph unavailable.")
        self._visit = visit
        self._decision = ""
        self.visit_heading.setText(
            f"{visit.visitor_name or 'Unnamed visitor'} — {visit.vehicle_number or 'no plate'}"
        )
        while self.detail_grid.count():
            item = self.detail_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        pairs = (
            ("Visit", visit.visit_id), ("Receipt", visit.barcode),
            ("Category", visit.vehicle_category or "—"),
            ("CNIC", visit.cnic or "—"), ("Destination", visit.destination or "—"),
            ("Entry time", visit.entry_time.strftime("%d %b %Y  %H:%M:%S")),
            ("Entry operator", visit.entry_operator), ("Inside", describe_duration(visit)),
            ("Mobile", visit.mobile or "—"),
        )
        for index, (label, value) in enumerate(pairs):
            row, column = divmod(index, 3)
            box = QVBoxLayout()
            box.setSpacing(2)
            caption = QLabel(label.upper())
            caption.setObjectName("pageEyebrow")
            text = QLabel(value)
            text.setWordWrap(True)
            box.addWidget(caption)
            box.addWidget(text)
            holder = QWidget()
            holder.setLayout(box)
            self.detail_grid.addWidget(holder, row, column)
        self._update_decision_note()
        self._show_message(f"{visit.visit_id} open since {visit.entry_time:%H:%M}.", error=False)

    # ---------- operator actions ----------

    def set_decision(self, decision: str) -> None:
        if self._visit is None:
            self.status.setText("Find the visit before marking the driver.")
            return
        self._decision = decision
        for button, value in ((self.matched_button, DriverMatch.MATCHED),
                              (self.mismatched_button, DriverMatch.MISMATCHED)):
            button.setProperty("pageActive", decision == value)
            button.style().unpolish(button)
            button.style().polish(button)
        self._update_decision_note()

    def _update_decision_note(self) -> None:
        if not self._decision:
            self.decision_note.setObjectName("")
            self.decision_note.setText("Mark the driver Matched or Mismatched before submitting.")
        elif self._decision == DriverMatch.MISMATCHED:
            # A mismatch is recorded, never enforced: the operator still lets them out.
            self.decision_note.setObjectName("formError")
            self.decision_note.setText(
                "Driver marked MISMATCHED. This is recorded for the file — you may still "
                "submit and open the gate."
            )
        else:
            self.decision_note.setObjectName("")
            self.decision_note.setText("Driver matched.")
        self.decision_note.style().unpolish(self.decision_note)
        self.decision_note.style().polish(self.decision_note)

    def capture(self) -> None:
        # ANPR evidence remains behind its adapter. The real driver frame is saved
        # independently, and its failure never prevents checkout.
        self._captured["anpr"] = True
        self._captured["plate"] = True
        self._capture_driver()
        self._panels["anpr"].setText("Captured (simulated)")
        if self._driver_camera is None:
            self._captured["driver"] = True
            self._panels["driver"].setText("Captured (simulated)")
        self.status.setText(
            "Exit driver photo captured." if self._captured["driver"]
            else "No fresh driver photo. You may still submit."
        )

    def _refresh_driver(self) -> None:
        frame = self._driver_camera.latest()
        panel = self._panels["driver"]
        available = frame.fresh()
        if available and frame.received_at != self._driver_displayed_at:
            pixmap = QPixmap()
            available = pixmap.loadFromData(frame.jpeg, "JPG")
            if available:
                panel.setPixmap(pixmap.scaled(
                    max(320, panel.width()), 240,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                self._driver_displayed_at = frame.received_at
        if not available:
            panel.clear()
            panel.setText(frame.message if not frame.jpeg else "Driver camera stalled — reconnecting.")
        if available != self._driver_available:
            if available or self._driver_available is not None:
                self._audit.record(
                    action="Hardware event", target="Visitor Exit driver camera",
                    summary="Driver camera connected" if available else "Driver camera unavailable",
                    details="Automatic snapshot feed. Manual visitor exit remains available.",
                    severity=AuditSeverity.INFO if available else AuditSeverity.WARNING,
                )
            self._driver_available = available

    def _refresh_anpr_camera(self) -> None:
        frame = self._anpr_camera.latest()
        panel = self._panels["anpr"]
        if frame.fresh():
            pixmap = QPixmap()
            if pixmap.loadFromData(frame.jpeg, "JPG"):
                panel.setPixmap(pixmap.scaled(max(320, panel.width()), 240,
                    Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                return
        panel.clear()
        panel.setText(frame.message if not frame.jpeg else "ANPR camera stalled — reconnecting.")

    def _refresh_anpr(self) -> None:
        readings, (_connected, message) = self._anpr_feed.drain()
        if self._anpr_camera is None:
            self._panels["anpr"].setText(message)
        for reading in readings:
            self.read_plate(reading.plate)

    def _refresh_controller_events(self) -> None:
        readings, (_connected, message) = self._controller_event_feed.drain()
        for reading in readings:
            self._record_controller_reading(reading)
        if readings:
            self.status.setText(message)

    def _refresh_central_sync(self) -> None:
        visits, (connected, message) = self._central_sync.latest()
        if visits is not None:
            selected_id = self._visit.visit_id if self._visit else ""
            self._visits = visits
            if selected_id:
                refreshed = next((visit for visit in visits if visit.visit_id == selected_id), None)
                if refreshed is not None:
                    self._visit = refreshed
        self.status.setText(message)
        self.status.setProperty("syncConnected", connected)

    def _record_controller_reading(self, reading: ControllerReading) -> ETagEvent | None:
        # Both controllers use physical Door 1 for e-tags. Door 2 belongs to VMS.
        if reading.door != 1 or not reading.card:
            return None
        event_id = f"ETL-exit-{reading.event_id}"
        if any(item.event_id == event_id for item in self._events):
            return None
        tag = reading.card.strip()
        note = " — ".join(part for part in (reading.event, reading.note) if part)
        repeated_index = next((index for index, prior in enumerate(self._events)
            if prior.rfid == tag and prior.door == "E-tag Exit" and prior.direction == OUT
            and 0 <= (reading.timestamp - prior.timestamp).total_seconds() <= REPEAT_PASSAGE_SECONDS), None)
        if repeated_index is not None:
            prior = self._events.pop(repeated_index)
            event = replace(prior, timestamp=reading.timestamp, note=note or prior.note)
        else:
            event = build_event(event_id, reading.timestamp, "Exit Controller", "E-tag Exit",
                                OUT, tag, self._etags, note=note)
        self._events.insert(0, event)
        if self._on_etag_event is not None:
            self._on_etag_event(event)
        if event.is_critical and repeated_index is None:
            self._audit.record(
                action="Hardware event", target="E-tag Exit",
                summary=f"Unknown e-tag {event.rfid} read at E-tag Exit",
                details=f"Exit Controller reported RFID {event.rfid}. Check card programming.",
                severity=AuditSeverity.CRITICAL, operator="SYSTEM", timestamp=event.timestamp,
            )
        return event

    def _capture_driver(self) -> None:
        if self._driver_camera is None or self._driver_image:
            return
        frame = self._driver_camera.latest()
        if not frame.fresh(self._driver_after):
            self._captured["driver"] = False
            return
        image = QImage.fromData(frame.jpeg, "JPG")
        try:
            if image.isNull() or not self._image_directory:
                raise OSError("No image or data directory")
            folder = Path(self._image_directory) / "images" / "driver_exit" / datetime.now().strftime("%Y-%m-%d")
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{uuid4().hex}.jpg"
            if not image.save(str(path), "JPG", 95):
                raise OSError("Image write failed")
        except OSError:
            self._captured["driver"] = False
            return
        self._driver_image = str(path)
        self._captured["driver"] = True

    def clear(self) -> None:
        self.evidence.clear()
        self.evidence.setMinimumHeight(0)
        self.evidence.setText("Find a visit to view its entry driver photograph.")
        self.evidence.setToolTip("")
        self.anpr_evidence.clear()
        self.anpr_evidence.setMinimumHeight(0)
        self.anpr_evidence.setText("Find a visit to view its Entry ANPR photograph.")
        self.anpr_evidence.setToolTip("")
        self._visit = None
        self._decision = ""
        self._matches = []
        self._captured = {key: False for key in self._captured}
        self._driver_image = ""
        self._driver_after = time.monotonic()
        self.search.clear()
        self.receipt_lost.setChecked(False)
        self.results.hide()
        self.visit_heading.setText("No visit selected")
        while self.detail_grid.count():
            item = self.detail_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for key, _title in STREAMS:
            self._panels[key].setText("No feed")
        for button in (self.matched_button, self.mismatched_button):
            button.setProperty("pageActive", False)
            button.style().unpolish(button)
            button.style().polish(button)
        self._update_decision_note()
        self.message.hide()
        self.status.setText("Ready for the next vehicle.")
        # The scanner types wherever focus is, so it must come back here every time.
        self.search.setFocus()

    def _show_message(self, text: str, error: bool) -> None:
        self.message.setText(text)
        self.message.setObjectName("formError" if error else "infoBanner")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.show()

    # ---------- leaving ----------

    def open_gate_manually(self) -> None:
        """The operator may always open the barrier, with or without a visit."""
        target = self._visit.visit_id if self._visit else "no visit"
        self._audit.record(
            action="Gate command",
            target="Visitor Exit",
            summary="Visitor Exit opened manually",
            details=(
                f"Operator opened Visitor Exit by hand ({target}). No exit transaction was "
                "submitted. Simulated: no physical command was transmitted."
            ),
            severity=AuditSeverity.WARNING,
        )
        self.status.setText("Visitor Exit opened manually. The action is recorded.")

    def submit(self) -> VisitRecord | None:
        """Close the visit and open the gate. Refuses only when nothing is selected."""
        if self._visit is None:
            self._show_message(NO_ACTIVE_ENTRY, error=True)
            return None
        if not self._decision:
            # The decision is mandatory — but it is the only thing that gates submit.
            self.status.setText("Mark the driver Matched or Mismatched first.")
            self._update_decision_note()
            return None

        if not self._captured["driver"]:
            self._capture_driver()
        if self._driver_image:
            self._visit = replace(self._visit, exit_driver_image=self._driver_image)

        closed = check_out(
            self._visit,
            operator=self._session.operator if self._session else "unknown",
            driver_match=self._decision,
            exit_door="Visitor Exit",
            receipt_lost=self.receipt_lost.isChecked(),
        )
        self._visits = [closed if v.visit_id == closed.visit_id else v for v in self._visits]

        uncaptured = [CAPTURE_LABELS[k] for k in CAPTURE_LABELS if not self._captured[k]]
        details = [
            f"Visit {closed.visit_id} exited at Visitor Exit after {describe_duration(closed)} "
            f"inside. Driver marked {self._decision}."
        ]
        if closed.receipt_lost:
            details.append("Receipt reported lost; the visit was found by manual search.")
        if uncaptured:
            details.append("No exit image captured from: " + ", ".join(uncaptured) + ".")
        details.append("Simulated: no physical command was transmitted.")

        mismatched = self._decision == DriverMatch.MISMATCHED
        self._audit.record(
            action="Visitor decision",
            target=closed.visit_id,
            summary=f"Exit {closed.vehicle_number or 'no plate'} — driver {self._decision}",
            details=" ".join(details),
            severity=AuditSeverity.WARNING if (mismatched or uncaptured or closed.receipt_lost)
            else AuditSeverity.INFO,
        )
        self._audit.record(
            action="Gate command",
            target="Visitor Exit",
            summary=f"Visitor Exit opened for {closed.visit_id}",
            details=(
                f"Exit submitted for {closed.visit_id} and Visitor Exit opened. "
                "Simulated: no physical command was transmitted."
            ),
        )

        if self._on_checkout is not None:
            self._on_checkout(closed)
        self.closed_visit.emit(closed)

        message = f"{closed.visit_id} exited. Driver {self._decision}. Visitor Exit opened."
        self.clear()
        self.status.setText(message)
        return closed

    def sign_out(self) -> None:
        if self._session is not None:
            self._audit.record(
                action="Logout",
                target="Exit portal",
                summary=f"{self._session.display_name} signed out",
                details=f"{self._session.describe()}. Signed in at {self._session.signed_in_at:%d %b %Y %H:%M:%S}.",
            )
            self._session = None
        self.signed_out.emit()
        self.close()

    def closeEvent(self, event) -> None:
        if self._driver_timer is not None:
            self._driver_timer.stop()
        if self._anpr_camera_timer is not None:
            self._anpr_camera_timer.stop()
        if self._anpr_timer is not None:
            self._anpr_timer.stop()
        if self._controller_event_timer is not None:
            self._controller_event_timer.stop()
        if self._central_sync_timer is not None:
            self._central_sync_timer.stop()
        if self._driver_camera is not None:
            self._driver_camera.stop()
        if self._anpr_camera is not None:
            self._anpr_camera.stop()
        if self._anpr_feed is not None:
            self._anpr_feed.stop()
        if self._controller_event_feed is not None:
            self._controller_event_feed.stop()
        if self._central_sync is not None:
            self._central_sync.stop()
        super().closeEvent(event)
