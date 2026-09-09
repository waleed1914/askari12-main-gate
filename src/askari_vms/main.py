from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from askari_vms.audit import AuditLog
from askari_vms.cnic_ocr import LocalCNICReader

from askari_vms.routing import Portal, portal_for
from askari_vms.settings import default_settings
from askari_vms.storage import Store, default_database_path
from askari_vms.ui.brand import app_icon
from askari_vms.ui.admin_window import AdminWindow
from askari_vms.ui.pages.entry_portal import EntryPortalWindow
from askari_vms.ui.pages.exit_portal import ExitPortalWindow
from askari_vms.ui.pages.login import LoginWindow
from askari_vms.ui.theme import APP_STYLESHEET


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Askari VMS")
    app.setOrganizationName("Askari 12")
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f4f7f4"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#15231a"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f8faf8"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#15231a"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#173526"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#dfeee4"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#102d20"))
    app.setPalette(palette)
    app.setStyleSheet(APP_STYLESHEET)
    # Set once on the application: every window, the taskbar and Alt-Tab inherit it.
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    store = Store(default_database_path(default_settings()))

    def current_settings():
        """Read fresh every time.

        The workstation role can be changed in Settings while the app is running, so
        capturing it at startup would keep routing sign-ins to the old lane until the
        next restart.
        """
        return store.settings.load() or default_settings()

    login = LoginWindow(store, workstation=current_settings().workstation_role)
    open_windows: list = []

    def show_login() -> None:
        login.set_workstation(current_settings().workstation_role)
        login.reset()
        login.show()

    def open_portal(session) -> None:
        # The workstation decides the lane; the account role decides Admin vs operator.
        settings = current_settings()
        destination = portal_for(session.role, settings.workstation_role)
        if destination is Portal.ADMIN:
            window = AdminWindow(store, session)
        elif destination is Portal.EXIT:
            audit = AuditLog(store.audit.list(), repository=store.audit)
            audit.set_operator(session.operator, session.workstation)
            window = ExitPortalWindow(
                audit_log=audit,
                visits=store.visits.list(),
                session=session,
                on_checkout=store.visits.save,
            )
            audit.record(
                action="Login",
                target="Exit portal",
                summary=f"{session.display_name} signed in",
                details=f"{session.describe()}. Session started {session.signed_in_at:%d %b %Y %H:%M:%S}.",
            )
        else:
            window = EntryPortalWindow(
                audit_log=AuditLog(store.audit.list(), repository=store.audit),
                categories=store.categories.active(),
                visits=store.visits.list(),
                session=session,
                on_submit=store.visits.save,
                events=store.etag_events.list(),
                gate=f"C1 - {destination.value}",
                cnic_reader=LocalCNICReader(
                    settings.peripherals.id_card_camera_index,
                    settings.storage.data_directory,
                ),
            )
            window._audit.set_operator(session.operator, session.workstation)
            window._audit.record(
                action="Login",
                target=f"{destination.value} portal",
                summary=f"{session.display_name} signed in",
                details=f"{session.describe()}. Session started {session.signed_in_at:%d %b %Y %H:%M:%S}.",
            )
            window.refresh_events()
        window.signed_out.connect(show_login)
        open_windows.append(window)
        login.hide()
        window.show()

    login.authenticated.connect(open_portal)
    login.show()
    try:
        return app.exec()
    finally:
        store.close()
