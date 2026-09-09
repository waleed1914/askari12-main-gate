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
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from askari_vms.audit import AuditLog, AuditSeverity
from askari_vms.auth import Session
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
    ) -> None:
        super().__init__()
        self._audit = audit_log if audit_log is not None else AuditLog()
        self._visits = list(visits or [])
        self._session = session
        self._on_checkout = on_checkout
        self._gate = gate
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
        layout.addWidget(self.evidence)
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
            self.evidence.setPixmap(photo.scaled(360, 180, Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation))
            self.evidence.setToolTip("Driver photographed at entry")
        else:
            self.evidence.setText("Entry driver photo unavailable. Compare manually and record your decision.")
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
        for key in self._captured:
            self._captured[key] = True
        for key, _title in STREAMS:
            self._panels[key].setText("Captured (simulated)")
        self.status.setText("Captured exit driver, ANPR overview and plate crop (simulated).")

    def clear(self) -> None:
        self.evidence.clear()
        self.evidence.setText("Find a visit to view its entry driver photograph.")
        self.evidence.setToolTip("")
        self._visit = None
        self._decision = ""
        self._matches = []
        self._captured = {key: False for key in self._captured}
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
