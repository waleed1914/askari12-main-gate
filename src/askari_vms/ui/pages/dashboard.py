from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from askari_vms.dashboard import Metric, category_counts, in_society, metrics
from askari_vms.etag_events import ETagEvent
from askari_vms.etags import ETagRecord
from askari_vms.ui.pagination import Pager
from askari_vms.ui.tables import ProportionalColumns, fit_height_to_rows
from askari_vms.visits import VisitRecord, describe_duration, is_incomplete

COLUMNS = ("Sr #", "Vehicle Type", "Vehicle Number", "Visitor", "CNIC", "Entry", "Inside", "Destination")
COLUMN_WEIGHTS = (5, 13, 13, 17, 14, 15, 8, 15)
ALL_CATEGORIES = "All categories"

INCOMPLETE_COLOUR = QColor("#fff7df")


def _label(text: str, object_name: str | None = None, **properties: str) -> QLabel:
    label = QLabel(text)
    if object_name:
        label.setObjectName(object_name)
    for name, value in properties.items():
        label.setProperty(name, value)
    return label


class MetricCard(QFrame):
    def __init__(self, metric: Metric) -> None:
        super().__init__()
        self.setProperty("card", True)
        self.setMinimumHeight(118)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.addWidget(_label(metric.caption, muted="true"))
        row.addStretch()
        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background: {metric.accent}; border-radius: 5px;")
        row.addWidget(dot)
        layout.addLayout(row)

        self.value = _label(f"{metric.value:,}", metric="true")
        layout.addWidget(self.value)
        layout.addStretch()
        self.note = _label(metric.note, muted="true")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

    def update_metric(self, metric: Metric) -> None:
        self.value.setText(f"{metric.value:,}")
        self.note.setText(metric.note)


