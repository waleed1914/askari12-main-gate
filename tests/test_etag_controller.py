from datetime import date
from urllib.parse import parse_qs

from askari_vms.demo_data import etag_records
from askari_vms.etag_controller import HttpETagController


class _Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *_): return None
    def read(self, _limit): return self.body.encode()


def test_new_card_uses_free_slot_door_one_and_verifies(monkeypatch):
    requests = []
    record = etag_records(1)[0]
    searches = iter([
        "no result",
        f'<input name="Index" value="2"><input name="Card" value="{record.rfid}">',
    ])

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            if request.full_url.endswith("/SearchCard.shtm"):
                return _Response(next(searches))
            if "ShowCards" in request.full_url:
                return _Response(
                    '<tr><th>1</th><th>CAR1</th><th>123</th><th>*</th>'
                    '<th>x</th><th>x</th><th><a href=EditCard.shtm?ID=0>Edit</a></th></tr>'
                    '<tr><th>2</th><th></th><th></th><th></th><th></th><th></th>'
                    '<th><a href=EditCard.shtm?ID=1>Edit</a></th></tr>'
                )
            return _Response("saved")

    monkeypatch.setattr("askari_vms.etag_controller.read_credentials", lambda *_: ("admin", "secret"))
    monkeypatch.setattr("askari_vms.etag_controller.urllib.request.build_opener", lambda *_: Opener())
    record = record.__class__(**{**record.__dict__}) if hasattr(record, "__dict__") else record
    result = HttpETagController("entry", "192.168.1.10", max_pages=1).save(record)
    assert result.slot == 1
    write = next(request for request in requests if request.full_url.endswith("/EditCard.shtm"))
    data = parse_qs(write.data.decode())
    assert data["Index"] == ["2"]
    assert data["TZ1"] == ["1"]
    assert "TZ17" not in data
    assert data["Card"] == [record.rfid]
    assert data["Name"] == [record.vehicle_number.replace(" ", "")[:8]]
    assert data["Year"] == ["2000"]
    assert data["YearB"] == [str(record.expiry_date.year)]


def test_existing_card_reuses_its_slot(monkeypatch):
    requests = []
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return _Response(
                f'<input name="Index" value="9"><input name="Card" value="{record.rfid}">'
                if request.full_url.endswith("/SearchCard.shtm") else "saved"
            )
    monkeypatch.setattr("askari_vms.etag_controller.read_credentials", lambda *_: ("admin", "secret"))
    monkeypatch.setattr("askari_vms.etag_controller.urllib.request.build_opener", lambda *_: Opener())
    record = etag_records(1)[0]
    HttpETagController("exit", "192.168.1.11").save(record)
    write = next(request for request in requests if request.full_url.endswith("/EditCard.shtm"))
    assert parse_qs(write.data.decode())["Index"] == ["9"]
