from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from askari_vms.auth import Session, authenticate, start_session
from askari_vms.storage import Store
from askari_vms.ui.brand import circular_logo

LOGO_SIZE = 84


class LoginWindow(QWidget):
    """Sign-in screen. Nothing else opens until an active account authenticates."""

    authenticated = Signal(object)

    def __init__(self, store: Store, workstation: str = "Admin / Entry PC") -> None:
        super().__init__()
        self._store = store
        self._workstation = workstation
        self.setWindowTitle("Askari VMS — Sign in")
        self.setObjectName("loginRoot")
        self.setMinimumSize(460, 520)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 40, 40, 40)
        outer.addStretch()

        card = QFrame()
        card.setProperty("card", True)
        card.setMaximumWidth(380)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(30, 28, 30, 28)
        layout.setSpacing(12)

        badge = circular_logo(LOGO_SIZE)
        if not badge.isNull():
            emblem = QLabel()
            emblem.setPixmap(badge)
            emblem.setFixedSize(LOGO_SIZE, LOGO_SIZE)
            emblem.setObjectName("brandLogo")
            layout.addWidget(emblem, 0, Qt.AlignmentFlag.AlignHCenter)

        title = QLabel("Askari VMS")
        title.setProperty("section", "true")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle = subtitle = QLabel(f"Sign in to continue  •  {workstation}")
        subtitle.setProperty("muted", "true")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(8)

        layout.addWidget(QLabel("Username"))
        self.username = QLineEdit()
        self.username.setPlaceholderText("Your username")
        self.username.setMinimumHeight(38)
        layout.addWidget(self.username)

        layout.addWidget(QLabel("Password"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Your password")
        self.password.setMinimumHeight(38)
        layout.addWidget(self.password)

        self.error = QLabel()
        self.error.setObjectName("formError")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)

        self.sign_in_button = QPushButton("Sign in")
        self.sign_in_button.setObjectName("primaryButton")
        self.sign_in_button.setMinimumHeight(40)
        self.sign_in_button.clicked.connect(self.sign_in)
        layout.addWidget(self.sign_in_button)

        # Enter submits from either field.
        self.username.returnPressed.connect(self.sign_in)
        self.password.returnPressed.connect(self.sign_in)

        outer.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch()

    def sign_in(self) -> Session | None:
        account, problem = authenticate(
            self._store.users.list(), self.username.text(), self.password.text()
        )
        if account is None:
            self._fail(problem)
            return None

        self.error.hide()
        self.password.clear()
        session = start_session(account, self._workstation)
        self.authenticated.emit(session)
        return session

    def _fail(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()
        self.password.clear()
        self.password.setFocus()

    def set_workstation(self, workstation: str) -> None:
        """Follow a role changed in Settings, so the screen never names the wrong lane."""
        self._workstation = workstation
        self.subtitle.setText(f"Sign in to continue  •  {workstation}")

    def reset(self) -> None:
        self.username.clear()
        self.password.clear()
        self.error.hide()
        self.username.setFocus()
