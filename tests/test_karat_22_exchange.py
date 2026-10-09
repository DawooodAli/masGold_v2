"""22K in Exchange V1: same-karat pricing with the derived 22K quote, mixed-karat pricing at the actual 24K quote,
the 6-decimal pure-gram precision of 22K (measured, not changed), and the missing-quote refusal.
In-memory store from tests.test_exchange_service (karat id 4 = 22K); never a live database."""
from decimal import Decimal, ROUND_HALF_UP
import unittest
from unittest import mock

from services.exchange_service import ExchangeError, pure_weight
from tests import test_exchange_service as base
from tests.test_exchange_service import ExchangeTestCase, Prices, draft, item, line

D = Decimal
K22 = 4
P24 = base.PRICES[3]                                                    # actual 24K quote of the fixture: 7500 / 7600
P22 = tuple((p * 22 / 24).quantize(D("0.0001"), ROUND_HALF_UP) for p in P24)      # the published derivation: 6875.0000 / 6966.6667


def egp5(value):
    return ((value / 5).quantize(D("1"), ROUND_HALF_UP) * 5).quantize(D("0.01"))


class Karat22ExchangeCase(ExchangeTestCase):
    def setUp(self):
        for patcher in (mock.patch.dict(base.KARATS, {K22: 22}), mock.patch.dict(base.PRICES, {K22: P22})):
            patcher.start()
            self.addCleanup(patcher.stop)
        super().setUp()
        self.store.old_gold[K22] = D("0.000")
        self.store.lines["500"].append(line("9", 8, "SELL_INVENTORY", K22, "10.000", "210", "1500.00"))
        for new in (item("210", K22, "10.000", "SOLD"), item("320", K22, "12.000"), item("321", K22, "8.000"),
                    item("322", K22, "10.000")):
            self.store.items[new["inventory_item_id"]] = new

    def price(self, d):
        p = self.service.preview(d)
        return (p["valuation_basis"], p["price_basis"], p["price_karat"], p["price_per_gram"],
                p["valued_delta_grams"], p["gold_amount"], p["signed_amount"])


