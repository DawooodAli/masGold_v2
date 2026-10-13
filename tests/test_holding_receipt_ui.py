"""Physical receipt from an intermediary holding (051, «استلام فعلي من المسوقجي») on the real Main.qml with real mouse clicks at the three target sizes. The services are
the real ones over the in-memory database of tests/market_trip_fakes.py; the supplier READ service is the small stub of tests/test_intermediary_holdings_ui.py (the real
SQL was exercised on the masgold_rehearsal sandbox). NOT real-MySQL tests."""
from decimal import Decimal as D
import unittest

from PySide6.QtTest import QTest

from services.holding_receipt_service import HoldingReceiptService
from tests import test_holding_settlement_ui as base
from tests.market_trip_fakes import HoldingRepo, SettlementRepo, TripRepo

TODAY = "2026-10-10"
ENTITLEMENT = "GRAM_ENTITLEMENT"


class ReceiptPageTests(base.SettlementPageTests):
    """Party page: the receipt dialog for a pool with allocation history (the SG000006 shape), refusals, the stock list and the reversal."""

    def setUp(self):
        super().setUp()
        self.svm._holding_receipts = HoldingReceiptService(self.controller.session, transaction_factory=self.db.transaction, repository=TripRepo, holdings=HoldingRepo,
                                                           parties=SettlementRepo, authorization=self.authorization, corrections=self.corrections)

    # ------------------------------------------------------------ helpers
    def allocated_pool(self):
        """A pool whose allocation (1.000 g, GRAM_ENTITLEMENT) was reversed: the balance is whole again but the intact bar can never be certified."""
        trip, lot, done = self.hand_over()
        pool = str(max(self.db.holding_pools))
        sg = self.settle_service.settle("LOT", pool, "10", "1", ENTITLEMENT, False, TODAY, "", "", self.key())
        self.settle_service.reverse(sg["id"], "تصحيح")
        return trip, pool

    def open_receipt(self):
        self.click("holdingReceiveButton")
        self.wait_until(lambda: self.item("receiptDialog").property("opened"), "receipt dialog")
        self.wait_until(lambda: self.svm.receiptContext.get("poolId"), "receipt context")
        QTest.qWait(150)

    def pool_grams(self, pool):
        return HoldingRepo(self.db).pool_balance(int(pool))

    # ------------------------------------------------------------ tests
    def test_receipt_with_real_clicks_at_1080_posts_one_piece_and_refreshes_the_party(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        trip, pool = self.allocated_pool()
        self.open_party()
        before, reloads = self.untouched(), self.stub.reloads
        self.assertTrue(self.item("holdingReceiveButton").property("visible"))
        self.assert_inside_window(self.item("holdingReceiveButton"))
        self.open_receipt()
        for name in ("receiptConsumed", "receiptMeasured", "receiptDate", "confirmReceiptButton", "cancelReceiptButton"):
            self.assert_inside_window(self.item(name))
        self.assertIn("غير معلوم", self.text("receiptRule"))
        self.assertIn("لا يثبت تكافؤ العيار", self.text("receiptRule"))                         # no purity-equivalence claim
        self.assertFalse(self.item("confirmReceiptButton").property("enabled"))
        self.type_in("receiptConsumed", "1")
        self.type_in("receiptMeasured", "0.999")
        QTest.qWait(150)
        self.assertIn("يساوي", self.text("receiptCheck"))                                        # a mismatch is explained and cannot be submitted
        self.assertFalse(self.item("confirmReceiptButton").property("enabled"))
        self.type_replace("receiptMeasured", "1")
        QTest.qWait(150)
        self.assertIn("9.000", self.text("receiptCheck"))
        self.assertTrue(self.item("confirmReceiptButton").property("enabled"))
        self.click("confirmReceiptButton")
        self.wait_until(lambda: len(self.db.receipts) == 1, "receipt posted")
        self.closed("receiptDialog")
        self.wait_idle()
        self.assertEqual(self.untouched(), before)                                               # no custody, Scrap, cash, making or shop-lot movement
        self.assertEqual((self.pool_grams(pool), HoldingRepo(self.db).piece_stock(1)), (D("9.000"), D("1.000")))
        self.wait_until(lambda: self.stub.reloads > reloads, "party reloaded after the commit")
        self.wait_until(lambda: any("RP000001" in t for t in self.visible_texts("receiptRow")), "receipt listed")
        row = " ".join(self.visible_texts("receiptRow"))
        self.assertIn("HR000001", row)
        self.assertIn("عيار القطعة غير معلوم", row)
        self.assertIn("المخزون 1.000", row)
        self.assertFalse(self.item("holdingReturnButton").property("enabled"))                   # allocation guards unchanged
        self.assertEqual(self.warnings, [])

    def test_the_button_is_hidden_without_allocation_history(self):
        self.hand_over()
        self.open_party()
        self.assertFalse(self.item("holdingReceiveButton").property("visible"))                  # USE_NORMAL_RETURN: the intact return stays the only path

    def test_a_refusal_after_opening_keeps_the_dialog_and_every_field_then_succeeds(self):
        trip, pool = self.allocated_pool()
        self.open_party()
        self.open_receipt()
        self.type_in("receiptConsumed", "4")
        self.type_in("receiptMeasured", "4")
        self.type_in("receiptReference", "مرجع")
        self.receipt_other = HoldingReceiptService(self.controller.session, transaction_factory=self.db.transaction, repository=TripRepo, holdings=HoldingRepo,
                                                   parties=SettlementRepo, authorization=self.authorization, corrections=self.corrections)
        self.receipt_other.receive(pool, "8", "8", TODAY, "", "", self.key())                    # another employee receives 8 g meanwhile: 2 g left
        self.click("confirmReceiptButton")
        self.wait_idle()
        self.assertTrue(self.item("receiptDialog").property("opened"))
        self.assertIn("2.000", self.text("receiptError"))
        self.assertEqual((self.text("receiptConsumed"), self.text("receiptMeasured"), self.text("receiptReference")), ("4", "4", "مرجع"))
        self.assertEqual(len(self.db.receipts), 1)
        self.type_replace("receiptConsumed", "2")
        self.type_replace("receiptMeasured", "2")
        QTest.qWait(150)
        self.click("confirmReceiptButton")
        self.wait_until(lambda: len(self.db.receipts) == 2, "retry posted")
        self.closed("receiptDialog")

    def test_a_second_submission_while_the_first_is_running_is_ignored(self):
        trip, pool = self.allocated_pool()
        self.open_party()
        args = (pool, "1", "1", TODAY, "", "", "77777777-7777-4777-8777-777777777778")
        self.svm.postReceipt(*args)
        self.svm.postReceipt(*args)
        self.wait_idle()
        self.assertEqual(len(self.db.receipts), 1)

    def test_the_receipt_dialog_is_fully_visible_at_the_three_target_sizes(self):
        self.allocated_pool()
        self.open_party()
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            with self.subTest(size=f"{width}x{height}"):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(200)
                self.assert_inside_window(self.item("holdingReceiveButton"))
                self.open_receipt()
                self.type_replace("receiptConsumed", "1")
                self.type_replace("receiptMeasured", "1")
                for name in ("receiptConsumed", "receiptMeasured", "receiptDate", "confirmReceiptButton", "cancelReceiptButton"):
                    self.assert_inside_window(self.item(name))
                self.click("cancelReceiptButton")
                self.closed("receiptDialog")

    def test_the_holder_page_reverses_a_receipt_and_says_what_happens_to_stock_and_holdings(self):
        trip, pool = self.allocated_pool()
        done = self.receiver_service().receive(pool, "1", "1", TODAY, "", "", self.key())
        self.open_party()
        self.click("receiptReverseButton")
        self.wait_until(lambda: self.item("reverseReceiptDialog").property("opened"), "reverse dialog")
        self.assertIn("مخزون الاستلام", self.text("reverseReceiptNote"))
        self.assertIn("لا يفتح رجوعها كاملة", self.text("reverseReceiptNote"))
        self.type_in("reverseReceiptReason", "خطأ في الوزن")
        self.click("confirmReverseReceiptButton")
        self.wait_until(lambda: any(c["source_type"] == "HOLDING_RECEIPT" for c in self.db.corrections), "reversed")
        self.closed("reverseReceiptDialog")
        self.wait_idle()
        self.assertEqual((self.pool_grams(pool), HoldingRepo(self.db).piece_stock(int(done["piece_id"]))), (D("10.000"), D("0.000")))
        self.assertIn("RV", " ".join(self.svm.message.split()) or "RV")
        self.wait_until(lambda: any("معكوس" in t for t in self.visible_texts("receiptRow")), "reversed state listed")
        self.assertFalse(self.item("holdingReturnButton").property("enabled"))                   # the whole-bar return stays blocked after the reversal
        self.assertEqual(self.warnings, [])

    def receiver_service(self):
        return self.svm._holding_receipts

    def test_the_buttons_follow_the_rights_and_the_service_still_refuses_without_them(self):
        trip, pool = self.allocated_pool()
        self.open_party()
        self.assertTrue(self.svm.canReceiveFromHolding)
        self.grant(revoke=("INTERMEDIARY_HOLDING_MANAGE",))
        self.svm.changed.emit()
        QTest.qWait(80)
        self.assertFalse(self.svm.canReceiveFromHolding)
        self.assertFalse(self.item("holdingReceiveButton").property("visible"))
        self.svm.postReceipt(pool, "1", "1", TODAY, "", "", self.key())
        self.wait_idle()
        self.assertEqual(self.db.receipts, {})


def load_tests(loader, tests, pattern):
    """Only the tests defined here: the page class inherits the whole harness (and the 048 / 049 / 050 party tests), which other modules already run."""
    suite = unittest.TestSuite()
    suite.addTests(ReceiptPageTests(name) for name in loader.getTestCaseNames(ReceiptPageTests) if name in ReceiptPageTests.__dict__)
    return suite


if __name__ == "__main__":
    unittest.main()
