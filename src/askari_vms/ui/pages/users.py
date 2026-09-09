from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

from dataclasses import replace

from askari_vms.ui.pagination import Pager
from askari_vms.users import UserAccount, UserRole, count_active_admins, hash_password, validate_user


LAST_ADMIN_MESSAGE = "At least one active Admin account must remain. Assign another Admin first."


# Ctrl+N focus jumps, matching the reference screen.
SHORTCUTS = (
    ("Ctrl+1", "Focus Employee ID", "employee_id"),
    ("Ctrl+2", "Focus Name", "full_name"),
    ("Ctrl+3", "Focus Mobile Number", "contact"),
    ("Ctrl+4", "Focus Email", "email"),
    ("Ctrl+5", "Focus Department", "department"),
    ("Ctrl+6", "Focus Role", "designation"),
    ("Ctrl+7", "Focus CNIC", "cnic"),
    ("Ctrl+8", "Focus Password", "password"),
    ("Ctrl+9", "Focus Confirm Password", "confirmation"),
)
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp);;All files (*)"


class UserFormPage(QWidget):
    cancelled = Signal()

    def __init__(self, account: UserAccount | None = None) -> None:
        super().__init__()
        self.account = account
        self._cnic_paths: dict[str, str] = {"cnic_front": "", "cnic_back": ""}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)

        heading = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Edit Employee" if account else "New Employee")
        title.setProperty("section", "true")
        subtitle = QLabel("Individual credentials and permissions; the PC configuration determines Entry or Exit")
        subtitle.setProperty("muted", "true")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        heading.addLayout(titles)
        heading.addStretch()
        back = QPushButton("Back")
        back.clicked.connect(self.cancelled.emit)
        heading.addWidget(back)
        layout.addLayout(heading)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        columns.addWidget(self._details_card(account), 2)
        columns.addWidget(self._identity_card(), 1)
        layout.addLayout(columns)

        footer = QHBoxLayout()
        self.error = QLabel()
        self.error.setObjectName("formError")
        self.error.setWordWrap(True)
        self.error.hide()
        footer.addWidget(self.error, 1)
        self.save_button = QPushButton("Update Record" if account else "Save Employee")
        self.save_button.setObjectName("primaryButton")
        self.save_button.setMinimumWidth(170)
        footer.addStretch()
        footer.addWidget(self.save_button, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(footer)
        layout.addStretch()

        self._install_shortcuts()
        if account:
            self._populate(account)

    # ---------- cards ----------

    def _details_card(self, account: UserAccount | None) -> QFrame:
        card = QFrame()
        card.setProperty("card", True)
        grid = QGridLayout(card)
        grid.setContentsMargins(22, 20, 22, 22)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self.employee_id = self._line()
        self.full_name = self._line()
        self.status = self._combo(("Active", "Deactivated"))
        self.contact = self._line()
        self.address = QTextEdit()
        self.address.setMaximumHeight(84)
        self.email = self._line()
        self.department = self._line()
        self.department.setText("Security")
        self.designation = self._line("Job title, for example Gate Supervisor")
        self.role = self._combo(tuple(item.value for item in UserRole))
        # Least privilege by default: an account only becomes Admin on purpose.
        self.role.setCurrentText(UserRole.OPERATOR.value)
        self.username = self._line()
        self.password = self._line("Leave blank to keep the existing password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirmation = self._line("Repeat password")
        self.confirmation.setEchoMode(QLineEdit.EchoMode.Password)

        self._field(grid, 0, 0, "Employee ID *", self.employee_id)
        self._field(grid, 0, 1, "Employee Name *", self.full_name)
        self._field(grid, 1, 0, "Status", self.status)
        self._field(grid, 1, 1, "Mobile Number", self.contact)
        self._field(grid, 2, 0, "Address", self.address, span=2)
        self._field(grid, 3, 0, "Email", self.email)
        self._field(grid, 4, 0, "Team / Department", self.department)
        self._field(grid, 4, 1, "Role", self.designation)
        self._field(grid, 5, 0, "Employee Type", self.role, span=2)
        self._field(grid, 6, 0, "Username *", self.username)
        self._field(grid, 7, 0, "Password" + ("" if account else " *"), self.password)
        self._field(grid, 8, 0, "Confirm Password", self.confirmation)
        return card

    def _identity_card(self) -> QFrame:
        card = QFrame()
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 22)
        layout.setSpacing(12)

        layout.addWidget(QLabel("CNIC"))
        self.cnic = self._line("00000-0000000-0")
        layout.addWidget(self.cnic)

        self._cnic_labels: dict[str, QLabel] = {}
        for key, label in (("cnic_front", "CNIC Front"), ("cnic_back", "CNIC Back")):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            choose = QPushButton("Choose File")
            choose.clicked.connect(lambda _=False, k=key, name=label: self._choose_image(k, name))
            row.addWidget(choose)
            chosen = QLabel("No file chosen")
            chosen.setProperty("muted", "true")
            chosen.setWordWrap(True)
            self._cnic_labels[key] = chosen
            row.addWidget(chosen, 1)
            layout.addLayout(row)

        layout.addSpacing(10)
        shortcuts = QLabel(
            "<b>Shortcut Keys</b><br>"
            + "<br>".join("<b>{0}</b>: {1}".format(key, what) for key, what, _ in SHORTCUTS)
            + "<br><b>Ctrl + Enter</b>: Submit Form"
        )
        shortcuts.setObjectName("shortcutPanel")
        shortcuts.setWordWrap(True)
        layout.addWidget(shortcuts)
        layout.addStretch()
        return card

    def _choose_image(self, key: str, label: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select " + label + " image", "", IMAGE_FILTER)
        if not path:
            return
        self._cnic_paths[key] = path
        self._cnic_labels[key].setText(Path(path).name)

    def _install_shortcuts(self) -> None:
        for key, _what, attribute in SHORTCUTS:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda widget=getattr(self, attribute): widget.setFocus())
        for key in ("Ctrl+Return", "Ctrl+Enter"):
            submit = QShortcut(QKeySequence(key), self)
            submit.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            submit.activated.connect(self.save_button.click)

    # ---------- helpers ----------

    @staticmethod
    def _line(placeholder: str = "") -> QLineEdit:
        widget = QLineEdit()
        widget.setPlaceholderText(placeholder)
        widget.setMinimumHeight(36)
        return widget

    @staticmethod
    def _combo(items: tuple[str, ...]) -> QComboBox:
        widget = QComboBox()
        widget.addItems(items)
        widget.setMinimumHeight(36)
        return widget

    @staticmethod
    def _field(layout: QGridLayout, row: int, column: int, label: str, widget: QWidget, span: int = 1) -> None:
        box = QVBoxLayout()
        box.setSpacing(4)
        box.addWidget(QLabel(label))
        box.addWidget(widget)
        layout.addLayout(box, row, column, 1, span)

    def _populate(self, account: UserAccount) -> None:
        for widget, value in (
            (self.employee_id, account.employee_id), (self.full_name, account.full_name),
            (self.username, account.username), (self.contact, account.contact), (self.cnic, account.cnic),
            (self.email, account.email), (self.department, account.department),
            (self.designation, account.designation),
        ):
            widget.setText(value)
        self.address.setPlainText(account.address)
        self.role.setCurrentText(account.role)
        self.status.setCurrentText(account.status)
        for key, value in (("cnic_front", account.cnic_front), ("cnic_back", account.cnic_back)):
            self._cnic_paths[key] = value
            self._cnic_labels[key].setText(Path(value).name if value else "No file chosen")

    def value(self) -> UserAccount:
        return UserAccount(
            employee_id=self.employee_id.text(), full_name=self.full_name.text(), username=self.username.text(),
            role=self.role.currentText(), status=self.status.currentText(),
            contact=self.contact.text(), cnic=self.cnic.text(), email=self.email.text(),
            department=self.department.text(), designation=self.designation.text(),
            address=self.address.toPlainText(),
            cnic_front=self._cnic_paths["cnic_front"], cnic_back=self._cnic_paths["cnic_back"],
        ).normalized()

    def show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()


class UsersPage(QWidget):
    def __init__(self, records: list[UserAccount] | None = None, repository: object | None = None) -> None:
        super().__init__()
        self._delete_armed = False
        self._repository = repository
        if records is None:
            if repository is not None:
                records = repository.list()
            else:
                from askari_vms.demo_data import user_accounts

                records = user_accounts()
        self._records = records
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)
        self.list_page = QWidget()
        self.stack.addWidget(self.list_page)
        self._build_list()
        self.refresh()

    def _build_list(self) -> None:
        layout = QVBoxLayout(self.list_page)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Users and Operators")
        title.setProperty("section", "true")
        subtitle = QLabel("Individual accounts with role permissions and session auditing")
        subtitle.setProperty("muted", "true")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        heading.addLayout(titles)
        heading.addStretch()
        add = QPushButton("+ Add User")
        add.setObjectName("primaryButton")
        add.clicked.connect(self._show_add)
        heading.addWidget(add)
        layout.addLayout(heading)

        toolbar = QFrame()
        toolbar.setProperty("card", True)
        tools = QHBoxLayout(toolbar)
        tools.setContentsMargins(14, 12, 14, 12)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search employee ID, name, username, role, or department…")
        self.search.setMinimumHeight(38)
        self.search.textChanged.connect(self.refresh)
        tools.addWidget(self.search, 1)
        self.actions = QWidget()
        action_layout = QHBoxLayout(self.actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        self.edit = QPushButton("Edit selected")
        self.edit.clicked.connect(self._show_edit)
        apply_status = QPushButton("Activate / Deactivate")
        apply_status.clicked.connect(self._toggle_status)
        self.delete = QPushButton("Delete selected")
        self.delete.setObjectName("dangerButton")
        self.delete.clicked.connect(self._delete_selected)
        action_layout.addWidget(self.edit)
        action_layout.addWidget(apply_status)
        action_layout.addWidget(self.delete)
        self.actions.hide()
        tools.addWidget(self.actions)
        layout.addWidget(toolbar)
        self.message = QLabel()
        self.message.setProperty("muted", "true")
        layout.addWidget(self.message)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(("", "Employee ID", "Full Name", "Username", "Role", "Department", "Status"))
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, 42)
        self.table.itemChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(self._show_edit)
        layout.addWidget(self.table, 1)

        self.pager = Pager()
        self.pager.changed.connect(self.refresh)
        layout.addWidget(self.pager)

    def _store_save(self, account: UserAccount) -> None:
        if self._repository is not None:
            self._repository.save(account)

    def _store_delete(self, employee_id: str) -> None:
        if self._repository is not None:
            self._repository.delete(employee_id)

    def refresh(self, *_args: object) -> None:
        query = self.search.text().strip().casefold()
        visible = [(i, user) for i, user in enumerate(self._records) if query in " ".join((user.employee_id, user.full_name, user.username, user.role, user.department)).casefold()]
        visible = self.pager.slice(visible)
        self.table.blockSignals(True)
        self.table.setRowCount(len(visible))
        for row, (index, user) in enumerate(visible):
            selector = QTableWidgetItem()
            selector.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            selector.setCheckState(Qt.CheckState.Unchecked)
            selector.setData(Qt.ItemDataRole.UserRole, index)
            self.table.setItem(row, 0, selector)
            values = (user.employee_id, user.full_name, user.username, user.role, user.department, user.status)
            for column, value in enumerate(values, start=1):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        self.table.blockSignals(False)
        self.actions.hide()
        self._delete_armed = False
        self.delete.setText("Delete selected")
        self.message.setText(f"{len(visible)} shown  •  {len(self._records)} total")

    def _selected(self) -> list[int]:
        return [int(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) for row in range(self.table.rowCount()) if self.table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def _selection_changed(self, _item: QTableWidgetItem) -> None:
        selected = self._selected()
        self.actions.setVisible(bool(selected))
        self.edit.setVisible(len(selected) == 1)
        self._delete_armed = False
        self.delete.setText("Delete selected")
        self.message.setText(f"{len(selected)} selected" if selected else f"{len(self._records)} total")

    def _show_form(self, form: UserFormPage) -> None:
        form.cancelled.connect(self._show_list)
        self.stack.addWidget(form)
        self.stack.setCurrentWidget(form)

    def _show_list(self) -> None:
        current = self.stack.currentWidget()
        self.stack.setCurrentWidget(self.list_page)
        if current is not self.list_page:
            self.stack.removeWidget(current)
            current.deleteLater()

    def _show_add(self) -> None:
        form = UserFormPage()
        form.save_button.clicked.connect(lambda: self._save(form))
        self._show_form(form)

    def _show_edit(self, *_args: object) -> None:
        index = int(self.table.item(_args[0].row(), 0).data(Qt.ItemDataRole.UserRole)) if _args and hasattr(_args[0], "row") else (self._selected()[0] if len(self._selected()) == 1 else -1)
        if index < 0:
            return
        form = UserFormPage(self._records[index])
        form.save_button.clicked.connect(lambda: self._save(form, index))
        self._show_form(form)

    def _locks_out_admins(self, proposed: list[UserAccount]) -> bool:
        """True when a change would leave no active Admin able to sign in."""
        return count_active_admins(self._records) > 0 and count_active_admins(proposed) == 0

    def _save(self, form: UserFormPage, editing: int | None = None) -> None:
        account = form.value()
        password = form.password.text()
        errors = validate_user(account, password, form.confirmation.text(), require_password=editing is None)
        if any(i != editing and item.username == account.username for i, item in enumerate(self._records)):
            errors["username_unique"] = "This username is already in use."
        if any(i != editing and item.employee_id == account.employee_id for i, item in enumerate(self._records)):
            errors["employee_unique"] = "This employee ID is already in use."
        if editing is not None and self._locks_out_admins(self._records[:editing] + [account] + self._records[editing + 1:]):
            errors["last_admin"] = LAST_ADMIN_MESSAGE
        if errors:
            form.show_error("  •  ".join(errors.values()))
            return
        if password:
            account = replace(account, password_hash=hash_password(password))
        elif editing is not None:
            account = replace(account, password_hash=self._records[editing].password_hash)
        if editing is None:
            self._records.append(account)
        else:
            previous = self._records[editing]
            self._records[editing] = account
            if previous.employee_id != account.employee_id:
                self._store_delete(previous.employee_id)
        self._store_save(account)
        self._show_list()
        self.refresh()

    def _toggle_status(self) -> None:
        indexes = self._selected()
        if not indexes:
            return
        proposed = list(self._records)
        activated = 0
        deactivated = 0
        for index in indexes:
            is_active = proposed[index].status == "Active"
            new_status = "Deactivated" if is_active else "Active"
            proposed[index] = replace(proposed[index], status=new_status)
            if is_active:
                deactivated += 1
            else:
                activated += 1
        if self._locks_out_admins(proposed):
            self.message.setText(LAST_ADMIN_MESSAGE)
            return
        self._records = proposed
        for index in indexes:
            self._store_save(self._records[index])
        self.refresh()
        changes = []
        if activated:
            changes.append(f"{activated} activated")
        if deactivated:
            changes.append(f"{deactivated} deactivated")
        self.message.setText("Updated user accounts: " + ", ".join(changes) + ".")

    def _delete_selected(self) -> None:
        indexes = self._selected()
        if not indexes:
            return
        remaining = [record for index, record in enumerate(self._records) if index not in set(indexes)]
        if self._locks_out_admins(remaining):
            self._delete_armed = False
            self.delete.setText("Delete selected")
            self.message.setText(LAST_ADMIN_MESSAGE)
            return
        if not self._delete_armed:
            self._delete_armed = True
            self.delete.setText(f"Confirm delete ({len(indexes)})")
            self.message.setText("Click Confirm delete to remove the selected user accounts.")
            return
        for index in indexes:
            self._store_delete(self._records[index].employee_id)
        self._records = remaining
        self.refresh()
        self.message.setText(f"Deleted {len(indexes)} user account(s).")
