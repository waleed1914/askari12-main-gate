from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QColor
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from askari_vms.etags import ETagRecord, ETagState, expiry_state, renew_for_one_year, validate_etag
from askari_vms.ui.pagination import Pager


# Prompt entries shown first in their combo. They are not real values: value()
# maps them back to "" so validation reports the field as missing.
SELECT_GENDER = "Select Gender"
SELECT_VEHICLE_TYPE = "Select Vehicle Type"
PLACEHOLDER_CHOICES = frozenset({SELECT_GENDER, SELECT_VEHICLE_TYPE})

CONTROLLERS = (
    ("entry", "Entry Controller — E-tag Entry (Door 2)"),
    ("exit", "Exit Controller — E-tag Exit (Door 2)"),
)


class ETagFormPage(QWidget):
    saved = Signal(object)
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None, record: ETagRecord | None = None,
                 controllers: tuple[tuple[str, str], ...] = CONTROLLERS) -> None:
        super().__init__(parent)
        self.record = record
        self._controllers = controllers
        self._vehicle_image = ""
        self._fields: dict[str, QWidget] = {}
        self._controller_checks: dict[str, QCheckBox] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("dialogHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(24, 18, 24, 18)
        title = QLabel("Edit E-Tag" if record else "Add E-Tag")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("Resident, vehicle, validity, and controller access")
        subtitle.setProperty("muted", "true")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 20, 24, 20)
        body_layout.setSpacing(16)
        body_layout.addWidget(self._identity_section())
        body_layout.addWidget(self._vehicle_section())
        body_layout.addWidget(self._access_section())
        body_layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        footer = QFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 12, 24, 18)
        self.error_label = QLabel()
        self.error_label.setObjectName("formError")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        footer_layout.addWidget(self.error_label, 1)
        back = QPushButton("Back")
        back.clicked.connect(self.cancelled.emit)
        save = QPushButton("Save E-Tag")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._validate_and_save)
        footer_layout.addWidget(back)
        footer_layout.addWidget(save)
        outer.addWidget(footer)
        self._populate(record)

    def _section(self, title: str) -> tuple[QFrame, QGridLayout]:
        card = QFrame()
        card.setProperty("card", True)
        layout = QGridLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(10)
        heading = QLabel(title)
        heading.setProperty("section", "true")
        layout.addWidget(heading, 0, 0, 1, 2)
        return card, layout

    def _line(self, key: str, placeholder: str = "") -> QLineEdit:
        widget = QLineEdit()
        widget.setPlaceholderText(placeholder)
        widget.setMinimumHeight(36)
        self._fields[key] = widget
        return widget

    def _combo(self, key: str, items: tuple[str, ...]) -> QComboBox:
        widget = QComboBox()
        widget.addItems(items)
        widget.setMinimumHeight(36)
        self._fields[key] = widget
        return widget

    def _field(self, layout: QGridLayout, row: int, column: int, label: str, widget: QWidget) -> None:
        box = QVBoxLayout()
        box.setSpacing(4)
        box.addWidget(QLabel(label))
        box.addWidget(widget)
        layout.addLayout(box, row, column)

    def _identity_section(self) -> QFrame:
        card, layout = self._section("Member and resident information")
        self._field(layout, 1, 0, "Member ID", self._line("member_id", "Society member number"))
        self._field(layout, 1, 1, "User ID *", self._line("user_id", "Unique resident identifier"))
        self._field(layout, 2, 0, "Resident Name *", self._line("resident_name"))
        self._field(layout, 2, 1, "S/O, F/O, D/O", self._line("care_of"))
        self._field(layout, 3, 0, "Resident CNIC No", self._line("cnic", "00000-0000000-0"))
        self._field(layout, 3, 1, "Mobile No", self._line("mobile_no"))
        self._field(layout, 4, 0, "Address", self._line("address", "House, street, phase"))
        self._field(layout, 4, 1, "LESCO Ref No", self._line("lesco_ref_no"))
        self._field(layout, 5, 0, "Street No", self._line("street_no"))
        self._field(layout, 5, 1, "H/Apt No", self._line("house_no"))
        self._field(layout, 6, 0, "Gender", self._combo("gender", (SELECT_GENDER, "Male", "Female", "Other")))
        return card

    def _vehicle_section(self) -> QFrame:
        card, layout = self._section("Vehicle and service information")
        self._field(layout, 1, 0, "Vehicle Type *", self._combo("vehicle_type", (SELECT_VEHICLE_TYPE, "Car", "SUV", "Van", "Truck", "Motorcycle")))
        self._field(layout, 1, 1, "Vehicle Number *", self._line("vehicle_number", "ABC-123"))
        self._field(layout, 2, 0, "Make", self._line("make"))
        self._field(layout, 2, 1, "Model", self._line("model"))
        self._field(layout, 3, 0, "Position", self._line("position"))
        self._field(layout, 3, 1, "Station / Unit", self._line("station_unit"))
        self._field(layout, 4, 0, "Department", self._line("department"))
        self._field(layout, 4, 1, "Challan Number", self._line("challan_number"))

        picker = QWidget()
        picker_row = QHBoxLayout(picker)
        picker_row.setContentsMargins(0, 0, 0, 0)
        choose = QPushButton("Choose File")
        choose.clicked.connect(self._choose_vehicle_image)
        picker_row.addWidget(choose)
        self.vehicle_image_label = QLabel("No file chosen")
        self.vehicle_image_label.setProperty("muted", "true")
        picker_row.addWidget(self.vehicle_image_label, 1)
        self._field(layout, 5, 0, "Vehicle Image", picker)
        return card

    def _choose_vehicle_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select vehicle image", "", "Images (*.png *.jpg *.jpeg *.bmp);;All files (*)"
        )
        if path:
            self._vehicle_image = path
            self.vehicle_image_label.setText(Path(path).name)

    def _access_section(self) -> QFrame:
        card, layout = self._section("E-Tag access and validity")
        self._field(layout, 1, 0, "E-Tag RFID *", self._line("rfid", "Scan or enter RFID"))
        self._field(layout, 1, 1, "Status", self._combo("status", ("Active", "Blocked", "Expired")))

        for key in ("issue_date", "expiry_date"):
            editor = QDateEdit()
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd MMM yyyy")
            editor.setMinimumHeight(36)
            self._fields[key] = editor
        self._field(layout, 2, 0, "Issue Date", self._fields["issue_date"])
        self._field(layout, 2, 1, "Expiry Date", self._fields["expiry_date"])

        checks = QVBoxLayout()
        for key, label in self._controllers:
            checkbox = QCheckBox(label)
            checkbox.setChecked(True)
            self._controller_checks[key] = checkbox
            checks.addWidget(checkbox)
        help_text = QLabel(
            "Select the controllers this E-Tag may enter through. Unselected controllers will "
            "block it. Only the E-tag door on each controller is used — visitor doors never "
            "carry E-tag access."
        )
        help_text.setWordWrap(True)
        help_text.setProperty("muted", "true")
        checks.addWidget(help_text)
        controller_box = QWidget()
        controller_box.setLayout(checks)
        self._field(layout, 3, 0, "Allowed Controllers *", controller_box)

        comments = QTextEdit()
        comments.setMaximumHeight(90)
        comments.setPlaceholderText("Optional notes")
        self._fields["comments"] = comments
        self._field(layout, 3, 1, "Comments", comments)
        return card

    def _populate(self, record: ETagRecord | None) -> None:
        today = QDate.currentDate()
        issue = self._fields["issue_date"]
        expiry = self._fields["expiry_date"]
        assert isinstance(issue, QDateEdit) and isinstance(expiry, QDateEdit)
        issue.setDate(today)
        expiry.setDate(today.addYears(1))
        if not record:
            return
        for key in (
            "user_id", "resident_name", "care_of", "street_no", "house_no", "mobile_no", "cnic",
            "vehicle_number", "make", "model", "position", "station_unit", "department",
            "challan_number", "rfid", "member_id", "address", "lesco_ref_no",
        ):
            widget = self._fields[key]
            assert isinstance(widget, QLineEdit)
            widget.setText(str(getattr(record, key)))
        for key in ("gender", "vehicle_type", "status"):
            widget = self._fields[key]
            assert isinstance(widget, QComboBox)
            widget.setCurrentText(str(getattr(record, key)))
        comments = self._fields["comments"]
        assert isinstance(comments, QTextEdit)
        comments.setPlainText(record.comments)
        issue.setDate(QDate(record.issue_date.year, record.issue_date.month, record.issue_date.day))
        expiry.setDate(QDate(record.expiry_date.year, record.expiry_date.month, record.expiry_date.day))
        for key, checkbox in self._controller_checks.items():
            checkbox.setChecked(key in record.allowed_controllers)
        self._vehicle_image = record.vehicle_image
        self.vehicle_image_label.setText(Path(record.vehicle_image).name if record.vehicle_image else "No file chosen")

    def value(self) -> ETagRecord:
        def text(key: str) -> str:
            widget = self._fields[key]
            assert isinstance(widget, QLineEdit)
            return widget.text()

        def current(key: str) -> str:
            widget = self._fields[key]
            assert isinstance(widget, QComboBox)
            text = widget.currentText()
            return "" if text in PLACEHOLDER_CHOICES else text

        def pydate(key: str) -> date:
            widget = self._fields[key]
            assert isinstance(widget, QDateEdit)
            value = widget.date()
            return date(value.year(), value.month(), value.day())

        comments = self._fields["comments"]
        assert isinstance(comments, QTextEdit)
        return ETagRecord(
            user_id=text("user_id"), resident_name=text("resident_name"), care_of=text("care_of"),
            street_no=text("street_no"), house_no=text("house_no"), mobile_no=text("mobile_no"),
            cnic=text("cnic"), gender=current("gender"), vehicle_type=current("vehicle_type"),
            vehicle_number=text("vehicle_number"), make=text("make"), position=text("position"),
            station_unit=text("station_unit"), department=text("department"),
            challan_number=text("challan_number"), rfid=text("rfid"), status=current("status"),
            issue_date=pydate("issue_date"), expiry_date=pydate("expiry_date"),
            comments=comments.toPlainText(),
            allowed_controllers=tuple(key for key, checkbox in self._controller_checks.items() if checkbox.isChecked()),
            member_id=text("member_id"), address=text("address"),
            lesco_ref_no=text("lesco_ref_no"), model=text("model"),
            vehicle_image=self._vehicle_image,
        ).normalized()

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()

    def _validate_and_save(self) -> None:
        errors = validate_etag(self.value())
        if errors:
            self.show_error("  •  ".join(errors.values()))
            return
        self.error_label.hide()
        self.saved.emit(self.value())


