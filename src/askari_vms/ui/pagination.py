"""A reusable pager: rows per page, page numbers, and a 'showing X-Y of Z' line.

Every list in the reference system pages its results, and the real data sets are large
enough that rendering everything is not an option.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget

T = TypeVar("T")

PAGE_SIZES = (10, 20, 50, 100)
DEFAULT_PAGE_SIZE = 20
_ELLIPSIS = "…"


def page_numbers(current: int, total: int, window: int = 2) -> list[int | str]:
    """Page buttons to show: always the ends, plus a window around the current page."""
    if total <= 1:
        return [1]
    wanted = {1, total, current}
    for offset in range(1, window + 1):
        wanted.add(current - offset)
        wanted.add(current + offset)
    pages = sorted(p for p in wanted if 1 <= p <= total)

    out: list[int | str] = []
    previous = 0
    for page in pages:
        gap = page - previous - 1
        if previous and gap == 1:
            out.append(previous + 1)   # never hide a single page behind an ellipsis
        elif previous and gap > 1:
            out.append(_ELLIPSIS)
        out.append(page)
        previous = page
    return out


class Pager(QWidget):
    """Owns the current page and page size; emits `changed` when either moves."""

    changed = Signal()

    def __init__(self, page_size: int = DEFAULT_PAGE_SIZE) -> None:
        super().__init__()
        self.setObjectName("pager")
        self._page = 1
        self._total = 0

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.summary = QLabel()
        self.summary.setProperty("muted", "true")
        row.addWidget(self.summary)
        row.addStretch()

        row.addWidget(QLabel("Rows per page:"))
        self.size_box = QComboBox()
        self.size_box.addItems([str(size) for size in PAGE_SIZES])
        self.size_box.setCurrentText(str(page_size if page_size in PAGE_SIZES else DEFAULT_PAGE_SIZE))
        self.size_box.setMinimumHeight(32)
        self.size_box.currentTextChanged.connect(self._size_changed)
        row.addWidget(self.size_box)

        self.prev_button = QPushButton("Prev")
        self.prev_button.clicked.connect(lambda: self.set_page(self._page - 1))
        row.addWidget(self.prev_button)

        self._buttons_host = QWidget()
        self._buttons_host.setObjectName("pagerButtons")
        self._buttons = QHBoxLayout(self._buttons_host)
        self._buttons.setContentsMargins(0, 0, 0, 0)
        self._buttons.setSpacing(4)
        row.addWidget(self._buttons_host)

        self.next_button = QPushButton("Next")
        self.next_button.clicked.connect(lambda: self.set_page(self._page + 1))
        row.addWidget(self.next_button)

    # ---------- state ----------

    @property
    def page(self) -> int:
        return self._page

    @property
    def page_size(self) -> int:
        return int(self.size_box.currentText())

    @property
    def page_count(self) -> int:
        return max(1, -(-self._total // self.page_size))  # ceiling division

    def set_page(self, page: int) -> None:
        page = max(1, min(page, self.page_count))
        if page != self._page:
            self._page = page
            self.changed.emit()

    def _size_changed(self, _text: str) -> None:
        self._page = 1  # a different page size invalidates the current position
        self.changed.emit()

    def slice(self, rows: Sequence[T]) -> list[T]:
        """The rows belonging to the current page, clamping if the list shrank."""
        self._total = len(rows)
        if self._page > self.page_count:
            self._page = self.page_count
        start = (self._page - 1) * self.page_size
        visible = list(rows[start:start + self.page_size])
        self._render(start, len(visible))
        return visible

    # ---------- display ----------

    def _render(self, start: int, shown: int) -> None:
        if self._total:
            self.summary.setText(f"Showing {start + 1}–{start + shown} of {self._total}")
        else:
            self.summary.setText("No records")

        self.prev_button.setEnabled(self._page > 1)
        self.next_button.setEnabled(self._page < self.page_count)

        while self._buttons.count():
            item = self._buttons.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for entry in page_numbers(self._page, self.page_count):
            if entry == _ELLIPSIS:
                gap = QLabel(_ELLIPSIS)
                gap.setProperty("muted", "true")
                self._buttons.addWidget(gap)
                continue
            button = QPushButton(str(entry))
            button.setProperty("pageActive", entry == self._page)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMaximumWidth(46)
            button.clicked.connect(lambda _checked=False, page=entry: self.set_page(page))
            self._buttons.addWidget(button)
