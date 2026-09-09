from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from askari_vms.audit import AuditLog, AuditSeverity, sample_audit_events
from askari_vms.auth import Session
from askari_vms.cnic_ocr import LocalCNICReader
from askari_vms.ui.brand import circular_logo
from askari_vms.navigation import ADMIN_NAVIGATION, NavigationItem
from askari_vms.routing import Portal, workstation_lane
from askari_vms.settings import controller_choices, default_settings
from askari_vms.storage import Store, default_database_path
from askari_vms.ui.pages.categories import CategoriesPage
from askari_vms.ui.pages.audit_logs import AuditLogsPage
from askari_vms.ui.pages.door_controls import DoorControlsPage
from askari_vms.ui.pages.dashboard import DashboardPage
from askari_vms.ui.pages.entry_portal import EntryPortalWindow
from askari_vms.ui.pages.etag_logs import ETagLogsPage
from askari_vms.ui.pages.exit_portal import ExitPortalWindow
from askari_vms.ui.pages.etags import ETagsPage
from askari_vms.ui.pages.placeholder import PlaceholderPage
from askari_vms.ui.pages.reports import ReportsPage
from askari_vms.ui.pages.settings import SettingsPage
from askari_vms.ui.pages.users import UsersPage
from askari_vms.ui.pages.vms_operations import VmsOperationsPage
from askari_vms.ui.nav_icons import navigation_icon


BRAND_LOGO_SIZE = 46


PAGE_DESCRIPTIONS = {
    "vms": "Monitor visitor transactions, active visits, checkouts, and incomplete records.",
    "categories": "Configure vehicle categories, prices, lost-receipt values, and operator shortcut keys.",
    "etags": "Register, renew, block, and synchronize resident e-tags with selected controllers.",
    "users": "Create Admin, Entry, and Exit operator accounts and review login sessions.",
    "reports": "Search and export visitor, e-tag, controller, operator, and audit activity.",
    "audit_logs": "Review operator sessions, record changes, overrides, printing failures, gate commands, and system events.",
    "settings": "Configure gates, controllers, cameras, OCR, printers, storage, backup, and workstation roles.",
    "etag_logs": "Review live e-tag events, including expiry warnings and unknown-tag critical events.",
    "doors": "View controller health and issue audited visitor-gate commands.",
}


