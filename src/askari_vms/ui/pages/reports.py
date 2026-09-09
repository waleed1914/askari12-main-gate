from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from askari_vms.audit import AuditLog
from askari_vms.reports import (
    ALL_CATEGORIES,
    ALL_DOORS,
    CSV_HEADER,
    ETAG,
    GROUP_CATEGORY,
    GROUP_DOOR,
    GROUP_NONE,
    GROUP_OPTIONS,
    ReportRow,
    build_rows,
    categories,
    doors,
    filter_rows,
    group_counts,
    to_csv,
    totals,
)
from askari_vms.ui.pagination import Pager
from askari_vms.ui.tables import ProportionalColumns, fit_height_to_rows

COLUMNS = ("Type", "Date & Time", "Name", "Vehicle Number", "Entry Door", "Exit Door", "Captured By")
# Name gets the most room, but never all the slack.
COLUMN_WEIGHTS = (11, 17, 22, 14, 13, 12, 11)
ETAG_BADGE = (QColor("#e6efff"), QColor("#24558c"))
VISITOR_BADGE = (QColor("#fdeaea"), QColor("#a3282f"))


class ReportsPage(QScrollArea):
    """Filters, totals, grouped tiles and a combined record list, with CSV export."""

    def __init__(self, audit_log: AuditLog | None = None,
                 visits: list | None = None, etag_events: list | None = None) -> None:
        super().__init__()
        self._audit = audit_log if audit_log is not None else AuditLog()
        self._rows: list[ReportRow] = build_rows(visits or [], etag_events or [])
        self._visible: list[ReportRow] = []

        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self._layout = QVBoxLayout(body)
        self._layout.setContentsMargins(28, 24, 28, 28)
        self._layout.setSpacing(16)

        self._build_header()
        self._build_filters()
        self._build_totals()
        self._category_card, self._category_row, self._category_heading = self._tile_card(
            "Entries by Vehicle Category")
        self._layout.addWidget(self._category_card)
        self._door_card, self._door_row, _ = self._tile_card("Entries by Door/Gate")
        self._layout.addWidget(self._door_card)
        self._build_table()
        self._layout.addStretch()
        self.setWidget(body)

        self._load_filter_options()
        self.refresh()

    # ---------- construction ----------

    def _build_header(self) -> None:
        row = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("Reports & Analytics")
        title.setProperty("section", "true")
        subtitle = QLabel("Visitor, e-tag, controller and operator activity in one view")
        subtitle.setProperty("muted", "true")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        row.addLayout(titles)
        row.addStretch()
        self.export_button = QPushButton("Export CSV")
        self.export_button.setObjectName("primaryButton")
        self.export_button.clicked.connect(self.export_csv)
        row.addWidget(self.export_button)
        self._layout.addLayout(row)

    def _build_filters(self) -> None:
        card = QFrame()
        card.setProperty("card", True)
        grid = QGridLayout(card)
        grid.setContentsMargins(18, 16, 18, 18)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        heading = QLabel("Filters & Search")
        heading.setProperty("section", "true")
        grid.addWidget(heading, 0, 0, 1, 6)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name, vehicle number, CNIC, or card…")
        self.search.setMinimumHeight(36)
        self.search.returnPressed.connect(self.refresh)

        self.start_date = self._date_edit()
        self.end_date = self._date_edit()

        self.door_filter = QComboBox()
        self.door_filter.setMinimumHeight(36)
        self.category_filter = QComboBox()
        self.category_filter.setMinimumHeight(36)
        self.group_by = QComboBox()
        self.group_by.addItems(GROUP_OPTIONS)
        self.group_by.setMinimumHeight(36)
        self.group_by.currentTextChanged.connect(self.refresh)

        for column, (label, widget) in enumerate((
            ("Search Records", self.search),
            ("Start Date", self.start_date),
            ("End Date", self.end_date),
            ("Door/Gate", self.door_filter),
            ("Vehicle Category", self.category_filter),
            ("Group By", self.group_by),
        )):
            box = QVBoxLayout()
            box.setSpacing(4)
            box.addWidget(QLabel(label))
            box.addWidget(widget)
            grid.addLayout(box, 1, column)
            grid.setColumnStretch(column, 2 if column == 0 else 1)

        buttons = QHBoxLayout()
        apply_button = QPushButton("Apply Filters")
        apply_button.setObjectName("primaryButton")
        apply_button.clicked.connect(self.refresh)
        reset = QPushButton("Reset")
        reset.clicked.connect(self.reset_filters)
        buttons.addWidget(apply_button)
        buttons.addWidget(reset)
        buttons.addStretch()
        grid.addLayout(buttons, 2, 0, 1, 6)
        self._layout.addWidget(card)

    ANY_DATE = QDate(2000, 1, 1)

    @classmethod
    def _date_edit(cls) -> QDateEdit:
        """Sitting on the minimum date means 'no bound'.

        A QDateEdit clamps an invalid date to its minimum, so an unset End Date would
        otherwise read as "before 2000" and filter every record away.
        """
        editor = QDateEdit()
        editor.setCalendarPopup(True)
        editor.setDisplayFormat("dd MMM yyyy")
        editor.setMinimumHeight(36)
        editor.setMinimumDate(cls.ANY_DATE)
        editor.setSpecialValueText("Any date")
        editor.setDate(cls.ANY_DATE)
        return editor

    def _build_totals(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(14)
        self._total_tiles: dict[str, QLabel] = {}
        for key, caption in (("etag", "Total E-Tag Entries"),
                             ("visitor", "Total Visitor/Commercial"),
                             ("total", "Total Entries")):
            tile = QFrame()
            tile.setObjectName("statTile")
            box = QVBoxLayout(tile)
            box.setContentsMargins(18, 14, 18, 14)
            box.setSpacing(4)
            label = QLabel(caption)
            label.setObjectName("statCaption")
            value = QLabel("0")
            value.setObjectName("statValue")
            box.addWidget(label)
            box.addWidget(value)
            self._total_tiles[key] = value
            row.addWidget(tile)
        row.addStretch()
        self._layout.addLayout(row)

    def _tile_card(self, title: str) -> tuple[QFrame, QHBoxLayout, QLabel]:
        card = QFrame()
        card.setProperty("card", True)
        box = QVBoxLayout(card)
        box.setContentsMargins(18, 14, 18, 16)
        box.setSpacing(10)
        heading = QLabel(title)
        heading.setProperty("section", "true")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(heading)
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        box.addLayout(tiles)
        return card, tiles, heading

    def _build_table(self) -> None:
        heading = QLabel("Detailed Records")
        heading.setProperty("section", "true")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(heading)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self._columns = ProportionalColumns(self.table, COLUMN_WEIGHTS)
        self._layout.addWidget(self.table)

        self.pager = Pager()
        self.pager.changed.connect(self._render_table)
        self._layout.addWidget(self.pager)

    # ---------- state ----------

    def _load_filter_options(self) -> None:
        for combo, values, everything in (
            (self.door_filter, doors(self._rows), ALL_DOORS),
            (self.category_filter, categories(self._rows), ALL_CATEGORIES),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems([everything, *values])
            combo.blockSignals(False)

    def _selected_date(self, editor: QDateEdit) -> date | None:
        value = editor.date()
        if not value.isValid() or value == editor.minimumDate():
            return None
        return date(value.year(), value.month(), value.day())

    def reset_filters(self) -> None:
        self.search.clear()
        self.start_date.setDate(self.ANY_DATE)
        self.end_date.setDate(self.ANY_DATE)
        self.door_filter.setCurrentIndex(0)
        self.category_filter.setCurrentIndex(0)
        self.group_by.setCurrentIndex(0)
        self.refresh()

    def refresh(self, *_args: object) -> None:
        self._visible = filter_rows(
            self._rows,
            query=self.search.text(),
            start=self._selected_date(self.start_date),
            end=self._selected_date(self.end_date),
            door=self.door_filter.currentText() or ALL_DOORS,
            category=self.category_filter.currentText() or ALL_CATEGORIES,
        )
        tally = totals(self._visible)
        for key, label in self._total_tiles.items():
            label.setText(f"{tally[key]:,}")

        # The breakdown is always shown; Group By just changes what it breaks down by.
        chosen = self.group_by.currentText()
        grouping = GROUP_CATEGORY if chosen in ("", GROUP_NONE) else chosen
        self._category_heading.setText(f"Entries by {grouping}")
        self._fill_tiles(self._category_row, group_counts(self._visible, grouping), "#2e8b57")
        self._fill_tiles(self._door_row, group_counts(self._visible, GROUP_DOOR), "#bb4a4a")
        self._category_card.setVisible(bool(self._visible))
        self._door_card.setVisible(bool(self._visible))
        self._render_table()

    @staticmethod
    def _fill_tiles(row: QHBoxLayout, counts: list[tuple[str, int]], accent: str) -> None:
        while row.count():
            item = row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Unparent now: deleteLater alone leaves the old tile occupying space,
                # so the replacements inherit a stale width.
                widget.setParent(None)
                widget.deleteLater()
        for name, count in counts[:12]:
            tile = QFrame()
            tile.setObjectName("groupTile")
            tile.setMinimumHeight(60)
            tile.setStyleSheet(
                f"QFrame#groupTile {{ background: #f8faf9; border: 1px solid #d3ded7;"
                f" border-left: 4px solid {accent}; border-radius: 7px; }}"
            )
            box = QVBoxLayout(tile)
            box.setContentsMargins(12, 8, 12, 8)
            box.setSpacing(2)
            caption = QLabel(name)
            caption.setProperty("muted", "true")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption.setWordWrap(True)
            value = QLabel(f"{count:,}")
            value.setObjectName("groupValue")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(caption)
            box.addWidget(value)
            # Equal stretch, and no trailing spacer: the tiles divide the card's full
            # width instead of bunching up on the left.
            row.addWidget(tile, 1)
        row.activate()

    def _render_table(self) -> None:
        page = self.pager.slice(self._visible)
        self.table.setRowCount(len(page))
        for index, row in enumerate(page):
            background, foreground = ETAG_BADGE if row.is_etag else VISITOR_BADGE
            values = (
                row.kind,
                row.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                row.name or "—",
                row.vehicle_number or "—",
                row.entry_door or "—",
                row.exit_door or "N/A",
                row.captured_by or "N/A",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setBackground(QBrush(background))
                    item.setForeground(QBrush(foreground))
                self.table.setItem(index, column, item)
        # The page scrolls, so the table grows to its rows instead of scrolling inside.
        fit_height_to_rows(self.table)
        self._columns.apply()

    # ---------- export ----------

    def export_csv(self) -> str | None:
        """Write the filtered rows, and audit it — data leaving the system matters."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Export report", "askari-vms-report.csv", "CSV files (*.csv)"
        )
        if not path:
            return None
        text = to_csv(self._visible)
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write(text)
        self._audit.record(
            action="Bulk action",
            target="Reports",
            summary=f"Exported {len(self._visible)} report row(s) to CSV",
            details=f"Filtered report written to {path}. Columns: {', '.join(CSV_HEADER)}.",
        )
        return path
