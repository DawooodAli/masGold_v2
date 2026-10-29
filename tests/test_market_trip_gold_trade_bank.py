"""Gold purchase / sale paid or received through a bank / wallet account (091): the gold moves the custody gold, the custody CASH stays untouched and
ONE real bank movement posts and is reversed with the entry. Real MarketTripService and CorrectionService over the in-memory transactional database
of tests/market_trip_fakes.py (NOT real-MySQL: the SQL is covered by the static schema test)."""
from decimal import Decimal as D
import unittest

from services.market_trip_service import TripError
from tests.test_market_trip_gold_buy import REFERENCE_PRICES, bkey, line
from tests.test_market_trip_gold_sale import skey
from tests.test_market_trips import K21, TODAY, TripCase


class BankTradeCase(TripCase):
    def setUp(self):
        super().setUp()
        self.service._price_reference = lambda: dict(REFERENCE_PRICES)
        self.trip = self.issue("10000", [(K21, "20.000", "LOGO")])["trip_id"]

    def buy(self, bank="1", method="BANK", total="30000", request=None):
        self.n += 1
        return self.service.buy_gold(self.trip, [line(K21, "5", total)], "", "تاجر السوق", TODAY, "", "", request or bkey(self.n), True,
                                     payment_method=method, bank_account_id=bank)

    def sell(self, bank="1", method="BANK", total="60000", request=None):
        self.n += 1
        return self.service.sell_gold(self.trip, "CUSTODY_GOLD", K21, "LOGO", None, "10", "TOTAL", total, "", "تاجر الصاغة", TODAY, "", "",
                                      request or skey(self.n), True, payment_method=method, bank_account_id=bank)

    def gold(self):
        rows = {(g["karat_value"], g["designation"]): g for g in self.service.trip_detail(self.trip)["custody"]["gold"]}
        return rows[(21, "LOGO")]["outstanding"]

    def cash(self):
        return self.service.trip_detail(self.trip)["custody"]["cash"]["outstanding"]


class BankPurchaseTests(BankTradeCase):
    def test_a_bank_purchase_adds_gold_posts_one_bank_out_and_leaves_the_custody_cash(self):
        cash = self.cash()
        done = self.buy()
        self.assertEqual((done["payment_method"], done["bank_account_id"]), ("BANK", "1"))
        self.assertEqual(self.gold(), D("25.000"))
        self.assertEqual(self.cash(), cash)
        moves = [m for m in self.db.bank_moves if m["type_code"] == "TRIP_GOLD_BUY_BANK"]
        self.assertEqual([(m["account_id"], m["amount_delta"]) for m in moves], [(1, D("-30000.00"))])

    def test_a_bank_purchase_is_not_capped_by_the_custody_cash(self):
        done = self.buy(total="900000")
        self.assertEqual(done["total_price"], "900000.00")
        self.assertEqual(self.cash(), D("10000.00"))

    def test_a_custody_purchase_still_spends_the_custody_cash_and_posts_no_bank_movement(self):
        cash = self.cash()
        self.buy(method="CUSTODY", bank=None, total="4000")
        self.assertEqual(self.cash(), cash - D("4000.00"))
        self.assertEqual([m for m in self.db.bank_moves if m["type_code"] == "TRIP_GOLD_BUY_BANK"], [])

    def test_a_bank_purchase_needs_an_active_account(self):
        self.db.bank_accounts[1]["is_active"] = 0
        with self.assertRaises(TripError) as caught:
            self.buy()
        self.assertEqual(caught.exception.code, "BANK_ACCOUNT_NOT_FOUND")
        with self.assertRaises(TripError) as caught:
            self.buy(bank="")
        self.assertEqual(caught.exception.code, "BANK_ACCOUNT_REQUIRED")
        self.assertEqual(self.db.bank_moves, [])

    def test_an_exact_replay_returns_the_original_and_posts_one_movement(self):
        first = self.buy(request=bkey(900))
        again = self.buy(request=bkey(900))
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["entry_id"], first["entry_id"])
        self.assertEqual(len([m for m in self.db.bank_moves if m["type_code"] == "TRIP_GOLD_BUY_BANK"]), 1)

    def test_the_reversal_removes_the_gold_and_returns_the_money_to_the_account(self):
        done = self.buy()
        self.service.reverse(done["entry_id"], "خطأ في الشراء")
        self.assertEqual(self.gold(), D("20.000"))
        self.assertEqual(sum((m["amount_delta"] for m in self.db.bank_moves if m["account_id"] == 1), D("0")), D("0"))


class BankSaleTests(BankTradeCase):
    def test_a_bank_sale_removes_gold_posts_one_bank_in_and_leaves_the_custody_cash(self):
        cash = self.cash()
        done = self.sell()
        self.assertEqual((done["payment_method"], done["bank_account_id"]), ("BANK", "1"))
        self.assertEqual(self.gold(), D("10.000"))
        self.assertEqual(self.cash(), cash)
        moves = [m for m in self.db.bank_moves if m["type_code"] == "TRIP_GOLD_SALE_BANK"]
        self.assertEqual([(m["account_id"], m["amount_delta"]) for m in moves], [(1, D("60000.00"))])

    def test_a_custody_sale_adds_the_custody_cash_and_posts_no_bank_movement(self):
        cash = self.cash()
        self.sell(method="CUSTODY", bank=None)
        self.assertEqual(self.cash(), cash + D("60000.00"))
        self.assertEqual([m for m in self.db.bank_moves if m["type_code"] == "TRIP_GOLD_SALE_BANK"], [])

    def test_the_reversal_restores_the_gold_and_takes_the_money_back_from_the_account(self):
        done = self.sell()
        self.service.reverse(done["entry_id"], "خطأ في الصفقة")
        self.assertEqual(self.gold(), D("20.000"))
        self.assertEqual(sum((m["amount_delta"] for m in self.db.bank_moves if m["account_id"] == 1), D("0")), D("0"))


if __name__ == "__main__":
    unittest.main()
