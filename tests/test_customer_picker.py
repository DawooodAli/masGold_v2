"""Independent customer pickers; all database access is blocked or mocked."""

from copy import deepcopy
from decimal import Decimal
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")

from PySide6.QtCore import QObject, QPointF, QUrl, QMetaObject, Qt, Q_ARG
from PySide6.QtGui import QFontDatabase, QGuiApplication
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
        self.assertIn("8,500", self.picker.results[0]["balance_text"])
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



# ---------------------------------------------------------------- typography pilot (Task 08): geometry helpers shared with tests/test_return_history_ux.py
CAIRO = Path(__file__).resolve().parents[1] / "resources" / "fonts" / "Cairo.ttf"
SELECTED_CARD_HEIGHT = 52          # PickerButton.implicitHeight while a customer is selected
ROW_FONT = {"رقم العميل": 11, "الهاتف": 11, "تحذيرات نشطة": 11}      # result-row lines by their text prefix
BALANCE_FONT = 12                                                      # the balance line of the selected card and of a result row


def scene_rect(item):
    p = item.mapToScene(QPointF(0, 0))
    return (round(p.x(), 3), round(p.y(), 3), round(p.x() + item.width(), 3), round(p.y() + item.height(), 3))


def inside(inner, outer):
    return inner[0] >= outer[0] and inner[1] >= outer[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def text_items(root):
    """Visible items that carry a non-empty text, found through the item tree (ListView delegates are not QObject children)."""
    found, pending = [], [root]
    while pending:
        item = pending.pop()
        if not isinstance(item, QQuickItem) or not item.property("visible"):
            continue
        text = item.property("text")
        if isinstance(text, str) and text.strip() and item.width() > 0:
            found.append(item)
        pending.extend(item.childItems())
    return found


def px(item):
    return item.property("font").pixelSize()


def stub_customers(picker, rows):
    """Replace the picker's service by an in-memory double and return its view model."""
    vm = picker.findChild(CustomerPickerViewModel)
    vm._service = MagicMock()
    by_id = {str(r["customer_id"]): r for r in rows}
    vm._service.get_customer_for_selection.side_effect = lambda identifier, **unused: deepcopy(by_id[identifier])
    vm._service.search_customers.return_value = result_page(rows)
    return vm


def sample_rows():
    return [customer(9007199254740993, name="أحمد محمد علي الشرقاوي", balance=Decimal("8500.10"), active_warning_count=2, customer_code="C000143"),
            customer(18446744073709551615, name="محمود سعيد", balance=Decimal("-3200"), balance_status="SHOP_OWES_CUSTOMER", active_warning_count=0,
                     customer_code="C000144")]


def selected_card_findings(picker, label=""):
    """Problems of the selected-customer card: heights, the 12 px balance line, and every line inside the button's padded area."""
    problems = []
    button = picker.findChild(QObject, "customerPickerOpenButton")
    content = button.property("contentItem")
    lines = [c for c in content.childItems() if isinstance(c, QQuickItem) and c.property("visible")]
    where = f"{label} selected card"
    if button.height() != SELECTED_CARD_HEIGHT:
        problems.append(f"{where}: button height {button.height()} != {SELECTED_CARD_HEIGHT}")
    if len(lines) != 2:
        return problems + [f"{where}: expected 2 visible lines, found {len(lines)}"]
    if px(lines[1]) != BALANCE_FONT:
        problems.append(f"{where}: balance line is {px(lines[1])} px, expected {BALANCE_FONT}")
    brect = scene_rect(button)
    inner = (brect[0], brect[1] + button.property("topPadding"), brect[2], brect[3] - button.property("bottomPadding"))
    if content.property("implicitHeight") > inner[3] - inner[1]:
        problems.append(f"{where}: content needs {content.property('implicitHeight')} px but the padded button has {inner[3] - inner[1]}")
    for line in lines:
        if not inside(scene_rect(line), inner):
            problems.append(f"{where}: line {line.property('text')[:24]!r} {scene_rect(line)} is outside the padded button {inner}")
    return problems


def result_rows_findings(picker, window, label=""):
    """Problems of the result popup: inside the window, row lines inside their rows, expected font sizes."""
    problems = []
    popup = picker.findChild(QObject, "customerPickerPopup")
    results = picker.findChild(QObject, "customerPickerResults")
    box = (popup.property("x"), popup.property("y"), popup.property("x") + popup.property("width"), popup.property("y") + popup.property("height"))
    if not inside(box, (0, 0, window[0], window[1])):
        problems.append(f"{label} popup {box} leaves the {window[0]}x{window[1]} window")
    delegates = [c for c in results.property("contentItem").childItems() if isinstance(c, QQuickItem) and c.property("visible") and c.height() > 0]
    if not delegates:
        problems.append(f"{label} popup shows no result row")
    for index, row in enumerate(delegates):
        rrect = scene_rect(row)
        for item in text_items(row):
            if not inside(scene_rect(item), rrect):
                problems.append(f"{label} row {index}: {item.property('text')[:24]!r} {scene_rect(item)} is outside its row {rrect}")
            text = item.property("text").lstrip("\u200f")
            for prefix, size in ROW_FONT.items():
                if text.startswith(prefix) and px(item) != size:
                    problems.append(f"{label} row {index}: {prefix} line is {px(item)} px, expected {size}")
            if "—" in text and ("العميل" in text or "الحساب" in text) and px(item) != BALANCE_FONT:
                problems.append(f"{label} row {index}: balance line is {px(item)} px, expected {BALANCE_FONT}")
    return problems


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

    def test_selected_card_error_and_result_rows_fit_with_the_pilot_font_sizes_at_every_size(self):
        """Task 08: balance line 12 px in a 52 px card, error and result lines 11 px, nothing outside its button / row, popup inside the window.
        Runs with the bundled Cairo font (as the real application) so the line heights are the real ones."""
        font_id = QFontDatabase.addApplicationFont(str(CAIRO))
        self.assertGreaterEqual(font_id, 0, "Cairo.ttf did not load")
        self.addCleanup(QFontDatabase.removeApplicationFont, font_id)
        with patch.object(DatabaseConnection, "connect", side_effect=AssertionError("No database access")), \
                patch("app.viewmodels.customer_picker_viewmodel.CustomerService") as service_factory:
            service_factory.side_effect = [MagicMock()]
            engine = QQmlEngine()
            warnings = []
            engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
            component = QQmlComponent(engine)
            component.setData(b'''
                import QtQuick
                import QtQuick.Controls
                import "."
                ApplicationWindow {
                    visible: true
                    width: 1080
                    height: 680
                    CustomerPicker { objectName: "pilotPicker"; x: 24; y: 20; width: 430 }
                }
            ''', QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "qml" / "CustomerPickerTest.qml")))
            root = component.create()
            self.assertIsNotNone(root, [error.toString() for error in component.errors()])
            try:
                QTest.qWait(30)
                picker = root.findChild(QObject, "pilotPicker")
                rows = sample_rows()
                vm = stub_customers(picker, rows)
                # the error line (no selection yet): the service refuses the choice
                vm._service.get_customer_for_selection.side_effect = ValueError("تعذر اختيار العميل")
                QMetaObject.invokeMethod(picker, "setCustomerId", Qt.DirectConnection, Q_ARG("QVariant", str(rows[0]["customer_id"])))
                QTest.qWait(30)
                self.assertTrue(vm.errorMessage)
                errors = [i for i in text_items(picker) if i.property("text").lstrip("\u200f") == vm.errorMessage]
                self.assertEqual(len(errors), 1)
                self.assertEqual(px(errors[0]), 11)
                self.assertTrue(inside(scene_rect(errors[0]), scene_rect(picker)), "the error line is outside the picker")
                vm._service.get_customer_for_selection.side_effect = lambda identifier, **unused: deepcopy({str(r["customer_id"]): r for r in rows}[identifier])
                QMetaObject.invokeMethod(picker, "setCustomerId", Qt.DirectConnection, Q_ARG("QVariant", str(rows[0]["customer_id"])))
                for width, height in ((1440, 900), (1280, 800), (1080, 680)):
                    with self.subTest(size=f"{width}x{height}"):
                        root.setWidth(width)
                        root.setHeight(height)
                        QTest.qWait(120)
                        label = f"{width}x{height}"
                        self.assertEqual(selected_card_findings(picker, label), [])
                        self.assertEqual(picker.height(), SELECTED_CARD_HEIGHT)
                        QMetaObject.invokeMethod(picker, "open", Qt.DirectConnection)
                        QTest.qWait(300)
                        self.assertEqual(result_rows_findings(picker, (width, height), label), [])
                        QMetaObject.invokeMethod(picker, "close", Qt.DirectConnection)
                        QTest.qWait(300)
                self.assertEqual(warnings, [])
            finally:
                root.close()
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
                    balance=Decimal("-3200"), balance_status="SHOP_OWES_CUSTOMER",
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
                self.assertEqual(second_vm.selectedCustomer["balance"], "-3200")
                self.assertIn("3,200", second_vm.selectedCustomer["balance_text"])

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
