"""Legacy HR/RP history remains visible and reversible; new LOT receipts have no UI posting surface."""
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal as D
import unittest

from PySide6.QtTest import QTest

from repositories.intermediary_holding_repository import MOVE_RECEIPT, RECEIVE
from services.holding_receipt_service import SOURCE, HoldingReceiptService
from services.market_trip_service import TripError
from tests import test_holding_settlement_ui as base
from tests.market_trip_fakes import HoldingRepo, SettlementRepo, TripRepo

TODAY = "2026-10-10"
ENTITLEMENT = "GRAM_ENTITLEMENT"


class ReceiptPageTests(base.SettlementPageTests):
    """The owner can correct legacy receipts but cannot create new LOT receipt records."""

    def setUp(self):
        super().setUp()
        self.svm._holding_receipts = HoldingReceiptService(self.controller.session, transaction_factory=self.db.transaction,
                                                            repository=TripRepo, holdings=HoldingRepo, parties=SettlementRepo,
                                                            authorization=self.authorization, corrections=self.corrections)

    def allocated_pool(self):
        trip, lot, _ = self.hand_over()
        pool = str(max(self.db.holding_pools))
        sg = self.settle_service.settle("LOT", pool, "10", "1", ENTITLEMENT, False, TODAY, "", "", self.key())
        self.settle_service.reverse(sg["id"], "تصحيح")
        return trip, pool

    def seed_legacy_receipt(self, pool, grams="1"):
        pool_id = int(pool)
        weight = D(grams).quantize(D("0.001"))
        request = self.key()
        receipt_date = date.fromisoformat(TODAY)
        digest = hashlib.sha256(json.dumps(
            [SOURCE, pool_id, str(weight), receipt_date.isoformat(), None, None], ensure_ascii=False,
        ).encode()).hexdigest()
        repository = HoldingRepo(self.db)
        actor = self.controller.session.require_authenticated()
        pool_row = repository.pool(pool_id)
        receipt_id = repository.insert_receipt(pool=pool_row, grams=weight, receipt_date=receipt_date, reference=None, notes=None,
                                               key=request, digest=digest, actor=actor, now=datetime.now())
        piece_id = repository.insert_piece(receipt_id=receipt_id, pool=pool_row, grams=weight)
        repository.insert_stock_movement(piece_id=piece_id, receipt_id=receipt_id, movement_type=RECEIVE, weight=weight, delta=weight)
        repository.insert_receipt_movement(pool_id=pool_id, receipt_id=receipt_id, movement_type=MOVE_RECEIPT,
                                            weight=weight, delta=-weight)
        return {"receipt_id": str(receipt_id), "piece_id": str(piece_id)}

    def test_new_lot_receipt_has_no_posting_ui_and_service_refuses_without_writes(self):
        _, pool = self.allocated_pool()
        self.open_party()
        self.assertFalse(self.exists("holdingReceiveButton"))
        self.assertFalse(self.exists("receiptDialog"))
        self.assertFalse(self.svm.canReceiveFromHolding)
        before = (len(self.db.receipts), len(self.db.pieces), len(self.db.stock_moves), len(self.db.holding_moves))
        with self.assertRaises(TripError) as refused:
            self.svm._holding_receipts.receive(pool, "1", "1", TODAY, "", "", self.key())
        self.assertEqual(refused.exception.code, "LOT_RECEIPTS_DISABLED")
        self.assertEqual((len(self.db.receipts), len(self.db.pieces), len(self.db.stock_moves), len(self.db.holding_moves)), before)
        self.assertEqual(self.warnings, [])

    def test_legacy_receipt_history_stays_reversible_and_then_uses_the_event_reviewed_return(self):
        _, pool = self.allocated_pool()
        receipt = self.seed_legacy_receipt(pool)
        self.open_party()
        self.assertEqual(len(self.visible_texts("receiptRow")), 1)
        self.assertFalse(self.item("holdingReturnButton").property("enabled"))  # outstanding legacy receipt stock is not returned as the bar

        self.click("receiptMoreButton")
        self.click("receiptReverseButton")
        self.wait_until(lambda: self.item("reverseReceiptDialog").property("opened"), "legacy receipt correction dialog")
        self.type_in("reverseReceiptReason", "تصحيح سجل مادي")
        self.click("confirmReverseReceiptButton")
        self.wait_until(lambda: any(c["source_type"] == "HOLDING_RECEIPT" for c in self.db.corrections), "legacy receipt reversed")
        self.closed("reverseReceiptDialog")
        self.wait_idle()

        self.assertEqual((HoldingRepo(self.db).pool_balance(int(pool)),
                          HoldingRepo(self.db).piece_stock(int(receipt["piece_id"]))), (D("10.000"), D("0.000")))
        self.assertEqual(len(self.db.receipts), 1)  # receipt and RP identity remain historical
        self.assertEqual(len(self.visible_texts("receiptRow")), 0)
        self.assertIn("HR000001", " ".join(self.visible_texts("receiptHistoryRow")))
        self.assertIn("RP000001", " ".join(self.visible_texts("receiptHistoryRow")))
        self.assertTrue(self.item("holdingReturnButton").property("enabled"))
        self.click("holdingReturnButton")
        self.wait_until(lambda: self.item("holdingReturnDialog").property("opened"), "return event review")
        self.assertIn("HR000001 / RP000001", self.text("holdingReturnEventReview"))
        self.assertFalse(self.exists("holdingPhysicalEventsConfirm"))
        self.assertTrue(self.item("holdingIntactConfirm").property("visible"))
        self.assertIn("سجلات القسمة أو الاستلام المادي المعروضة", self.text("holdingIntactText"))
        self.click("cancelHoldingReturnButton")
        self.closed("holdingReturnDialog")
        self.assertEqual(self.warnings, [])

    def test_legacy_receipt_reversal_action_follows_permissions_and_service_refuses_without_them(self):
        _, pool = self.allocated_pool()
        receipt = self.seed_legacy_receipt(pool)
        self.open_party()
        self.assertTrue(self.item("receiptMoreButton").property("visible"))

        self.grant(revoke=("INTERMEDIARY_HOLDING_REVERSE",))
        self.svm.refresh()
        self.wait_idle()
        self.assertFalse(self.item("receiptMoreButton").property("visible"))
        before = (len(self.db.corrections), HoldingRepo(self.db).pool_balance(int(pool)),
                  HoldingRepo(self.db).piece_stock(int(receipt["piece_id"])))
        with self.assertRaises(PermissionError):
            self.svm._holding_receipts.reverse(receipt["receipt_id"], "تصحيح مادي")
        self.assertEqual((len(self.db.corrections), HoldingRepo(self.db).pool_balance(int(pool)),
                          HoldingRepo(self.db).piece_stock(int(receipt["piece_id"]))), before)
        self.assertEqual(self.warnings, [])

    def test_receipt_reversal_dialog_explains_stock_and_holding_balance_effects(self):
        _, pool = self.allocated_pool()
        self.seed_legacy_receipt(pool)
        self.open_party()
        self.click("receiptMoreButton")
        self.click("receiptReverseButton")
        self.wait_until(lambda: self.item("reverseReceiptDialog").property("opened"), "legacy receipt correction dialog")
        note = self.text("reverseReceiptNote")
        self.assertIn("مخزون الاستلام", note)
        self.assertIn("تخرج القطعة المستلمة", note)
        self.assertIn("تعود 1.000 جرام إلى رصيد المسوقجي", note)
        self.assertIn("أثر التصحيح على الأرصدة", note)
        self.assertEqual(self.warnings, [])


def load_tests(loader, tests, pattern):
    """Run only this module's page-specific checks; inherited settlement tests run in their own module."""
    suite = unittest.TestSuite()
    suite.addTests(ReceiptPageTests(name) for name in loader.getTestCaseNames(ReceiptPageTests)
                   if name in ReceiptPageTests.__dict__)
    return suite


if __name__ == "__main__":
    unittest.main()
