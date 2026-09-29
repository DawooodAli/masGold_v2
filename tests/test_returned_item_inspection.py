"""Returned Item Inspection V1 (021): manager review of every piece returned in an exchange.

Runs on the Exchange in-memory store (tests/test_exchange_service.py): an exchange is
finalized for real through ExchangeService, then the manager resolves the review through
ManagementReviewService on the same store. The transaction factory restores the whole
store on any exception, like a MySQL rollback.
"""
from contextlib import contextmanager
import copy
from decimal import Decimal
import unittest

from app.session import AppSession
from app.viewmodels.invoice_history_viewmodel import InvoiceHistoryViewModel
from repositories.invoice_lifecycle_repository import exchange_code
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.exchange_service import (
    CONVERTED_TEXT, INSPECTION_PENDING_TEXT, RESTOCKED_TEXT, ExchangeError, exchange_summary,
)
from services.management_review_service import InspectionError, ManagementReviewService
from tests import test_exchange_service as ex


D = Decimal


class ReviewRepo:
    """ManagementReviewRepository over the exchange store (the SQL contract, enforced in Python)."""
    fail_on = None
    calls = []

    def __init__(self, connection):
        self.store = connection.store

    def _maybe_fail(self, name):
        ReviewRepo.calls.append(name)
        if ReviewRepo.fail_on == name:
            raise RuntimeError(f"forced failure in {name}")

    def _row(self, review):
        line = self.store.lifecycle_lines[int(review["lifecycle_line_id"]) - 1]
        item = self.store.items[line["inventory_item_id"]]
        return {"review_id": review["review_id"], "review_status": review["status"],
                "resolution_code": review["resolution"], "invoice_id": review["invoice_id"],
                "invoice_number": self.store.invoices[review["invoice_id"]]["invoice_number"],
                "lifecycle_line_id": review["lifecycle_line_id"], "lifecycle_id": line["lifecycle_id"],
                "exchange_code": exchange_code(line["lifecycle_id"]), "exchanged_at": ex.NOW, "created_at": ex.NOW,
                "source_invoice_line_id": line["source_invoice_line_id"], "inventory_item_id": line["inventory_item_id"],
                "item_type_name": item["item_type_name"], "karat_value": item["karat_value"],
                "weight_grams": line["weight"], "item_weight_grams": item["weight_grams"],
                "inventory_status": item["inventory_status"], "customer_name_snapshot": "عميل",
                # The piece keeps its stored barcode for good (never re-assigned, never new).
                "stored_barcode": item["barcode_value"], "size": None, "item_notes": None}

    def open_counts(self):
        return {"RETURNED_ITEM_INSPECTION": sum(r["status"] == "OPEN" for r in self.store.inspections)}

    def open_reviews(self, review_type):
        return []

    def open_inventory_cases(self):
        return []

    def open_inspections(self):
        return [self._row(r) for r in self.store.inspections if r["status"] == "OPEN"]

    def lock_inspection(self, review_id):
        self._maybe_fail("lock_inspection")
        review = next((r for r in self.store.inspections if r["review_id"] == str(review_id)), None)
        return None if review is None else self._row(review)

    def lock_item(self, inventory_item_id):
        self._maybe_fail("lock_item")
        return dict(self.store.items[str(inventory_item_id)])

    def lock_old_gold_balance(self, karat_id):
        self._maybe_fail("lock_old_gold_balance")

    def db_now(self):
        return ex.NOW

    def set_item_status(self, *, inventory_item_id, from_status, to_status, updated_by_user_id):
        row = self.store.items[str(inventory_item_id)]
        assert row["inventory_status"] == from_status
        # 021 status CHECK
        assert to_status in ("IN_STOCK", "SOLD", "SCRAPPED", "REMOVED", "RETURNED_PENDING", "CONVERTED_TO_OLD_GOLD")
        row["inventory_status"] = to_status

    def add_inventory_movement(self, *, item, movement_code, from_status, to_status, review_id, **kwargs):
        self._maybe_fail("add_inventory_movement")
        self.store.inventory_movements.append((item["inventory_item_id"], movement_code, from_status, to_status))
        return str(len(self.store.inventory_movements))

    def add_old_gold_in(self, *, karat_id, weight_grams, inventory_item_id, review_id, **kwargs):
        self._maybe_fail("add_old_gold_in")
        assert weight_grams > 0
        self.store.old_gold[int(karat_id)] += weight_grams
        self.store.old_gold_movements.append({"code": "RETURNED_ITEM_TO_OLD_GOLD", "karat_id": karat_id,
                                              "delta": weight_grams, "item": inventory_item_id,
                                              "reference": ("MANAGEMENT_REVIEW", review_id)})
        return str(len(self.store.old_gold_movements))

    def resolve_inspection(self, *, review_id, resolution_code, resolved_by_user_id, resolved_at, notes):
        review = next(r for r in self.store.inspections if r["review_id"] == str(review_id))
        if review["status"] != "OPEN":
            return False
        review.update(status="RESOLVED", resolution=resolution_code, resolved_by=resolved_by_user_id,
                      resolved_at=resolved_at)
        return True

    def returned_lines_without_inspection(self):
        reviewed = {r["lifecycle_line_id"] for r in self.store.inspections}
        rows = []
        for number, line in enumerate(self.store.lifecycle_lines, 1):
            item = self.store.items.get(line["inventory_item_id"] or "")
            if (line["role"] == "RETURNED" and str(number) not in reviewed and item
                    and item["inventory_status"] == "RETURNED_PENDING"):
                rows.append({"lifecycle_line_id": str(number), "lifecycle_id": line["lifecycle_id"],
                             "invoice_id": "500", "source_invoice_line_id": line["source_invoice_line_id"],
                             "inventory_item_id": line["inventory_item_id"], "karat_value": item["karat_value"],
                             "weight_grams": line["weight"], "item_name_snapshot": line["item_name"],
                             "stored_barcode": item["barcode_value"], "barcode_snapshot": None})
        return rows


