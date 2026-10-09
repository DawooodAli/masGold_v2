"""22K in Return V1: historical refund inside the window, current 22K SHOP_BUY (derived) after it, F7 22K grams restored to the
counted Scrap, and the missing-quote refusal. In-memory store from tests.test_return_service (karat id 4 = 22K)."""
from decimal import Decimal, ROUND_HALF_UP
import unittest
from unittest import mock

from services.return_service import ReturnError
from tests import test_return_service as base
from tests.test_return_service import Prices, ReturnTestCase, cash, line

D = Decimal
K22 = 4
P22 = tuple((p * 22 / 24).quantize(D("0.0001"), ROUND_HALF_UP) for p in base.PRICES[3][:1] + base.PRICES[3][1:])   # from 24K 7100 / 7300


def egp5(value):
    return ((value / 5).quantize(D("1"), ROUND_HALF_UP) * 5).quantize(D("0.01"))


class Karat22ReturnCase(ReturnTestCase):
    def setUp(self):
        for patcher in (mock.patch.dict(base.KARATS, {K22: 22}), mock.patch.dict(base.PRICES, {K22: P22}),
                        mock.patch.dict(base.SALE_PRICE, {K22: D("6500")})):
            patcher.start()
            self.addCleanup(patcher.stop)
        super().setUp()
        self.store.lines["500"] += [line("20", 8, "SELL_INVENTORY", K22, "5.000", "205", "500.00"),
                                    line("21", 9, "SELL_OLD_GOLD", K22, "10.000", None, "1000.00", name="دبلة من الكسر")]
        self.store.lines["500"][-2]["line_total"] = D("33000.00")          # what the customer paid for the 22K piece
        self.store.lines["500"][-1]["line_total"] = D("66000.00")          # 10.000 g 22K F7
        self.store.items["205"] = {"inventory_item_id": "205", "inventory_status": "SOLD", "weight_grams": D("5.000"),
                                   "stock_classification": "NEW_STOCK"}
        self.store.old_gold_movements.append({"code": "CUSTOMER_BUY_IN", "karat_id": K22, "delta": D("0.000"), "ref": "seed"})


class Karat22ReturnValuationTests(Karat22ReturnCase):
    def test_the_derived_prices_used_by_this_fixture(self):
        self.assertEqual(P22, (D("6508.3333"), D("6691.6667")))

    def test_inside_the_window_the_historical_value_is_refunded_and_no_22k_quote_is_needed(self):
        self.service = self.make_service(base.EMPLOYEE)
        self.prices.prices = {k: v for k, v in base.PRICES.items() if k != K22}        # no published 22K price at all
        p = self.service.preview(self.draft((("20", None),)))
        self.assertEqual((p["valuation_mode"], p["refund_basis"], p["total_amount"]), ("HISTORICAL", "33000.00", "33000.00"))
        self.assertIsNone(p["lines"][0]["shop_buy_price_per_gram"])

    def test_after_the_window_the_22k_shop_buy_quote_is_used_not_another_karats(self):
        self.at(hours=80)
        p = self.service.preview(self.draft((("20", None),)))
        gold = (D("5.000") * P22[0]).quantize(D("0.01"), ROUND_HALF_UP)               # 5 g * SHOP_BUY 6508.3333 = 32,541.67
        self.assertEqual(gold, D("32541.67"))
        self.assertEqual((p["valuation_mode"], p["gold_amount"], p["making_refund"], p["total_amount"]),
                         ("CURRENT_BUY", "32541.67", "0.00", str(egp5(gold))))
        self.assertEqual(p["lines"][0]["shop_buy_price_per_gram"], "6508.3333")        # never SHOP_SELL 6691.6667
        self.assertEqual(p["total_amount"], "32540.00")

    def test_after_the_window_without_a_22k_quote_the_return_is_refused_never_substituted(self):
        self.at(hours=80)
        self.prices.prices = {k: v for k, v in base.PRICES.items() if k != K22}
        with self.assertRaises(ReturnError) as refused:
            self.service.preview(self.draft((("20", None),)))
        self.assertEqual(refused.exception.code, "PRICE_MISSING")

    def test_the_return_snapshots_the_22k_quote_id_and_rounds_once(self):
        self.at(hours=80)
        request = self.service.request_approval(self.draft((("20", None),)))
        self.manager.decide_approval(self.store.reviews[-1]["review_id"], True)
        self.finalize(self.draft((("20", None),), payments=(cash("32540.00"),), return_id=request["return_id"]))
        saved = self.store.return_lines[0]
        self.assertEqual((saved["karat_value"], saved["shop_buy_price_per_gram"], saved["quote_id"]), (22, P22[0], 50 + K22))
        self.assertEqual(self.store.returns[0]["total_amount"], D("32540.00"))
        self.assertEqual(self.store.items["205"]["inventory_status"], "CONVERTED_TO_OLD_GOLD")   # held in Scrap for the manager (033)


class Karat22ScrapReturnTests(Karat22ReturnCase):
    def test_a_partial_f7_return_restores_exactly_the_returned_22k_grams_to_counted_scrap(self):
        before22, before21 = self.store.old_gold_balance(K22), self.store.old_gold_balance(1)
        p = self.service.preview(self.draft((("21", "3.5"),)))
        self.assertEqual((p["valuation_mode"], p["refund_basis"]), ("HISTORICAL", "23100.00"))      # 66,000 * 3.5 / 10
        self.finalize(self.full_cash((("21", "3.5"),)))
        self.assertEqual(self.store.old_gold_balance(K22) - before22, D("3.500"))
        self.assertEqual(self.store.old_gold_balance(1), before21)                    # no other karat moves
        item = next(l for l in self.service.context("500")["lines"] if l["line_id"] == "21")
        self.assertEqual((item["remaining_grams"], item["returnable"]), ("6.500", True))
        self.assertEqual(self.store.inventory_movements, [])                            # F7 never creates or moves a piece


if __name__ == "__main__":
    unittest.main()
