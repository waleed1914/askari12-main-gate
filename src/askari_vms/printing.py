"""Visitor receipt: layout, ESC/POS encoding, and the printer backends.

The receipt is what the visitor carries to the exit gate, so its barcode is the token
the Exit portal scans. Rendering is pure and testable; only the backend touches Windows.

The attached unit is a POS80-class 80mm thermal printer (STMicroelectronics
VID_0416/PID_5011) on port USB001. Those speak ESC/POS, which is sent to the Windows
spooler as a RAW job — the spooler passes the bytes through untouched.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from askari_vms.visits import VisitRecord

# Font A on an 80mm head is 48 columns. Narrower paper (58mm) is 32.
WIDTH_80MM = 48
WIDTH_58MM = 32

ESC = b"\x1b"
GS = b"\x1d"

INIT = ESC + b"@"
ALIGN_LEFT = ESC + b"a\x00"
ALIGN_CENTRE = ESC + b"a\x01"
BOLD_ON = ESC + b"E\x01"
BOLD_OFF = ESC + b"E\x00"
DOUBLE_ON = GS + b"!\x11"          # double width and height
DOUBLE_OFF = GS + b"!\x00"
FEED_AND_CUT = b"\n\n\n\n" + GS + b"V\x00"

# CODE39 accepts 0-9 A-Z and a few symbols, which covers our uppercase hex token, and
# is read by every keyboard-wedge scanner without a configuration change.
BARCODE_CODE39 = 4
_CODE39_ALLOWED = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-. $/+%")


class ReceiptPrinter(Protocol):
    def print_bytes(self, payload: bytes) -> None: ...


class PrinterError(RuntimeError):
    """A recoverable failure. Printing must never stop the operator or the gate."""


@dataclass(frozen=True, slots=True)
class ReceiptHeader:
    society: str = "Askari Greens"
    address: str = ""
    gate: str = "Visitor Entry"


def _fit(text: str, width: int) -> str:
    return text[:width]


def _pair(label: str, value: str, width: int) -> str:
    """Label left, value right, dotted to the edge; wraps rather than truncating."""
    value = value or "-"
    room = width - len(label) - 1
    if len(value) <= room:
        return f"{label} {value:>{room}}"
    return f"{label}\n  {value[: width - 2]}"


def _rule(width: int, character: str = "-") -> str:
    return character * width


def _centre(text: str, width: int) -> str:
    """Centre, clipping first: an over-long line would otherwise wrap on the head."""
    return _fit(text, width).center(width).rstrip()


def render_text(
    visit: VisitRecord,
    header: ReceiptHeader | None = None,
    width: int = WIDTH_80MM,
    now: datetime | None = None,
) -> str:
    """The receipt as plain text — also what the on-screen preview shows."""
    header = header or ReceiptHeader()
    lines: list[str] = [_centre(header.society, width)]
    if header.address:
        lines.append(_centre(header.address, width))
    lines += [
        _centre("VISITOR PASS", width),
        _rule(width, "="),
        _pair("Visit", visit.visit_id, width),
        _pair("Entry", visit.entry_time.strftime("%d %b %Y  %H:%M:%S"), width),
        _pair("Gate", header.gate, width),
        _rule(width),
        _pair("Name", visit.visitor_name or "-", width),
        _pair("CNIC", visit.cnic or "-", width),
        _pair("Vehicle", visit.vehicle_number or "-", width),
        _pair("Category", visit.vehicle_category or "-", width),
        _pair("Destination", visit.destination or "-", width),
        _rule(width),
        _pair("Operator", visit.entry_operator or "-", width),
        "",
        # 58mm paper is only 32 columns, so the long form does not fit.
        _centre("Present this pass at the exit gate" if width >= 36 else "Show this pass at exit", width),
        _centre(visit.barcode, width),
    ]
    return "\n".join(lines)


def barcode_payload(token: str) -> bytes:
    """CODE39 barcode for the receipt token, with the number printed underneath."""
    value = token.strip().upper()
    if not value or any(character not in _CODE39_ALLOWED for character in value):
        raise PrinterError(f"{token!r} cannot be encoded as CODE39")
    return b"".join((
        GS + b"h\x50",              # height 80 dots
        GS + b"w\x02",              # module width
        GS + b"H\x02",              # print the digits below the bars
        GS + b"k" + bytes([BARCODE_CODE39]) + value.encode("ascii") + b"\x00",
    ))


def render_escpos(
    visit: VisitRecord,
    header: ReceiptHeader | None = None,
    width: int = WIDTH_80MM,
) -> bytes:
    """The bytes sent to the printer: text, then the scannable barcode, then a cut."""
    header = header or ReceiptHeader()
    body = render_text(visit, header, width)
    heading, _, remainder = body.partition("\n")

    parts = [
        INIT,
        ALIGN_CENTRE,
        BOLD_ON + DOUBLE_ON,
        heading.strip().encode("cp437", "replace") + b"\n",
        DOUBLE_OFF + BOLD_OFF,
        ALIGN_LEFT,
        remainder.encode("cp437", "replace") + b"\n",
        ALIGN_CENTRE,
        barcode_payload(visit.barcode),
        b"\n",
        ALIGN_LEFT,
        FEED_AND_CUT,
    ]
    return b"".join(parts)


class NullPrinter:
    """Accepts and discards. Used until a printer is configured."""

    def __init__(self) -> None:
        self.jobs: list[bytes] = []

    def print_bytes(self, payload: bytes) -> None:
        self.jobs.append(payload)


class FilePrinter:
    """Writes each job to a file — for bench testing without paper."""

    def __init__(self, path) -> None:
        self.path = path

    def print_bytes(self, payload: bytes) -> None:
        try:
            with open(self.path, "ab") as handle:
                handle.write(payload)
        except OSError as exc:
            raise PrinterError(f"could not write {self.path}: {exc}") from exc


class WindowsRawPrinter:
    """Sends ESC/POS straight through the Windows spooler as a RAW job.

    RAW means the spooler does not render or reinterpret the bytes, which is what a
    thermal printer needs. Requires a print queue bound to the printer's USB port.
    """

    def __init__(self, queue_name: str) -> None:
        self.queue_name = queue_name

    def print_bytes(self, payload: bytes) -> None:
        try:
            import win32print
        except ImportError as exc:  # pragma: no cover - depends on the host
            raise PrinterError(
                "pywin32 is required to print on Windows (pip install pywin32)"
            ) from exc
        try:
            handle = win32print.OpenPrinter(self.queue_name)
        except Exception as exc:
            raise PrinterError(f"printer {self.queue_name!r} could not be opened: {exc}") from exc
        try:
            job = win32print.StartDocPrinter(handle, 1, ("Askari VMS receipt", None, "RAW"))
            try:
                win32print.StartPagePrinter(handle)
                win32print.WritePrinter(handle, payload)
                win32print.EndPagePrinter(handle)
            finally:
                win32print.EndDocPrinter(handle)
        except Exception as exc:
            raise PrinterError(f"receipt could not be sent to {self.queue_name!r}: {exc}") from exc
        finally:
            win32print.ClosePrinter(handle)


class DirectUsbPrinter:
    """Send ESC/POS to the attached POS80 USB endpoint without a print queue.

    The tested SNC-830 identifies as VID_0416/PID_5011. Direct access avoids Windows
    reassigning USB001/USB002 and does not require the unsigned vendor installer.
    """

    def __init__(self, vid: str = "0416", pid: str = "5011") -> None:
        self.vid = vid.casefold()
        self.pid = pid.casefold()

    def print_bytes(self, payload: bytes) -> None:
        try:
            from askari_vms.usb_printer import device_paths, write_direct

            matches = [
                path for path in device_paths()
                if f"vid_{self.vid}" in path.casefold() and f"pid_{self.pid}" in path.casefold()
            ]
            if not matches:
                raise PrinterError("SNC-830 receipt printer is not connected")
            written = write_direct(matches[0], payload)
            if written != len(payload):
                raise PrinterError(f"receipt printer accepted only {written} of {len(payload)} bytes")
        except PrinterError:
            raise
        except OSError as exc:
            raise PrinterError(f"receipt printer USB write failed: {exc}") from exc


def available_printers() -> list[str]:
    """Windows print queue names, for the Settings dropdown. Empty when unavailable."""
    try:
        import win32print
    except ImportError:
        return []
    try:
        return [
            printer[2]
            for printer in win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS, None, 1
            )
        ]
    except Exception:
        return []


def make_printer(queue_name: str) -> ReceiptPrinter:
    """A real printer when one is configured, otherwise a sink that swallows jobs."""
    return WindowsRawPrinter(queue_name) if queue_name.strip() else NullPrinter()


def print_receipt(
    printer: ReceiptPrinter,
    visit: VisitRecord,
    header: ReceiptHeader | None = None,
    width: int = WIDTH_80MM,
    on_error: Callable[[PrinterError], None] | None = None,
) -> bool:
    """Print, reporting failure rather than raising.

    A dead printer must not stop the gate: the caller opens it anyway and the failure
    is audited. Returns whether the receipt actually reached the printer.
    """
    try:
        printer.print_bytes(render_escpos(visit, header, width))
    except PrinterError as exc:
        if on_error is not None:
            on_error(exc)
        return False
    return True
