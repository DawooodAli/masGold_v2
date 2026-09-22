"""Independent customer pickers; all database access is blocked or mocked."""

from copy import deepcopy
from decimal import Decimal
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")

from PySide6.QtCore import QObject, QUrl, QMetaObject, Qt, Q_ARG
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine, qmlRegisterType, qmlTypeId
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from app.viewmodels.customer_picker_viewmodel import CustomerPickerViewModel
from database.connection import DatabaseConnection


def customer(customer_id=9007199254740993, active=True, **changes):
    row = {
        "customer_id": customer_id,
        "customer_code": "C" + str(customer_id),
        "name": "أحمد محمد",
        "mobile": "٠١٠ ١٢٣٤ ٥٦٧٨",
        "place": "مدينة نصر",
        "customer_type_id": 1,
        "customer_type_code": "REGULAR",
        "is_active": active,
        "balance": Decimal("8500.10"),
        "balance_status": "CUSTOMER_OWES_SHOP",
        "active_warning_count": 0,
        "last_transaction_at": None,
    }
    row.update(changes)
    return row


def result_page(rows, page=1, has_more=False):
    return {"items": rows, "page": page, "page_size": 20, "has_more": has_more}


class CustomerPickerTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(
            DatabaseConnection, "connect",
            side_effect=AssertionError("Picker tests must never connect to MySQL."),
        )
        guard.start()
        self.addCleanup(guard.stop)
        self.service = MagicMock()
        self.service.search_customers.return_value = result_page([customer()])
        self.service.get_customer_for_selection.return_value = customer()
        self.picker = CustomerPickerViewModel(service=self.service)

    def test_constructor_and_initial_configuration_do_not_query(self):
        self.assertEqual(self.service.mock_calls, [])
        self.assertFalse(self.picker.allowInactive)
        self.assertFalse(self.picker.hasSelection)
        self.assertEqual(self.picker.page, 1)
        self.assertEqual(self.picker.pageSize, 20)
        self.picker.allowInactive = True
        self.assertEqual(self.service.mock_calls, [])
        self.assertTrue(self.picker.allowInactive)

    def test_search_forwards_original_digits_and_query_to_shared_service(self):
        query = "  أَحْمَد ٠١٠-۱۲۳۴-٥٦٧٨  "
        self.picker.search(query)
        self.service.search_customers.assert_called_once_with(
            search=query, status="ACTIVE", page=1, page_size=20,
        )
        self.assertEqual(self.picker.searchText, query)
        self.assertEqual(self.picker.results[0]["id"], "9007199254740993")
        self.assertEqual(self.picker.results[0]["balance"], "8500.10")
        self.assertIn("8,500.10", self.picker.results[0]["balance_text"])
        self.service.get_customer_for_selection.assert_not_called()

    def test_pages_use_small_service_pages_and_policy_changes_reset_to_first(self):
        self.service.search_customers.return_value = result_page([customer()], page=2, has_more=True)
        self.picker.loadPage(2)
        self.assertEqual(self.picker.page, 2)
        self.assertTrue(self.picker.hasMore)
        self.service.search_customers.assert_called_with(
            search="", status="ACTIVE", page=2, page_size=20,
        )
        self.service.search_customers.return_value = result_page([customer()])
        self.picker.setAllowInactive(True)
        self.service.search_customers.assert_called_with(
            search="", status="ALL", page=1, page_size=20,
        )
        self.assertEqual(self.picker.page, 1)

    def test_two_instances_keep_search_results_and_selection_independent(self):
        second_service = MagicMock()
        second_service.search_customers.return_value = result_page([customer(9007199254740995)])
        second_service.get_customer_for_selection.return_value = customer(9007199254740995)
        second = CustomerPickerViewModel(service=second_service)
        self.picker.search("أحمد")
        second.search("محمد")
        self.assertTrue(self.picker.selectCustomer("9007199254740993"))
        self.assertTrue(second.selectCustomer("9007199254740995"))
        self.assertEqual(self.picker.searchText, "أحمد")
        self.assertEqual(second.searchText, "محمد")
        self.assertEqual(self.picker.selectedCustomerId, "9007199254740993")
        self.assertEqual(second.selectedCustomerId, "9007199254740995")
        self.picker.clearSelection()
        self.assertFalse(self.picker.hasSelection)
        self.assertTrue(second.hasSelection)
        self.assertEqual(second.results[0]["id"], "9007199254740995")

    def test_duplicate_names_use_fresh_bigint_identity_and_latest_balance(self):
        self.service.search_customers.return_value = result_page([
            customer(9007199254740993), customer(18446744073709551615),
        ])
        self.picker.search("أحمد")
        self.service.get_customer_for_selection.return_value = customer(
            18446744073709551615, balance=Decimal("-3200.01"),
            balance_status="SHOP_OWES_CUSTOMER", active_warning_count=2,
        )
        self.assertTrue(self.picker.selectCustomer("18446744073709551615"))
        self.service.get_customer_for_selection.assert_called_once_with(
            "18446744073709551615", allow_inactive=False,
        )
        self.assertEqual(self.picker.selectedCustomerId, "18446744073709551615")
        self.assertEqual(self.picker.selectedCustomer["balance"], "-3200.01")
        self.assertEqual(self.picker.selectedCustomer["balance_meaning"], "للعميل عند المحل")
        self.assertEqual(self.picker.selectedCustomer["active_warning_count"], 2)
        self.service.get_customer_profile.assert_not_called()

    def test_new_search_and_failed_replacement_preserve_confirmed_selection(self):
        self.assertTrue(self.picker.selectCustomer("9007199254740993"))
        selected = deepcopy(self.picker.selectedCustomer)
        self.picker.search("اسم آخر")
        self.assertEqual(self.picker.selectedCustomer, selected)
        self.service.get_customer_for_selection.side_effect = ValueError("لا يمكن اختيار عميل غير نشط.")
        self.assertFalse(self.picker.selectCustomer("12"))
        self.assertEqual(self.picker.selectedCustomer, selected)
        self.assertIn("غير نشط", self.picker.errorMessage)
        self.assertFalse(self.picker.busy)

    def test_search_failure_clears_suggestions_but_not_confirmed_selection(self):
        self.picker.selectCustomer("9007199254740993")
        self.service.search_customers.side_effect = RuntimeError("database unavailable")
        with self.assertLogs("app.viewmodels.customer_presentation", level="ERROR"):
            self.picker.search("أحمد")
        self.assertEqual(self.picker.results, [])
        self.assertTrue(self.picker.hasSelection)
        self.assertFalse(self.picker.hasMore)
        self.assertIn("تعذر", self.picker.errorMessage)
        self.assertNotIn("database", self.picker.errorMessage)

    def test_turning_policy_off_clears_cached_inactive_choice_immediately(self):
        self.picker.setAllowInactive(True)
        self.service.get_customer_for_selection.return_value = customer(active=False)
        self.assertTrue(self.picker.selectCustomer("9007199254740993"))
        events = []
        self.picker.selectionInvalidated.connect(lambda identifier, reason: events.append((identifier, reason)))
        self.service.get_customer_for_selection.reset_mock()
        self.picker.setAllowInactive(False)
        self.assertFalse(self.picker.hasSelection)
        self.assertEqual(events[0][0], "9007199254740993")
        self.assertIn("غير نشط", events[0][1])
        self.service.get_customer_for_selection.assert_not_called()

    def test_refresh_revalidates_newly_inactive_confirmed_choice(self):
        self.picker.selectCustomer("9007199254740993")
        self.service.get_customer_for_selection.return_value = customer(active=False)
        events = []
        self.picker.selectionInvalidated.connect(lambda *args: events.append(args))
        self.picker.refresh()
        self.service.get_customer_for_selection.assert_called_with(
            "9007199254740993", allow_inactive=True,
        )
        self.assertFalse(self.picker.hasSelection)
        self.assertEqual(len(events), 1)

    def test_policy_change_revalidates_active_snapshot_and_retains_it_on_read_error(self):
        self.picker.setAllowInactive(True)
        self.picker.selectCustomer("9007199254740993")
        self.service.get_customer_for_selection.side_effect = RuntimeError("offline")
        with self.assertLogs("app.viewmodels.customer_presentation", level="ERROR"):
            self.picker.setAllowInactive(False)
        self.assertTrue(self.picker.hasSelection)
        self.assertFalse(self.picker.allowInactive)
        self.assertIn("تعذر", self.picker.errorMessage)

    def test_status_change_before_selection_is_checked_by_service(self):
        self.picker.search("أحمد")
        self.service.get_customer_for_selection.side_effect = ValueError("العميل غير نشط.")
        self.assertFalse(self.picker.selectCustomer(self.picker.results[0]["id"]))
        self.assertFalse(self.picker.hasSelection)
        self.assertIn("غير نشط", self.picker.errorMessage)