class InspectionTestCase(ex.ExchangeTestCase):
    manager_permissions = frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW"})

    def setUp(self):
        super().setUp()
        ReviewRepo.fail_on, ReviewRepo.calls = None, []
        self.reviews = self.review_service(self.manager_permissions)

    def review_service(self, permissions):
        session = AppSession()
        session._establish(AuthenticationResult(3, None, "المدير", permissions, _VERIFIED), int(session.generation))

        @contextmanager
        def transaction():
            saved = copy.deepcopy(vars(self.store))
            try:
                yield ex.Connection(self.store)
            except BaseException:
                self.store.__dict__.clear()
                self.store.__dict__.update(saved)
                raise
        return ManagementReviewService(session, transaction_factory=transaction, repository=ReviewRepo,
                                       lifecycle_repository=ex.LifecycleRepo,
                                       authorization=ex.Authorization(session, permissions))

    def exchange(self):
        """Returned: line 1 (item 200, 21K 10.000, barcode B200) for item 300 (21K 10.000): zero result."""
        self.finalize(ex.draft())
        return self.store.inspections[0]["review_id"]

    def item(self, item_id="200"):
        return self.store.items[item_id]


class CreationTests(InspectionTestCase):
    def test_a_every_returned_piece_gets_exactly_one_linked_review(self):
        self.finalize(ex.draft(returned=("1", "2"), replacements=(("300", None), ("303", None))))
        self.assertEqual(len(self.store.inspections), 2)
        for review, (line_id, item_id, barcode) in zip(self.store.inspections, (("1", "200", "B200"),
                                                                               ("2", "201", "B201"))):
            line = self.store.lifecycle_lines[int(review["lifecycle_line_id"]) - 1]
            self.assertEqual((line["role"], line["source_invoice_line_id"], line["inventory_item_id"]),
                             ("RETURNED", line_id, item_id))
            d = review["details"]
            self.assertEqual((review["invoice_id"], review["status"], d["lifecycle_id"], d["exchange_code"],
                              d["source_invoice_line_id"], d["inventory_item_id"], d["stored_barcode"]),
                             ("500", "OPEN", "1", "EX000001", line_id, item_id, barcode))
            self.assertIn(f"الباركود المسجل {barcode}", review["summary"])
        # The replacement is never reviewed; the returned piece waits as RETURNED_PENDING.
        self.assertEqual(self.item("200")["inventory_status"], "RETURNED_PENDING")
        self.assertEqual(self.store.events[0]["details"]["inspection_review_ids"], ["901", "902"])

    def test_a_review_creation_failure_rolls_the_whole_exchange_back(self):
        ex.LifecycleRepo.fail_on = "open_returned_item_inspection"
        before = self.store.tables()
        with self.assertRaises(RuntimeError):
            self.finalize(ex.draft())
        self.assertEqual(self.store.tables(), before)

    def test_b_damaged_label_needs_no_scan(self):
        """The piece is identified by the review (line + inventory item); no barcode is typed."""
        review_id = self.exchange()
        row = self.reviews.open_reviews()["RETURNED_ITEM_INSPECTION"][0]
        self.assertEqual((row["inventory_item_id"], row["stored_barcode"]), ("200", "B200"))
        result = self.reviews.return_to_stock(review_id)
        self.assertEqual((result["inventory_item_id"], result["stored_barcode"]), ("200", "B200"))


