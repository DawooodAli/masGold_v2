from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.master_data_service import MasterDataService


# ============================================================
# CATEGORY RULES DIALOG
# ============================================================


class CategoryRulesDialog(QDialog):

    def __init__(
        self,
        category: dict,
        parent=None,
    ):
        super().__init__(parent)

        self.category = category

        self.setWindowTitle(
            f"Category {category['code']} Rules"
        )

        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        title = QLabel(
            f"Category {category['code']}"
        )
        title.setStyleSheet(
            "font-size: 18px; font-weight: bold;"
        )

        layout.addWidget(title)

        form = QFormLayout()

        self.purchase_min = QLineEdit()
        self.purchase_max = QLineEdit()

        self.sale_min = QLineEdit()
        self.sale_max = QLineEdit()

        self.approval_below = QLineEdit()

        self.notes = QLineEdit()

        self._set_optional_number(
            self.purchase_min,
            category[
                "purchase_making_min_per_gram"
            ],
        )

        self._set_optional_number(
            self.purchase_max,
            category[
                "purchase_making_max_per_gram"
            ],
        )

        self._set_optional_number(
            self.sale_min,
            category[
                "sale_making_min_per_gram"
            ],
        )

        self._set_optional_number(
            self.sale_max,
            category[
                "sale_making_max_per_gram"
            ],
        )

        self._set_optional_number(
            self.approval_below,
            category[
                "manager_approval_below_per_gram"
            ],
        )

        self.notes.setText(
            category.get("notes") or ""
        )

        form.addRow(
            "Purchase Min / gram:",
            self.purchase_min,
        )

        form.addRow(
            "Purchase Max / gram:",
            self.purchase_max,
        )

        form.addRow(
            "Sale Min / gram:",
            self.sale_min,
        )

        form.addRow(
            "Sale Max / gram:",
            self.sale_max,
        )

        form.addRow(
            "Manager approval below:",
            self.approval_below,
        )

        form.addRow(
            "Notes:",
            self.notes,
        )

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            |
            QDialogButtonBox.StandardButton.Cancel
        )

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    @staticmethod
    def _set_optional_number(
        field: QLineEdit,
        value,
    ) -> None:

        if value is None:
            field.clear()
        else:
            field.setText(
                str(value)
            )

    @staticmethod
    def _optional_float(
        text: str,
    ) -> float | None:

        value = text.strip()

        if not value:
            return None

        return float(value)

    def get_values(self) -> dict:

        return {
            "purchase_min":
                self._optional_float(
                    self.purchase_min.text()
                ),

            "purchase_max":
                self._optional_float(
                    self.purchase_max.text()
                ),

            "sale_min":
                self._optional_float(
                    self.sale_min.text()
                ),

            "sale_max":
                self._optional_float(
                    self.sale_max.text()
                ),

            "manager_approval_below":
                self._optional_float(
                    self.approval_below.text()
                ),

            "notes":
                self.notes.text().strip() or None,
        }


# ============================================================
# FACTORY DIALOG
# ============================================================


class FactoryDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle(
            "Add Factory"
        )

        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.name_field = QLineEdit()
        self.code_field = QLineEdit()
        self.notes_field = QLineEdit()

        form.addRow(
            "Factory name:",
            self.name_field,
        )

        form.addRow(
            "Code:",
            self.code_field,
        )

        form.addRow(
            "Notes:",
            self.notes_field,
        )

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            |
            QDialogButtonBox.StandardButton.Cancel
        )

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    def get_values(self) -> dict:

        return {
            "name":
                self.name_field.text().strip(),

            "code":
                self.code_field.text().strip()
                or None,

            "notes":
                self.notes_field.text().strip()
                or None,
        }


# ============================================================
# ITEM TYPE DIALOG
# ============================================================


class ItemTypeDialog(QDialog):

    def __init__(
        self,
        categories: list[dict],
        parent=None,
    ):
        super().__init__(parent)

        self.setWindowTitle(
            "Add Item Type"
        )

        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.name_field = QLineEdit()
        self.code_field = QLineEdit()
        self.category_combo = QComboBox()
        self.notes_field = QLineEdit()

        self.category_combo.addItem(
            "No default category",
            None,
        )

        for category in categories:

            self.category_combo.addItem(
                category["code"],
                category["id"],
            )

        form.addRow(
            "Item name:",
            self.name_field,
        )

        form.addRow(
            "Code:",
            self.code_field,
        )

        form.addRow(
            "Default category:",
            self.category_combo,
        )

        form.addRow(
            "Notes:",
            self.notes_field,
        )

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            |
            QDialogButtonBox.StandardButton.Cancel
        )

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    def get_values(self) -> dict:

        return {
            "name":
                self.name_field.text().strip(),

            "code":
                self.code_field.text().strip()
                or None,

            "default_category_id":
                self.category_combo.currentData(),

            "notes":
                self.notes_field.text().strip()
                or None,
        }


