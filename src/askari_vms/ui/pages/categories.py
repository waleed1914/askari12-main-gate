from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from askari_vms.categories import ALLOWED_SHORTCUTS, VehicleCategory, validate_category


class CategoryFormPage(QWidget):
    cancelled = Signal()

    def __init__(
        self,
        category: VehicleCategory | None = None,
        available_shortcuts: tuple[str, ...] = ALLOWED_SHORTCUTS,
    ) -> None:
        super().__init__()
        self.category = category
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)

        heading_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Edit Vehicle Category" if category else "Add Vehicle Category")
        title.setProperty("section", "true")
        subtitle = QLabel("Configure the Entry shortcut and optional receipt values")
        subtitle.setProperty("muted", "true")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        heading_row.addLayout(title_box)
        heading_row.addStretch()
        back = QPushButton("Back")
        back.clicked.connect(self.cancelled.emit)
        heading_row.addWidget(back)
        layout.addLayout(heading_row)

        card = QFrame()
        card.setProperty("card", True)
        grid = QGridLayout(card)
        grid.setContentsMargins(22, 20, 22, 22)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(13)

        self.name = QLineEdit()
        self.name.setPlaceholderText("Example: Car")
        self.shortcut = QComboBox()
        self.shortcut.addItems(available_shortcuts)
        self.price = self._amount()
        self.lost_price = self._amount()
        self.description = QTextEdit()
        self.description.setPlaceholderText("Optional category description")
        self.description.setMaximumHeight(110)
        self.active = QCheckBox("Category is available in the Entry portal")
        self.active.setChecked(True)

        self._add_field(grid, 0, 0, "Category Name *", self.name)
        self._add_field(grid, 0, 1, "Shortcut Key *", self.shortcut)
        self._add_field(grid, 1, 0, "Price (optional)", self.price)
        self._add_field(grid, 1, 1, "Lost Receipt Price (optional)", self.lost_price)
        self._add_field(grid, 2, 0, "Description", self.description, 1, 2)
        grid.addWidget(self.active, 3, 0, 1, 2)
        layout.addWidget(card)

        footer = QHBoxLayout()
        self.error = QLabel()
        self.error.setObjectName("formError")
        self.error.setWordWrap(True)
        self.error.hide()
        footer.addWidget(self.error, 1)
        self.save_button = QPushButton("Save Category")
        self.save_button.setObjectName("primaryButton")
        footer.addWidget(self.save_button)
        layout.addLayout(footer)
        layout.addStretch()

        if category:
            self.name.setText(category.name)
            self.shortcut.setCurrentText(category.shortcut)
            self.price.setValue(category.price)
            self.lost_price.setValue(category.lost_receipt_price)
            self.description.setPlainText(category.description)
            self.active.setChecked(category.active)

    @staticmethod
    def _amount() -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setRange(0, 1_000_000)
        field.setDecimals(0)
        field.setPrefix("Rs. ")
        field.setMinimumHeight(36)
        return field

    @staticmethod
    def _add_field(layout: QGridLayout, row: int, column: int, label: str, widget: QWidget, row_span: int = 1, column_span: int = 1) -> None:
        box = QVBoxLayout()
        box.setSpacing(4)
        box.addWidget(QLabel(label))
        box.addWidget(widget)
        layout.addLayout(box, row, column, row_span, column_span)

    def value(self) -> VehicleCategory:
        return VehicleCategory(
            name=self.name.text(), shortcut=self.shortcut.currentText(),
            description=self.description.toPlainText(), price=self.price.value(),
            lost_receipt_price=self.lost_price.value(), active=self.active.isChecked(),
        ).normalized()

    def show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()


