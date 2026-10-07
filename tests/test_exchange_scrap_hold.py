"""Exchange returned piece → Scrap (032, owner-approved lifecycle).

At Exchange finalize the returned identified piece leaves sellable stock and its exact physical weight enters
the COUNTED Scrap ledger of its own karat, HELD for one manager decision:
  إرجاع من الكسر إلى المخزون → the held weight leaves Scrap; the SAME item / barcode is IN_STOCK again;
  تثبيت في الكسر           → no movement; the held weight becomes ordinary available Scrap.
Every Scrap consumer sees AVAILABLE grams (physical − held). Exchange reversal: only a HELD piece reverses.

Runs on the in-memory stores of tests/test_exchange_service.py, tests/test_returned_item_inspection.py and
tests/test_correction_service.py (no database; each transaction is rolled back on any exception).
"""
import copy
from decimal import Decimal as D
import unittest

from services.exchange_service import ExchangeDraft, ExchangeError
from services.management_review_service import InspectionError, with_scrap_story
from tests import test_correction_service as cx
from tests import test_exchange_service as ex
from tests import test_returned_item_inspection as ix


class ScrapHoldTestCase(ix.InspectionTestCase):
    def exchange(self):
        """Returned: invoice line 1 = item 200 (21K 10.000 g, barcode B200); replacement item 300."""
        self.physical_before = self.store.old_gold[1]
        self.finalize(ex.draft())
        return self.store.inspections[0]["review_id"]

    def hold(self):
        (hold,) = self.store.holds
        return hold

    def available(self, karat_id=1):
        return self.store.old_gold[karat_id] - self.store.held(karat_id)

    def scrap_codes(self):
        return [m["code"] for m in self.store.old_gold_movements]


# ----------------------------------------------------------------------------- 28. direct Scrap posting
class DirectScrapPostingTests(ScrapHoldTestCase):
    def test_finalize_moves_the_piece_into_scrap_atomically(self):
        self.exchange()
        item = self.store.items["200"]
        # Inventory: out of sellable stock (not IN_STOCK, not SOLD), barcode and identity unchanged.
        self.assertEqual((item["inventory_status"], item["barcode_value"]), ("CONVERTED_TO_OLD_GOLD", "B200"))
        with self.assertRaises(ExchangeError):
            self.service.find_replacement_by_barcode("B200")
        found = self.service.search_replacements(karat_id=1, weight="10", tolerance="0")["items"]
        self.assertNotIn("200", [i["inventory_item_id"] for i in found])
        # Scrap: +10.000 g 21K physical, its actual karat and weight (never 24K / pure normalised).
        self.assertEqual(self.store.old_gold[1] - self.physical_before, D("10.000"))
        (movement,) = self.store.old_gold_movements
        self.assertEqual((movement["code"], movement["karat_id"], movement["delta"], movement["item"],
                          movement["invoice"], movement["reference"]),
                         ("EXCHANGE_RETURN_SCRAP_IN", 1, D("10.000"), "200", "500", ("INVOICE_LIFECYCLE", "1")))
        # Held: the weight is in Scrap but not consumable; exactly one review and one hold, linked.
        hold = self.hold()
        self.assertEqual((hold["review"], hold["line"], hold["item"], hold["karat_id"], hold["weight"], hold["in"],
                          hold["status"]), ("901", "1", "200", 1, D("10.000"), "1", "HELD"))
        self.assertEqual(self.available(), self.physical_before)
        self.assertEqual([r["status"] for r in self.store.inspections], ["OPEN"])
        self.assertEqual(self.store.events[0]["details"]["scrap_hold_ids"], ["1"])

    def test_actual_physical_weight_is_posted(self):
        self.store.items["200"]["weight_grams"] = D("9.876")       # the piece's own weight, not a snapshot
        self.exchange()
        self.assertEqual((self.store.old_gold_movements[0]["delta"], self.hold()["weight"]), (D("9.876"), D("9.876")))

    def test_any_failure_leaves_no_partial_exchange(self):
        for step in ("add_returned_scrap_in", "insert_scrap_hold", "open_returned_item_inspection"):
            with self.subTest(step=step):
                ex.LifecycleRepo.fail_on = step
                before = self.store.tables()
                with self.assertRaises(RuntimeError):
                    self.finalize(ex.draft())
                self.assertEqual(self.store.tables(), before)
        ex.LifecycleRepo.fail_on = None

    def test_read_model_shows_where_the_piece_is_and_its_history(self):
        self.exchange()
        row = self.reviews.open_reviews()["RETURNED_ITEM_INSPECTION"][0]
        self.assertEqual((row["scrap_destination"], row["scrap_state_text"]),
                         ("رصيد الكسر — عيار 21", "معلّقة لقرار المدير"))
        self.assertEqual([e["title"] for e in row["timeline"]], ["عملية استبدال", "تم تحويل القطعة إلى الكسر"])
        self.assertIn("EX000001", row["timeline"][0]["detail"])
        self.assertIn("10.000 جرام", row["timeline"][1]["detail"])