class ReturnToStockTests(InspectionTestCase):
    def test_c_same_item_same_barcode_back_in_stock(self):
        review_id = self.exchange()
        items_before, barcodes = len(self.store.items), {k: v["barcode_value"] for k, v in self.store.items.items()}
        old_gold = dict(self.store.old_gold)
        result = self.reviews.return_to_stock(review_id)
        self.assertEqual((result["resolution"], result["inventory_status"]), ("RETURN_TO_STOCK", "IN_STOCK"))
        self.assertEqual(self.item()["inventory_status"], "IN_STOCK")
        self.assertEqual(self.store.inventory_movements[-1],
                         ("200", "RETURNED_ITEM_RESTOCKED", "RETURNED_PENDING", "IN_STOCK"))
        # No new item, no new barcode, no old-gold movement.
        self.assertEqual(len(self.store.items), items_before)
        self.assertEqual({k: v["barcode_value"] for k, v in self.store.items.items()}, barcodes)
        self.assertEqual(self.store.old_gold, old_gold)
        self.assertFalse([m for m in self.store.old_gold_movements if m["code"] == "RETURNED_ITEM_TO_OLD_GOLD"])
        review = self.store.inspections[0]
        self.assertEqual((review["status"], review["resolution"], review["resolved_by"], review["resolved_at"]),
                         ("RESOLVED", "RETURN_TO_STOCK", 3, ex.NOW))
        self.assertEqual(self.store.audit[-1][1], "RETURNED_ITEM_RETURN_TO_STOCK")

    def test_d_barcode_and_search_find_the_piece_again(self):
        review_id = self.exchange()
        with self.assertRaises(ExchangeError):                       # waiting: not sellable
            self.service.find_replacement_by_barcode("B200")
        self.reviews.return_to_stock(review_id)
        self.assertEqual(self.service.find_replacement_by_barcode("B200")["inventory_item_id"], "200")
        found = self.service.search_replacements(karat_id=1, weight="10", tolerance="0")["items"]
        self.assertIn("200", [i["inventory_item_id"] for i in found])


