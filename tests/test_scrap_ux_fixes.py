"""Small UX fix pack after the Exchange → Scrap (032) manual pass.

BUG-EXCH-SCRAP-REFRESH  a manager decision (and an Exchange) reloads every loaded Scrap / stock view at once;
UX-SCRAP-HOLD-01        consumers explain physical / held / available Scrap and why held grams are refused;
UX-INV-ADD-01           «إدخال قطعة جديدة» stays open after a successful save, ready for the next piece.

Service rules run on the in-memory Exchange store; the screens run on the real Main.qml (database blocked)."""
from decimal import Decimal as D
import unittest
from unittest.mock import Mock

from PySide6.QtCore import QMetaObject, Qt
from PySide6.QtTest import QTest

from services.exchange_service import ExchangeDraft
from services.old_gold_availability import breakdown_text, shortage_message
from tests import test_exchange_service as ex
from tests import test_invoice_page_qml as harness


# ----------------------------------------------------------------------------- UX-SCRAP-HOLD-01 (rules / texts)
class HeldScrapMessageTests(ex.ExchangeTestCase):
    def hold(self, physical, held):
        """21K Scrap: `physical` grams, of which `held` belong to an Exchange piece awaiting the manager."""
        self.store.old_gold[1] = D(physical)
        self.store.holds = [{"hold_id": "1", "review": "901", "line": "1", "item": "999", "karat_id": 1,
                             "weight": D(held), "in": "1", "status": "HELD", "out": None,
                             "resolved_by": None, "resolved_at": None}]

    def f7(self, weight):
        return ExchangeDraft("501", ("7",), (ex.from_old_gold(weight),))

    def test_physical_5_held_5_requesting_4_990_explains_the_hold(self):
        self.hold("5.000", "5.000")
        expected = ("رصيد الكسر المتاح عيار 21 غير كافٍ: المطلوب 4.990 جرام والمتاح 0.000 جرام. "
                    "يوجد 5.000 جرام محجوز لمراجعة المدير ولا يمكن استخدامه حتى اعتماد القرار.")
        problems = self.service.preview(self.f7("4.990"))["blocking_problems"]
        self.assertEqual((problems[0]["code"], problems[0]["message"]), ("OLD_GOLD_STOCK_INSUFFICIENT", expected))
        self.assertRefused("OLD_GOLD_STOCK_INSUFFICIENT", self.f7("4.990"), expected)    # finalize: same words

    def test_physical_10_held_5_requesting_4_990_is_allowed(self):
        self.hold("10.000", "5.000")
        problems = self.service.preview(self.f7("4.990"))["blocking_problems"]
        self.assertNotIn("OLD_GOLD_STOCK_INSUFFICIENT", [p["code"] for p in problems])

    def test_without_a_hold_the_normal_message_stays(self):
        self.store.old_gold[1] = D("2.000")
        problems = self.service.preview(self.f7("4.990"))["blocking_problems"]
        self.assertEqual(problems[0]["message"], "رصيد الكسر عيار 21 غير كافٍ: المتاح 2.000 جرام والمطلوب 4.990 جرام.")

    def test_exchange_f7_list_shows_physical_held_available(self):
        self.hold("5.000", "5.000")
        stock = {k["karat"]: k for k in self.service.context("501")["old_gold_stock"]}[21]
        self.assertEqual((stock["grams"], stock["physical"], stock["held"]), ("0.000", "5.000", "5.000"))
        from app.viewmodels.exchange_viewmodel import _scrap_breakdown
        self.assertEqual(_scrap_breakdown(stock), "الرصيد الفعلي: 5.000 جرام  •  المحجوز لمراجعة المدير: 5.000 جرام"
                                                  "  •  المتاح للاستخدام: 0.000 جرام")

    def test_billing_f7_stock_carries_the_breakdown(self):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.instance() or QGuiApplication([])
        from app.viewmodels.invoice_viewmodel import InvoiceViewModel
        vm = InvoiceViewModel.__new__(InvoiceViewModel)
        vm._reference = {"old_gold_stock": [
            {"karat_id": 1, "karat": 21, "grams": "0.000", "physical": "5.000", "held": "5.000"},
            {"karat_id": 2, "karat": 18, "grams": "3.000", "physical": "3.000", "held": "0"}]}
        rows = InvoiceViewModel.oldGoldStock.fget(vm)
        self.assertEqual([r["breakdown"] for r in rows], [breakdown_text("5.000", "5.000", "0.000"), ""])

    def test_texts(self):
        self.assertEqual(breakdown_text("10", "5", "5"), "الرصيد الفعلي: 10.000 جرام  •  المحجوز لمراجعة المدير: "
                                                          "5.000 جرام  •  المتاح للاستخدام: 5.000 جرام")
        self.assertIn("محجوز لمراجعة المدير", shortage_message(18, D("1"), D("0"), D("3")))
        from app.viewmodels.exchange_viewmodel import _scrap_breakdown
        self.assertEqual(_scrap_breakdown({"grams": "5.00", "physical": "5.000", "held": "0"}), "")  # nothing held