# ----------------------------------------------------------------------------- 29. return from Scrap to stock
class ReturnFromScrapTests(ScrapHoldTestCase):
    def test_same_item_returns_and_the_exact_weight_leaves_scrap(self):
        review_id = self.exchange()
        available = self.available()
        result = self.reviews.return_to_stock(review_id)
        item = self.store.items["200"]
        self.assertEqual((item["inventory_status"], item["barcode_value"], item["weight_grams"]),
                         ("IN_STOCK", "B200", D("10.000")))
        self.assertEqual(self.store.old_gold[1], self.physical_before)              # +10 then −10
        self.assertEqual(self.available(), available)                              # available untouched
        self.assertEqual(self.scrap_codes(), ["EXCHANGE_RETURN_SCRAP_IN", "EXCHANGE_RETURN_TO_STOCK_OUT"])
        self.assertEqual(self.store.old_gold_movements[-1]["delta"], D("-10.000"))
        self.assertEqual(self.store.inventory_movements[-1],
                         ("200", "RETURNED_ITEM_RESTOCKED", "CONVERTED_TO_OLD_GOLD", "IN_STOCK"))
        hold = self.hold()
        self.assertEqual((hold["status"], hold["out"], hold["resolved_by"]), ("RETURNED_TO_STOCK", "2", 3))
        self.assertEqual(self.store.inspections[0]["status"], "RESOLVED")
        self.assertEqual((result["hold_status"], result["inventory_status"]), ("RETURNED_TO_STOCK", "IN_STOCK"))
        self.assertEqual(self.service.find_replacement_by_barcode("B200")["inventory_item_id"], "200")  # sellable
        self.assertEqual(self.store.audit[-1][1], "RETURNED_ITEM_RETURN_TO_STOCK")

    def test_timeline_records_the_return(self):
        row = with_scrap_story({"hold_id": "1", "hold_status": "RETURNED_TO_STOCK", "karat_value": 21,
                                "hold_weight_grams": D("10.000"), "exchange_code": "EX000001",
                                "resolved_by_name": "المدير", "resolved_at": ex.NOW})
        self.assertEqual(row["timeline"][-1]["title"], "تمت إعادة القطعة من الكسر إلى المخزون")
        self.assertEqual(row["timeline"][-1]["by"], "المدير")


# ----------------------------------------------------------------------------- 30. permanent Scrap
class CommitToScrapTests(ScrapHoldTestCase):
    def test_commit_releases_the_hold_without_a_second_credit(self):
        review_id = self.exchange()
        physical = self.store.old_gold[1]
        self.reviews.convert_to_old_gold(review_id)
        self.assertEqual(self.store.old_gold[1], physical)                           # no +10 again
        self.assertEqual(self.scrap_codes(), ["EXCHANGE_RETURN_SCRAP_IN"])           # no new movement
        self.assertEqual(self.available(), physical)                                 # held → available
        self.assertEqual(self.store.items["200"]["inventory_status"], "CONVERTED_TO_OLD_GOLD")
        self.assertEqual(self.store.items["200"]["barcode_value"], "B200")          # historical identity
        self.assertEqual((self.hold()["status"], self.hold()["out"]), ("COMMITTED", None))
        self.assertEqual((self.store.inspections[0]["status"], self.store.inspections[0]["resolution"]),
                         ("RESOLVED", "CONVERT_TO_OLD_GOLD"))
        with self.assertRaises(ExchangeError):
            self.service.find_replacement_by_barcode("B200")