class CategoriesPage(QWidget):
    def __init__(self, records: list[VehicleCategory] | None = None,
                 repository: object | None = None) -> None:
        super().__init__()
        self._repository = repository
        if records is None:
            if repository is not None:
                records = repository.list()
            else:
                from askari_vms.categories import default_categories

                records = default_categories()
        self._records = records
        self._delete_armed = False
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

        title_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Vehicle Categories")
        title.setProperty("section", "true")
        subtitle = QLabel("Categories and shortcut keys used by Entry operators")
        subtitle.setProperty("muted", "true")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        title_row.addLayout(title_box)
        title_row.addStretch()
        add = QPushButton("+ Add Category")
        add.setObjectName("primaryButton")
        add.clicked.connect(self._show_add)
        title_row.addWidget(add)
        layout.addLayout(title_row)

        toolbar = QFrame()
        toolbar.setProperty("card", True)
        tools = QHBoxLayout(toolbar)
        tools.setContentsMargins(14, 12, 14, 12)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search categories or shortcuts…")
        self.search.setMinimumHeight(38)
        self.search.textChanged.connect(self.refresh)
        tools.addWidget(self.search, 1)
        self.actions = QWidget()
        action_layout = QHBoxLayout(self.actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        edit = QPushButton("Edit selected")
        edit.clicked.connect(self._show_edit)
        self.delete = QPushButton("Delete selected")
        self.delete.setObjectName("dangerButton")
        self.delete.clicked.connect(self._delete_selected)
        action_layout.addWidget(edit)
        action_layout.addWidget(self.delete)
        self.actions.hide()
        tools.addWidget(self.actions)
        layout.addWidget(toolbar)

        self.message = QLabel()
        self.message.setProperty("muted", "true")
        layout.addWidget(self.message)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(("", "Name", "Shortcut", "Price", "Lost Receipt", "Status"))
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

    def _store_save(self, category: VehicleCategory) -> None:
        if self._repository is not None:
            self._repository.save(category)

    def _store_delete(self, name: str) -> None:
        if self._repository is not None:
            self._repository.delete(name)

    def refresh(self, *_args: object) -> None:
        query = self.search.text().strip().casefold()
        visible = [(i, record) for i, record in enumerate(self._records) if query in f"{record.name} {record.shortcut}".casefold()]
        self.table.blockSignals(True)
        self.table.setRowCount(len(visible))
        for row, (index, record) in enumerate(visible):
            selector = QTableWidgetItem()
            selector.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            selector.setCheckState(Qt.CheckState.Unchecked)
            selector.setData(Qt.ItemDataRole.UserRole, index)
            self.table.setItem(row, 0, selector)
            values = (record.name, record.shortcut, f"Rs. {record.price:,.0f}", f"Rs. {record.lost_receipt_price:,.0f}", "Active" if record.active else "Inactive")
            for column, value in enumerate(values, start=1):
                self.table.setItem(row, column, QTableWidgetItem(value))
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
        self._delete_armed = False
        self.delete.setText("Delete selected")
        self.message.setText(f"{len(selected)} selected" if selected else f"{len(self._records)} total")

    def _show_form(self, form: CategoryFormPage) -> None:
        self.stack.addWidget(form)
        self.stack.setCurrentWidget(form)

    def _show_list(self) -> None:
        current = self.stack.currentWidget()
        self.stack.setCurrentWidget(self.list_page)
        if current is not self.list_page:
            self.stack.removeWidget(current)
            current.deleteLater()

    def _show_add(self) -> None:
        form = CategoryFormPage(available_shortcuts=self._available_shortcuts())
        form.cancelled.connect(self._show_list)
        form.save_button.clicked.connect(lambda: self._save_new(form))
        self._show_form(form)

    def _show_edit(self, *_args: object) -> None:
        index: int | None = None
        if _args and hasattr(_args[0], "row"):
            index = int(self.table.item(_args[0].row(), 0).data(Qt.ItemDataRole.UserRole))
        elif len(self._selected()) == 1:
            index = self._selected()[0]
        if index is None:
            self.message.setText("Check exactly one category to edit.")
            return
        form = CategoryFormPage(self._records[index], self._available_shortcuts(editing=index))
        form.cancelled.connect(self._show_list)
        form.save_button.clicked.connect(lambda: self._save_edit(index, form))
        self._show_form(form)

    def _available_shortcuts(self, editing: int | None = None) -> tuple[str, ...]:
        used = {record.shortcut for index, record in enumerate(self._records) if index != editing}
        return tuple(shortcut for shortcut in ALLOWED_SHORTCUTS if shortcut not in used)

    def _check(self, form: CategoryFormPage, editing: int | None = None) -> VehicleCategory | None:
        candidate = form.value()
        errors = validate_category(candidate)
        duplicate_name = any(i != editing and item.name.casefold() == candidate.name.casefold() for i, item in enumerate(self._records))
        duplicate_shortcut = any(i != editing and item.shortcut == candidate.shortcut for i, item in enumerate(self._records))
        if duplicate_name:
            errors["name_unique"] = "A category with this name already exists."
        if duplicate_shortcut:
            errors["shortcut_unique"] = "This shortcut is already assigned to another category."
        if errors:
            form.show_error("  •  ".join(errors.values()))
            return None
        return candidate

    def _save_new(self, form: CategoryFormPage) -> None:
        candidate = self._check(form)
        if candidate:
            self._records.append(candidate)
            self._store_save(candidate)
            self._show_list()
            self.refresh()

    def _save_edit(self, index: int, form: CategoryFormPage) -> None:
        candidate = self._check(form, index)
        if candidate:
            previous = self._records[index]
            self._records[index] = candidate
            if previous.name != candidate.name:
                self._store_delete(previous.name)
            self._store_save(candidate)
            self._show_list()
            self.refresh()

    def _delete_selected(self) -> None:
        indexes = self._selected()
        if not indexes:
            return
        if not self._delete_armed:
            self._delete_armed = True
            self.delete.setText(f"Confirm delete ({len(indexes)})")
            self.message.setText("Deleting categories removes their Entry shortcuts. Click again to confirm.")
            return
        for index in sorted(indexes, reverse=True):
            self._store_delete(self._records[index].name)
            del self._records[index]
        self.refresh()
