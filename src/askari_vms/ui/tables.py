"""Shared table sizing.

Two rules every list here follows: columns share the width proportionally rather than
letting one column swallow the slack, and a table inside a scrolling page grows to fit
its rows instead of scrolling within itself.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QHeaderView, QTableWidget

DEFAULT_MINIMUM_WIDTH = 54


class ProportionalColumns(QObject):
    """Divide the viewport between columns by weight, and never scroll sideways."""

    def __init__(self, table: QTableWidget, weights: Sequence[int], minimum: int = DEFAULT_MINIMUM_WIDTH) -> None:
        super().__init__(table)
        self._table = table
        self._weights = tuple(weights)
        self._minimum = minimum
        self._busy = False

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setMinimumSectionSize(minimum)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # The page's own resizeEvent fires before the table is laid out, so react to the
        # viewport: that is the width the columns actually have to share.
        table.viewport().installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        try:
            if watched is self._table.viewport() and event.type() == QEvent.Type.Resize:
                self.apply()
        except RuntimeError:
            # The table's C++ side is already gone (teardown); nothing left to size.
            return False
        return super().eventFilter(watched, event)

    def apply(self) -> None:
        try:
            available = self._table.viewport().width()
        except RuntimeError:
            return
        if available <= 0 or self._busy:
            return
        self._busy = True  # setColumnWidth can toggle a scrollbar and re-enter
        try:
            total = sum(self._weights)
            widths = [max(self._minimum, available * weight // total) for weight in self._weights]
            # Enforcing the minimum can push the row past the viewport on a narrow
            # window; take the excess off the widest column rather than scroll.
            excess = sum(widths) - available
            if excess > 0:
                widest = widths.index(max(widths))
                widths[widest] = max(self._minimum, widths[widest] - excess)
            for column, width in enumerate(widths):
                self._table.setColumnWidth(column, width)
        finally:
            self._busy = False


def fit_height_to_rows(table: QTableWidget, maximum_rows: int | None = None) -> None:
    """Size a table to exactly its header plus rows, so it never scrolls internally.

    Measure the header rather than guessing: it is ~38px, not the 46px it looks like,
    and a guess leaves a dead strip above the bottom border that reads as a doubled line.
    """
    rows = table.rowCount() if maximum_rows is None else min(table.rowCount(), maximum_rows)
    header = table.horizontalHeader().sizeHint().height()
    body = sum(table.rowHeight(row) for row in range(rows))
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setFixedHeight(header + body + 2 * table.frameWidth())