# ============================================================
# SETTINGS PAGE
# ============================================================


class SettingsPage(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.service = MasterDataService()

        self.categories: list[dict] = []

        self._build_ui()
        self.refresh_all()

    # --------------------------------------------------------
    # UI
    # --------------------------------------------------------

    def _build_ui(self) -> None:

        main_layout = QVBoxLayout(self)

        main_layout.setContentsMargins(
            24,
            24,
            24,
            24,
        )

        title = QLabel(
            "Settings"
        )

        title.setStyleSheet(
            """
            font-size: 26px;
            font-weight: 700;
            """
        )

        subtitle = QLabel(
            "Master Data"
        )

        subtitle.setStyleSheet(
            """
            font-size: 15px;
            color: #777;
            """
        )

        main_layout.addWidget(title)
        main_layout.addWidget(subtitle)

        self.tabs = QTabWidget()

        self.category_tab = QWidget()
        self.factory_tab = QWidget()
        self.item_type_tab = QWidget()

        self.tabs.addTab(
            self.category_tab,
            "Categories",
        )

        self.tabs.addTab(
            self.factory_tab,
            "Factories",
        )

        self.tabs.addTab(
            self.item_type_tab,
            "Item Types",
        )

        main_layout.addWidget(
            self.tabs
        )

        self._build_category_tab()
        self._build_factory_tab()
        self._build_item_type_tab()

    # --------------------------------------------------------
    # Categories
    # --------------------------------------------------------

    def _build_category_tab(self) -> None:

        layout = QVBoxLayout(
            self.category_tab
        )

        button_row = QHBoxLayout()

        edit_button = QPushButton(
            "Edit Category Rules"
        )

        refresh_button = QPushButton(
            "Refresh"
        )

        edit_button.clicked.connect(
            self.edit_selected_category
        )

        refresh_button.clicked.connect(
            self.refresh_categories
        )

        button_row.addWidget(
            edit_button
        )

        button_row.addWidget(
            refresh_button
        )

        button_row.addStretch()

        layout.addLayout(
            button_row
        )

        self.category_table = QTableWidget()

        self.category_table.setColumnCount(6)

        self.category_table.setHorizontalHeaderLabels(
            [
                "Category",
                "Purchase Min/g",
                "Purchase Max/g",
                "Sale Min/g",
                "Sale Max/g",
                "Approval Below/g",
            ]
        )

        self.category_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )

        self.category_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )

        self.category_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )

        self.category_table.verticalHeader().setVisible(
            False
        )

        layout.addWidget(
            self.category_table
        )

    # --------------------------------------------------------
    # Factories
    # --------------------------------------------------------

    def _build_factory_tab(self) -> None:

        layout = QVBoxLayout(
            self.factory_tab
        )

        button_row = QHBoxLayout()

        add_button = QPushButton(
            "Add Factory"
        )

        refresh_button = QPushButton(
            "Refresh"
        )

        add_button.clicked.connect(
            self.add_factory
        )

        refresh_button.clicked.connect(
            self.refresh_factories
        )

        button_row.addWidget(
            add_button
        )

        button_row.addWidget(
            refresh_button
        )

        button_row.addStretch()

        layout.addLayout(
            button_row
        )

        self.factory_table = QTableWidget()

        self.factory_table.setColumnCount(3)

        self.factory_table.setHorizontalHeaderLabels(
            [
                "ID",
                "Code",
                "Factory Name",
            ]
        )

        self.factory_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )

        self.factory_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )

        self.factory_table.verticalHeader().setVisible(
            False
        )

        layout.addWidget(
            self.factory_table
        )

    # --------------------------------------------------------
    # Item types
    # --------------------------------------------------------

    def _build_item_type_tab(self) -> None:

        layout = QVBoxLayout(
            self.item_type_tab
        )

        button_row = QHBoxLayout()

        add_button = QPushButton(
            "Add Item Type"
        )

        refresh_button = QPushButton(
            "Refresh"
        )

        add_button.clicked.connect(
            self.add_item_type
        )

        refresh_button.clicked.connect(
            self.refresh_item_types
        )

        button_row.addWidget(
            add_button
        )

        button_row.addWidget(
            refresh_button
        )

        button_row.addStretch()

        layout.addLayout(
            button_row
        )

        self.item_type_table = QTableWidget()

        self.item_type_table.setColumnCount(4)

        self.item_type_table.setHorizontalHeaderLabels(
            [
                "ID",
                "Code",
                "Item Type",
                "Default Category",
            ]
        )

        self.item_type_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )

        self.item_type_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )

        self.item_type_table.verticalHeader().setVisible(
            False
        )

        layout.addWidget(
            self.item_type_table
        )

    # --------------------------------------------------------
    # Refresh
    # --------------------------------------------------------

    def refresh_all(self) -> None:

        try:
            self.refresh_categories()
            self.refresh_factories()
            self.refresh_item_types()

        except Exception as exc:

            QMessageBox.critical(
                self,
                "Database Error",
                str(exc),
            )

    def refresh_categories(self) -> None:

        self.categories = (
            self.service.get_categories()
        )

        self.category_table.setRowCount(
            len(self.categories)
        )

        for row, category in enumerate(
            self.categories
        ):

            values = [
                category["code"],
                category[
                    "purchase_making_min_per_gram"
                ],
                category[
                    "purchase_making_max_per_gram"
                ],
                category[
                    "sale_making_min_per_gram"
                ],
                category[
                    "sale_making_max_per_gram"
                ],
                category[
                    "manager_approval_below_per_gram"
                ],
            ]

            for column, value in enumerate(
                values
            ):

                text = (
                    ""
                    if value is None
                    else str(value)
                )

                item = QTableWidgetItem(
                    text
                )

                if column == 0:
                    item.setData(
                        Qt.ItemDataRole.UserRole,
                        category["id"],
                    )

                self.category_table.setItem(
                    row,
                    column,
                    item,
                )

        self.category_table.resizeColumnsToContents()

    def refresh_factories(self) -> None:

        factories = (
            self.service.get_factories()
        )

        self.factory_table.setRowCount(
            len(factories)
        )

        for row, factory in enumerate(
            factories
        ):

            values = [
                factory["id"],
                factory["code"] or "",
                factory["name"],
            ]

            for column, value in enumerate(
                values
            ):

                self.factory_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(
                        str(value)
                    ),
                )

        self.factory_table.resizeColumnsToContents()

    def refresh_item_types(self) -> None:

        item_types = (
            self.service.get_item_types()
        )

        self.item_type_table.setRowCount(
            len(item_types)
        )

        for row, item_type in enumerate(
            item_types
        ):

            values = [
                item_type["id"],
                item_type["code"] or "",
                item_type["name"],
                item_type[
                    "default_category_code"
                ]
                or "",
            ]

            for column, value in enumerate(
                values
            ):

                self.item_type_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(
                        str(value)
                    ),
                )

        self.item_type_table.resizeColumnsToContents()

    # --------------------------------------------------------
    # Category editing
    # --------------------------------------------------------

    def edit_selected_category(self) -> None:

        row = (
            self.category_table.currentRow()
        )

        if row < 0:

            QMessageBox.information(
                self,
                "Category",
                "Select a category first.",
            )

            return

        category = self.categories[row]

        dialog = CategoryRulesDialog(
            category,
            self,
        )

        if (
            dialog.exec()
            != QDialog.DialogCode.Accepted
        ):
            return

        try:

            values = dialog.get_values()

            self.service.update_category_rules(
                category_id=category["id"],
                **values,
            )

            self.refresh_categories()

            QMessageBox.information(
                self,
                "Category",
                "Category rules saved.",
            )

        except Exception as exc:

            QMessageBox.warning(
                self,
                "Could not save",
                str(exc),
            )

    # --------------------------------------------------------
    # Factory
    # --------------------------------------------------------

    def add_factory(self) -> None:

        dialog = FactoryDialog(
            self
        )

        if (
            dialog.exec()
            != QDialog.DialogCode.Accepted
        ):
            return

        try:

            values = dialog.get_values()

            self.service.create_factory(
                **values
            )

            self.refresh_factories()

        except Exception as exc:

            QMessageBox.warning(
                self,
                "Could not add factory",
                str(exc),
            )

    # --------------------------------------------------------
    # Item type
    # --------------------------------------------------------

    def add_item_type(self) -> None:

        dialog = ItemTypeDialog(
            self.categories,
            self,
        )

        if (
            dialog.exec()
            != QDialog.DialogCode.Accepted
        ):
            return

        try:

            values = dialog.get_values()

            self.service.create_item_type(
                **values
            )

            self.refresh_item_types()

        except Exception as exc:

            QMessageBox.warning(
                self,
                "Could not add item type",
                str(exc),
            )