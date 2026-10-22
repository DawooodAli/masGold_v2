"""إلغاء فاتورة قديمة: the service rules (fake repository, no database) and the real screen (مراجعات المدير -> the window -> search -> reason -> confirm -> cancel)."""
from contextlib import contextmanager
from decimal import Decimal as D
import unittest

from PySide6.QtTest import QTest

from services.legacy_invoice_service import LegacyInvoiceError, LegacyInvoiceService
from tests import test_invoice_page_qml as harness
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import session_for

MANAGER = frozenset({"APP_LOGIN", "TRANSACTION_CORRECT"})


class Store:
    def __init__(self):
        self.invoice = {"id": 5, "invoice_number": "26102202", "invoice_status": "FINALIZED", "record_origin": "LEGACY", "invoice_date": "2026-10-22 12:00:00", "net_amount": D("28000.00"),
                        "net_direction": "CUSTOMER_PAYS_SHOP", "customer_id": 4849, "customer_name_snapshot": "مريم راضي سلامه"}
        self.lines = [{"id": 1, "line_number": 1, "line_type": "SELL_INVENTORY", "inventory_item_id": 11, "barcode_snapshot": "12260330", "item_name_snapshot": "خاتم", "weight_grams": D("1.580"), "karat_value": 21},
                      {"id": 2, "line_number": 2, "line_type": "SELL_INVENTORY", "inventory_item_id": 12, "barcode_snapshot": "74260919", "item_name_snapshot": "خاتم", "weight_grams": D("2.560"), "karat_value": 21},
                      {"id": 3, "line_number": 3, "line_type": "BUY_OLD_GOLD", "inventory_item_id": None, "barcode_snapshot": "1-old", "item_name_snapshot": "متنوع", "weight_grams": D("2.0"), "karat_value": 21}]
        self.items = {11: {"id": 11, "inventory_status": "SOLD", "stock_classification": "NEW_STOCK", "weight_grams": D("1.580")}, 12: {"id": 12, "inventory_status": "SOLD", "stock_classification": "NEW_STOCK", "weight_grams": D("2.560")}}
        self.movements, self.audits = [], []


class Repo:
    store = None

    def __init__(self, connection=None):
        pass

    def now(self):
        return "2026-10-23 10:00:00"

    def invoice_by_number(self, number, lock=False):
        return dict(self.store.invoice) if number == self.store.invoice["invoice_number"] else None

    def lines(self, invoice_id):
        return list(self.store.lines)

    def items(self, ids, lock=False):
        return [self.store.items[i] for i in sorted(ids) if i in self.store.items]

    def movement_type_id(self, code):
        return 7

    def restock_piece(self, item_id, user_id):
        if self.store.items[item_id]["inventory_status"] != "SOLD":
            return False
        self.store.items[item_id]["inventory_status"] = "IN_STOCK"
        return True

    def insert_movement(self, **kw):
        self.store.movements.append(kw)

    def void_invoice(self, invoice_id, user_id, reason):
        if self.store.invoice["invoice_status"] != "FINALIZED":
            return False
        self.store.invoice["invoice_status"] = "VOIDED"
        return True

    def audit(self, **kw):
        self.store.audits.append(kw)


