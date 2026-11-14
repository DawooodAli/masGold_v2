"""The owner's private account (migration 090): transfers between a bank / wallet account and the private account, its derived balance,
reversal, and who may use it. Service rules over the in-memory financial fixture (no application database)."""
import unittest
from decimal import Decimal as D
from uuid import uuid4

from services.correction_service import FO
from tests.test_financial_accounts import FinancialTestCase, OWNER, SALESPERSON

PRIVATE_OWNER = OWNER | {"PRIVATE_ACCOUNT_MANAGE"}


class PrivateAccountTests(FinancialTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.fin_service(PRIVATE_OWNER)

    def test_a_transfer_to_the_private_account_leaves_the_bank_and_raises_the_private_balance(self):
        before, cash = self.bank(), self.cash()
        result = self.owner.private_transfer("BANK:7", "TO", "5000", "سحب للمالك", str(uuid4()))
        self.assertEqual(self.bank(), before - D("5000"))
        self.assertEqual(self.cash(), cash)                                          # the safe is untouched
        self.assertEqual(self.store.ops[result["operation_id"]]["category"], "PRIVATE_OUT")
        view = self.owner.private_account()
        self.assertEqual((view["balance"], view["total_in"], view["total_out"]), ("5000.00", "5000.00", "0.00"))
        self.assertEqual((view["entries"][0]["text"], view["entries"][0]["account"]), ("إلى الحساب الخاص", "الأهلي"))

    def test_a_transfer_from_the_private_account_returns_to_the_bank_and_lowers_the_balance(self):
        self.owner.private_transfer("BANK:7", "TO", "5000", "", str(uuid4()))
        before = self.bank()
        self.owner.private_transfer("BANK:7", "FROM", "1200.50", "", str(uuid4()))
        self.assertEqual(self.bank(), before + D("1200.50"))
        view = self.owner.private_account()
        self.assertEqual((view["balance"], view["total_out"]), ("3799.50", "1200.50"))
        self.assertEqual([e["balance"] for e in view["entries"]], ["3799.50", "5000.00"])       # newest first, running balance kept

    def test_the_private_account_accepts_banks_and_wallets_only(self):
        self.assertFinRefused("ACCOUNT_INVALID", lambda: self.owner.private_transfer("CASH:1", "TO", "100", "", str(uuid4())))
        self.assertFinRefused("TYPE_INVALID", lambda: self.owner.private_transfer("BANK:7", "SIDEWAYS", "100", "", str(uuid4())))

    def test_an_exact_replay_posts_once(self):
        key = str(uuid4())
        first = self.owner.private_transfer("BANK:7", "TO", "700", "", key)
        before = self.bank()
        again = self.owner.private_transfer("BANK:7", "TO", "700", "", key)
        self.assertTrue(again["duplicate"] and again["operation_id"] == first["operation_id"])
        self.assertEqual((self.bank(), self.owner.private_account()["balance"]), (before, "700.00"))

    def test_a_reversed_transfer_stops_counting_but_stays_listed(self):
        result = self.owner.private_transfer("BANK:7", "TO", "900", "", str(uuid4()))
        self.make(PRIVATE_OWNER).reverse(FO, result["operation_id"], "خطأ في الإدخال")
        view = self.owner.private_account()
        self.assertEqual((view["balance"], view["entries"][0]["voided"]), ("0.00", True))

    def custody_entry(self, entry_id, amount, trip_id=12, entry_no=3):
        self.store.__dict__.setdefault("private_entries_list", []).append(
            {"id": f"c{entry_id}", "direction": "TO_PRIVATE", "amount": D(amount), "operation_id": None, "trip_entry_id": str(entry_id), "trip_id": trip_id, "entry_no": entry_no,
             "note": "من العهدة", "created_at": None})

    def test_cash_taken_from_a_market_custody_counts_and_is_listed_with_its_custody_entry(self):
        """Migration 105: an entry may come from a custody cash transfer (no bank / wallet operation): it is listed under the custody entry's code and counts in the balance."""
        self.owner.private_transfer("BANK:7", "TO", "1000", "", str(uuid4()))
        self.custody_entry(40, "750.00")
        view = self.owner.private_account()
        self.assertEqual((view["balance"], view["total_in"], view["total_out"]), ("1750.00", "1750.00", "0.00"))
        newest = view["entries"][0]
        self.assertEqual((newest["operation"], newest["account"], newest["voided"], newest["fromCustody"]), ("MT000012-3", "نقدية عهدة السوق", False, True))
        self.assertEqual((view["entries"][1]["account"], view["entries"][1]["fromCustody"]), ("الأهلي", False))     # the bank transfer reads as before

    def test_reversing_the_custody_entry_voids_its_private_entry_but_keeps_it_listed(self):
        self.custody_entry(41, "300.00")
        self.store.corrections.append({"correction_type": "FULL_REVERSAL", "source_type": "MARKET_TRIP_ENTRY", "source_id": "41", "chain_sequence": None})
        view = self.owner.private_account()
        self.assertEqual((view["balance"], view["entries"][0]["voided"]), ("0.00", True))

    def test_only_the_owner_right_opens_or_uses_the_private_account(self):
        manager = self.fin_service(OWNER)                                          # operates accounts but lacks PRIVATE_ACCOUNT_MANAGE
        before = self.store.tables()
        with self.assertRaises(PermissionError):
            manager.private_transfer("BANK:7", "TO", "100", "", str(uuid4()))
        with self.assertRaises(PermissionError):
            manager.private_account()
        with self.assertRaises(PermissionError):
            self.fin_service(SALESPERSON).private_account()
        self.assertEqual(self.store.tables(), before)

    def test_the_private_categories_are_not_offered_by_the_plain_cash_movement(self):
        self.assertFinRefused("USE_ADVANCE", lambda: self.fin.cash_movement("CASH:1", "OUT", "PRIVATE_OUT", "100", "", str(uuid4())))


if __name__ == "__main__":
    unittest.main()