class ConversionHeldScrapTests(unittest.TestCase):
    """Old Gold conversion (inside «إدخال قطعة جديدة»): the same breakdown and refusal."""

    def test_breakdown_for_the_selected_karat(self):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.instance() or QGuiApplication([])
        from app.viewmodels.old_gold_conversion_viewmodel import OldGoldConversionViewModel
        vm = OldGoldConversionViewModel.__new__(OldGoldConversionViewModel)
        vm._balances = [{"karat_id": 1, "balance_grams": "0.000", "physical_grams": "5.000", "held_grams": "5.000"},
                        {"karat_id": 2, "balance_grams": "3.000", "physical_grams": "3.000", "held_grams": "0.000"}]
        self.assertEqual(OldGoldConversionViewModel.scrapBreakdownForKarat(vm, 1),
                         breakdown_text("5.000", "5.000", "0.000"))
        self.assertEqual(OldGoldConversionViewModel.scrapBreakdownForKarat(vm, 2), "")

    def test_service_refusal_names_the_held_grams(self):
        """Physical 5.000 g 21K, all held: converting 4.990 g is refused with the held explanation; with
        physical 10.000 g (5.000 held) the balance check passes (the write path then runs)."""
        from contextlib import contextmanager
        from unittest.mock import patch
        from services.old_gold_conversion_service import OldGoldConversionService

        from app.session import AppSession
        from services.authentication_service import AuthenticationResult, _VERIFIED
        from tests.auth_fakes import FakeAuthorization
        session = AppSession()
        session._establish(AuthenticationResult(3, None, "مدير", frozenset({"APP_LOGIN", "OLD_GOLD_MANAGE"}), _VERIFIED),
                           int(session.generation))

        def run(physical, held):
            repository = Mock()
            repository.get_active_item_type.return_value = {"is_active": 1, "default_category_id": 1}
            repository.get_active_karat.return_value = {"is_active": 1, "karat_value": 21}
            repository.lock_old_gold_balance.return_value = [{"weight_delta_grams": D(physical)},
                                                             {"weight_delta_grams": -D(held)}]
            repository.held_old_gold.return_value = D(held)

            @contextmanager
            def transaction():
                yield Mock(connection=Mock())
            with patch("services.old_gold_conversion_service.DatabaseTransaction", transaction),                     patch("services.old_gold_conversion_service.OldGoldConversionRepository", return_value=repository):
                OldGoldConversionService(session=session, authorization=FakeAuthorization(session)).convert_to_new_stock(
                    item_type_id=2, karat_id=1, category_id=1, factory_id=0, result_weight_grams="4.990",
                    consumed_old_gold_grams="4.990", size="", barcode_value="", notes="")
            return repository

        with self.assertRaises(ValueError) as refused:
            run("5.000", "5.000")
        self.assertEqual(str(refused.exception),
                         "رصيد الكسر المتاح عيار 21 غير كافٍ: المطلوب 4.990 جرام والمتاح 0.000 جرام. "
                         "يوجد 5.000 جرام محجوز لمراجعة المدير ولا يمكن استخدامه حتى اعتماد القرار.")
        try:
            repository = run("10.000", "5.000")
        except ValueError as exc:                          # only a later (write-path) step may object
            self.assertNotIn("غير كافٍ", str(exc))
            self.assertNotEqual(str(exc), "Not enough old-gold balance.")
        else:
            repository.held_old_gold.assert_not_called()   # enough available: no shortage path at all


# ----------------------------------------------------------------------------- screens (real Main.qml)
class MasterData:
    """MasterDataService stand-in: one active row of each list."""
    rows = {"get_item_types": [{"id": 2, "name": "خاتم", "default_category_id": 1, "is_active": 1}],
            "get_all_item_types": [{"id": 2, "name": "خاتم", "default_category_id": 1, "is_active": 1}],
            "get_karats": [{"id": 1, "name": "21", "karat_value": 21}],
            "get_categories": [{"id": 1, "code": "A", "name": "خواتم"}],
            "get_all_categories": [{"id": 1, "code": "A", "name": "خواتم"}],
            "get_factories": [{"id": 5, "name": "لوكس"}], "get_all_factories": [{"id": 5, "name": "لوكس"}]}

    def __getattr__(self, name):
        return lambda *a, **k: list(self.rows.get(name, []))


