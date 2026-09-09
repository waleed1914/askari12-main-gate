from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from askari_vms.audit import AuditLog, AuditSeverity
from askari_vms.ui.pagination import Pager
from askari_vms.ui.tables import ProportionalColumns
from askari_vms.visits import (
    DriverMatch,
    VisitRecord,
    VisitState,
    check_out,
    counts,
    describe_duration,
    is_incomplete,
    missing_fields,
    visits_for_vehicle,
)

COLUMNS = ("Sr #", "Visit ID", "Vehicle No", "Visitor Name", "Destination", "Category",
           "Entry Time", "Exit Time", "Status", "Action")
COLUMN_WEIGHTS = (4, 9, 10, 15, 14, 8, 13, 13, 8, 8)
ACTION_COLUMN = COLUMNS.index("Action")
MINIMUM_COLUMN_WIDTH = 54

INSIDE_COLOUR = QColor("#eef8f1")
EXITED_COLOUR = QColor("#ffffff")
INCOMPLETE_COLOUR = QColor("#fff7df")
MISMATCH_COLOUR = QColor("#fde8e8")

ALL_STATUSES = "All statuses"


def row_colour(visit: VisitRecord) -> QColor:
    """Incomplete and mismatched records must stand out; they are what Admin chases."""
    if visit.mismatched:
        return MISMATCH_COLOUR
    if is_incomplete(visit):
        return INCOMPLETE_COLOUR
    return INSIDE_COLOUR if visit.is_inside else EXITED_COLOUR