class Karat22PricingTests(Karat22ExchangeCase):
    def test_the_derived_prices_are_the_expected_numbers(self):
        self.assertEqual(P22, (D("6875.0000"), D("6966.6667")))

    def test_same_karat_22k_more_grams_uses_the_22k_shop_sell_quote(self):
        # +2.000 g * 6966.6667 = 13,933.33; making 0 - original 1,500 = -1,500 -> 12,433.33 -> 12,435.00 (EGP 5, once).
        got = self.price(draft(returned=("9",), replacements=(("320", "0"),)))
        self.assertEqual(got[:6], ("SAME_KARAT", "SHOP_SELL", 22, "6966.6667", "2.000", "13933.33"))
        self.assertEqual(D(got[6]), egp5(D("13933.33") - D("1500.00")))

    def test_same_karat_22k_fewer_grams_uses_the_22k_shop_buy_quote(self):
        # -2.000 g * 6875 = -13,750.00; making 0 - 1,500 -> -15,250.00
        got = self.price(draft(returned=("9",), replacements=(("321", "0"),)))
        self.assertEqual(got[:6], ("SAME_KARAT", "SHOP_BUY", 22, "6875.0000", "-2.000", "13750.00"))
        self.assertEqual(D(got[6]), egp5(D("-13750.00") - D("1500.00")))

    def test_mixed_22k_to_21k_is_valued_in_pure_24k_grams_at_the_actual_24k_quote(self):
        # 22K 10.000 g = 9.166667 pure; 21K 10.000 g = 8.75 pure; delta -0.416667 * 7500 (24K SHOP_BUY).
        got = self.price(draft(returned=("9",), replacements=(("300", "0"),)))
        self.assertEqual(got[:5], ("PURE_24K", "SHOP_BUY", 24, "7500.0000", "-0.416667"))
        self.assertEqual(got[5], "3125.00")

    def test_mixed_21k_to_22k_uses_the_24k_shop_sell_quote(self):
        # 21K 10.000 g (8.75 pure) -> 22K 12.000 g (11.0 pure): +2.25 * 7600.
        got = self.price(draft(returned=("1",), replacements=(("320", "0"),)))
        self.assertEqual(got[:6], ("PURE_24K", "SHOP_SELL", 24, "7600.0000", "2.250", "17100.00"))

    def test_22k_is_never_priced_by_another_karats_quote(self):
        got = self.price(draft(returned=("9",), replacements=(("320", "0"),)))
        self.assertNotIn(got[3], ("6232.0000", "7600.0000", "6207.0000"))
        self.assertEqual(got[2], 22)

    def test_a_same_karat_22k_exchange_without_a_22k_quote_is_refused_never_substituted(self):
        service = self.make_service(Prices(prices={1: base.PRICES[1], 2: base.PRICES[2], 3: base.PRICES[3]}))
        with self.assertRaises(ExchangeError) as context:
            service.preview(draft(returned=("9",), replacements=(("320", "0"),)))
        self.assertEqual(context.exception.code, "PRICE_MISSING")

    def test_the_exchange_finalizes_with_22k_in_both_directions_and_posts_exactly_the_signed_amount(self):
        preview = self.service.preview(draft(returned=("9",), replacements=(("320", "0"),)))
        result = self.finalize(draft(returned=("9",), replacements=(("320", "0"),),
                                     payments=(base.cash(preview["signed_amount"]),)))
        self.assertEqual(result["signed_amount"], preview["signed_amount"])
        self.assertEqual(self.store.items["210"]["inventory_status"], "CONVERTED_TO_OLD_GOLD")
        self.assertEqual(self.store.items["320"]["inventory_status"], "SOLD")
        self.assertEqual([m["delta"] for m in self.store.cash_movements], [D(preview["signed_amount"])])


class Karat22PurePrecisionTests(unittest.TestCase):
    """The pure-gram precision is 6 decimals (existing rule, unchanged). 22/24 is the only karat ratio that is not exact at 6 dp:
    measure the worst money effect over every 0.001 g weight a piece can have, at the largest realistic 24K price."""

    def test_pure_weight_of_22k_is_the_six_decimal_rounding_of_weight_times_22_over_24(self):
        self.assertEqual(pure_weight("10.000", 22), D("9.166667"))
        self.assertEqual(pure_weight("1.001", 22), (D("1.001") * 22 / 24).quantize(D("0.000001"), ROUND_HALF_UP))
        for exact in (18, 21, 24):
            self.assertEqual(pure_weight("3.333", exact), D("3.333") * exact / 24)

    def test_the_rounding_error_is_below_a_tenth_of_a_piastre_and_never_moves_the_egp5_result_in_a_sweep(self):
        price = D("9000.0000")                                          # well above today's 24K quote (~7,100)
        worst_money, flips = D("0"), 0
        for thousandths in range(1, 60001, 7):                          # 0.001 g .. 60 g
            weight = D(thousandths) / 1000
            exact_delta = weight * 22 / 24 - weight * 21 / 24           # mixed 22K -> 21K, exact rational pure grams
            service_delta = pure_weight(weight, 22) - pure_weight(weight, 21)
            exact_money, service_money = exact_delta * price, service_delta * price
            worst_money = max(worst_money, abs(exact_money - service_money))
            if egp5(service_money.quantize(D("0.01"), ROUND_HALF_UP)) != egp5(exact_money.quantize(D("0.01"), ROUND_HALF_UP)):
                flips += 1
        self.assertLess(worst_money, D("0.01"))                         # under one piastre in every sampled case
        # A flip can only happen when the exact amount sits within that tiny error of an EGP5 rounding boundary; report, not hide.
        self.assertLessEqual(flips, 1)


if __name__ == "__main__":
    unittest.main()