class InventoryEntry:
    """InventoryService stand-in: records each created piece; a used barcode is refused like the real one."""

    def __init__(self):
        self.created, self.refreshes = [], 0

    def create_inventory_item(self, **kwargs):
        if kwargs.get("barcode") and any(c["barcode"] == kwargs["barcode"] for c in self.created):
            raise ValueError("Barcode already exists.")
        self.created.append(kwargs)
        return 100 + len(self.created)

    def get_stock_items(self, **kwargs):
        self.refreshes += 1
        return []

    def get_stock_summary(self):
        return {"total_items": 0, "new_stock_items": 0, "old_stock_items": 0, "without_barcode_items": 0,
                "total_weight_grams": D("0")}

    def __getattr__(self, name):
        return lambda *a, **k: []


class Conversion:
    def __init__(self):
        self.balance_reads = 0

    def get_balances(self):
        self.balance_reads += 1
        return [{"karat_id": 1, "karat_value": 21, "balance_grams": D("0.000"), "physical_grams": D("5.000"),
                 "held_grams": D("5.000")}]


class ScrapUxPageTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        vms = self.controller.operational_viewmodels
        vms["masterDataVM"]._service = MasterData()
        self.entry = InventoryEntry()
        vms["inventoryVM"]._service = self.entry
        self.conversion = Conversion()
        vms["oldGoldConversionVM"]._service = self.conversion
        self.grant("INVENTORY_MANAGE", "OLD_GOLD_MANAGE")        # the add-stock controls are permission-gated
        self.balances_vm, self.reviews_vm = vms["balancesVM"], vms["reviewsVM"]
        # A decision also refreshes the Customers area (by design); keep it on an in-memory double.
        vms["customerVM"]._service = Mock(
            get_customer_types=Mock(return_value=[]), get_customer_transaction_types=Mock(return_value=[]),
            search_customers=Mock(return_value={"items": [], "page": 1, "has_more": False}))

    # ---------------------------------------------------------------- BUG-EXCH-SCRAP-REFRESH
    def test_a_manager_decision_reloads_every_scrap_view_at_once(self):
        reads, inventory = self.conversion.balance_reads, self.entry.refreshes
        strip = harness.Balances.strip
        calls = []
        harness.Balances.strip = lambda s, **k: calls.append(1) or strip(s, **k)
        try:
            self.reviews_vm.inspectionResolved.emit()          # the decision succeeded (service already wrote)
            # Synchronous views reloaded within the emit itself — no timer, no page change.
            self.assertEqual((self.conversion.balance_reads, self.entry.refreshes), (reads + 1, inventory + 1))
            # The top-bar strip reload is started at once (a worker): it completes without any timer.
            self.wait_until(lambda: calls, "top-bar Scrap reload")
        finally:
            harness.Balances.strip = strip
        self.assertEqual(self.warnings, [])

    def test_an_exchange_reloads_the_available_scrap_views(self):
        exchange = self.controller.operational_viewmodels["exchangeVM"]
        reads = self.conversion.balance_reads
        exchange.finalized.emit("500")
        self.assertEqual(self.conversion.balance_reads, reads + 1)

    def test_a_return_reloads_the_available_scrap_views(self):
        returned = self.controller.operational_viewmodels["returnVM"]
        reads = self.conversion.balance_reads
        returned.finalized.emit("500")                       # Return V2: the piece entered Scrap (held)
        self.assertEqual(self.conversion.balance_reads, reads + 1)

    # ---------------------------------------------------------------- UX-SCRAP-HOLD-01 on screen
    def test_conversion_shows_the_held_breakdown(self):
        dialog = self.open_dialog(1080, 680)
        self.choose("inventoryEntryMethodCombo", 1)                 # تحويل من الكسر
        self.choose("inventoryKaratCombo", 0)
        self.wait_until(lambda: self.visible_item("conversionHeldBreakdown") is not None, "breakdown")
        self.assertEqual(self.visible_item("conversionHeldBreakdown").property("text"),
                         breakdown_text("5.000", "5.000", "0.000"))
        self.assert_inside_window(self.visible_item("conversionHeldBreakdown"))
        dialog.close()
        self.assertEqual(self.warnings, [])

    # ---------------------------------------------------------------- UX-INV-ADD-01
    def open_dialog(self, width, height):
        self.root.setWidth(width)
        self.root.setHeight(height)
        self.root.setProperty("currentPage", 2)
        QTest.qWait(100)
        dialog = self.item("newInventoryItemDialog")
        QMetaObject.invokeMethod(dialog, "openForNewItem")
        self.wait_until(lambda: dialog.property("opened"), "entry dialog")
        return dialog

    def choose(self, name, index):
        combo = self.item(name)
        combo.setProperty("currentIndex", index)
        QMetaObject.invokeMethod(combo, "activated", Qt.DirectConnection, harness.Q_ARG("int", index)) \
            if hasattr(harness, "Q_ARG") else None
        QTest.qWait(30)

    def type_into_field(self, name, text):
        field = self.item(name)
        self.click(field)
        QTest.keyClick(self.root, Qt.Key_A, Qt.ControlModifier)
        self.type_text(text)

    def fill_piece(self, weight, barcode, notes):
        self.type_into_field("inventoryWeightField", weight)
        self.type_into_field("inventoryBarcodeField", barcode)
        self.type_into_field("inventoryNotesField", notes)

    def batch_choices(self):
        return [self.item(n).property("currentIndex") for n in
                ("inventoryItemTypeCombo", "inventoryKaratCombo", "inventoryCategoryCombo", "inventoryFactoryCombo",
                 "inventoryEntryMethodCombo")]

    def test_save_keeps_the_dialog_open_for_the_next_piece_at_all_sizes(self):
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.entry.created.clear()
                dialog = self.open_dialog(width, height)
                for name in ("inventoryItemTypeCombo", "inventoryKaratCombo", "inventoryCategoryCombo",
                             "inventoryFactoryCombo"):
                    self.choose(name, 0)
                choices = self.batch_choices()
                self.fill_piece("3.600", "01261001", "فص أزرق")
                self.click("saveInventoryItemButton")
                self.assertEqual(len(self.entry.created), 1)                        # saved once
                self.assertEqual((self.entry.created[0]["weight_grams"], self.entry.created[0]["barcode"],
                                  self.entry.created[0]["notes"]), ("3.600", "01261001", "فص أزرق"))
                self.assertTrue(dialog.property("opened"))                         # stays open
                self.assertEqual([self.item(n).property("text") for n in
                                  ("inventoryWeightField", "inventoryBarcodeField", "inventoryNotesField",
                                   "inventorySizeField")], ["", "", "", ""])
                self.assertEqual(self.batch_choices(), choices)                    # batch choices kept
                self.assertTrue(self.item("inventoryWeightField").property("activeFocus"))
                success = self.visible_item("inventoryEntrySuccess")
                self.assertEqual(success.property("text"), "تم إدخال القطعة بنجاح — رقم القطعة #101")
                self.assert_inside_window(success)
                self.assert_inside_window(self.item("saveInventoryItemButton"))
                # The next piece without reopening: weight is already focused.
                self.type_text("4.250")
                self.type_into_field("inventoryBarcodeField", "02261001")
                self.click("saveInventoryItemButton")
                self.assertEqual([c["weight_grams"] for c in self.entry.created], ["3.600", "4.250"])
                self.assertEqual(self.entry.created[1]["item_type_id"], self.entry.created[0]["item_type_id"])
                self.assertTrue(dialog.property("opened"))
                dialog.close()
                self.wait_until(lambda: not dialog.property("visible"), "closed by the user")
        self.assertEqual(self.warnings, [])

    def test_a_failed_save_keeps_everything(self):
        dialog = self.open_dialog(1080, 680)
        for name in ("inventoryItemTypeCombo", "inventoryKaratCombo", "inventoryCategoryCombo", "inventoryFactoryCombo"):
            self.choose(name, 0)
        self.fill_piece("3.600", "01261001", "أول")
        self.click("saveInventoryItemButton")
        self.fill_piece("5.100", "01261001", "مكرر")                                  # duplicate barcode
        self.click("saveInventoryItemButton")
        self.assertEqual(len(self.entry.created), 1)
        self.assertTrue(dialog.property("opened"))
        self.assertEqual([self.item(n).property("text") for n in
                          ("inventoryWeightField", "inventoryBarcodeField", "inventoryNotesField")],
                         ["5.100", "01261001", "مكرر"])                               # nothing reset
        self.assertEqual(self.visible_item("inventoryEntryError").property("text"), "هذا الباركود مستخدم بالفعل.")
        self.assertIsNone(self.visible_item("inventoryEntrySuccess"))
        dialog.close()
        self.assertEqual(self.warnings, [])


# unittest otherwise inherits every Billing harness test, inflating the suite count.
for _name in dir(harness.InvoicePageTests):
    if _name.startswith("test_"):
        setattr(ScrapUxPageTests, _name, None)
