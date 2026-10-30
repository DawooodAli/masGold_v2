"""Gold adjustment of a making group of a POSTED supplier invoice (092): real SupplierPurchaseService and CorrectionService over the in-memory
database of tests/supplier_fakes.py. The SQL (table, CHECKs, locks) is covered by the static schema test, not here."""
from decimal import Decimal as D
import unittest

from services.supplier_rules import SupplierRuleError
from tests import test_supplier_payments as base
from tests.supplier_fakes import OWNER_RIGHTS
from tests.test_supplier_invoice_reversal import InvoiceReversalCase, ReversalRepo

ADJUST = "SUPPLIER_INVOICE_ADJUST"


class AdjustCase(InvoiceReversalCase):
    def setUp(self):
        super().setUp()
        self.service = self.with_rights(list(OWNER_RIGHTS) + [ADJUST])
        self.group = next(iter(self.db.groups))
        self.n = 700

    def with_rights(self, permissions):
        session = base.session_for(permissions, user_id=8)
        authorization = base.FakeAuthorization(session)
        return self.purchases.__class__(session, transaction_factory=self.db.transaction, repository=ReversalRepo,
                                        barcodes=self.purchases._barcodes, inventory=self.purchases._inventory,
                                        authorization=authorization, corrections=self.correction_service(session, authorization))

    def adjust(self, direction, weight, reason="غلط في وزن المورد", key=None, service=None):
        self.n += 1
        return (service or self.service).adjust_group(self.invoice, self.group, direction, weight, reason, key or base.key(self.n))


class AdjustTests(AdjustCase):
    def test_adding_gold_increases_what_we_owe_and_deducting_decreases_it(self):
        before = self.gold()
        self.adjust("ADD", "0.9")
        self.assertEqual(self.gold(), before + D("0.900"))
        self.adjust("DEDUCT", "0.4")
        self.assertEqual(self.gold(), before + D("0.500"))
        self.assertEqual(self.making(), D("5000.00"))                      # making is untouched
        detail = self.purchases.invoice_detail(self.invoice)
        group = detail["groups"][0]
        self.assertEqual((group["adjustment"], group["effective_weight"]), (D("0.500"), group["supplier_weight"] + D("0.500")))

    def test_a_deduction_cannot_exceed_the_group_weight(self):
        with self.assertRaises(SupplierRuleError) as caught:
            self.adjust("DEDUCT", "999")
        self.assertEqual(caught.exception.code, "ADJUSTMENT_EXCEEDS_GROUP")

    def test_reason_and_right_are_required(self):
        with self.assertRaises(SupplierRuleError):
            self.adjust("ADD", "1", reason="  ")
        weak = self.with_rights(list(OWNER_RIGHTS))
        with self.assertRaises(Exception):
            self.adjust("ADD", "1", service=weak)

    def test_an_exact_replay_posts_once(self):
        key = base.key(999)
        first = self.adjust("ADD", "1", key=key)
        again = self.adjust("ADD", "1", key=key)
        self.assertTrue(again["duplicate"])
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(len(self.db.adjustments), 1)

    def test_a_reversed_invoice_cannot_be_adjusted(self):
        self.reverse()
        with self.assertRaises(SupplierRuleError) as caught:
            self.adjust("ADD", "1")
        self.assertEqual(caught.exception.code, "INVOICE_REVERSED")

    def test_the_invoice_cannot_be_reversed_until_the_adjustments_net_to_zero(self):
        self.adjust("ADD", "1")
        preview = self.purchases.reversal_preview(self.invoice)
        self.assertFalse(preview["can_reverse"])
        self.assertIn("INVOICE_HAS_ADJUSTMENTS", [b[0] if isinstance(b, (tuple, list)) else b.get("code") for b in preview["blockers"]])
        self.adjust("DEDUCT", "1")
        self.assertTrue(self.purchases.reversal_preview(self.invoice)["can_reverse"])


if __name__ == "__main__":
    unittest.main()