class CustomerPickerQmlTests(unittest.TestCase):
    _application = None

    @classmethod
    def setUpClass(cls):
        if QQuickStyle.name() != "Material":
            QQuickStyle.setStyle("Material")
        cls._application = QGuiApplication.instance() or QGuiApplication([])
        for uri in ("MasGold.PickerTests", "MasGold.Customers"):
            if qmlTypeId(uri, 1, 0, "CustomerPickerViewModel") < 0:
                qmlRegisterType(CustomerPickerViewModel, uri, 1, 0, "CustomerPickerViewModel")

    def test_qml_creates_two_independent_models_with_no_eager_reads(self):
        with patch.object(DatabaseConnection, "connect", side_effect=AssertionError("No database access")), \
                patch("app.viewmodels.customer_picker_viewmodel.CustomerService") as service_factory:
            services = [MagicMock(), MagicMock()]
            service_factory.side_effect = services
            engine = QQmlEngine()
            component = QQmlComponent(engine)
            component.setData(b'''
                import QtQml
                import MasGold.PickerTests 1.0
                QtObject {
                    property CustomerPickerViewModel first: CustomerPickerViewModel { objectName: "firstPicker" }
                    property CustomerPickerViewModel second: CustomerPickerViewModel { objectName: "secondPicker"; allowInactive: true }
                }
            ''', QUrl())
            root = component.create()
            self.assertIsNotNone(root, [error.toString() for error in component.errors()])
            first = root.findChild(QObject, "firstPicker")
            second = root.findChild(QObject, "secondPicker")
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertIsNot(first, second)
            self.assertFalse(first.property("allowInactive"))
            self.assertTrue(second.property("allowInactive"))
            self.assertEqual(service_factory.call_count, 2)
            self.assertTrue(all(not service.mock_calls for service in services))
            root.deleteLater()
            engine.deleteLater()
            self._application.processEvents()

    def test_two_actual_picker_controls_preserve_selection_on_search_highlight_and_close(self):
        with patch.object(DatabaseConnection, "connect", side_effect=AssertionError("No database access")), \
                patch("app.viewmodels.customer_picker_viewmodel.CustomerService") as service_factory:
            services = [MagicMock(), MagicMock()]
            service_factory.side_effect = services
            engine = QQmlEngine()
            warnings = []
            engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
            component = QQmlComponent(engine)
            # Resolves the real CustomerPicker.qml and Theme.qml without writing
            # a harness file, importing main.py, or starting production services.
            source_url = QUrl.fromLocalFile(str(
                Path(__file__).resolve().parents[1] / "qml" / "CustomerPickerTest.qml"
            ))
            component.setData(b'''
                import QtQuick
                import QtQuick.Controls
                import "."
                ApplicationWindow {
                    visible: true
                    width: 1080
                    height: 680
                    CustomerPicker { objectName: "firstControl"; x: 24; y: 20; width: 430 }
                    CustomerPicker { objectName: "secondControl"; x: 600; y: 20; width: 430; allowInactive: true }
                }
            ''', source_url)
            root = component.create()
            self.assertIsNotNone(root, [error.toString() for error in component.errors()])
            try:
                QTest.qWait(20)
                first = root.findChild(QObject, "firstControl")
                second = root.findChild(QObject, "secondControl")
                self.assertIsNotNone(first)
                self.assertIsNotNone(second)
                first_vm = first.findChild(CustomerPickerViewModel)
                second_vm = second.findChild(CustomerPickerViewModel)
                self.assertIsNotNone(first_vm)
                self.assertIsNotNone(second_vm)
                self.assertIsNot(first_vm, second_vm)
                self.assertTrue(all(not service.mock_calls for service in services))

                first_id = "9007199254740993"
                second_id = "18446744073709551615"
                first_row = customer(
                    int(first_id), has_dead_cash_history=True,
                    has_active_dead_cash_warning=False, unreversed_dead_cash_count=0,
                )
                second_row = customer(
                    int(second_id), active_warning_count=2,
                    balance=Decimal("-3200.01"), balance_status="SHOP_OWES_CUSTOMER",
                )
                rows_by_id = {first_id: first_row, second_id: second_row}
                for vm in (first_vm, second_vm):
                    vm._service.get_customer_for_selection.side_effect = (
                        lambda identifier, **unused: deepcopy(rows_by_id[identifier])
                    )
                first_vm._service.search_customers.return_value = result_page([first_row, second_row])
                second_vm._service.search_customers.return_value = result_page([second_row])

                self.assertTrue(QMetaObject.invokeMethod(
                    first, "setCustomerId", Qt.DirectConnection, Q_ARG("QVariant", first_id),
                ))
                self.assertTrue(QMetaObject.invokeMethod(
                    second, "setCustomerId", Qt.DirectConnection, Q_ARG("QVariant", second_id),
                ))
                self.assertEqual(first.property("selectedCustomerId"), first_id)
                self.assertEqual(second.property("selectedCustomerId"), second_id)
                self.assertEqual(first_vm.selectedCustomer["active_warning_count"], 0)
                self.assertFalse(first_vm.selectedCustomer["dead_cash_attention"])
                self.assertTrue(first_vm.selectedCustomer["has_dead_cash_history"])
                self.assertEqual(second_vm.selectedCustomer["active_warning_count"], 2)
                self.assertEqual(second_vm.selectedCustomer["balance"], "-3200.01")
                self.assertIn("3,200.01", second_vm.selectedCustomer["balance_text"])

                self.assertTrue(QMetaObject.invokeMethod(first, "open", Qt.DirectConnection))
                QTest.qWait(30)
                first_popup = first.findChild(QObject, "customerPickerPopup")
                first_results = first.findChild(QObject, "customerPickerResults")
                first_search = first.findChild(QObject, "customerPickerSearchField")
                self.assertTrue(first_popup.property("visible"))
                self.assertEqual(first_results.property("count"), 2)
                self.assertEqual(second_vm.results, [])
                self.assertFalse(second.findChild(QObject, "customerPickerPopup").property("visible"))

                query = "أَحْمَد ٠١٠-۱۲۳۴-٥٦٧٨"
                first_search.setProperty("text", query)
                self.assertTrue(QMetaObject.invokeMethod(first, "runSearch", Qt.DirectConnection))
                QTest.qWait(20)
                first_vm._service.search_customers.assert_called_with(
                    search=query, status="ACTIVE", page=1, page_size=20,
                )
                self.assertEqual(first_vm.searchText, query)
                self.assertEqual(second_vm.searchText, "")
                first_results.setProperty("currentIndex", 1)
                self.assertEqual(first_results.property("currentIndex"), 1)
                self.assertEqual(first.property("selectedCustomerId"), first_id)
                self.assertEqual(second.property("selectedCustomerId"), second_id)

                # Result warnings are visible only for customers with warnings.
                # ListView delegates have visual ownership under contentItem;
                # they are not necessarily QObject descendants of the picker.
                result_items = []
                pending = [first_results]
                while pending:
                    item = pending.pop()
                    result_items.append(item)
                    if isinstance(item, QQuickItem):
                        pending.extend(item.childItems())
                warning_labels = [
                    obj for obj in result_items
                    if obj.metaObject().indexOfProperty("text") >= 0
                    and "تحذيرات نشطة:" in str(obj.property("text"))
                ]
                self.assertTrue(any("2" in label.property("text") and label.property("visible")
                                    for label in warning_labels))
                self.assertFalse(any("0" in label.property("text") and label.property("visible")
                                     for label in warning_labels))

                self.assertTrue(QMetaObject.invokeMethod(first, "close", Qt.DirectConnection))
                QTest.qWait(250)  # Let the production Material exit transition finish.
                self.assertFalse(first_popup.property("visible"))
                self.assertEqual(first.property("selectedCustomerId"), first_id)
                self.assertEqual(second.property("selectedCustomerId"), second_id)

                self.assertTrue(QMetaObject.invokeMethod(second, "open", Qt.DirectConnection))
                QTest.qWait(20)
                second_vm._service.search_customers.assert_called_with(
                    search="", status="ALL", page=1, page_size=20,
                )
                self.assertEqual(len(second_vm.results), 1)
                self.assertEqual(first_vm.searchText, query)
                self.assertTrue(QMetaObject.invokeMethod(second, "clear", Qt.DirectConnection))
                self.assertFalse(second.property("hasSelection"))
                self.assertEqual(first.property("selectedCustomerId"), first_id)
                self.assertEqual(warnings, [])
            finally:
                root.close()
                root.deleteLater()
                engine.deleteLater()
                self._application.processEvents()


if __name__ == "__main__":
    unittest.main()
