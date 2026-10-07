"""Owner manual-review fixes (Inventory, no QML): 21K-equivalent totals, exact identity lookup of a
SOLD piece, karat / item-type filters, the old-gold conversion without a chosen employee, and English
digits in every parser. The live database is blocked."""
from contextlib import contextmanager
from decimal import Decimal as D
import unittest
from unittest.mock import patch

from services.inventory_service import InventoryService
from services.number_text import normalize_digits
from tests.test_inventory_stock import K18, K21, K24, MIXED, Repo, Store, piece, service_for


class EquivalentTests(unittest.TestCase):
    def test_owner_example(self):
        # 18K 4.500 → 3.857142…; 21K 5.000; 24K 10.000 → 11.428571… = 20.286 (21K equivalent)
        self.assertEqual(InventoryService.equivalent_21([(18, D("4.500")), (21, D("5.000")), (24, D("10.000"))]),
                         D("20.286"))
        self.assertEqual(InventoryService.equivalent_21([]), D("0.000"))

    def test_balance_keeps_physical_karat_weights_and_adds_the_equivalent(self):
        balance = service_for(Store([piece(1, K18, "دبلة", "4.500"), piece(2, K21, "خاتم", "5.000"),
                                     piece(3, K24, "سبيكة", "10.000")])).get_stock_balance()
        self.assertEqual([k["weight_grams"] for k in balance["karats"]], [D("5.000"), D("4.500"), D("10.000")])
        self.assertEqual((balance["weight_grams"], balance["equivalent_21_weight_grams"]),
                         (D("19.500"), D("20.286")))

    def test_summary_equivalent_comes_from_current_stock_by_karat(self):
        class SummaryRepo(Repo):
            def get_stock_summary(self):
                return {"total_items": 3, "total_weight_grams": D("19.500")}

            def get_stock_weight_by_karat(self):
                return [{"karat_value": 18, "weight_grams": D("4.500")}, {"karat_value": 21, "weight_grams": D("5.000")},
                        {"karat_value": 24, "weight_grams": D("10.000")}]
        store = Store()

        @contextmanager
        def transaction():
            yield type("T", (), {"connection": type("C", (), {"store": store})()})()
        summary = InventoryService(transaction_factory=transaction, repository=SummaryRepo).get_stock_summary()
        self.assertEqual(summary["equivalent_21_weight_grams"], D("20.286"))


class HistoryLookupTests(unittest.TestCase):
    SOLD = {"inventory_item_id": "7", "inventory_status": "SOLD", "weight_grams": D("3.600"), "size": "55",
            "karat_id": 1, "item_type_id": 1, "item_type_name": "خاتم", "karat_value": 21, "factory_code": "NR",
            "factory_name": "النور للتصنيع", "category_code": "A", "barcode_value": "01261001",
            "sale": {"invoice_id": "15", "invoice_number": "26100203", "invoice_date": "2026-10-01 19:51:00",
                     "customer_name": "علي عبدالرحمن"}}

    def vm(self, found):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.instance() or QGuiApplication([])
        from app.viewmodels.inventory_viewmodel import InventoryViewModel
        calls = []

        class Service:
            def get_stock_items(self, **kwargs):
                calls.append(kwargs)
                return []                                      # the SOLD piece is not current stock

            def get_stock_summary(self):
                return {"total_items": 0, "equivalent_21_weight_grams": D("0")}

            def find_piece(self, text):
                return found
        with patch("app.viewmodels.inventory_viewmodel.InventoryService", Service):
            vm = InventoryViewModel()
        return vm, calls

    def test_sold_barcode_is_found_read_only_with_its_invoice(self):
        vm, calls = self.vm(self.SOLD)
        vm.search("01261001")
        self.assertEqual((vm.itemCount, len(vm.items)), (0, 0))               # never current stock
        piece = vm.historyPiece
        self.assertTrue(vm.hasHistoryPiece)
        self.assertEqual((piece["itemNumber"], piece["barcode"], piece["status"], piece["weight"]),
                         ("#7", "01261001", "مباعة", "3.600"))
        self.assertEqual((piece["invoiceNumber"], piece["invoiceId"], piece["customer"]),
                         ("26100203", "15", "علي عبدالرحمن"))
        vm.setFilters(2, 0)                                                     # another karat: no card
        self.assertFalse(vm.hasHistoryPiece)
        self.assertEqual(calls[-1], {"search": "01261001", "karat_id": 2, "item_type_id": None})

    def test_current_stock_pieces_never_show_a_history_card(self):
        vm, _ = self.vm(dict(self.SOLD, inventory_status="IN_STOCK"))
        vm.search("01261001")
        self.assertFalse(vm.hasHistoryPiece)
        vm, _ = self.vm(None)
        vm.search("99999999")
        self.assertFalse(vm.hasHistoryPiece)


