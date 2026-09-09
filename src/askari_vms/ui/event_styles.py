"""Shared colours for e-tag event rows, used by the log and the resident profile."""

from __future__ import annotations

from PySide6.QtGui import QColor

from askari_vms.etag_events import ETagEventKind

# Blue near expiry, red once expired, deeper red for a tag the controller should not
# have opened for.
ROW_COLOURS = {
    ETagEventKind.GRANTED: QColor("#eef8f1"),
    ETagEventKind.EXPIRING: QColor("#e6efff"),
    ETagEventKind.EXPIRED: QColor("#fde8e8"),
    ETagEventKind.BLOCKED: QColor("#f3e8e8"),
    ETagEventKind.UNKNOWN: QColor("#f9d2d2"),
}

TEXT_COLOURS = {
    ETagEventKind.EXPIRING: QColor("#24558c"),
    ETagEventKind.EXPIRED: QColor("#9b2525"),
    ETagEventKind.BLOCKED: QColor("#9b2525"),
    ETagEventKind.UNKNOWN: QColor("#7d1a1a"),
}

DEFAULT_TEXT = QColor("#2b4436")

LEGEND = (
    (ETagEventKind.GRANTED, "Valid tag, gate opened"),
    (ETagEventKind.EXPIRING, "Expires within 10 days"),
    (ETagEventKind.EXPIRED, "Past its expiry date"),
    (ETagEventKind.BLOCKED, "Blocked by Admin"),
    (ETagEventKind.UNKNOWN, "Not in the registry — critical"),
)
