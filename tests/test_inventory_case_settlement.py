"""Manager review of an unidentified (F5) sale (migration 089): link a piece from stock, take the weight from the Scrap, or record
that no source exists. Service rules over an in-memory store; the reversal side is in tests/test_correction_service.py."""
import copy
from contextlib import contextmanager
from decimal import Decimal as D
import unittest

from app.session import AppSession
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.management_review_service import InspectionError, ManagementReviewService
from tests.auth_fakes import FakeAuthorization

MANAGER = frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW"})
CLERK = frozenset({"APP_LOGIN", "INVENTORY_RECONCILE"})


class Store:
    def __init__(self):
        self.case = {"case_id": "70", "case_status": "OPEN", "invoice_id": "500", "invoice_number": "26102608", "karat_id": 1,
                     "karat_value": 21, "weight_grams": "3.000", "invoice_line_id": "4"}
        self.items = {"A1": {"inventory_item_id": "200", "karat_id": 1, "karat_value": 21, "weight_grams": "3.100",
                             "stock_classification": "NEW", "inventory_status": "IN_STOCK"},
                      "SOLD1": {"inventory_item_id": "201", "karat_id": 1, "karat_value": 21, "weight_grams": "3.000",
                                "stock_classification": "NEW", "inventory_status": "SOLD"},
                      "K18": {"inventory_item_id": "202", "karat_id": 2, "karat_value": 18, "weight_grams": "3.000",
                              "stock_classification": "NEW", "inventory_status": "IN_STOCK"}}
        self.scrap = D("10.000")
        self.movements, self.inventory_movements, self.audit, self.events = [], [], [], []


class Repo:
    def __init__(self, connection):
        self.s = connection.store

    def lock_unidentified_case(self, case_id):
        return dict(self.s.case) if str(case_id) == self.s.case["case_id"] else None

    def lock_stock_item_by_barcode(self, barcode):
        row = self.s.items.get(barcode)
        return None if row is None else dict(row)

    def set_item_status(self, *, inventory_item_id, from_status, to_status, updated_by_user_id):
        item = next(v for v in self.s.items.values() if v["inventory_item_id"] == inventory_item_id)
        assert item["inventory_status"] == from_status
        item["inventory_status"] = to_status

    def add_inventory_movement(self, *, item, movement_code, from_status, to_status, **kw):
        self.s.inventory_movements.append((item["inventory_item_id"], movement_code, from_status, to_status))
        return "9"

    def lock_old_gold_balance(self, karat_id):
        return self.s.scrap

    def add_case_scrap_out(self, *, karat_id, weight_grams, **kw):
        self.s.scrap -= D(str(weight_grams))
        self.s.movements.append((karat_id, -D(str(weight_grams))))
        return "8"

    def settle_case(self, *, case_id, status, resolution_code, matched_item_id, notes, user_id, at):
        if self.s.case["case_status"] != "OPEN":
            return False
        self.s.case.update(case_status=status, resolution_code=resolution_code, matched=matched_item_id, notes=notes)
        self.s.events.append((status, resolution_code))
        return True

    def db_now(self):
        import datetime
        return datetime.datetime(2026, 10, 29, 12, 0, 0)


class Cursor:
    def __init__(self, store):
        self.store = store

    def execute(self, sql, params=()):
        self.store.audit.append(params[1])

    def close(self):
        pass


class Conn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return Cursor(self.store)


class CaseSettlementTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.service = self.make(MANAGER)

    def make(self, permissions):
        session = AppSession()
        session._establish(AuthenticationResult(3, None, "المدير", permissions, _VERIFIED), int(session.generation))

        @contextmanager
        def transaction():
            saved = copy.deepcopy(vars(self.store))
            try:
                yield Conn(self.store)
            except BaseException:
                self.store.__dict__.clear()
                self.store.__dict__.update(saved)
                raise
        return ManagementReviewService(session, transaction_factory=transaction, repository=Repo,
                                       authorization=FakeAuthorization(session, None))

    def refused(self, code, call):
        before = copy.deepcopy(vars(self.store))
        with self.assertRaises(InspectionError) as ctx:
            call()
        self.assertEqual(ctx.exception.code, code)
        self.assertEqual(vars(self.store), before)                                   # nothing written

    def test_a_piece_found_in_stock_is_linked_and_leaves_the_stock(self):
        result = self.service.match_case_to_stock("70", "A1")
        self.assertEqual((result["status"], result["resolution"]), ("MATCHED", "MATCH_STOCK"))
        self.assertEqual(self.store.items["A1"]["inventory_status"], "SOLD")
        self.assertEqual(self.store.inventory_movements, [("200", "SOLD", "IN_STOCK", "SOLD")])
        self.assertEqual((self.store.case["case_status"], self.store.case["matched"]), ("MATCHED", "200"))
        self.assertIn("فرق الوزن +0.100", self.store.case["notes"])                   # a different weight is recorded, never hidden
        self.assertEqual(self.store.scrap, D("10.000"))                              # the Scrap is untouched
        self.assertEqual(len(self.store.audit), 1)

    def test_the_link_refuses_a_missing_sold_or_other_karat_piece(self):
        self.refused("PIECE_NOT_FOUND", lambda: self.service.match_case_to_stock("70", "NOPE"))
        self.refused("PIECE_NOT_IN_STOCK", lambda: self.service.match_case_to_stock("70", "SOLD1"))
        self.refused("KARAT_MISMATCH", lambda: self.service.match_case_to_stock("70", "K18"))
        self.refused("BARCODE_REQUIRED", lambda: self.service.match_case_to_stock("70", "  "))

    def test_taking_the_sale_from_the_scrap_posts_the_exact_weight_out(self):
        self.service.take_case_from_scrap("70")
        self.assertEqual(self.store.movements, [(1, D("-3.000"))])
        self.assertEqual(self.store.scrap, D("7.000"))
        self.assertEqual((self.store.case["case_status"], self.store.case["resolution_code"]), ("CLOSED", "FROM_SCRAP"))
        self.assertEqual(self.store.inventory_movements, [])                         # no stock movement

    def test_the_scrap_may_not_go_negative(self):
        self.store.scrap = D("2.999")
        self.refused("SCRAP_SHORTAGE", lambda: self.service.take_case_from_scrap("70"))

    def test_no_source_moves_nothing_and_needs_a_written_reason(self):
        self.refused("REASON_REQUIRED", lambda: self.service.close_case_no_source("70", "   "))
        self.service.close_case_no_source("70", "القطعة جاءت من عميل ولم تُسجَّل")
        self.assertEqual((self.store.case["case_status"], self.store.case["resolution_code"]), ("CLOSED", "NO_SOURCE"))
        self.assertEqual((self.store.movements, self.store.inventory_movements, self.store.scrap), ([], [], D("10.000")))
        self.assertEqual(self.store.case["notes"], "القطعة جاءت من عميل ولم تُسجَّل")

    def test_a_case_is_settled_once(self):
        self.service.take_case_from_scrap("70")
        self.refused("CASE_ALREADY_SETTLED", lambda: self.service.take_case_from_scrap("70"))
        self.refused("CASE_ALREADY_SETTLED", lambda: self.service.match_case_to_stock("70", "A1"))

    def test_an_exchange_manual_case_without_an_invoice_is_not_settled_here(self):
        self.store.case["invoice_id"] = None
        self.refused("CASE_UNSUPPORTED", lambda: self.service.take_case_from_scrap("70"))

    def test_only_the_manager_right_settles_a_case(self):
        clerk = self.make(CLERK)
        for call in (lambda: clerk.take_case_from_scrap("70"), lambda: clerk.match_case_to_stock("70", "A1"),
                     lambda: clerk.close_case_no_source("70", "x")):
            with self.assertRaises(PermissionError):
                call()
        self.refused("CASE_NOT_FOUND", lambda: self.service.take_case_from_scrap("999"))


if __name__ == "__main__":
    unittest.main()