class ETagsPage(QWidget):
    add_requested = Signal()

    def __init__(
        self,
        controllers: tuple[tuple[str, str], ...] | None = None,
        records: list[ETagRecord] | None = None,
        repository: object | None = None,
    ) -> None:
        super().__init__()
        self._controllers = controllers if controllers is not None else CONTROLLERS
        self._repository = repository
        if records is None:
            if repository is not None:
                records = repository.list()
            else:
                from askari_vms.demo_data import etag_records

                records = etag_records()
        self._records = records
        self._refreshing = False
        self._delete_armed = False
        self._build()
        self.refresh()

    def _build(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack)

        self.list_page = QWidget()
        self.stack.addWidget(self.list_page)
        layout = QVBoxLayout(self.list_page)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)

        title_row = QHBoxLayout()
        title_box = QVBoxLayout()
        heading = QLabel("E-Tag Registry")
        heading.setProperty("section", "true")
        subtitle = QLabel("Register, renew, block, and manage controller access")
        subtitle.setProperty("muted", "true")
        title_box.addWidget(heading)
        title_box.addWidget(subtitle)
        title_row.addLayout(title_box)
        title_row.addStretch()
        self.add_button = QPushButton("+ Add E-Tag")
        self.add_button.setObjectName("primaryButton")
        self.add_button.clicked.connect(self.open_add_dialog)
        title_row.addWidget(self.add_button)
        layout.addLayout(title_row)

        toolbar = QFrame()
        toolbar.setProperty("card", True)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(14, 12, 14, 12)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search user ID, resident, vehicle, CNIC, or RFID…")
        self.search.setMinimumHeight(38)
        self.search.textChanged.connect(self.refresh)
        toolbar_layout.addWidget(self.search, 1)
        self.status_filter = QComboBox()
        self.status_filter.addItems(("All statuses", "Active", "Expiring soon", "Expired", "Blocked"))
        self.status_filter.setMinimumHeight(38)
        self.status_filter.currentTextChanged.connect(self.refresh)
        toolbar_layout.addWidget(self.status_filter)
        self.edit_button = QPushButton("Edit selected")
        self.edit_button.setMinimumHeight(38)
        self.edit_button.clicked.connect(self.open_edit_dialog)
        toolbar_layout.addWidget(self.edit_button)
        layout.addWidget(toolbar)

        bulk_bar = QFrame()
        bulk_bar.setObjectName("bulkBar")
        bulk_layout = QHBoxLayout(bulk_bar)
        bulk_layout.setContentsMargins(14, 9, 14, 9)
        self.select_all = QCheckBox("Select all shown")
        self.select_all.stateChanged.connect(self._toggle_all)
        bulk_layout.addWidget(self.select_all)
        self.selection_count = QLabel("0 selected")
        self.selection_count.setProperty("muted", "true")
        bulk_layout.addWidget(self.selection_count)
        bulk_layout.addStretch()

        self.bulk_actions = QWidget()
        action_layout = QHBoxLayout(self.bulk_actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        self.bulk_status = QComboBox()
        self.bulk_status.addItems(("Active", "Blocked", "Expired"))
        self.bulk_status.setMinimumHeight(34)
        action_layout.addWidget(self.bulk_status)
        status_button = QPushButton("Apply status")
        status_button.clicked.connect(self._apply_bulk_status)
        action_layout.addWidget(status_button)
        renew_button = QPushButton("Renew +1 year")
        renew_button.clicked.connect(self._renew_selected)
        action_layout.addWidget(renew_button)
        self.delete_button = QPushButton("Delete selected")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.clicked.connect(self._delete_selected)
        action_layout.addWidget(self.delete_button)
        self.bulk_actions.hide()
        bulk_layout.addWidget(self.bulk_actions)
        layout.addWidget(bulk_bar)

        self.summary = QLabel()
        self.summary.setProperty("muted", "true")
        layout.addWidget(self.summary)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(("", "User ID", "E-Tag RFID", "Vehicle No", "Resident", "Issue Date", "Expiry Date", "Status", "Controllers", "Access"))
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, 42)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self.open_edit_dialog)
        self.table.itemChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)

        self.pager = Pager()
        self.pager.changed.connect(self.refresh)
        layout.addWidget(self.pager)

    def open_add_dialog(self) -> None:
        form = ETagFormPage(self, controllers=self._controllers)
        form.cancelled.connect(self._show_list)
        form.saved.connect(lambda record, page=form: self._save_new(record, page))
        self._show_form(form)

    def open_edit_dialog(self, *_args: object) -> None:
        source_index: int | None = None
        if _args and hasattr(_args[0], "row"):
            row = _args[0].row()
            source_index = int(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))
        else:
            checked = self._selected_source_indexes()
            if len(checked) == 1:
                source_index = checked[0]
        if source_index is None:
            self.summary.setText("Check exactly one E-Tag record, then choose Edit selected.")
            return
        record = self._records[source_index]
        form = ETagFormPage(self, record, controllers=self._controllers)
        form.cancelled.connect(self._show_list)
        form.saved.connect(lambda updated, index=source_index: self._save_edit(index, updated))
        self._show_form(form)

    def set_controllers(self, controllers: tuple[tuple[str, str], ...]) -> None:
        """Called when Settings is saved, so the picker follows the configured controllers."""
        self._controllers = controllers

    def _show_form(self, form: ETagFormPage) -> None:
        self.stack.addWidget(form)
        self.stack.setCurrentWidget(form)

    def _show_list(self) -> None:
        current = self.stack.currentWidget()
        self.stack.setCurrentWidget(self.list_page)
        if current is not self.list_page:
            self.stack.removeWidget(current)
            current.deleteLater()

    def _duplicate_error(self, candidate: ETagRecord, editing: int | None = None) -> str | None:
        """One resident may hold several e-tags, but each vehicle only one."""
        for index, item in enumerate(self._records):
            if index == editing:
                continue
            if item.rfid == candidate.rfid:
                return "This RFID is already registered. Enter or scan a different RFID."
            if item.vehicle_number == candidate.vehicle_number:
                return f"Vehicle {candidate.vehicle_number} already has an E-Tag ({item.user_id}). Each vehicle may hold only one."
        return None

    def _save_new(self, candidate: ETagRecord, form: ETagFormPage) -> None:
        error = self._duplicate_error(candidate)
        if error:
            form.show_error(error)
            return
        self._records.append(candidate)
        self._store_save(candidate)
        self._show_list()
        self.refresh()

    def _save_edit(self, source_index: int, record: ETagRecord) -> None:
        error = self._duplicate_error(record, editing=source_index)
        if error:
            current = self.stack.currentWidget()
            if isinstance(current, ETagFormPage):
                current.show_error(error)
            return
        previous = self._records[source_index]
        self._records[source_index] = record
        if previous.rfid != record.rfid:
            self._store_delete(previous.rfid)
        self._store_save(record)
        self._show_list()
        self.refresh()

    def _selected_source_indexes(self) -> list[int]:
        selected: list[int] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                selected.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return selected

    def _selection_changed(self, _item: QTableWidgetItem | None = None) -> None:
        if self._refreshing:
            return
        count = len(self._selected_source_indexes())
        self.selection_count.setText(f"{count} selected")
        self.bulk_actions.setVisible(count > 0)
        self._delete_armed = False
        self.delete_button.setText("Delete selected")

    def _toggle_all(self, state: int) -> None:
        checked = Qt.CheckState.Checked if state == Qt.CheckState.Checked.value else Qt.CheckState.Unchecked
        self._refreshing = True
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(checked)
        self._refreshing = False
        self._selection_changed()

    def _apply_bulk_status(self) -> None:
        indexes = self._selected_source_indexes()
        if not indexes:
            self.summary.setText("Select one or more E-Tags before applying a status.")
            return
        status = self.bulk_status.currentText()
        for index in indexes:
            self._records[index] = replace(self._records[index], status=status)
            self._store_save(self._records[index])
        self.refresh()
        self.summary.setText(f"Updated {len(indexes)} E-Tag record(s) to {status}. Controller updates will be queued.")

    def _renew_selected(self) -> None:
        indexes = self._selected_source_indexes()
        if not indexes:
            self.summary.setText("Select one or more E-Tags to renew for one year.")
            return
        for index in indexes:
            self._records[index] = renew_for_one_year(self._records[index])
            self._store_save(self._records[index])
        self.refresh()
        self.summary.setText(f"Renewed {len(indexes)} E-Tag record(s) for one calendar year and set them Active.")

    def _delete_selected(self) -> None:
        indexes = self._selected_source_indexes()
        if not indexes:
            self.summary.setText("Select one or more E-Tags to delete.")
            return
        if not self._delete_armed:
            self._delete_armed = True
            self.delete_button.setText(f"Confirm delete ({len(indexes)})")
            self.summary.setText("Click Confirm delete to permanently remove the selected records and revoke controller access.")
            return
        for index in sorted(indexes, reverse=True):
            self._store_delete(self._records[index].rfid)
            del self._records[index]
        self._delete_armed = False
        self.delete_button.setText("Delete selected")
        self.refresh()
        self.summary.setText(f"Deleted {len(indexes)} E-Tag record(s). Controller revocation will be queued.")

    def _store_save(self, record: ETagRecord) -> None:
        if self._repository is not None:
            self._repository.save(record)

    def _store_delete(self, rfid: str) -> None:
        if self._repository is not None:
            self._repository.delete(rfid)

    def refresh(self, *_args: object) -> None:
        query = self.search.text().strip().casefold()
        selected_status = self.status_filter.currentText()
        visible: list[tuple[int, ETagRecord, ETagState]] = []
        for index, record in enumerate(self._records):
            state = expiry_state(record)
            haystack = " ".join((record.user_id, record.resident_name, record.vehicle_number, record.cnic, record.rfid)).casefold()
            if query and query not in haystack:
                continue
            if selected_status != "All statuses" and state.value != selected_status:
                continue
            visible.append((index, record, state))

        visible = self.pager.slice(visible)
        self._refreshing = True
        self.table.setRowCount(len(visible))
        colors = {
            ETagState.ACTIVE: QColor("#eef8f1"),
            ETagState.EXPIRING: QColor("#e6efff"),
            ETagState.EXPIRED: QColor("#fde8e8"),
            ETagState.BLOCKED: QColor("#f3e8e8"),
        }
        for row, (source_index, record, state) in enumerate(visible):
            values = (
                record.user_id, record.rfid, record.vehicle_number, record.resident_name,
                record.issue_date.strftime("%d %b %Y"), record.expiry_date.strftime("%d %b %Y"),
                state.value, str(len(record.allowed_controllers)), "Allowed" if state is ETagState.ACTIVE else state.value,
            )
            selector = QTableWidgetItem()
            selector.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            selector.setCheckState(Qt.CheckState.Unchecked)
            selector.setData(Qt.ItemDataRole.UserRole, source_index)
            selector.setBackground(colors[state])
            self.table.setItem(row, 0, selector)
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setBackground(colors[state])
                self.table.setItem(row, column, item)
        self._refreshing = False
        self.select_all.blockSignals(True)
        self.select_all.setChecked(False)
        self.select_all.blockSignals(False)
        self.selection_count.setText("0 selected")
        self.bulk_actions.hide()
        self._delete_armed = False
        self.delete_button.setText("Delete selected")
        expiring = sum(expiry_state(record) is ETagState.EXPIRING for record in self._records)
        self.summary.setText(f"{len(visible)} shown  •  {len(self._records)} total  •  {expiring} expiring within 10 days")
