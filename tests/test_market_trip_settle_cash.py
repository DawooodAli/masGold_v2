"""Market-custody settlement step 2e (064): after the owner returns, the custody CASH is settled: a free-text expense, a deposit of the remaining cash into a bank / wallet account, or a
write-off (إعدام), and the gold bought in the market needs no melt / logo decision. Real services over the in-memory transactional database of tests/market_trip_fakes.py. NOT real-MySQL tests."""
from decimal import Decimal as D
import unittest

from services.correction_service import CorrectionError
from tests.test_market_trip_settle_sale import SaleOfSettlementGoldCase
from tests.test_market_trips import K18, K21, TODAY, key


class CashSettleCase(SaleOfSettlementGoldCase):
    """The melted trip of the previous steps plus 100,000 EGP of custody cash: 40,000 issued and 60,000 from a sale of 10 g of the bar."""

    def setUp(self):
        super().setUp()
        self.n += 1
        self.service.issue(self.trip, "40000", [], TODAY, "", key(100 + self.n))
        self.sell_bar("10", "60000")

    def expense(self, amount="1000", text="أجرة نقل", request=None, trip=None):
        self.n += 1
        return self.service.record_custody_expense(trip or self.trip, amount, text, TODAY, "", "", request or key(200 + self.n))

    def bank(self, amount="5000", account="1", request=None):
        self.n += 1
        return self.service.deposit_custody_cash(self.trip, amount, account, TODAY, "", "", request or key(300 + self.n))

    def write_off(self, amount="50", reason="فرق عد", request=None):
        self.n += 1
        return self.service.write_off_custody_cash(self.trip, amount, reason, TODAY, "", request or key(400 + self.n))

    def reverse(self, entry):
        return self.corrections.reverse("MARKET_TRIP_ENTRY", entry, "خطأ")


class ExpenseTests(CashSettleCase):
    def test_an_expense_leaves_the_custody_cash_and_moves_nothing_else(self):
        safe, bank = self.db.cash(), len(self.db.bank_moves)
        done = self.expense("1500.50", "أجرة نقل")
        self.assertEqual((done["kind"], done["amount"], done["description"]), ("ST_EXPENSE", "1500.50", "أجرة نقل"))
        self.assertEqual(self.cash(), D("98499.50"))
        self.assertEqual((self.db.cash(), len(self.db.bank_moves)), (safe, bank))                  # no Main Safe, no bank

    def test_refusals_leave_no_trace(self):
        self.refused("CASH_EXCEEDS_CUSTODY", lambda: self.expense("100000.01"))
        self.refused("AMOUNT_INVALID", lambda: self.expense("0"))
        self.refused("AMOUNT_INVALID", lambda: self.expense("1.005"))
        self.refused("REASON_REQUIRED", lambda: self.expense("10", ""))

    def test_exact_replay_posts_once_and_a_changed_payload_conflicts(self):
        k = key(900)
        first = self.expense("100", request=k)
        again = self.expense("100", request=k)
        self.assertEqual((again["entry_id"], again["duplicate"]), (first["entry_id"], True))
        self.assertEqual(self.cash(), D("99900.00"))
        self.refused("REQUEST_CONFLICT", lambda: self.expense("101", request=k))

    def test_a_failure_in_any_step_rolls_everything_back(self):
        for step in ("entry", "settle_cash"):
            self.db.fail_at = step
            before = self.state()
            with self.assertRaises(RuntimeError):
                self.expense("10")
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)

    def test_a_reversal_gives_the_cash_back_to_the_custody(self):
        done = self.expense("1000")
        self.reverse(done["entry_id"])
        self.assertEqual(self.cash(), D("100000.00"))


class BankDepositTests(CashSettleCase):
    def test_the_remaining_cash_enters_the_bank_account_and_leaves_the_custody(self):
        safe = self.db.cash()
        done = self.bank("100000")
        self.assertEqual((done["kind"], done["amount"], done["bank_account_id"]), ("ST_CASH_BANK", "100000.00", "1"))
        self.assertEqual(self.cash(), D("0.00"))
        self.assertEqual([(m["account_id"], m["amount_delta"], m["type_code"]) for m in self.db.bank_moves], [(1, D("100000.00"), "TRIP_CUSTODY_BANK_IN")])
        self.assertEqual(self.db.cash(), safe)                                                      # Main Safe untouched

    def test_refusals(self):
        self.refused("BANK_ACCOUNT_NOT_FOUND", lambda: self.bank("10", account="2"))                 # inactive
        self.refused("BANK_ACCOUNT_NOT_FOUND", lambda: self.bank("10", account="99"))
        self.refused("BANK_ACCOUNT_REQUIRED", lambda: self.bank("10", account=""))
        self.refused("CASH_EXCEEDS_CUSTODY", lambda: self.bank("100000.01"))

    def test_a_reversal_takes_the_money_out_of_the_bank_account_again(self):
        done = self.bank("5000")
        self.reverse(done["entry_id"])
        self.assertEqual(self.cash(), D("100000.00"))
        self.assertEqual(sum(m["amount_delta"] for m in self.db.bank_moves if m["account_id"] == 1), D("0.00"))

    def test_the_trip_closes_only_when_the_cash_is_zero(self):
        self.bar_to("10", "33.520")
        self.raw_to("10", K21, "10")
        self.raw_to("11", K18, "5")
        self.refused("OUTSTANDING_CUSTODY", lambda: self.service.close(self.trip))                   # 100,000 of custody cash remain
        self.bank("60000")
        self.write_off("40000", "إعدام باقي العهدة")
        self.assertEqual(self.service.close(self.trip)["status"], "CLOSED")


class WriteOffTests(CashSettleCase):
    def test_a_write_off_needs_a_reason_and_moves_no_money(self):
        safe, bank = self.db.cash(), len(self.db.bank_moves)
        done = self.write_off("250", "فرق عد")
        self.assertEqual((done["kind"], done["description"]), ("ST_CASH_OFF", "فرق عد"))
        self.assertEqual(self.cash(), D("99750.00"))
        self.assertEqual((self.db.cash(), len(self.db.bank_moves)), (safe, bank))
        self.refused("REASON_REQUIRED", lambda: self.write_off("1", ""))

    def test_the_sale_cannot_be_reversed_once_its_proceeds_were_settled(self):
        sale = next(e for e in self.db.entries.values() if e["entry_kind"] == "GOLD_SALE")
        self.write_off("100000", "إعدام")
        with self.assertRaises(CorrectionError) as refused:
            self.reverse(sale["id"])
        self.assertEqual(refused.exception.code, "DEPENDENT_CASH_USE_EXISTS")


class StateTests(CashSettleCase):
    def test_a_closed_trip_is_refused(self):
        self.write_off("100000", "إعدام")
        self.bar_to("10", "33.520")
        self.raw_to("10", K21, "10")
        self.raw_to("11", K18, "5")
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.expense("1"))


class BoughtGoldTests(CashSettleCase):
    def test_bought_gold_needs_no_melt_or_logo_decision(self):
        self.n += 1
        done = self.service.buy_gold(self.trip, [{"karat_id": K21, "weight": "5", "price": "30000"}], "", "تاجر", TODAY, "", "", key(800 + self.n), False)
        self.assertEqual(done["kind"], "GOLD_BUY")
        self.assertEqual(self.gold()[(21, "UNCLASSIFIED")]["outstanding"], D("15.000"))               # 10 unmelted + 5 bought
        self.assertEqual(self.cash(), D("70000.00"))


if __name__ == "__main__":
    unittest.main()