class ServiceTests(unittest.TestCase):
    def build(self, permissions=MANAGER):
        self.store = Store()
        Repo.store = self.store
        self.session = session_for(permissions)

        @contextmanager
        def transaction():
            yield object()
        return LegacyInvoiceService(self.session, transaction_factory=transaction, repository=Repo, authorization=FakeAuthorization(self.session))

    def test_cancelling_puts_every_sold_piece_back_in_stock_and_voids_the_invoice(self):
        service = self.build()
        result = service.void("26102202", "رجعت الفاتورة", True)
        self.assertEqual((result["restocked"], result["grams"]), (2, D("4.140")))
        self.assertEqual({i["inventory_status"] for i in self.store.items.values()}, {"IN_STOCK"})
        self.assertEqual((self.store.invoice["invoice_status"], len(self.store.movements), len(self.store.audits)), ("VOIDED", 2, 1))
        with self.assertRaises(LegacyInvoiceError) as raised:
            service.void("26102202", "تاني", True)
        self.assertEqual(raised.exception.code, "ALREADY_VOIDED")

    def test_a_piece_that_is_no_longer_sold_refuses_the_whole_cancellation(self):
        service = self.build()
        self.store.items[11]["inventory_status"] = "IN_STOCK"
        with self.assertRaises(LegacyInvoiceError) as raised:
            service.void("26102202", "سبب", True)
        self.assertEqual(raised.exception.code, "PIECE_NOT_SOLD")
        self.assertIn("12260330", str(raised.exception))
        self.assertEqual((self.store.invoice["invoice_status"], self.store.items[12]["inventory_status"], self.store.movements), ("FINALIZED", "SOLD", []))

    def test_reason_confirmation_and_the_kind_of_invoice_are_required(self):
        service = self.build()
        for args, code in ((("26102202", " ", True), "REASON_REQUIRED"), (("26102202", "سبب", False), "CONFIRM_REQUIRED"), (("999", "سبب", True), "NOT_FOUND"), (("", "سبب", True), "NUMBER_REQUIRED")):
            with self.assertRaises(LegacyInvoiceError) as raised:
                service.void(*args)
            self.assertEqual(raised.exception.code, code, args)
        self.store.invoice["record_origin"] = "NATIVE"
        with self.assertRaises(LegacyInvoiceError) as raised:
            service.void("26102202", "سبب", True)
        self.assertEqual(raised.exception.code, "NOT_LEGACY")
        self.assertEqual(self.store.movements, [])

    def test_lookup_shows_the_pieces_and_who_may_cancel(self):
        service = self.build()
        info = service.lookup("26102202")
        self.assertEqual((info["customer"], len(info["pieces"]), info["old_gold_lines"], info["blocked"]), ("مريم راضي سلامه", 2, 1, []))
        nobody = self.build(frozenset({"APP_LOGIN"}))
        with self.assertRaises(PermissionError):
            nobody.void("26102202", "سبب", True)
        self.assertFalse(nobody.can_void())


class ScreenTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        self.store = Store()
        Repo.store = self.store

        @contextmanager
        def transaction():
            yield object()
        session = self.controller.session
        service = LegacyInvoiceService(session, transaction_factory=transaction, repository=Repo, authorization=FakeAuthorization(session))
        self.grant("TRANSACTION_CORRECT")
        self.legacy = self.controller.operational_viewmodels["legacyInvoiceVM"]
        self.legacy._service = service
        self.legacy.changed.emit()

    def test_the_window_searches_confirms_and_cancels_with_real_clicks_at_all_sizes(self):
        self.open_settings_dialog(10, "reviewsOpenLegacyVoid", "legacyVoidDialog")
        self.type_into("legacyVoidNumber", "26102202")
        self.click("legacyVoidSearch")
        self.wait_until(lambda: bool(self.legacy.info.get("number")), "found")
        self.assertFalse(self.item("legacyVoidDo").property("enabled"))                            # reason and confirmation are still missing
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            self.scroll_dialog_to("legacyVoidDialog", "legacyVoidDo")
            self.assert_inside_window(self.item("legacyVoidDo"))
        self.type_into("legacyVoidReason", "رجعت الفاتورة")
        self.click("legacyVoidConfirm")
        self.wait_until(lambda: self.item("legacyVoidDo").property("enabled"), "ready")
        self.click("legacyVoidDo")
        self.wait_until(lambda: self.store.invoice["invoice_status"] == "VOIDED", "voided")
        self.assertEqual({i["inventory_status"] for i in self.store.items.values()}, {"IN_STOCK"})
        self.wait_until(lambda: "تم إلغاء الفاتورة" in str(self.item("legacyVoidMessage").property("text")), "the result is shown")
        self.assertEqual(self.warnings, [])


for _cls in (ScreenTests,):
    for _name in dir(harness.InvoicePageTests):
        if _name.startswith("test_") and _name not in _cls.__dict__:
            setattr(_cls, _name, None)

if __name__ == "__main__":
    unittest.main()