class ConvertToOldGoldTests(InspectionTestCase):
    def test_e_exact_karat_and_weight_enter_the_counted_old_gold(self):
        review_id = self.exchange()
        before = self.store.old_gold[1]
        result = self.reviews.convert_to_old_gold(review_id)
        self.assertEqual(result["inventory_status"], "CONVERTED_TO_OLD_GOLD")
        self.assertEqual(self.store.old_gold[1], before + D("10.000"))
        movement = self.store.old_gold_movements[-1]
        self.assertEqual((movement["code"], movement["karat_id"], movement["delta"], movement["item"]),
                         ("RETURNED_ITEM_TO_OLD_GOLD", 1, D("10.000"), "200"))
        self.assertEqual(self.store.inventory_movements[-1],
                         ("200", "RETURNED_ITEM_TO_OLD_GOLD", "RETURNED_PENDING", "CONVERTED_TO_OLD_GOLD"))
        self.assertNotEqual(self.item()["inventory_status"], "SOLD")
        self.assertEqual(self.store.inspections[0]["resolution"], "CONVERT_TO_OLD_GOLD")

    def test_f_historical_barcode_is_kept(self):
        review_id = self.exchange()
        self.reviews.convert_to_old_gold(review_id)
        self.assertEqual(self.item()["barcode_value"], "B200")

    def test_g_converted_barcode_never_resolves_as_stock(self):
        review_id = self.exchange()
        self.reviews.convert_to_old_gold(review_id)
        with self.assertRaises(ExchangeError) as refused:
            self.service.find_replacement_by_barcode("B200")
        self.assertEqual(refused.exception.code, "ITEM_UNAVAILABLE")
        found = self.service.search_replacements(karat_id=1, weight="10", tolerance="0")["items"]
        self.assertNotIn("200", [i["inventory_item_id"] for i in found])
        with self.assertRaises(ExchangeError):                       # never an exchange replacement either
            self.finalize(ex.draft(returned=("6",), replacements=(("200", None),)))


class SafetyTests(InspectionTestCase):
    def test_h_a_second_decision_is_refused(self):
        review_id = self.exchange()
        self.reviews.return_to_stock(review_id)
        for resolve in (self.reviews.return_to_stock, self.reviews.convert_to_old_gold):
            before = self.store.tables()
            with self.assertRaises(InspectionError) as refused:
                resolve(review_id)
            self.assertEqual(refused.exception.code, "REVIEW_ALREADY_RESOLVED")
            self.assertEqual(self.store.tables(), before)

    def test_i_lock_order_and_lost_race_refused_atomically(self):
        review_id = self.exchange()
        self.reviews.convert_to_old_gold(review_id)
        self.assertEqual(ReviewRepo.calls[:2], ["lock_inspection", "lock_item"])
        self.assertLess(ReviewRepo.calls.index("lock_item"), ReviewRepo.calls.index("lock_old_gold_balance"))
        # A concurrent decision that won after our lock read: the guarded UPDATE refuses, all is rolled back.
        self.store.inspections[0].update(status="OPEN", resolution=None)
        self.item()["inventory_status"] = "RETURNED_PENDING"
        original = ReviewRepo.resolve_inspection
        ReviewRepo.resolve_inspection = lambda self, **kwargs: False
        try:
            before = self.store.tables()
            with self.assertRaises(InspectionError):
                self.reviews.return_to_stock(review_id)
            self.assertEqual(self.store.tables(), before)
        finally:
            ReviewRepo.resolve_inspection = original

    def test_i_item_no_longer_pending_is_refused(self):
        review_id = self.exchange()
        self.item()["inventory_status"] = "IN_STOCK"
        with self.assertRaises(InspectionError) as refused:
            self.reviews.convert_to_old_gold(review_id)
        self.assertEqual(refused.exception.code, "ITEM_NOT_PENDING")

    def test_j_failure_rolls_everything_back(self):
        review_id = self.exchange()
        ReviewRepo.fail_on = "add_old_gold_in"
        before = self.store.tables()
        with self.assertRaises(RuntimeError):
            self.reviews.convert_to_old_gold(review_id)
        self.assertEqual(self.store.tables(), before)
        self.assertEqual((self.item()["inventory_status"], self.store.inspections[0]["status"]),
                         ("RETURNED_PENDING", "OPEN"))

    def test_k_manager_only(self):
        review_id = self.exchange()
        for permissions in (frozenset({"APP_LOGIN", "INVENTORY_RECONCILE"}), frozenset({"APP_LOGIN", "INVOICE_EXCHANGE"})):
            service = self.review_service(permissions)
            before = self.store.tables()
            with self.assertRaises(PermissionError):
                service.return_to_stock(review_id)
            with self.assertRaises(PermissionError):
                service.convert_to_old_gold(review_id)
            self.assertEqual(self.store.tables(), before)

    def test_unknown_review_is_refused(self):
        with self.assertRaises(InspectionError):
            self.reviews.return_to_stock("12345")
        with self.assertRaises(InspectionError):
            self.reviews.return_to_stock("abc")