class DashboardPage(QScrollArea):
    """What the gate looks like right now: who is inside, and what needs attention."""

    def __init__(
        self,
        visits: Sequence[VisitRecord] = (),
        tags: Sequence[ETagRecord] = (),
        events: Sequence[ETagEvent] = (),
        on_open_entry: Callable[[], None] | None = None,
        on_open_page: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self._visits = list(visits)
        self._tags = list(tags)
        self._events = list(events)
        self._on_open_entry = on_open_entry
        self._on_open_page = on_open_page
        self._visible: list[VisitRecord] = []

        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self._layout = QVBoxLayout(body)
        self._layout.setContentsMargins(28, 24, 28, 28)
        self._layout.setSpacing(16)

        self._build_header()
        self._build_metrics()
        self._category_card, self._category_row = self._tile_card("Vehicles by category")
        self._layout.addWidget(self._category_card)
        self._build_table()
        self._layout.addStretch()
        self.setWidget(body)

        self._load_categories()
        self.refresh()

    # ---------- construction ----------

    def _build_header(self) -> None:
        row = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = _label("Dashboard", section="true")
        subtitle = _label("Live gate activity for this workstation", muted="true")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        row.addLayout(titles)
        row.addStretch()

        self.entry_button = QPushButton("Open Entry Portal")
        self.entry_button.setObjectName("primaryButton")
        self.entry_button.clicked.connect(self._open_entry)
        self.entry_button.setVisible(self._on_open_entry is not None)
        row.addWidget(self.entry_button)

        for label, key in (("Door Controls", "doors"), ("VMS Operations", "vms")):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, page=key: self._open_page(page))
            button.setVisible(self._on_open_page is not None)
            row.addWidget(button)
        self._layout.addLayout(row)

    def _build_metrics(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(12)
        self._cards: dict[str, MetricCard] = {}
        for metric in metrics(self._visits, self._tags, self._events):
            card = MetricCard(metric)
            self._cards[metric.key] = card
            row.addWidget(card, 1)
        self._layout.addLayout(row)

    def _tile_card(self, title: str) -> tuple[QFrame, QHBoxLayout]:
        card = QFrame()
        card.setProperty("card", True)
        box = QVBoxLayout(card)
        box.setContentsMargins(18, 14, 18, 16)
        box.setSpacing(10)
        heading = _label(title, section="true")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(heading)
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        box.addLayout(tiles)
        return card, tiles

    def _build_table(self) -> None:
        heading = _label("Currently in society", section="true")
        self._layout.addWidget(heading)

        filters = QFrame()
        filters.setProperty("card", True)
        row = QHBoxLayout(filters)
        row.setContentsMargins(14, 10, 14, 10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search vehicle, visitor, CNIC, or destination…")
        self.search.setMinimumHeight(36)
        self.search.textChanged.connect(self.refresh)
        row.addWidget(self.search, 1)
        self.category_filter = QComboBox()
        self.category_filter.setMinimumHeight(36)
        self.category_filter.currentTextChanged.connect(self.refresh)
        row.addWidget(self.category_filter)
        self._layout.addWidget(filters)

        self.summary = _label("", muted="true")
        self._layout.addWidget(self.summary)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self._columns = ProportionalColumns(self.table, COLUMN_WEIGHTS)
        self._layout.addWidget(self.table)

        self.pager = Pager(page_size=10)
        self.pager.changed.connect(self._render_table)
        self._layout.addWidget(self.pager)

    # ---------- data ----------

    def set_data(
        self,
        visits: Sequence[VisitRecord],
        tags: Sequence[ETagRecord] = (),
        events: Sequence[ETagEvent] = (),
    ) -> None:
        self._visits = list(visits)
        self._tags = list(tags)
        self._events = list(events)
        self._load_categories()
        self.refresh()

    def _load_categories(self) -> None:
        names = sorted({v.vehicle_category for v in in_society(self._visits) if v.vehicle_category})
        current = self.category_filter.currentText()
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItems([ALL_CATEGORIES, *names])
        if current in names:
            self.category_filter.setCurrentText(current)
        self.category_filter.blockSignals(False)

    def _open_entry(self) -> None:
        if self._on_open_entry is not None:
            self._on_open_entry()

    def _open_page(self, key: str) -> None:
        if self._on_open_page is not None:
            self._on_open_page(key)

    # ---------- display ----------

    def refresh(self, *_args: object) -> None:
        for metric in metrics(self._visits, self._tags, self._events):
            card = self._cards.get(metric.key)
            if card is not None:
                card.update_metric(metric)

        inside = in_society(self._visits)
        self._fill_tiles(self._category_row, category_counts(inside))
        self._category_card.setVisible(bool(inside))

        query = self.search.text().strip().casefold()
        category = self.category_filter.currentText()
        visible = []
        for visit in inside:
            haystack = " ".join((
                visit.vehicle_number, visit.visitor_name, visit.cnic, visit.destination,
            )).casefold()
            if query and query not in haystack:
                continue
            if category and category != ALL_CATEGORIES and visit.vehicle_category != category:
                continue
            visible.append(visit)
        self._visible = visible

        self.summary.setText(
            f"{len(visible)} shown  •  {len(inside)} inside now  •  {len(self._visits)} visits recorded"
        )
        self._render_table()

    @staticmethod
    def _fill_tiles(row: QHBoxLayout, counts: list[tuple[str, int]]) -> None:
        while row.count():
            item = row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Unparent now; deleteLater alone leaves the old tile holding space.
                widget.setParent(None)
                widget.deleteLater()
        for name, count in counts[:10]:
            tile = QFrame()
            tile.setObjectName("groupTile")
            tile.setMinimumHeight(58)
            tile.setStyleSheet(
                "QFrame#groupTile { background: #f8faf9; border: 1px solid #d3ded7;"
                " border-left: 4px solid #2e8b57; border-radius: 7px; }"
            )
            box = QVBoxLayout(tile)
            box.setContentsMargins(12, 8, 12, 8)
            box.setSpacing(2)
            caption = _label(name, muted="true")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption.setWordWrap(True)
            value = _label(f"{count:,}", "groupValue")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(caption)
            box.addWidget(value)
            row.addWidget(tile, 1)
        row.activate()

    def _render_table(self) -> None:
        page = self.pager.slice(self._visible)
        self.table.setRowCount(len(page))
        start = (self.pager.page - 1) * self.pager.page_size
        for index, visit in enumerate(page):
            values = (
                str(start + index + 1),
                visit.vehicle_category or "—",
                visit.vehicle_number or "—",
                visit.visitor_name or "—",
                visit.cnic or "—",
                visit.entry_time.strftime("%d %b  %H:%M"),
                describe_duration(visit),
                visit.destination or "—",
            )
            highlight = is_incomplete(visit)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if highlight:
                    # Same amber as VMS Operations: this record needs chasing.
                    item.setBackground(QBrush(INCOMPLETE_COLOUR))
                self.table.setItem(index, column, item)
        fit_height_to_rows(self.table)
        self._columns.apply()
