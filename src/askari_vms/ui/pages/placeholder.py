from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class PlaceholderPage(QWidget):
    def __init__(self, title: str, description: str) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 28)

        card = QFrame()
        card.setProperty("card", True)
        card.setMaximumHeight(230)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(10)

        heading = QLabel(title)
        heading.setProperty("section", "true")
        message = QLabel(description)
        message.setProperty("muted", "true")
        message.setWordWrap(True)
        message.setMaximumWidth(680)
        status = QLabel("Planned for the next UI milestone")
        status.setProperty("status", "warning")
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status.setMaximumWidth(220)

        card_layout.addWidget(heading)
        card_layout.addWidget(message)
        card_layout.addSpacing(8)
        card_layout.addWidget(status)
        card_layout.addStretch()
        outer.addWidget(card)
        outer.addStretch()