class AdminWindow(QMainWindow):
    signed_out = Signal()

    def __init__(self, store: Store | None = None, session: Session | None = None) -> None:
        super().__init__()
        self.session = session
        self.setWindowTitle("Askari VMS — Admin")
        self.resize(1440, 900)
        self.setMinimumSize(1120, 700)

        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # One audit log shared by every page, so gate commands and record changes
        # all surface in Audit Logs.
        # Settings decide where the database lives, so they are read first.
        self.settings = default_settings()
        self.store = store if store is not None else Store(default_database_path(self.settings))
        stored = self.store.settings.load()
        if stored is not None:
            self.settings = stored
        if self.store.seed_if_empty():
            self.store.audit.append_all(sample_audit_events())

        self.audit_log = AuditLog(self.store.audit.list(), repository=self.store.audit)
        if session is not None:
            self.audit_log.set_operator(session.operator, session.workstation)

        self._nav_buttons: dict[str, QPushButton] = {}
        self._page_indexes: dict[str, int] = {}
        root_layout.addWidget(self._build_sidebar())

        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(self._build_topbar())

        self.pages = QStackedWidget()
        self.dashboard_page = DashboardPage(
            visits=self.store.visits.list(),
            tags=self.store.etags.list(),
            events=self.store.etag_events.list(),
            on_open_entry=self.open_entry_portal if workstation_lane(
                self.settings.workstation_role) is Portal.ENTRY else None,
            on_open_page=self.select_page,
        )
        self.pages.addWidget(self.dashboard_page)
        self._page_indexes["dashboard"] = 0
        for item in ADMIN_NAVIGATION[1:]:
            if item.key == "etags":
                page = self.etags_page = ETagsPage(controller_choices(self.settings), repository=self.store.etags)
            elif item.key == "categories":
                page = CategoriesPage(repository=self.store.categories)
            elif item.key == "users":
                page = UsersPage(repository=self.store.users)
            elif item.key == "audit_logs":
                page = AuditLogsPage(self.audit_log)
            elif item.key == "doors":
                page = DoorControlsPage(self.audit_log)
            elif item.key == "settings":
                page = SettingsPage(self.audit_log, self.settings, on_saved=self._settings_saved,
                                    repository=self.store.settings)
            elif item.key == "etag_logs":
                page = self.etag_logs_page = ETagLogsPage(self.audit_log, records=self.store.etags.list(),
                                                         repository=self.store.etag_events)
            elif item.key == "vms":
                page = VmsOperationsPage(self.audit_log, repository=self.store.visits)
            elif item.key == "reports":
                page = ReportsPage(self.audit_log, self.store.visits.list(),
                                   self.store.etag_events.list())
            else:
                page = PlaceholderPage(item.label, PAGE_DESCRIPTIONS[item.key])
            self._page_indexes[item.key] = self.pages.addWidget(page)
        workspace_layout.addWidget(self.pages, 1)
        root_layout.addWidget(workspace, 1)

        self.select_page("dashboard")
        if session is not None:
            self.audit_log.record(
                action="Login",
                target="Admin portal",
                summary=f"{session.display_name} signed in",
                details=f"{session.describe()}. Session started {session.signed_in_at:%d %b %Y %H:%M:%S}.",
            )

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(250)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 20, 18, 14)
        layout.setSpacing(5)

        # Compact row, not a tall centred block: the sidebar has to fit ten nav items
        # inside the window's 700px minimum height.
        brand = QWidget()
        brand.setObjectName("brandBox")
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(10)
        brand_row.addWidget(self._brand_mark())
        names = QVBoxLayout()
        names.setSpacing(0)
        title = QLabel("Askari VMS")
        title.setObjectName("brandTitle")
        subtitle = QLabel("LOCAL CONTROL SYSTEM")
        subtitle.setObjectName("brandSubtitle")
        names.addWidget(title)
        names.addWidget(subtitle)
        brand_row.addLayout(names)
        brand_row.addStretch()
        layout.addWidget(brand)
        layout.addSpacing(14)

        for item in ADMIN_NAVIGATION:
            button = self._nav_button(item)
            self._nav_buttons[item.key] = button
            layout.addWidget(button)

        layout.addStretch()
        footer = QLabel("ADMIN WORKSTATION\nHardware mode: Simulation")
        footer.setObjectName("sidebarFooter")
        footer.setWordWrap(True)
        layout.addWidget(footer)
        return sidebar

    def _settings_saved(self, settings) -> None:
        self.settings = settings
        self.etags_page.set_controllers(controller_choices(settings))
        # The workstation role decides which lane this PC serves, so the portal buttons
        # have to follow it immediately — otherwise the change only appears on restart.
        self._apply_workstation_lane()

    def _apply_workstation_lane(self) -> None:
        lane = workstation_lane(self.settings.workstation_role)
        self.entry_portal_button.setVisible(lane is Portal.ENTRY)
        self.exit_portal_button.setVisible(lane is Portal.EXIT)
        self.dashboard_page.entry_button.setVisible(lane is Portal.ENTRY)

    def _brand_mark(self) -> QLabel:
        """The directorate emblem, or a text badge until the logo file is installed."""
        mark = QLabel()
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge = circular_logo(BRAND_LOGO_SIZE)
        if badge.isNull():
            mark.setObjectName("brandMark")
            mark.setText("A12")
            mark.setFixedSize(40, 40)
            return mark
        mark.setObjectName("brandLogo")
        mark.setPixmap(badge)
        mark.setFixedSize(BRAND_LOGO_SIZE, BRAND_LOGO_SIZE)
        return mark

    def _nav_button(self, item: NavigationItem) -> QPushButton:
        button = QPushButton(f"  {item.label}")
        button.setIcon(navigation_icon(item.key))
        button.setIconSize(QSize(21, 21))
        button.setProperty("nav", True)
        button.setProperty("active", False)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumHeight(42)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.clicked.connect(lambda checked=False, key=item.key: self.select_page(key))
        return button

    def _build_topbar(self) -> QFrame:
        topbar = QFrame()
        topbar.setObjectName("topbar")
        topbar.setFixedHeight(76)
        layout = QHBoxLayout(topbar)
        layout.setContentsMargins(28, 12, 28, 12)

        title_group = QVBoxLayout()
        title_group.setSpacing(1)
        self.page_eyebrow = QLabel("ADMIN PORTAL")
        self.page_eyebrow.setObjectName("pageEyebrow")
        self.page_title = QLabel("Dashboard")
        self.page_title.setObjectName("pageTitle")
        title_group.addWidget(self.page_eyebrow)
        title_group.addWidget(self.page_title)
        layout.addLayout(title_group)
        layout.addStretch()

        display = self.session.display_name if self.session else "Not signed in"
        initials = "".join(part[0] for part in display.split()[:2]).upper() or "?"
        avatar = QLabel(initials)
        avatar.setObjectName("userAvatar")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(36, 36)
        layout.addWidget(avatar)
        user = QVBoxLayout()
        user.setSpacing(0)
        name = QLabel(display)
        name.setObjectName("userName")
        role = QLabel(self.session.role if self.session else "—")
        role.setObjectName("muted")
        user.addWidget(name)
        user.addWidget(role)
        layout.addLayout(user)

        self.entry_portal_button = QPushButton("Entry Portal")
        self.entry_portal_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.entry_portal_button.clicked.connect(self.open_entry_portal)
        # Entry is restricted to its assigned PC, so the button follows the setting.
        lane = workstation_lane(self.settings.workstation_role)
        self.entry_portal_button.setVisible(lane is Portal.ENTRY)

        self.exit_portal_button = QPushButton("Exit Portal")
        self.exit_portal_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.exit_portal_button.clicked.connect(self.open_exit_portal)
        self.exit_portal_button.setVisible(lane is Portal.EXIT)
        layout.addWidget(self.exit_portal_button)
        layout.addSpacing(14)
        layout.addWidget(self.entry_portal_button)

        self.sign_out_button = QPushButton("Sign out")
        self.sign_out_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sign_out_button.clicked.connect(self.sign_out)
        self.sign_out_button.setVisible(self.session is not None)
        layout.addSpacing(14)
        layout.addWidget(self.sign_out_button)
        return topbar

    def open_entry_portal(self) -> EntryPortalWindow:
        """Open the operator screen for this workstation's Visitor Entry lane."""
        portal = EntryPortalWindow(
            audit_log=self.audit_log,
            categories=self.store.categories.active(),
            visits=self.store.visits.list(),
            session=self.session,
            on_submit=self._visit_submitted,
            events=self.store.etag_events.list(),
            cnic_reader=LocalCNICReader(
                self.settings.peripherals.id_card_camera_index,
                self.settings.storage.data_directory,
            ),
        )
        portal.refresh_events()
        self.entry_portal = portal
        portal.show()
        return portal

    def open_exit_portal(self) -> ExitPortalWindow:
        """The operator screen for this workstation's Visitor Exit lane."""
        portal = ExitPortalWindow(
            audit_log=self.audit_log,
            visits=self.store.visits.list(),
            session=self.session,
            on_checkout=self._visit_submitted,
        )
        self.exit_portal = portal
        portal.show()
        return portal

    def _visit_submitted(self, visit) -> None:
        self.store.visits.save(visit)
        # The dashboard is the live picture of the gate, so it must not go stale.
        self.dashboard_page.set_data(
            self.store.visits.list(), self.store.etags.list(), self.store.etag_events.list()
        )

    def sign_out(self) -> None:
        """Record the logout before tearing anything down, then hand back to login."""
        if self.session is not None:
            self.audit_log.record(
                action="Logout",
                target="Admin portal",
                summary=f"{self.session.display_name} signed out",
                details=f"{self.session.describe()}. Signed in at {self.session.signed_in_at:%d %b %Y %H:%M:%S}.",
            )
            self.session = None
        self.signed_out.emit()
        self.close()

    def select_page(self, key: str) -> None:
        self.pages.setCurrentIndex(self._page_indexes[key])
        selected = next(item for item in ADMIN_NAVIGATION if item.key == key)
        self.page_title.setText(selected.label)
        for button_key, button in self._nav_buttons.items():
            is_active = button_key == key
            button.setProperty("active", is_active)
            button.setIcon(navigation_icon(button_key, "#145536" if is_active else "#ffffff"))
            button.style().unpolish(button)
            button.style().polish(button)