class ConversionActorTests(unittest.TestCase):
    """محولة من الكسر: no chosen employee; the authenticated actor is recorded in the user columns."""

    def test_conversion_records_the_actor_and_no_employee(self):
        from app.session import AppSession
        from services import old_gold_conversion_service as module
        from services.authentication_service import AuthenticationResult, _VERIFIED
        from tests.auth_fakes import FakeAuthorization
        session = AppSession()
        session._establish(AuthenticationResult(1, None, "مدير", frozenset({"APP_LOGIN", "OLD_GOLD_MANAGE"}), _VERIFIED),
                           int(session.generation))
        writes = {}

        class FakeRepo:
            def __init__(self, connection):
                pass

            def get_active_employee(self, employee_id):
                raise AssertionError("no employee lookup without a chosen employee")

            def get_active_item_type(self, item_type_id):
                return {"id": item_type_id, "is_active": 1, "default_category_id": None}

            def get_active_karat(self, karat_id):
                return {"id": karat_id, "is_active": 1, "karat_value": 21}

            def lock_old_gold_balance(self, karat_id):
                return [{"weight_delta_grams": D("100.000")}]

            def get_movement_type_id(self, code):
                return 1

            def _record(self, name, kwargs):
                writes[name] = kwargs
                return 1

            def create_new_inventory_item(self, **kwargs):
                return self._record("create_new_inventory_item", kwargs)

            def create_inventory_movement(self, **kwargs):
                return self._record("create_inventory_movement", kwargs)

            def create_old_gold_out_movement(self, **kwargs):
                return self._record("create_old_gold_out_movement", kwargs)

            def create_conversion_batch(self, **kwargs):
                return self._record("create_conversion_batch", kwargs)

        @contextmanager
        def transaction():
            yield type("T", (), {"connection": None})()
        with patch.object(module, "DatabaseTransaction", transaction),                 patch.object(module, "OldGoldConversionRepository", FakeRepo):
            module.OldGoldConversionService(session=session, authorization=FakeAuthorization(session))                 .convert_to_new_stock(
                    item_type_id=1, karat_id=1, category_id=0, factory_id=0,
                    result_weight_grams="3.500", consumed_old_gold_grams="3.600", size="", barcode_value="",
                    notes="")
        self.assertEqual(sorted(writes), ["create_conversion_batch", "create_inventory_movement",
                                          "create_new_inventory_item", "create_old_gold_out_movement"])
        for name, values in writes.items():
            self.assertEqual((values.get("employee_id"), values.get("user_id")), (None, 1), name)

    def test_without_an_actor_the_conversion_is_refused(self):
        from services.old_gold_conversion_service import OldGoldConversionService
        with self.assertRaises(PermissionError):                    # no session / no authenticated actor
            OldGoldConversionService().convert_to_new_stock(
                item_type_id=1, karat_id=1, category_id=0, factory_id=0, result_weight_grams="3.5",
                consumed_old_gold_grams="3.6", size="", barcode_value="", notes="")


class EnglishDigitTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(normalize_digits("١٢٣٤٥"), "12345")
        self.assertEqual(normalize_digits("۱۲۳۴۵"), "12345")
        self.assertEqual(normalize_digits("٣٫٦٠٠"), "3.600")
        self.assertEqual(normalize_digits("خاتم ٢١"), "خاتم 21")                 # letters untouched
        self.assertEqual(normalize_digits(D("5")), D("5"))

    def test_parsers_accept_arabic_digits(self):
        from app.viewmodels.invoice_viewmodel import _decimal
        from services.correction_service import _money_value
        from services.invoice_calculator import InvoiceCalculator
        self.assertEqual(InventoryService._parse_weight("٣٫٦٠٠"), D("3.600"))
        self.assertEqual(_decimal("٢٥٠٣٥"), D("25035"))
        self.assertEqual(_money_value("١٠٠٠٫٥٠"), D("1000.50"))
        self.assertEqual(InvoiceCalculator.payment("IN", "۲۴۰۰۰").amount, D("24000.00"))


if __name__ == "__main__":
    unittest.main()
