"""Canonical Billing V2 arithmetic; pure Decimal, no database."""
from dataclasses import FrozenInstanceError
from decimal import Decimal
import unittest

from services.invoice_calculator import (
    InvoiceCalculationError, InvoiceCalculator as Calc, Payment,
)


D = Decimal


class SaleLineTests(unittest.TestCase):
    def test_making_per_gram(self):
        line = Calc.sale_line("10.500", "6232.0000", making_per_gram="150.00")
        self.assertEqual(line.gold_value, D("65436.00"))
        self.assertEqual(line.making_per_gram, D("150.00"))
        self.assertEqual(line.making_total, D("1575.00"))
        self.assertEqual(line.line_total, D("67011.00"))

    def test_making_total_only(self):
        line = Calc.sale_line(D("3.333"), D("6232"), making_total="500")
        self.assertIsNone(line.making_per_gram)
        self.assertEqual(line.gold_value, D("20771.26"))  # 20771.256 half-up
        self.assertEqual(line.line_total, D("21271.26"))

    def test_components_add_up_to_total_after_rounding(self):
        line = Calc.sale_line("1.005", "0.0050", making_per_gram="0.50")
        self.assertEqual(line.gold_value + line.making_total, line.line_total)

    def test_zero_making_is_explicit_and_allowed(self):
        self.assertEqual(Calc.sale_line("1", "100", making_total="0").line_total, D("100.00"))

    def test_making_must_be_exactly_one_form(self):
        for kwargs in ({}, {"making_per_gram": "1", "making_total": "1"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(InvoiceCalculationError) as ctx:
                Calc.sale_line("1", "100", **kwargs)
            self.assertEqual(ctx.exception.code, "MAKING_REQUIRED")

    def test_invalid_inputs(self):
        cases = [(("0", "100"), "OUT_OF_RANGE"), (("1", "0"), "OUT_OF_RANGE"),
                 (("-1", "100"), "OUT_OF_RANGE"), ((1.5, "100"), "INVALID_NUMBER"),
                 ((True, "100"), "INVALID_NUMBER"), (("abc", "100"), "INVALID_NUMBER"),
                 (("NaN", "100"), "INVALID_NUMBER"), (("Infinity", "100"), "INVALID_NUMBER"),
                 (("1.0001", "100"), "PRECISION"), (("1", "100.00001"), "PRECISION")]
        for (weight, price), code in cases:
            with self.subTest(weight=weight, price=price), \
                    self.assertRaises(InvoiceCalculationError) as ctx:
                Calc.sale_line(weight, price, making_total="0")
            self.assertEqual(ctx.exception.code, code)
        for kwargs in ({"making_per_gram": "-1"}, {"making_total": "0.001"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(InvoiceCalculationError):
                Calc.sale_line("1", "100", **kwargs)

    def test_trailing_zero_precision_is_not_excess(self):
        self.assertEqual(Calc.sale_line("1.5000", "100", making_total="0").weight_grams, D("1.500"))

    def test_money_cap(self):
        with self.assertRaises(InvoiceCalculationError) as ctx:
            Calc.sale_line("999999999.999", "9999999999.9999", making_total="0")
        self.assertEqual(ctx.exception.code, "TOO_LARGE")

    def test_results_are_immutable(self):
        line = Calc.sale_line("1", "100", making_total="0")
        with self.assertRaises(FrozenInstanceError):
            line.line_total = D("1")


class OldGoldLineTests(unittest.TestCase):
    def test_no_discount(self):
        line = Calc.old_gold_line("20.250", "6207")
        self.assertEqual(line.gross_total, D("125691.75"))
        self.assertEqual(line.discount_amount, D("0.00"))
        self.assertIsNone(line.discount_type)
        self.assertEqual(line.line_total, line.gross_total)

    def test_percent_discount_is_exact_not_rounded_to_five(self):
        line = Calc.old_gold_line("10", "6207", discount_type="PERCENT", discount_value="1.5")
        self.assertEqual(line.gross_total, D("62070.00"))
        self.assertEqual(line.discount_value, D("1.50"))
        self.assertEqual(line.discount_amount, D("931.05"))
        self.assertEqual(line.line_total, D("61138.95"))

    def test_amount_discount(self):
        line = Calc.old_gold_line("10", "6207", discount_type="AMOUNT", discount_value="73")
        self.assertEqual(line.discount_amount, D("73.00"))
        self.assertEqual(line.line_total, D("61997.00"))

    def test_full_discount_boundaries(self):
        self.assertEqual(Calc.old_gold_line("1", "100", discount_type="PERCENT",
                                            discount_value="100").line_total, D("0.00"))
        self.assertEqual(Calc.old_gold_line("1", "100", discount_type="AMOUNT",
                                            discount_value="100").line_total, D("0.00"))

    def test_discount_validation(self):
        cases = [({"discount_type": "PERCENT", "discount_value": "100.01"}, "DISCOUNT_RANGE"),
                 ({"discount_type": "PERCENT", "discount_value": "-1"}, "OUT_OF_RANGE"),
                 ({"discount_type": "PERCENT", "discount_value": "1.001"}, "PRECISION"),
                 ({"discount_type": "AMOUNT", "discount_value": "100.01"}, "DISCOUNT_RANGE"),
                 ({"discount_type": "AMOUNT", "discount_value": None}, "INVALID_NUMBER"),
                 ({"discount_type": "ROUND5", "discount_value": "1"}, "DISCOUNT_TYPE"),
                 ({"discount_value": "1"}, "DISCOUNT_TYPE")]
        for kwargs, code in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(InvoiceCalculationError) as ctx:
                Calc.old_gold_line("1", "100", **kwargs)
            self.assertEqual(ctx.exception.code, code)


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.sale = Calc.sale_line("10", "6000", making_total="1000")        # 61000.00
        self.old = Calc.old_gold_line("5", "5000")                          # 25000.00

    def test_sale_only_fully_paid(self):
        result = Calc.settlement([self.sale], [], [Calc.payment("IN", "61000")])
        self.assertEqual((result.invoice_type, result.net_direction, result.net_amount),
                         ("SELL", "CUSTOMER_PAYS_SHOP", D("61000.00")))
        self.assertEqual(result.residual_signed, D("0.00"))
        self.assertEqual(result.settlement_status, "SETTLED")
        self.assertFalse(result.requires_customer)

    def test_sale_partial_payment_leaves_customer_debt(self):
        result = Calc.settlement([self.sale], [], [Calc.payment("IN", "40000"),
                                                   Calc.payment("IN", "1000")])
        self.assertEqual(result.payment_signed, D("41000.00"))
        self.assertEqual(result.residual_signed, D("20000.00"))
        self.assertEqual(result.residual_direction, "CUSTOMER_OWES_SHOP")
        self.assertEqual(result.settlement_status, "PARTIAL")
        self.assertTrue(result.requires_customer)

    def test_sale_unpaid(self):
        result = Calc.settlement([self.sale], [])
        self.assertEqual(result.settlement_status, "UNSETTLED")
        self.assertEqual(result.residual_amount, D("61000.00"))

    def test_buy_only_shop_pays(self):
        result = Calc.settlement([], [self.old], [Calc.payment("OUT", "25000")])
        self.assertEqual((result.invoice_type, result.net_direction), ("BUY", "SHOP_PAYS_CUSTOMER"))
        self.assertEqual(result.net_signed, D("-25000.00"))
        self.assertEqual(result.net_amount, D("25000.00"))
        self.assertEqual(result.money_paid, D("25000.00"))
        self.assertEqual(result.settlement_status, "SETTLED")

    def test_buy_partially_paid_leaves_shop_owing(self):
        result = Calc.settlement([], [self.old], [Calc.payment("OUT", "20000")])
        self.assertEqual(result.residual_signed, D("-5000.00"))
        self.assertEqual(result.residual_direction, "SHOP_OWES_CUSTOMER")
        self.assertEqual(result.settlement_status, "PARTIAL")

    def test_exchange_offset_is_not_a_payment(self):
        result = Calc.settlement([self.sale], [self.old], [Calc.payment("IN", "36000")])
        self.assertEqual(result.invoice_type, "MIXED")
        self.assertEqual((result.sale_total, result.old_gold_total), (D("61000.00"), D("25000.00")))
        self.assertEqual(result.net_signed, D("36000.00"))
        self.assertEqual(result.money_received, D("36000.00"))
        self.assertEqual(result.settlement_status, "SETTLED")
        self.assertEqual((result.sale_weight_total, result.old_gold_weight_total),
                         (D("10.000"), D("5.000")))

    def test_exchange_where_old_gold_exceeds_sale(self):
        big_old = Calc.old_gold_line("20", "5000")                          # 100000.00
        result = Calc.settlement([self.sale], [big_old], [Calc.payment("OUT", "39000")])
        self.assertEqual(result.net_direction, "SHOP_PAYS_CUSTOMER")
        self.assertEqual(result.residual_signed, D("0.00"))

    def test_exact_exchange_is_settled_without_payments(self):
        old = Calc.old_gold_line("12.2", "5000")                            # 61000.00
        result = Calc.settlement([self.sale], [old])
        self.assertEqual((result.net_direction, result.net_amount), ("SETTLED", D("0.00")))
        self.assertEqual(result.settlement_status, "SETTLED")
        with self.assertRaises(InvoiceCalculationError) as ctx:
            Calc.settlement([self.sale], [old], [Calc.payment("IN", "1")])
        self.assertEqual(ctx.exception.code, "PAYMENT_NOT_EXPECTED")

    def test_fully_discounted_old_gold_still_makes_buy_invoice(self):
        free = Calc.old_gold_line("1", "100", discount_type="PERCENT", discount_value="100")
        result = Calc.settlement([], [free])
        self.assertEqual((result.invoice_type, result.net_direction), ("BUY", "SETTLED"))

    def test_payment_rule_violations(self):
        cases = [(([self.sale], [], [Calc.payment("IN", "61000.01")]), "OVERPAYMENT"),
                 (([], [self.old], [Calc.payment("OUT", "25001")]), "OVERPAYMENT"),
                 (([self.sale], [], [Calc.payment("OUT", "1")]), "PAYMENT_DIRECTION"),
                 (([], [self.old], [Calc.payment("IN", "1")]), "PAYMENT_DIRECTION"),
                 (([self.sale], [], [Calc.payment("IN", "10"), Calc.payment("OUT", "5")]),
                  "MIXED_PAYMENT_DIRECTIONS"),
                 (([], []), "EMPTY_INVOICE")]
        for args, code in cases:
            with self.subTest(code=code), self.assertRaises(InvoiceCalculationError) as ctx:
                Calc.settlement(*args)
            self.assertEqual(ctx.exception.code, code)

    def test_payment_validation(self):
        for direction, amount, code in (("SIDEWAYS", "1", "PAYMENT_DIRECTION"),
                                        ("IN", "0", "OUT_OF_RANGE"),
                                        ("IN", "1.001", "PRECISION"),
                                        ("IN", 1.0, "INVALID_NUMBER")):
            with self.subTest(amount=amount), self.assertRaises(InvoiceCalculationError) as ctx:
                Calc.payment(direction, amount)
            self.assertEqual(ctx.exception.code, code)

    def test_only_calculator_results_are_accepted(self):
        with self.assertRaises(TypeError):
            Calc.settlement([{"line_total": D("1")}], [])
        with self.assertRaises(TypeError):
            Calc.settlement([self.sale], [], [("IN", D("1"))])
        # Hand-built payments are re-validated, not trusted.
        self.assertEqual(Calc.settlement([self.sale], [], [Payment("IN", "1")])
                         .residual_signed, D("60999.00"))
        with self.assertRaises(InvoiceCalculationError):
            Calc.settlement([self.sale], [], [Payment("IN", D("-5"))])


if __name__ == "__main__":
    unittest.main()
