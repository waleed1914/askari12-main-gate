from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from askari_vms.audit import AUDITED_ACTIONS, AuditEvent, AuditLog, AuditSeverity, sample_audit_events
from askari_vms.ui.pagination import Pager


class AuditDetailPage(QWidget):
    def __init__(self, event: AuditEvent, on_back: callable) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Audit Event Details")
        title.setProperty("section", "true")
        subtitle = QLabel("Immutable record — audit events cannot be edited or deleted")
        subtitle.setProperty("muted", "true")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        heading.addLayout(titles)
        heading.addStretch()
        back = QPushButton("Back to Audit Logs")
        back.clicked.connect(on_back)
        heading.addWidget(back)
        layout.addLayout(heading)

        card = QFrame()
        card.setProperty("card", True)
        grid = QGridLayout(card)
        grid.setContentsMargins(24, 22, 24, 24)
        grid.setHorizontalSpacing(34)
        grid.setVerticalSpacing(16)
        values = (
            ("Event ID", event.event_id),
            ("Date and Time", event.timestamp.strftime("%d %b %Y  %I:%M:%S %p")),
            ("Operator", event.operator),
            ("Workstation", event.workstation),
            ("Action", event.action),
            ("Target", event.target),
            ("Severity", event.severity.value),
            ("Summary", event.summary),
            ("Full Details", event.details),
        )
        for index, (label, value) in enumerate(values):
            row, column = divmod(index, 2)
            box = QVBoxLayout()
            caption = QLabel(label.upper())
            caption.setObjectName("pageEyebrow")
            text = QLabel(value)
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            box.addWidget(caption)
            box.addWidget(text)
            grid.addLayout(box, row, column)
        layout.addWidget(card)
        layout.addStretch()


class AuditLogsPage(QWidget):
    def __init__(self, audit_log: AuditLog | None = None) -> None:
        super().__init__()
        self._log = audit_log if audit_log is not None else AuditLog(sample_audit_events())
        self._log.add_listener(lambda _event: self.refresh())
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)
        self.list_page = QWidget()
        self.stack.addWidget(self.list_page)
        self._build_list()
        self.refresh()

    def _build_list(self) -> None:
        layout = QVBoxLayout(self.list_page)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)
        title = QLabel("Audit Logs")
        title.setProperty("section", "true")
        subtitle = QLabel("Permanent record of users, changes, decisions, gate commands, and failures")
        subtitle.setProperty("muted", "true")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        filters = QFrame()
        filters.setProperty("card", True)
        row = QHBoxLayout(filters)
        row.setContentsMargins(14, 12, 14, 12)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search event ID, operator, target, summary, or details…")
        self.search.setMinimumHeight(38)
        self.search.textChanged.connect(self.refresh)
        row.addWidget(self.search, 1)
        self.action = QComboBox()
        self.action.addItems(("All actions", *AUDITED_ACTIONS))
        self.action.setMinimumHeight(38)
        self.action.currentTextChanged.connect(self.refresh)
        row.addWidget(self.action)
        self.severity = QComboBox()
        self.severity.addItems(("All severities", *(item.value for item in AuditSeverity)))
        self.severity.setMinimumHeight(38)
        self.severity.currentTextChanged.connect(self.refresh)
        row.addWidget(self.severity)
        self.view_button = QPushButton("View details")
        self.view_button.clicked.connect(self._show_selected)
        self.view_button.hide()
        row.addWidget(self.view_button)
        layout.addWidget(filters)

        self.message = QLabel()
        self.message.setProperty("muted", "true")
        layout.addWidget(self.message)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(("", "Date / Time", "Operator", "Workstation", "Action", "Target", "Summary", "Severity"))
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(48)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, 42)
        self.table.itemChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(self._show_selected)
        layout.addWidget(self.table, 1)

        self.pager = Pager()
        self.pager.changed.connect(self.refresh)
        layout.addWidget(self.pager)

    def refresh(self, *_args: object) -> None:
        query = self.search.text().strip().casefold()
        action = self.action.currentText()
        severity = self.severity.currentText()
        events = self._log.events()
        visible: list[tuple[int, AuditEvent]] = []
        for index, event in enumerate(events):
            text = " ".join((event.event_id, event.operator, event.workstation, event.action, event.target, event.summary, event.details)).casefold()
            if query and query not in text:
                continue
            if action != "All actions" and event.action != action:
                continue
            if severity != "All severities" and event.severity.value != severity:
                continue
            visible.append((index, event))
        visible = self.pager.slice(visible)
        self.table.blockSignals(True)
        self.table.setRowCount(len(visible))
        backgrounds = {
            AuditSeverity.INFO: QColor("#ffffff"),
            AuditSeverity.WARNING: QColor("#fff7df"),
            AuditSeverity.CRITICAL: QColor("#fde8e8"),
        }
        for table_row, (source_index, event) in enumerate(visible):
            selector = QTableWidgetItem()
            selector.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            selector.setCheckState(Qt.CheckState.Unchecked)
            selector.setData(Qt.ItemDataRole.UserRole, source_index)
            selector.setBackground(backgrounds[event.severity])
            self.table.setItem(table_row, 0, selector)
            values = (
                event.timestamp.strftime("%d %b %Y  %H:%M:%S"), event.operator, event.workstation,
                event.action, event.target, event.summary, event.severity.value,
            )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setBackground(backgrounds[event.severity])
                self.table.setItem(table_row, column, item)
        self.table.blockSignals(False)
        self.view_button.hide()
        self.message.setText(f"{len(visible)} shown  •  {len(events)} audit events")

    def _selected(self) -> list[int]:
        return [int(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) for row in range(self.table.rowCount()) if self.table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def _selection_changed(self, _item: QTableWidgetItem) -> None:
        count = len(self._selected())
        self.view_button.setVisible(count == 1)
        self.message.setText("Check exactly one event to view its complete details." if count > 1 else f"{count} selected" if count else f"{len(self._log.events())} audit events")

    def _show_selected(self, *_args: object) -> None:
        if _args and hasattr(_args[0], "row"):
            index = int(self.table.item(_args[0].row(), 0).data(Qt.ItemDataRole.UserRole))
        else:
            selected = self._selected()
            if len(selected) != 1:
                return
            index = selected[0]
        detail = AuditDetailPage(self._log.events()[index], self._show_list)
        self.stack.addWidget(detail)
        self.stack.setCurrentWidget(detail)

    def _show_list(self) -> None:
        current = self.stack.currentWidget()
        self.stack.setCurrentWidget(self.list_page)
        if current is not self.list_page:
            self.stack.removeWidget(current)
            current.deleteLater()