# ----------------------------------------------------------------------------- 31. exactly once
class ExactlyOnceTests(ScrapHoldTestCase):
    def test_a_second_decision_is_refused_in_either_order(self):
        for first, second in (("return_to_stock", "convert_to_old_gold"), ("convert_to_old_gold", "return_to_stock"),
                              ("return_to_stock", "return_to_stock"), ("convert_to_old_gold", "convert_to_old_gold")):
            with self.subTest(first=first, second=second):
                self.setUp()
                review_id = self.exchange()
                getattr(self.reviews, first)(review_id)
                before = self.store.tables()
                with self.assertRaises(InspectionError) as refused:
                    getattr(self.reviews, second)(review_id)
                self.assertEqual(refused.exception.code, "REVIEW_ALREADY_RESOLVED")
                self.assertEqual(self.store.tables(), before)                  # no movement, no restore

    def test_a_lost_race_on_the_hold_rolls_everything_back(self):
        review_id = self.exchange()
        original = ix.ReviewRepo.close_hold
        ix.ReviewRepo.close_hold = lambda self, **kwargs: False                  # another manager won
        try:
            before = self.store.tables()
            with self.assertRaises(InspectionError):
                self.reviews.return_to_stock(review_id)
            self.assertEqual(self.store.tables(), before)
        finally:
            ix.ReviewRepo.close_hold = original

    def test_failure_after_the_scrap_out_rolls_back(self):
        review_id = self.exchange()
        ix.ReviewRepo.fail_on = "add_inventory_movement"
        before = self.store.tables()
        with self.assertRaises(RuntimeError):
            self.reviews.return_to_stock(review_id)
        self.assertEqual(self.store.tables(), before)

    def test_lock_order_is_review_item_ledger_hold(self):
        review_id = self.exchange()
        self.reviews.return_to_stock(review_id)
        calls = ix.ReviewRepo.calls
        self.assertEqual([c for c in calls if c in ("lock_inspection", "lock_item", "lock_old_gold_balance", "lock_hold")],
                         ["lock_inspection", "lock_item", "lock_old_gold_balance", "lock_hold"])

    def test_manager_permission_is_rechecked_by_the_service(self):
        review_id = self.exchange()
        for permissions in (frozenset({"APP_LOGIN", "INVENTORY_RECONCILE"}), frozenset({"APP_LOGIN", "INVOICE_EXCHANGE"})):
            service = self.review_service(permissions)
            before = self.store.tables()
            for decide in (service.return_to_stock, service.convert_to_old_gold):
                with self.assertRaises(PermissionError):
                    decide(review_id)
            self.assertEqual(self.store.tables(), before)


# ----------------------------------------------------------------------------- 32. held Scrap is not consumable
class HeldScrapIsNotConsumableTests(ScrapHoldTestCase):
    def test_physical_10_held_5_means_available_5(self):
        """Physical 10.000 g 21K of which 5.000 g HELD (an Exchange piece): the displayed physical balance stays
        10.000, every consumer sees 5.000, and taking more than 5.000 is refused."""
        self.store.old_gold[1] = D("5.000")
        next(l for l in self.store.lines["500"] if l["line_id"] == "6")["making_total"] = D("500.00")
        draft = ExchangeDraft("500", ("6",), (ex.ReplacementInput("306", "0"),))   # returns item 202, 21K 5.000 g
        amount = self.service.preview(draft)["amount"]
        self.finalize(ExchangeDraft("500", ("6",), (ex.ReplacementInput("306", "0"),),
                                    (ex.cash(amount),) if amount != "0.00" else ()))
        self.assertEqual((self.store.old_gold[1], self.store.held(1), self.available()),
                         (D("10.000"), D("5.000"), D("5.000")))
        balances = {r["karat_id"]: r["balance_grams"] for r in ex.InvoiceRepo(ex.Connection(self.store)).old_gold_balances()}
        self.assertEqual(balances[1], D("5.000"))
        too_much = ExchangeDraft("501", ("7",), (ex.from_old_gold("5.001"),))
        self.assertIn("OLD_GOLD_STOCK_INSUFFICIENT",
                      [p["code"] for p in self.service.preview(too_much)["blocking_problems"]])
        self.assertRefused("OLD_GOLD_STOCK_INSUFFICIENT", too_much)
        # After تثبيت في الكسر the same 5.000 g are ordinary Scrap: 10.000 available.
        self.reviews.convert_to_old_gold(self.store.inspections[0]["review_id"])
        self.assertEqual(self.available(), D("10.000"))


