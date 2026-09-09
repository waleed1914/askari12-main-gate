from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from askari_vms.audit import AuditLog
from askari_vms.etag_events import IN, OUT, ETagEvent, ETagEventKind, build_event, history_for
from askari_vms.etags import ETagRecord, tags_for_resident
from askari_vms.ui.event_styles import DEFAULT_TEXT, LEGEND, ROW_COLOURS, TEXT_COLOURS
from askari_vms.ui.pagination import Pager
from askari_vms.ui.tables import ProportionalColumns
from askari_vms.ui.pages.resident_profile import ResidentProfilePage

COLUMNS = ("Sr #", "ETag ID", "Full Name", "Entry Time", "Door", "Status", "Action")
ACTION_COLUMN = COLUMNS.index("Action")
# Relative share of the table width. Nothing here holds long text, so stretching one
# column would leave it enormous; these keep the row balanced at any window size.
COLUMN_WEIGHTS = (5, 14, 26, 19, 13, 12, 11)
MINIMUM_COLUMN_WIDTH = 58


class ETagLogsPage(QWidget):
    """Live feed of controller e-tag readings, classified against the registry."""

    def __init__(self, audit_log: AuditLog | None = None, records: list[ETagRecord] | None = None,
                 repository: object | None = None) -> None:
        super().__init__()
        self._audit = audit_log if audit_log is not None else AuditLog()
        self._repository = repository
        self._records = records if records is not None else []
        self._events: list[ETagEvent] = list(repository.list()) if repository is not None else []
        self._visible: list[ETagEvent] = []
        self._sequence = 0
        self._build()
        if not self._records:
            self._seed_demonstration()
        self._sequence = max(self._sequence, len(self._events))
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
        title = QLabel("E-Tag Logs")
        title.setProperty("section", "true")
        subtitle = QLabel("Live controller events, expiry warnings, and unknown-tag alerts")
        subtitle.setProperty("muted", "true")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        layout.addLayout(heading)

        self.banner = QLabel(
            "The controller opens e-tag gates on its own. This page reports what it read; "
            "events arrive once controller polling is enabled."
        )
        self.banner.setObjectName("infoBanner")
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)

        filters = QFrame()
        filters.setProperty("card", True)
        row = QHBoxLayout(filters)
        row.setContentsMargins(14, 12, 14, 12)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search RFID, resident, or vehicle number…")
        self.search.setMinimumHeight(38)
        self.search.textChanged.connect(self.refresh)
        row.addWidget(self.search, 1)
        self.kind_filter = QComboBox()
        self.kind_filter.addItems(("All events", *(kind.value for kind in ETagEventKind)))
        self.kind_filter.setMinimumHeight(38)
        self.kind_filter.currentTextChanged.connect(self.refresh)
        row.addWidget(self.kind_filter)
        self.direction_filter = QComboBox()
        self.direction_filter.addItems(("Both directions", IN, OUT))
        self.direction_filter.setMinimumHeight(38)
        self.direction_filter.currentTextChanged.connect(self.refresh)
        row.addWidget(self.direction_filter)
        layout.addWidget(filters)

        legend = QHBoxLayout()
        legend.setSpacing(8)
        for kind, description in LEGEND:
            chip = QLabel(f"  {kind.value} — {description}  ")
            colour = ROW_COLOURS[kind].name()
            text = TEXT_COLOURS.get(kind, DEFAULT_TEXT).name()
            chip.setStyleSheet(
                f"background: {colour}; color: {text}; border: 1px solid #c1cfc6;"
                "border-radius: 9px; padding: 4px 6px; font-size: 11px; font-weight: 600;"
            )
            legend.addWidget(chip)
        legend.addStretch()
        layout.addLayout(legend)

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
        self.table.setToolTip("Click a row to open the resident profile and full history")
        self.table.cellClicked.connect(self._open_profile)
        layout.addWidget(self.table, 1)

        self.pager = Pager()
        self.pager.changed.connect(self.refresh)
        layout.addWidget(self.pager)

    def _seed_demonstration(self) -> None:
        """A realistic registry and feed so paging and every colour rule are visible."""
        from askari_vms.demo_data import etag_records

        today = date.today()
        self._records = etag_records(today=today)
        if self._repository is None and not self._events:
            self._seed_readings()

    def _seed_readings(self) -> None:
        """Readings drawn from the registry, plus a few tags the controller does not know."""
        import random

        rng = random.Random(20260903)
        now = datetime.now().replace(microsecond=0)
        pool = [record.rfid for record in self._records]
        readings = []
        for minutes in sorted(rng.sample(range(2, 60 * 24 * 5), 180), reverse=True):
            rfid = rng.choice(pool) if rng.random() > 0.04 else str(rng.randrange(10000000, 99999999))
            inbound = rng.random() < 0.5
            readings.append((
                now - timedelta(minutes=minutes),
                "Entry Controller" if inbound else "Exit Controller",
                "E-tag Entry" if inbound else "E-tag Exit",
                IN if inbound else OUT,
                rfid,
            ))
        for timestamp, controller, door, direction, rfid in reversed(readings):
            self.record_reading(controller, door, direction, rfid, timestamp=timestamp, refresh=False)

    # ---------- public API used by the controller adapter ----------

    def record_reading(
        self,
        controller_name: str,
        door: str,
        direction: str,
        rfid: str,
        timestamp: datetime | None = None,
        note: str = "",
        refresh: bool = True,
    ) -> ETagEvent:
        """Classify one controller reading and add it to the feed."""
        self._sequence += 1
        event = build_event(
            event_id=f"ETL-{self._sequence:06d}",
            timestamp=timestamp or datetime.now().replace(microsecond=0),
            controller_name=controller_name,
            door=door,
            direction=direction,
            rfid=rfid,
            records=self._records,
            note=note,
        )
        self._events.insert(0, event)
        if self._repository is not None:
            self._repository.append(event)
        if event.is_critical:
            # The controller should never have opened for a tag we do not know.
            self._audit.record(
                action="Hardware event",
                target=door,
                summary=f"Unknown e-tag {event.rfid} read at {door}",
                details=(
                    f"{controller_name} reported RFID {event.rfid} at {door} ({direction}), "
                    "which is not present in the E-Tag registry. Check controller card programming."
                ),
                severity=event.severity,
                operator="SYSTEM",
                timestamp=event.timestamp,
            )
        if refresh:
            self.refresh()
        return event

    def events(self) -> tuple[ETagEvent, ...]:
        return tuple(self._events)

    # ---------- display ----------

    # ---------- layout ----------

    # ---------- navigation ----------

    def _action_button(self, row: int) -> QPushButton:
        button = QPushButton("View profile")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMaximumWidth(130)
        button.clicked.connect(lambda _checked=False, index=row: self._open_profile(index))
        return button


    def _open_profile(self, row: int, _column: int = 0) -> None:
        """Open the resident behind the clicked reading, or the tag itself if unknown."""
        if not 0 <= row < len(self._visible):
            return
        event = self._visible[row]
        if event.kind is ETagEventKind.UNKNOWN:
            tags: tuple[ETagRecord, ...] = ()
            history = history_for(self._events, {event.rfid})
            unknown = event.rfid
        else:
            match = next((r for r in self._records if r.rfid == event.rfid), None)
            tags = tags_for_resident(self._records, match.user_id) if match else ()
            history = history_for(self._events, {tag.rfid for tag in tags})
            unknown = ""
            if not tags:
                return
        profile = ResidentProfilePage(tags, history, self._show_list, unknown_rfid=unknown)
        self.stack.addWidget(profile)
        self.stack.setCurrentWidget(profile)

    def _show_list(self) -> None:
        current = self.stack.currentWidget()
        self.stack.setCurrentWidget(self.list_page)
        if current is not self.list_page:
            self.stack.removeWidget(current)
            current.deleteLater()

    # ---------- display ----------

    def refresh(self, *_args: object) -> None:
        query = self.search.text().strip().casefold()
        kind = self.kind_filter.currentText()
        direction = self.direction_filter.currentText()

        visible: list[ETagEvent] = []
        for event in self._events:
            haystack = " ".join((event.rfid, event.resident_name, event.vehicle_number)).casefold()
            if query and query not in haystack:
                continue
            if kind != "All events" and event.kind.value != kind:
                continue
            if direction != "Both directions" and event.direction != direction:
                continue
            visible.append(event)

        self._visible = self.pager.slice(visible)
        visible = self._visible
        self.table.setRowCount(len(visible))
        for row, event in enumerate(visible):
            values = (
                str(row + 1),
                event.rfid,
                event.resident_name or "—",
                event.timestamp.strftime("%d %b %Y  %H:%M:%S"),
                event.door,
                event.kind.value,
            )
            background = QBrush(ROW_COLOURS[event.kind])
            foreground = TEXT_COLOURS.get(event.kind)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setBackground(background)
                if foreground is not None:
                    item.setForeground(QBrush(foreground))
                self.table.setItem(row, column, item)
            self.table.setCellWidget(row, ACTION_COLUMN, self._action_button(row))

        critical = sum(event.is_critical for event in self._events)
        expiring = sum(event.kind is ETagEventKind.EXPIRING for event in self._events)
        parts = [f"{len(self._events)} events"]
        if expiring:
            parts.append(f"{expiring} expiring within 10 days")
        if critical:
            parts.append(f"{critical} critical unknown-tag event(s)")
        self.summary.setText("  •  ".join(parts))
        self._columns.apply()
