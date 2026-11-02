"""تصحيح باركود قطعة مباعة: the service rules (fake repository, no database)."""
from contextlib import contextmanager
from decimal import Decimal as D
import unittest

from services.invoice_piece_correction_service import InvoicePieceCorrectionService, PieceCorrectionError
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import session_for

MANAGER = frozenset({"APP_LOGIN", "TRANSACTION_CORRECT"})


def item(item_id, status, karat=1, weight="3.710", barcode=None):
    return {"id": item_id, "karat_id": karat, "weight_grams": D(weight), "size": "56", "stock_classification": "NEW_STOCK", "inventory_status": status,
            "item_type_name": "دبلة", "factory_name": "لوكس", "category_code": "B", "barcode_value": barcode}


class Store:
    def __init__(self):
        self.invoice = {"id": 5, "invoice_number": "26102903", "invoice_status": "FINALIZED"}
        self.line = {"id": 9, "line_number": 1, "line_type": "SELL_INVENTORY", "inventory_item_id": 11, "karat_id": 1, "weight_grams": D("3.710"), "barcode_snapshot": "37260106"}
        self.items = {11: item(11, "SOLD", barcode="37260106"), 12: item(12, "IN_STOCK", barcode="40260107"), 13: item(13, "IN_STOCK", weight="3.800", barcode="41260108"),
                      14: item(14, "IN_STOCK", karat=2, barcode="42260109"), 15: item(15, "SOLD", barcode="43260110")}
        self.barcodes = {"37260106": 11, "40260107": 12, "41260108": 13, "42260109": 14, "43260110": 15}
        self.reversed, self.lifecycle = False, 0
        self.movements, self.audits, self.repointed = [], [], []


class Repo:
    store = None

    def __init__(self, connection=None):
        pass

    def now(self):
        return "2026-11-02 10:00:00"

    def invoice_by_number(self, number, lock=False):
        return dict(self.store.invoice) if number == self.store.invoice["invoice_number"] else None

    def line(self, invoice_id, line_id, lock=False):
        return dict(self.store.line) if int(line_id) == self.store.line["id"] else None

    def is_reversed(self, invoice_id):
        return self.store.reversed

    def lifecycle_count(self, invoice_id):
        return self.store.lifecycle

    def find_item_id_by_barcode(self, barcode):
        return self.store.barcodes.get(barcode)

    def items(self, ids):
        return [self.store.items[i] for i in sorted(set(ids)) if i in self.store.items]

    def movement_type_id(self, code):
        return 7

    def set_status(self, item_id, from_status, to_status, user_id):
        if self.store.items[item_id]["inventory_status"] != from_status:
            return False
        self.store.items[item_id]["inventory_status"] = to_status
        return True

    def insert_movement(self, **kw):
        self.store.movements.append(kw)

    def repoint_line(self, line_id, item):
        self.store.repointed.append((line_id, item["id"], item["barcode_value"]))
        self.store.line["inventory_item_id"] = item["id"]
        return True

    def audit(self, **kw):
        self.store.audits.append(kw)


class PieceCorrectionTests(unittest.TestCase):
    def build(self, permissions=MANAGER):
        self.store = Store()
        Repo.store = self.store
        self.session = session_for(permissions)

        @contextmanager
        def transaction():
            yield object()
        return InvoicePieceCorrectionService(self.session, transaction_factory=transaction, repository=Repo, authorization=FakeAuthorization(self.session))

    def refused(self, service, code, barcode="40260107", **kw):
        args = dict(number="26102903", line_id="9", new_barcode=barcode, reason="اتمسح باركود غلط", confirmed=True)
        args.update(kw)
        with self.assertRaises(PieceCorrectionError) as raised:
            service.correct(args["number"], args["line_id"], args["new_barcode"], args["reason"], args["confirmed"])
        self.assertEqual(raised.exception.code, code)
        self.assertEqual((self.store.movements, self.store.audits, self.store.repointed), ([], [], []))
        self.assertEqual((self.store.items[11]["inventory_status"], self.store.items[12]["inventory_status"]), ("SOLD", "IN_STOCK"))

    def test_the_real_piece_replaces_the_recorded_one_and_the_old_piece_returns_to_stock(self):
        service = self.build()
        result = service.correct("26102903", "9", "40260107", "اتمسح باركود غلط", True)
        self.assertEqual((result["old_barcode"], result["new_barcode"]), ("37260106", "40260107"))
        self.assertEqual((self.store.items[11]["inventory_status"], self.store.items[12]["inventory_status"]), ("IN_STOCK", "SOLD"))
        self.assertEqual(self.store.repointed, [(9, 12, "40260107")])
        self.assertEqual([(m["item"]["id"], m["from_status"], m["to_status"]) for m in self.store.movements], [(11, "SOLD", "IN_STOCK"), (12, "IN_STOCK", "SOLD")])
        meta = self.store.audits[0]["metadata"]
        self.assertEqual((meta["old_item_id"], meta["new_item_id"], meta["old_barcode"], meta["new_barcode"], meta["reason"]), (11, 12, "37260106", "40260107", "اتمسح باركود غلط"))

    def test_only_a_manager_may_correct(self):
        service = self.build(frozenset({"APP_LOGIN"}))
        with self.assertRaises(PermissionError):
            service.correct("26102903", "9", "40260107", "سبب", True)
        self.assertEqual(self.store.items[11]["inventory_status"], "SOLD")

    def test_reason_confirmation_and_barcode_are_mandatory(self):
        service = self.build()
        self.refused(service, "REASON_REQUIRED", reason="  ")
        self.refused(service, "CONFIRM_REQUIRED", confirmed=False)
        self.refused(service, "BARCODE_REQUIRED", barcode="")

    def test_the_real_piece_must_be_in_stock_with_the_same_karat_and_weight(self):
        service = self.build()
        self.refused(service, "BARCODE_UNKNOWN", barcode="99999999")
        self.refused(service, "SAME_PIECE", barcode="37260106")
        self.refused(service, "NEW_NOT_IN_STOCK", barcode="43260110")
        self.refused(service, "KARAT_OR_WEIGHT", barcode="41260108")
        self.refused(service, "KARAT_OR_WEIGHT", barcode="42260109")

    def test_a_reversed_voided_or_exchanged_invoice_and_a_non_piece_line_are_refused(self):
        service = self.build()
        self.store.reversed = True
        self.refused(service, "INVOICE_REVERSED")
        self.store.reversed, self.store.lifecycle = False, 1
        self.refused(service, "HAS_LIFECYCLE")
        self.store.lifecycle, self.store.invoice["invoice_status"] = 0, "VOIDED"
        self.refused(service, "NOT_FINALIZED")
        self.store.invoice["invoice_status"], self.store.line["line_type"] = "FINALIZED", "BUY_OLD_GOLD"
        self.refused(service, "LINE_NOT_PIECE")

    def test_a_recorded_piece_that_is_no_longer_sold_is_refused(self):
        service = self.build()
        self.store.items[11]["inventory_status"] = "IN_STOCK"
        with self.assertRaises(PieceCorrectionError) as raised:
            service.correct("26102903", "9", "40260107", "سبب", True)
        self.assertEqual(raised.exception.code, "OLD_NOT_SOLD")


if __name__ == "__main__":
    unittest.main()
