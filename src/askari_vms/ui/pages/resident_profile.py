from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from askari_vms.etag_events import ETagEvent, ETagEventKind, days_until_expiry
from askari_vms.etags import ETagRecord, ETagState, expiry_state
from askari_vms.ui.event_styles import ROW_COLOURS, TEXT_COLOURS

TAG_COLUMNS = ("E-Tag RFID", "Vehicle No", "Type", "Make", "Model", "Issued", "Expires", "Days left", "Status")
HISTORY_COLUMNS = ("Time", "E-Tag RFID", "Vehicle No", "Controller", "Door", "Direction", "Event")

_STATE_TEXT = {
    ETagState.ACTIVE: "#17643a",
    ETagState.EXPIRING: "#24558c",
    ETagState.EXPIRED: "#9b2525",
    ETagState.BLOCKED: "#9b2525",
}


class ResidentProfilePage(QScrollArea):
    """Everything known about one resident: their details, their tags, and every reading.

    Opened from a log row. An unrecognised tag has no resident, so the page then reports
    the tag itself and every time the controller read it.
    """

    def __init__(
        self,
        tags: Sequence[ETagRecord],
        history: Sequence[ETagEvent],
        on_back: Callable[[], None],
        unknown_rfid: str = "",
        today: date | None = None,
    ) -> None:
        super().__init__()
        self._tags = list(tags)
        self._history = list(history)
        self._today = today or date.today()
        self._unknown_rfid = unknown_rfid

        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)

        layout.addLayout(self._heading(on_back))
        if self._unknown_rfid:
            layout.addWidget(self._unknown_card())
        else:
            layout.addWidget(self._details_card())
            layout.addWidget(self._tags_card())
        if not self._unknown_rfid and any(tag.comments.strip() for tag in self._tags):
            layout.addWidget(self._comments_card())
        layout.addWidget(self._history_card())
        layout.addStretch()
        self.setWidget(body)

    # ---------- sections ----------

    def _heading(self, on_back: Callable[[], None]) -> QHBoxLayout:
        row = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        if self._unknown_rfid:
            name, detail = "Unregistered tag", f"RFID {self._unknown_rfid} is not in the E-Tag registry"
        else:
            first = self._tags[0]
            name = first.resident_name or "Resident"
            held = f"{len(self._tags)} e-tag" + ("s" if len(self._tags) != 1 else "")
            detail = f"User ID {first.user_id}  •  {held}  •  {len(self._history)} recorded events"
        title = QLabel(name)
        title.setProperty("section", "true")
        subtitle = QLabel(detail)
        subtitle.setProperty("muted", "true")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        row.addLayout(titles)
        row.addStretch()
        back = QPushButton("Back to E-Tag Logs")
        back.clicked.connect(on_back)
        row.addWidget(back)
        return row

    @staticmethod
    def _card(title: str) -> tuple[QFrame, QGridLayout]:
        card = QFrame()
        card.setProperty("card", True)
        grid = QGridLayout(card)
        grid.setContentsMargins(22, 20, 22, 22)
        grid.setHorizontalSpacing(30)
        grid.setVerticalSpacing(14)
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

    def _details_card(self) -> QFrame:
        record = self._tags[0]
        card, grid = self._card("Resident")
        for index, (label, value) in enumerate((
            ("Member ID", record.member_id),
            ("User ID", record.user_id),
            ("Resident name", record.resident_name),
            ("S/O, F/O, D/O", record.care_of),
            ("CNIC", record.cnic),
            ("Street No", record.street_no),
            ("H/Apt No", record.house_no),
            ("Mobile No", record.mobile_no),
            ("Address", record.address),
            ("LESCO Ref No", record.lesco_ref_no),
            ("Gender", record.gender),
            ("Position", record.position),
            ("Station / Unit", record.station_unit),
            ("Department", record.department),
            ("Challan No", record.challan_number),
        )):
            self._pair(grid, index, label, value)
        return card

    def _unknown_card(self) -> QFrame:
        card, grid = self._card("Unrecognised tag")
        note = QLabel(
            f"RFID {self._unknown_rfid} was read by a controller but is not registered. "
            "The controller should not have opened for it — check its card programming. "
            "Every reading below was raised as a critical audit event."
        )
        note.setWordWrap(True)
        note.setProperty("muted", "true")
        grid.addWidget(note, 1, 0, 1, 4)
        return card

    def _tags_card(self) -> QFrame:
        card, grid = self._card("E-Tags held")
        note = QLabel("A resident may hold several tags, each on a different vehicle.")
        note.setProperty("muted", "true")
        grid.addWidget(note, 1, 0, 1, 4)

        table = QTableWidget(len(self._tags), len(TAG_COLUMNS))
        table.setHorizontalHeaderLabels(TAG_COLUMNS)
        self._plain_table(table)
        for row, record in enumerate(self._tags):
            state = expiry_state(record, self._today)
            remaining = days_until_expiry(record, self._today)
            values = (
                record.rfid, record.vehicle_number, record.vehicle_type, record.make, record.model,
                record.issue_date.strftime("%d %b %Y"), record.expiry_date.strftime("%d %b %Y"),
                f"{remaining} day(s)" if remaining >= 0 else f"{abs(remaining)} day(s) ago",
                state.value,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == len(values) - 1:
                    item.setForeground(QBrush(_STATE_TEXT[state]))
                table.setItem(row, column, item)
        self._fit_to_rows(table, max(len(self._tags), 1))
        grid.addWidget(table, 2, 0, 1, 4)
        return card

    def _comments_card(self) -> QFrame:
        card, grid = self._card("Comments")
        for index, tag in enumerate(t for t in self._tags if t.comments.strip()):
            self._pair(grid, index * 4, f"{tag.rfid} — {tag.vehicle_number}", tag.comments)
        return card

    def _history_card(self) -> QFrame:
        card, grid = self._card("Event history")
        if not self._history:
            empty = QLabel("No controller readings recorded for this tag yet.")
            empty.setProperty("muted", "true")
            grid.addWidget(empty, 1, 0, 1, 4)
            return card

        counts: dict[ETagEventKind, int] = {}
        for event in self._history:
            counts[event.kind] = counts.get(event.kind, 0) + 1
        summary = QLabel(
            f"Last seen {self._history[0].timestamp:%d %b %Y  %H:%M:%S}  •  "
            + "  •  ".join(f"{count} × {kind.value}" for kind, count in counts.items())
        )
        summary.setProperty("muted", "true")
        summary.setWordWrap(True)
        grid.addWidget(summary, 1, 0, 1, 4)

        table = QTableWidget(len(self._history), len(HISTORY_COLUMNS))
        table.setHorizontalHeaderLabels(HISTORY_COLUMNS)
        self._plain_table(table)
        for row, event in enumerate(self._history):
            values = (
                event.timestamp.strftime("%d %b %Y  %H:%M:%S"), event.rfid,
                event.vehicle_number or "—", event.controller_name, event.door,
                event.direction, event.kind.value,
            )
            background = QBrush(ROW_COLOURS[event.kind])
            foreground = TEXT_COLOURS.get(event.kind)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setBackground(background)
                if foreground is not None:
                    item.setForeground(QBrush(foreground))
                table.setItem(row, column, item)
        self._fit_to_rows(table, min(len(self._history), 10))
        grid.addWidget(table, 2, 0, 1, 4)
        return card

    @staticmethod
    def _fit_to_rows(table: QTableWidget, visible_rows: int) -> None:
        """Size to header + N rows exactly.

        Guessing the header height leaves a dead strip above the bottom border, which
        reads as a doubled line, so measure it.
        """
        header = table.horizontalHeader().sizeHint().height()
        rows = sum(table.rowHeight(row) for row in range(min(visible_rows, table.rowCount())))
        table.setFixedHeight(header + rows + 2 * table.frameWidth())

    @staticmethod
    def _plain_table(table: QTableWidget) -> None:
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(44)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