class HistoryTests(unittest.TestCase):
    def row(self, **inspection):
        returned = {"line_role": "RETURNED", "inventory_item_id": "200", "item_name_snapshot": "خاتم",
                    "barcode_snapshot": "B200", "stored_barcode": "B200", "karat_value": 21,
                    "weight_grams": D("10.000"), "pure_weight_grams": D("8.750000"), **inspection}
        return {"lifecycle_id": "1", "source_invoice_id": "500", "source_invoice_number": "26092701",
                "occurred_at": ex.NOW, "performed_by_name": "موظف", "lines": [returned],
                "returned_pure_grams": D("8.75"), "replacement_pure_grams": D("8.75"), "pure_delta_grams": D("0"),
                "valuation_basis": "SAME_KARAT", "valued_delta_grams": D("0"), "gold_amount": D("0"),
                "making_charge": D("0"), "making_refund": D("0"), "making_refund_eligible": 0,
                "signed_amount": D("0"), "money_received": D("0"), "money_paid": D("0"), "account_signed": D("0"),
                "payments": []}

    def text(self, **inspection):
        return InvoiceHistoryViewModel._dispositions(exchange_summary(self.row(**inspection)))

    def test_l_history_shows_each_disposition(self):
        self.assertEqual(self.text(inspection_status="OPEN"), f"{INSPECTION_PENDING_TEXT} — الباركود المسجل: B200")
        restocked = self.text(inspection_status="RESOLVED", inspection_resolution="RETURN_TO_STOCK",
                              inspection_resolved_at=ex.NOW, inspection_resolved_by="المدير")
        self.assertTrue(restocked.startswith(RESTOCKED_TEXT + " (B200) — المدير"))
        converted = self.text(inspection_status="RESOLVED", inspection_resolution="CONVERT_TO_OLD_GOLD",
                              inspection_resolved_at=ex.NOW, inspection_resolved_by="المدير")
        self.assertTrue(converted.startswith(CONVERTED_TEXT + " — المدير"))
        # Exchange financials are untouched by the disposition.
        summary = exchange_summary(self.row(inspection_status="OPEN"))
        self.assertEqual((summary["signed_amount"], summary["direction"]), ("0", "NONE"))


class BackfillTests(InspectionTestCase):
    def test_m_backfill_is_idempotent_and_never_duplicates(self):
        self.exchange()
        self.finalize(ex.draft(returned=("2",), replacements=(("303", None),)))
        self.store.inspections = [self.store.inspections[0]]      # the second piece predates 021
        created = self.reviews.backfill_returned_inspections()
        self.assertEqual(len(created), 1)
        self.assertEqual([r["lifecycle_line_id"] for r in self.store.inspections], ["1", "3"])
        self.assertEqual(self.store.inspections[1]["details"]["stored_barcode"], "B201")
        self.assertEqual(self.reviews.backfill_returned_inspections(), [])
        self.assertEqual(len(self.store.inspections), 2)

    def test_m_backfill_skips_decided_pieces_and_is_manager_only(self):
        review_id = self.exchange()
        self.reviews.return_to_stock(review_id)
        self.store.inspections.clear()
        self.assertEqual(self.reviews.backfill_returned_inspections(), [])   # IN_STOCK: nothing to inspect
        with self.assertRaises(PermissionError):
            self.review_service(frozenset({"APP_LOGIN"})).backfill_returned_inspections()


if __name__ == "__main__":
    unittest.main()
