import os

# Qt must run headless under pytest; set before PySide6 is imported anywhere.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolate_live_camera(monkeypatch):
    """Application wiring tests must not contact the real gate camera."""
    monkeypatch.setattr("askari_vms.ui.admin_window.entry_driver_feed", lambda settings: None)
    monkeypatch.setattr("askari_vms.main.entry_driver_feed", lambda settings: None)
    monkeypatch.setattr("askari_vms.ui.admin_window.entry_anpr_feed", lambda settings: None)
    monkeypatch.setattr("askari_vms.main.entry_anpr_feed", lambda settings: None)