class VisitDetailPage(QScrollArea):
    """One visitor transaction, with its history for the same vehicle."""

    def __init__(
        self,
        visit: VisitRecord,
        history: Sequence[VisitRecord],
        on_back: Callable[[], None],
        on_checkout: Callable[[VisitRecord], None],
    ) -> None:
        super().__init__()
        self.visit = visit
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel(visit.visitor_name or "Unnamed visitor")
        title.setProperty("section", "true")
        subtitle = QLabel(f"{visit.visit_id}  •  {visit.vehicle_number or 'no plate recorded'}  •  {visit.state.value}")
        subtitle.setProperty("muted", "true")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        head.addLayout(titles)
        head.addStretch()
        if visit.is_inside:
            self.checkout_button = QPushButton("Manual checkout")
            self.checkout_button.setObjectName("primaryButton")
            self.checkout_button.clicked.connect(lambda: on_checkout(visit))
            head.addWidget(self.checkout_button)
        back = QPushButton("Back to VMS Operations")
        back.clicked.connect(on_back)
        head.addWidget(back)
        layout.addLayout(head)

        gaps = missing_fields(visit)
        if gaps:
            warning = QLabel(
                "Submitted with missing data: " + ", ".join(gaps)
                + f".  Entry operator: {visit.entry_operator}."
            )
            warning.setObjectName("formError")
            warning.setWordWrap(True)
            layout.addWidget(warning)

        layout.addWidget(self._details_card(visit))
        layout.addWidget(self._evidence_card())
        if len(history) > 1:
            layout.addWidget(self._history_card(history))
        layout.addStretch()
        self.setWidget(body)

    @staticmethod
    def _card(title: str) -> tuple[QFrame, QGridLayout]:
        card = QFrame()
        card.setProperty("card", True)
        grid = QGridLayout(card)
        grid.setContentsMargins(22, 20, 22, 22)
        grid.setHorizontalSpacing(30)
        grid.setVerticalSpacing(14)
        for column in range(4):
            grid.setColumnStretch(column, 1)
        heading = QLabel(title)
        heading.setProperty("section", "true")
        grid.addWidget(heading, 0, 0, 1, 4)
        return card, grid

    @staticmethod
    def _pair(grid: QGridLayout, index: int, label: str, value: str) -> None:
        row, column = divmod(index, 4)
        box = QVBoxLayout()
        box.setSpacing(2)
        caption = QLabel(label.upper())
        caption.setObjectName("pageEyebrow")
        text = QLabel(value or "—")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(caption)
        box.addWidget(text)
        grid.addLayout(box, row + 1, column)

    def _details_card(self, visit: VisitRecord) -> QFrame:
        card, grid = self._card("Visit")
        exit_time = visit.exit_time.strftime("%d %b %Y  %H:%M:%S") if visit.exit_time else ""
        for index, (label, value) in enumerate((
            ("Visit ID", visit.visit_id),
            ("Receipt barcode", visit.barcode),
            ("Visitor name", visit.visitor_name),
            ("CNIC", visit.cnic),
            ("Mobile", visit.mobile),
            ("Vehicle number", visit.vehicle_number),
            ("Category", visit.vehicle_category),
            ("Destination", visit.destination),
            ("Entry time", visit.entry_time.strftime("%d %b %Y  %H:%M:%S")),
            ("Entry door", visit.entry_door),
            ("Entry operator", visit.entry_operator),
            ("Time inside", describe_duration(visit)),
            ("Exit time", exit_time),
            ("Exit door", visit.exit_door),
            ("Exit operator", visit.exit_operator),
            ("Driver check", visit.driver_match + ("  (receipt lost)" if visit.receipt_lost else "")),
        )):
            self._pair(grid, index, label, value)
        return card

    def _evidence_card(self) -> QFrame:
        card, grid = self._card("Captured evidence")
        if self.visit.driver_image:
            photo = QPixmap(self.visit.driver_image)
            if not photo.isNull():
                preview = QLabel()
                preview.setPixmap(photo.scaled(480, 240, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
                grid.addWidget(QLabel("Entry driver photograph"), 1, 0, 1, 4)
                grid.addWidget(preview, 2, 0, 1, 4)
                return card
        note = QLabel(
            "Driver photo, ANPR overview, cropped plate and ID card image are captured for "
            "every entry and exit. Images appear here once the camera adapters are enabled."
        )
        note.setProperty("muted", "true")
        note.setWordWrap(True)
        grid.addWidget(note, 1, 0, 1, 4)
        return card

    def _history_card(self, history: Sequence[VisitRecord]) -> QFrame:
        card, grid = self._card("Previous visits by this vehicle")
        table = QTableWidget(len(history), 5)
        table.setHorizontalHeaderLabels(("Visit ID", "Entry Time", "Exit Time", "Destination", "Status"))
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(42)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        for row, item in enumerate(history):
            values = (
                item.visit_id,
                item.entry_time.strftime("%d %b %Y  %H:%M"),
                item.exit_time.strftime("%d %b %Y  %H:%M") if item.exit_time else "—",
                item.destination or "—",
                item.state.value,
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setBackground(QBrush(row_colour(item)))
                table.setItem(row, column, cell)
        visible = min(len(history), 8)
        header = table.horizontalHeader().sizeHint().height()
        table.setFixedHeight(header + sum(table.rowHeight(r) for r in range(visible)) + 2 * table.frameWidth())
        grid.addWidget(table, 1, 0, 1, 4)
        return card


class VmsOperationsPage(QWidget):
    """Visitor transactions: active visits, checkouts, and incomplete records."""

    def __init__(self, audit_log: AuditLog | None = None, visits: list[VisitRecord] | None = None,
                 repository: object | None = None) -> None:
        super().__init__()
        self._audit = audit_log if audit_log is not None else AuditLog()
        self._repository = repository
        if visits is None and repository is not None:
            visits = repository.list()
        self._visits = visits if visits is not None else []
        self._visible: list[VisitRecord] = []
        self._build()
        if not self._visits:
            self._seed_demonstration()
        self._load_categories()
        self.refresh()

    # ---------- construction ----------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)
        self.list_page = QWidget()
        self.stack.addWidget(self.list_page)

        layout = QVBoxLayout(self.list_page)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)

        heading = QVBoxLayout()
        heading.setSpacing(2)
        title = QLabel("VMS Operations")
        title.setProperty("section", "true")
        subtitle = QLabel("Visitor transactions, active visits, checkouts, and incomplete records")
        subtitle.setProperty("muted", "true")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        layout.addLayout(heading)

        filters = QFrame()
        filters.setProperty("card", True)
        row = QHBoxLayout(filters)
        row.setContentsMargins(14, 12, 14, 12)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search visit ID, barcode, vehicle, visitor, CNIC, or destination…")
        self.search.setMinimumHeight(38)
        self.search.textChanged.connect(self.refresh)
        row.addWidget(self.search, 1)
        self.status_filter = QComboBox()
        self.status_filter.addItems((ALL_STATUSES, VisitState.INSIDE.value, VisitState.EXITED.value))
        self.status_filter.setMinimumHeight(38)
        self.status_filter.currentTextChanged.connect(self.refresh)
        row.addWidget(self.status_filter)
        self.category_filter = QComboBox()
        self.category_filter.setMinimumHeight(38)
        self.category_filter.currentTextChanged.connect(self.refresh)
        row.addWidget(self.category_filter)
        self.missing_only = QCheckBox("Missing data only")
        self.missing_only.stateChanged.connect(self.refresh)
        row.addWidget(self.missing_only)
        self.mismatched_only = QCheckBox("Mismatched only")
        self.mismatched_only.stateChanged.connect(self.refresh)
        row.addWidget(self.mismatched_only)
        layout.addWidget(filters)

        self.summary = QLabel()
        self.summary.setProperty("muted", "true")
        layout.addWidget(self.summary)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self._columns = ProportionalColumns(self.table, COLUMN_WEIGHTS, MINIMUM_COLUMN_WIDTH)
        self.table.setCursor(Qt.CursorShape.PointingHandCursor)
        self.table.setToolTip("Click a row to open the full visit record")
        self.table.cellClicked.connect(self._open_detail)
        layout.addWidget(self.table, 1)

        self.pager = Pager()
        self.pager.changed.connect(self.refresh)
        layout.addWidget(self.pager)

    def _seed_demonstration(self) -> None:
        from askari_vms.demo_data import visit_records

        self._visits = visit_records()

    def _load_categories(self) -> None:
        categories = sorted({visit.vehicle_category for visit in self._visits if visit.vehicle_category})
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItems(("All categories", *categories))
        self.category_filter.blockSignals(False)

    # ---------- layout ----------

    # ---------- navigation ----------

    def _open_detail(self, row: int, _column: int = 0) -> None:
        if not 0 <= row < len(self._visible):
            return
        visit = self._visible[row]
        history = visits_for_vehicle(self._visits, visit.vehicle_number) if visit.vehicle_number else (visit,)
        detail = VisitDetailPage(visit, history, self._show_list, self._manual_checkout)
        self.stack.addWidget(detail)
        self.stack.setCurrentWidget(detail)

    def _show_list(self) -> None:
        current = self.stack.currentWidget()
        self.stack.setCurrentWidget(self.list_page)
        if current is not self.list_page:
            self.stack.removeWidget(current)
            current.deleteLater()

    def _manual_checkout(self, visit: VisitRecord) -> None:
        """Close a visit by hand. The operator is never blocked; the action is audited."""
        index = next((i for i, item in enumerate(self._visits) if item.visit_id == visit.visit_id), None)
        if index is None or not self._visits[index].is_inside:
            return
        closed = check_out(self._visits[index], operator="admin", exit_door="Visitor Exit")
        self._visits[index] = closed
        if self._repository is not None:
            self._repository.save(closed)
        self._audit.record(
            action="Record updated",
            target=closed.visit_id,
            summary=f"Manual checkout of {closed.vehicle_number or 'unknown vehicle'}",
            details=(
                f"Visit {closed.visit_id} was closed by hand from VMS Operations after "
                f"{describe_duration(closed)} inside. Entry operator {closed.entry_operator}; "
                "no exit evidence was captured because this bypassed the Exit portal."
            ),
            severity=AuditSeverity.WARNING,
        )
        self._show_list()
        self.refresh()

    # ---------- display ----------

    def _action_button(self, row: int, visit: VisitRecord) -> QPushButton:
        button = QPushButton("Check out" if visit.is_inside else "View")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _checked=False, index=row: self._open_detail(index))
        return button

    def refresh(self, *_args: object) -> None:
        query = self.search.text().strip().casefold()
        status = self.status_filter.currentText()
        category = self.category_filter.currentText()
        missing_only = self.missing_only.isChecked()
        mismatched_only = self.mismatched_only.isChecked()

        visible: list[VisitRecord] = []
        for visit in self._visits:
            haystack = " ".join((
                visit.visit_id, visit.barcode, visit.vehicle_number, visit.visitor_name,
                visit.cnic, visit.destination,
            )).casefold()
            if query and query not in haystack:
                continue
            if status != ALL_STATUSES and visit.state.value != status:
                continue
            if category and category != "All categories" and visit.vehicle_category != category:
                continue
            if missing_only and not is_incomplete(visit):
                continue
            if mismatched_only and not visit.mismatched:
                continue
            visible.append(visit)

        self._visible = self.pager.slice(visible)
        visible = self._visible
        self.table.setRowCount(len(visible))
        for row, visit in enumerate(visible):
            values = (
                str(row + 1), visit.visit_id, visit.vehicle_number or "—",
                visit.visitor_name or "—", visit.destination or "—",
                visit.vehicle_category or "—",
                visit.entry_time.strftime("%d %b %Y  %H:%M:%S"),
                visit.exit_time.strftime("%d %b %Y  %H:%M:%S") if visit.exit_time else "—",
                visit.state.value,
            )
            background = QBrush(row_colour(visit))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setBackground(background)
                self.table.setItem(row, column, item)
            self.table.setCellWidget(row, ACTION_COLUMN, self._action_button(row, visit))

        tally = counts(self._visits)
        parts = [
            f"{tally['inside']} inside now",
            f"{tally['exited']} checked out",
        ]
        if tally["incomplete"]:
            parts.append(f"{tally['incomplete']} with missing data")
        if tally["mismatched"]:
            parts.append(f"{tally['mismatched']} mismatched driver")
        self.summary.setText("  •  ".join(parts))
        self._columns.apply()
