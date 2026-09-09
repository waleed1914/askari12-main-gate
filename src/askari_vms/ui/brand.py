"""Prepare the directorate emblem for the sidebar.

The supplied file is a JPEG: no transparency, a square near-white background, and a
black band along one edge from the original export. Both are removed here so the
emblem reads as a circular badge on the green sidebar.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPainterPath, QPixmap, qGray

LOGO_NAMES = ("logo.png", "logo.jpeg", "logo.jpg", "logo.webp", "logo.bmp")
_ASSETS = Path(__file__).parent / "assets"

_DARK = 45          # luminance at or below this counts as part of a black border
_SAMPLE_STEP = 24   # pixels sampled across a line when testing for uniform dark


def logo_path() -> Path | None:
    return next((_ASSETS / name for name in LOGO_NAMES if (_ASSETS / name).is_file()), None)


def _line_is_dark(image: QImage, index: int, horizontal: bool) -> bool:
    length = image.width() if horizontal else image.height()
    step = max(1, length // _SAMPLE_STEP)
    for offset in range(0, length, step):
        x, y = (offset, index) if horizontal else (index, offset)
        if qGray(image.pixel(x, y)) > _DARK:
            return False
    return True


def trim_dark_border(image: QImage) -> QImage:
    """Drop uniformly black rows and columns around the artwork."""
    top, bottom = 0, image.height() - 1
    while top < bottom and _line_is_dark(image, top, True):
        top += 1
    while bottom > top and _line_is_dark(image, bottom, True):
        bottom -= 1
    left, right = 0, image.width() - 1
    while left < right and _line_is_dark(image, left, False):
        left += 1
    while right > left and _line_is_dark(image, right, False):
        right -= 1
    return image.copy(left, top, right - left + 1, bottom - top + 1)


def circular_logo(size: int, ratio: int = 2, path: Path | None = None) -> QPixmap:
    """A round emblem `size` points across, rendered at `ratio`x for sharpness.

    Returns a null pixmap when no logo file is installed, so the caller can fall back.
    """
    source = path or logo_path()
    if source is None:
        return QPixmap()
    image = QImage(str(source))
    if image.isNull():
        return QPixmap()

    image = trim_dark_border(image)
    side = min(image.width(), image.height())
    if side <= 0:
        return QPixmap()
    image = image.copy((image.width() - side) // 2, (image.height() - side) // 2, side, side)

    pixels = max(1, size * ratio)
    scaled = QPixmap.fromImage(image).scaled(
        pixels, pixels,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    badge = QPixmap(pixels, pixels)
    badge.fill(Qt.GlobalColor.transparent)
    painter = QPainter(badge)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addEllipse(0, 0, pixels, pixels)
    painter.setClipPath(clip)
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    badge.setDevicePixelRatio(ratio)
    return badge


# Windows picks the nearest size for the title bar, taskbar and Alt-Tab, so supply
# several rather than letting it rescale one.
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def app_icon() -> QIcon:
    """The emblem as a window icon. Null when no logo file is installed."""
    icon = QIcon()
    for size in ICON_SIZES:
        # ratio=1: an icon is addressed in device pixels, not logical ones.
        badge = circular_logo(size, ratio=1)
        if badge.isNull():
            return QIcon()
        icon.addPixmap(badge)
    return icon
