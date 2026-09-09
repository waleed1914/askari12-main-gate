from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF


def navigation_icon(key: str, color: str = "#ffffff") -> QIcon:
    """Create small dependency-free line icons that remain crisp on Windows."""
    pixmap = QPixmap(QSize(24, 24))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if key == "dashboard":
        for rect in (QRectF(3, 3, 7, 7), QRectF(14, 3, 7, 7), QRectF(3, 14, 7, 7), QRectF(14, 14, 7, 7)):
            painter.drawRoundedRect(rect, 1.5, 1.5)
    elif key == "vms":
        painter.drawRoundedRect(QRectF(2.5, 4, 19, 13), 2, 2)
        painter.drawLine(QPointF(8, 21), QPointF(16, 21))
        painter.drawLine(QPointF(12, 17), QPointF(12, 21))
    elif key == "categories":
        painter.drawRoundedRect(QRectF(3, 8, 18, 9), 2, 2)
        painter.drawLine(QPointF(7, 8), QPointF(10, 4))
        painter.drawLine(QPointF(10, 4), QPointF(17, 4))
        painter.drawLine(QPointF(17, 4), QPointF(20, 8))
        painter.drawEllipse(QPointF(7, 18.5), 2, 2)
        painter.drawEllipse(QPointF(17, 18.5), 2, 2)
    elif key in {"members", "users"}:
        painter.drawEllipse(QPointF(12, 7), 4, 4)
        painter.drawArc(QRectF(4, 12, 16, 10), 0, 180 * 16)
        if key == "members":
            painter.drawEllipse(QPointF(4.5, 10), 2.5, 2.5)
    elif key in {"etags", "etag_logs"}:
        polygon = QPolygonF((QPointF(3, 5), QPointF(13, 5), QPointF(21, 13), QPointF(13, 21), QPointF(3, 11)))
        painter.drawPolygon(polygon)
        painter.drawEllipse(QPointF(8, 9), 1.5, 1.5)
        if key == "etag_logs":
            painter.drawLine(QPointF(13, 15), QPointF(18, 15))
    elif key == "reports":
        painter.drawRoundedRect(QRectF(3, 3, 18, 18), 2, 2)
        painter.drawLine(QPointF(7, 17), QPointF(7, 13))
        painter.drawLine(QPointF(12, 17), QPointF(12, 9))
        painter.drawLine(QPointF(17, 17), QPointF(17, 6))
    elif key == "audit_logs":
        painter.drawRoundedRect(QRectF(4, 2.5, 16, 19), 2, 2)
        painter.drawLine(QPointF(8, 8), QPointF(10, 10))
        painter.drawLine(QPointF(10, 10), QPointF(14, 6))
        painter.drawLine(QPointF(8, 15), QPointF(16, 15))
    elif key == "settings":
        painter.drawEllipse(QPointF(12, 12), 4, 4)
        painter.drawEllipse(QPointF(12, 12), 9, 9)
        for start, end in ((QPointF(12, 1), QPointF(12, 4)), (QPointF(12, 20), QPointF(12, 23)), (QPointF(1, 12), QPointF(4, 12)), (QPointF(20, 12), QPointF(23, 12))):
            painter.drawLine(start, end)
    elif key == "member_logs":
        painter.drawEllipse(QPointF(12, 12), 9, 9)
        painter.drawLine(QPointF(12, 12), QPointF(12, 6))
        painter.drawLine(QPointF(12, 12), QPointF(17, 15))
    elif key == "doors":
        painter.drawRoundedRect(QRectF(4, 2.5, 16, 19), 1, 1)
        painter.drawLine(QPointF(12, 3), QPointF(12, 21))
        painter.drawEllipse(QPointF(9, 12), 1, 1)
        painter.drawEllipse(QPointF(15, 12), 1, 1)
    else:
        path = QPainterPath()
        path.addEllipse(QPointF(12, 12), 8, 8)
        painter.drawPath(path)

    painter.end()
    return QIcon(pixmap)

