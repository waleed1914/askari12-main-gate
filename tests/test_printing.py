from datetime import datetime

import pytest

from askari_vms.printing import (
    ALIGN_CENTRE,
    FEED_AND_CUT,
    INIT,
    WIDTH_58MM,
    WIDTH_80MM,
    FilePrinter,
    DirectUsbPrinter,
    NullPrinter,
    PrinterError,
    ReceiptHeader,
    WindowsRawPrinter,
    barcode_payload,
    make_printer,
    print_receipt,
    render_escpos,
    render_text,
)
from askari_vms.visits import VisitRecord

NOW = datetime(2026, 9, 3, 21, 15, 30)


def a_visit(**changes) -> VisitRecord:
    values = dict(
        visit_id="VIS-000141", barcode="8F2A19C4", entry_time=NOW, entry_operator="operator01",
        visitor_name="Ahmed Khan", cnic="35202-1234567-1", mobile="03001234567",
        vehicle_number="LEA-4410", vehicle_category="Car", destination="House 44-B",
    )
    values.update(changes)
    return VisitRecord(**values)


def test_the_receipt_carries_everything_the_exit_gate_needs() -> None:
    text = render_text(a_visit(), ReceiptHeader("Askari Greens", "Lahore", "Visitor Entry"))
    for expected in ("Askari Greens", "VISITOR PASS", "VIS-000141", "Ahmed Khan",
                     "35202-1234567-1", "LEA-4410", "Car", "House 44-B",
                     "operator01", "8F2A19C4", "03 Sep 2026"):
        assert expected in text, f"missing {expected!r}"


def test_no_line_exceeds_the_paper_width() -> None:
    long_visit = a_visit(
        visitor_name="Muhammad Abdul Rehman Siddiqui Chaudhry",
        destination="House 210, Street 14, Phase 3, Askari Greens, Lahore Cantonment",
    )
    for width in (WIDTH_80MM, WIDTH_58MM):
        for line in render_text(long_visit, width=width).splitlines():
            assert len(line) <= width, f"{line!r} is {len(line)} wide, paper is {width}"


def test_a_blank_field_prints_a_dash_not_an_empty_gap() -> None:
    text = render_text(a_visit(visitor_name="", cnic="", destination=""))
    assert "Name" in text and "-" in text
    assert "None" not in text


def test_escpos_initialises_and_cuts() -> None:
    payload = render_escpos(a_visit())
    assert payload.startswith(INIT), "the printer must be reset before each receipt"
    assert payload.endswith(FEED_AND_CUT), "the paper must be fed clear and cut"
    assert ALIGN_CENTRE in payload
    assert b"VIS-000141" in payload


def test_the_barcode_encodes_the_receipt_token() -> None:
    payload = barcode_payload("8F2A19C4")
    assert payload.endswith(b"8F2A19C4\x00"), "CODE39 data is NUL terminated"
    assert b"\x1dk\x04" in payload, "function 4 selects CODE39"
    assert b"\x1dH\x02" in payload, "the digits print under the bars for manual entry"


def test_a_token_the_scanner_could_not_read_is_refused() -> None:
    for bad in ("", "   ", "abc*def", "TOKEN!"):
        with pytest.raises(PrinterError):
            barcode_payload(bad)


def test_lowercase_tokens_are_accepted_as_uppercase() -> None:
    assert barcode_payload("8f2a19c4").endswith(b"8F2A19C4\x00")


def test_text_survives_a_name_outside_the_printer_codepage() -> None:
    payload = render_escpos(a_visit(visitor_name="Ayesha Qureshi \u2014 \u0627\u062d\u0645\u062f"))
    assert isinstance(payload, bytes), "unencodable characters must not raise"


def test_the_null_printer_accepts_and_keeps_the_job() -> None:
    printer = NullPrinter()
    assert print_receipt(printer, a_visit()) is True
    assert len(printer.jobs) == 1 and printer.jobs[0].startswith(INIT)


def test_direct_usb_printer_selects_the_tested_snc830(monkeypatch) -> None:
    import askari_vms.usb_printer as usb

    paths = [
        r"\\?\usb#vid_9999&pid_0001#other",
        r"\\?\usb#vid_0416&pid_5011#snc830",
    ]
    writes = []
    monkeypatch.setattr(usb, "device_paths", lambda: paths)
    monkeypatch.setattr(usb, "write_direct", lambda path, payload: writes.append((path, payload)) or len(payload))
    DirectUsbPrinter().print_bytes(b"receipt")
    assert writes == [(paths[1], b"receipt")]


def test_a_file_printer_writes_the_bytes(tmp_path) -> None:
    target = tmp_path / "receipt.bin"
    printer = FilePrinter(target)
    assert print_receipt(printer, a_visit()) is True
    assert print_receipt(printer, a_visit(visit_id="VIS-000142")) is True
    data = target.read_bytes()
    assert data.count(INIT) == 2, "each receipt is a separate job"
    assert b"VIS-000142" in data


def test_a_printer_failure_is_reported_not_raised() -> None:
    """A dead printer must never stop the gate."""
    class Broken:
        def print_bytes(self, payload: bytes) -> None:
            raise PrinterError("out of paper")

    seen: list[PrinterError] = []
    assert print_receipt(Broken(), a_visit(), on_error=seen.append) is False
    assert len(seen) == 1 and "out of paper" in str(seen[0])


def test_an_unconfigured_printer_falls_back_to_a_sink() -> None:
    assert isinstance(make_printer(""), NullPrinter)
    assert isinstance(make_printer("   "), NullPrinter)
    assert isinstance(make_printer("POS80"), WindowsRawPrinter)


def test_a_missing_queue_reports_clearly() -> None:
    """Whichever is missing — the module or the queue — the operator gets a reason."""
    printer = WindowsRawPrinter("No Such Printer Queue")
    with pytest.raises(PrinterError) as error:
        printer.print_bytes(b"test")
    message = str(error.value)
    assert "No Such Printer Queue" in message or "pywin32" in message


def test_the_receipt_still_renders_when_no_printer_exists() -> None:
    """Rendering is pure: it must not depend on a printer being installed."""
    assert render_escpos(a_visit()).startswith(INIT)
    assert "VIS-000141" in render_text(a_visit())
