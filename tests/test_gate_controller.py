from askari_vms.controllers import DoorCommand
from askari_vms.gate_controller import (
    GateControllerError,
    HttpGateController,
    credential_target,
    entry_gate_controller,
    exit_gate_controller,
)
from askari_vms.settings import default_settings


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return b"OK"


def test_entry_factory_uses_current_controller_address():
    controller = entry_gate_controller(default_settings())
    assert controller is not None
    assert controller.address == "192.168.1.10"
    assert credential_target("entry", controller.address) == "AskariVMS/controller/entry/192.168.1.10"


def test_exit_factory_uses_confirmed_controller_address():
    controller = exit_gate_controller(default_settings())
    assert controller is not None
    assert controller.address == "192.168.1.11"
    assert credential_target("exit", controller.address) == "AskariVMS/controller/exit/192.168.1.11"


def test_visitor_entry_open_targets_physical_door_two(monkeypatch):
    requests = []

    class _Opener:
        def open(self, request, timeout):
            requests.append((request, timeout))
            return _Response()

    monkeypatch.setattr("askari_vms.gate_controller.read_credentials", lambda *_: ("admin", "secret"))
    monkeypatch.setattr("askari_vms.gate_controller.urllib.request.build_opener", lambda *_: _Opener())
    HttpGateController("entry", "192.168.1.10").command(1, DoorCommand.OPEN)

    request, timeout = requests[0]
    assert request.full_url == "http://192.168.1.10:80/cdor.cgi?open=1&door=1"
    assert request.get_header("Authorization").startswith("Basic ")
    assert timeout == 2.0


def test_missing_credentials_never_leak_private_error(monkeypatch):
    import pytest

    monkeypatch.setattr(
        "askari_vms.gate_controller.read_credentials",
        lambda *_: (_ for _ in ()).throw(RuntimeError("private password")),
    )
    with pytest.raises(GateControllerError, match="credentials unavailable") as error:
        HttpGateController("entry", "192.168.1.10").command(1, DoorCommand.OPEN)
    assert "private" not in str(error.value)