# ----------------------------------------------------------------------------- amendment 1: reversal per hold state
class ReversalByHoldStateTests(cx.CorrectionTestCase):
    """Exchange 900 returned item 202 (21K 10.000 g). After 032 that piece is in Scrap with a hold."""

    def held(self, status, review_status="OPEN"):
        self.store.items["202"]["inventory_status"] = "IN_STOCK" if status == "RETURNED_TO_STOCK" else "CONVERTED_TO_OLD_GOLD"
        self.store.old_gold.append((1, D("10.000"), "EXCHANGE_RETURN_SCRAP_IN"))
        if status == "RETURNED_TO_STOCK":
            self.store.old_gold.append((1, D("-10.000"), "EXCHANGE_RETURN_TO_STOCK_OUT"))
        self.store.holds = [{"hold_id": "1", "line": "9001", "item": "202", "karat_id": 1, "weight": D("10.000"),
                             "status": status, "out": None}]
        self.store.reviews[0].update(status=review_status,
                                     code=None if review_status == "OPEN" else
                                     ("RETURN_TO_STOCK" if status == "RETURNED_TO_STOCK" else "CONVERT_TO_OLD_GOLD"))

    def test_held_piece_reverses_with_its_exact_weight(self):
        self.held("HELD")
        physical = self.store.old_gold_balance()
        self.service.reverse("EXCHANGE", "900", "تم تنفيذ الاستبدال بالقطعة الخطأ")
        self.assertEqual(self.store.items["202"]["inventory_status"], "SOLD")              # back to the customer
        self.assertIn(("202", "REVERSAL_EXCHANGE_RETURN_TO_SOLD", "CONVERTED_TO_OLD_GOLD", "SOLD"),
                      self.store.inv_movements)
        scrap_out = [g for g in self.store.old_gold if g[2] == "REVERSAL_EXCHANGE_RETURN_SCRAP_OUT"]
        self.assertEqual(scrap_out, [(1, D("-10.000"), "REVERSAL_EXCHANGE_RETURN_SCRAP_OUT")])
        self.assertEqual(self.store.old_gold_balance() - physical, D("-10.000") + D("2.000"))  # piece out, F7 back
        self.assertEqual((self.store.holds[0]["status"], self.store.holds[0]["out"]),
                         ("REVERSED", str(self.store.old_gold.index(scrap_out[0]) + 1)))
        self.assertEqual(self.store.reviews[0]["status"], "DISMISSED")

    def test_held_reversal_is_not_blocked_by_its_own_reservation(self):
        self.held("HELD")
        self.store.old_gold[0] = (1, D("0.000"), "OPENING_BALANCE")           # only the held piece in 21K Scrap
        self.service.reverse("EXCHANGE", "900", "خطأ")
        self.assertEqual(self.store.holds[0]["status"], "REVERSED")

    def test_committed_piece_blocks_the_reversal(self):
        self.held("COMMITTED", review_status="RESOLVED")
        error = self.assertRefused("RETURNED_ITEM_COMMITTED_TO_SCRAP",
                                   lambda: self.service.reverse("EXCHANGE", "900", "خطأ"))
        self.assertIn("ثُبّتت في الكسر", str(error))
        self.assertEqual(self.store.holds[0]["status"], "COMMITTED")

    def test_restocked_piece_blocks_the_reversal(self):
        self.held("RETURNED_TO_STOCK", review_status="RESOLVED")
        error = self.assertRefused("RETURNED_ITEM_RESTOCKED", lambda: self.service.reverse("EXCHANGE", "900", "خطأ"))
        self.assertIn("أعاد القطعة", str(error))
        self.assertEqual(self.store.items["202"]["inventory_status"], "IN_STOCK")       # never restored twice

    def test_a_reversed_hold_cannot_be_reversed_again(self):
        self.held("HELD")
        self.service.reverse("EXCHANGE", "900", "خطأ")
        self.assertRefused("ALREADY_REVERSED", lambda: self.service.reverse("EXCHANGE", "900", "خطأ"))
        self.assertEqual(len([g for g in self.store.old_gold if g[2] == "REVERSAL_EXCHANGE_RETURN_SCRAP_OUT"]), 1)


# ----------------------------------------------------------------------------- 33. Manager Reviews UI
class ScrapPieceViewModelTests(ScrapHoldTestCase):
    def test_rows_split_scrap_pieces_from_legacy_inspections(self):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.instance() or QGuiApplication([])
        from app.viewmodels.management_review_viewmodel import ManagementReviewViewModel
        self.exchange()
        vm = ManagementReviewViewModel(self.reviews._session, service=self.reviews)
        vm._reviews = self.reviews.open_reviews()
        (row,) = vm.scrapPieces
        self.assertEqual(vm.inspections, [])
        self.assertEqual((row["inScrap"], row["barcode"], row["itemId"], row["exchange"], row["invoice"], row["karat"],
                          row["weight"], row["scrapDestination"], row["scrapState"]),
                         (True, "B200", "200", "EX000001", "26092701", "21", "10.000", "رصيد الكسر — عيار 21",
                          "معلّقة لقرار المدير"))
        self.assertEqual([e["title"] for e in row["timeline"]], ["عملية استبدال", "تم تحويل القطعة إلى الكسر"])


if __name__ == "__main__":
    unittest.main()
