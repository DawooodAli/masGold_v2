"""The old stock and bills import: the transformation rules and the generated SQL (pure, no database)."""
from datetime import date
from decimal import Decimal as D
import unittest

from tools import import_golddb_stock as imp

LOOKUPS = {"item_types": {imp.norm("خاتم"): 1, imp.norm("جنية"): 2, imp.norm("متنوع"): 3}, "factories": {imp.norm("لوكس"): 10, imp.norm("SAM"): 11}, "factory_codes": {"lz": 12},
           "karats": {18: 1, 21: 2, 24: 3}, "categories": {"A": 5}, "employees": {"حسين": 7}, "customers": {744, 5}}


def stock(idstock, barcode, op, item="خاتم", karat="21", weight="3.5", factory="لوكس", bill="NULL", sold="NULL", cust="NULL", high="6130", low="6090", size="NULL", cat="NULL", note="", day="2026-10-01"):
    return (str(idstock), barcode, str(op), item, karat, weight, factory, bill, sold, cust, high, low, size, cat, note, day)


class TransformTests(unittest.TestCase):
    def run_transform(self, rows, cash=(), buysell=(), emp=()):
        return imp.transform(rows, cash, buysell, emp, LOOKUPS)

    def test_a_sold_piece_becomes_a_sold_item_on_a_legacy_invoice_with_its_customer_employee_and_cash(self):
        data = self.run_transform([stock(1, "26261002", 1, bill="26100301", sold="2026-10-03", item="جنية", cat="a")], cash=[("26100301", "فاتورة", "50000", "0", "744")], buysell=[("26100301", "744")], emp=[("26100301", "حسين")])
        piece, invoice = data["pieces"][0], data["invoices"][0]
        self.assertEqual((piece["status"], piece["type_id"], piece["category_id"], piece["factory_id"], piece["weight"]), ("SOLD", 2, 5, 10, D("3.500")))
        self.assertEqual((invoice["number"], invoice["type"], invoice["customer_id"], invoice["employee_id"], invoice["day"]), ("26100301", "SELL", 744, 7, date(2026, 10, 3)))
        self.assertEqual((invoice["net_amount"], invoice["net_direction"], invoice["completeness"]), (D("50000"), "CUSTOMER_PAYS_SHOP", "PARTIAL"))
        self.assertEqual(invoice["lines"][0]["kind"], "SELL_INVENTORY")
        self.assertEqual(invoice["lines"][0]["price"], D("6130.00"))

    def test_unsold_pieces_stay_in_stock_and_make_no_invoice(self):
        data = self.run_transform([stock(2, "0126", 0)])
        self.assertEqual(([p["status"] for p in data["pieces"]], data["invoices"]), (["IN_STOCK"], []))

    def test_an_unknown_type_falls_back_and_an_unknown_factory_stays_empty_with_a_note(self):
        data = self.run_transform([stock(3, "B3", 0, item="قفل", factory="NA")])
        piece = data["pieces"][0]
        self.assertEqual((piece["type_id"], piece["factory_id"]), (3, None))
        self.assertIn("الصنف القديم: قفل", piece["notes"])
        self.assertIn("المصنع القديم: NA", piece["notes"])
        self.assertEqual((data["unmatched_types"], data["unmatched_factories"]), ({"قفل": 1}, {"NA": 1}))

    def test_spelling_variants_match_and_a_factory_symbol_matches(self):
        data = self.run_transform([stock(4, "B4", 0, item="جنيه", factory="sam"), stock(5, "B5", 0, factory="LZ")])
        self.assertEqual([(p["type_id"], p["factory_id"]) for p in data["pieces"]], [(2, 11), (1, 12)])

    def test_scrap_rows_and_old_gold_sales_are_lines_of_their_bill_never_stock_pieces(self):
        rows = [stock(6, "1-261022-old", 2, item="متنوع", factory="", bill="26102205", day="2026-10-22", high="NULL", low="NULL"),
                stock(7, "5-261022-old", 3, item="متنوع", factory="", bill="26102205", sold="2026-10-22", day="2026-10-22"),
                stock(8, "26261003", 1, bill="26102205", sold="2026-10-22")]
        data = self.run_transform(rows)
        invoice = data["invoices"][0]
        self.assertEqual(([p["id"] for p in data["pieces"]], invoice["type"]), ([8], "MIXED"))
        self.assertEqual([line["kind"] for line in invoice["lines"]], ["SELL_INVENTORY", "SELL_OLD_GOLD", "BUY_OLD_GOLD"])
        self.assertEqual([line["number"] for line in invoice["lines"]], [1, 2, 3])

    def test_a_piece_without_a_usable_weight_is_skipped_and_reported_not_a_blocking_problem(self):
        data = self.run_transform([stock(9, "B9", 0, weight="0"), stock(10, "B10", 0, karat="14")])
        self.assertEqual((data["pieces"], [s[0] for s in data["skipped"]], data["problems"]), ([], [9, 10], []))

    def test_a_repeated_or_blank_barcode_is_a_problem(self):
        data = self.run_transform([stock(11, "DUP", 0), stock(12, "DUP", 0), stock(13, "", 0)])
        self.assertEqual(sorted(p[0] for p in data["problems"]), [12, 13])

    def test_an_unknown_customer_is_left_empty_and_a_bill_without_cash_is_unknown(self):
        data = self.run_transform([stock(14, "B14", 1, bill="B1", sold="2026-10-03")], cash=[("B1", "فاتورة", "10", "0", "99999")], buysell=[("B1", "99999")])
        self.assertEqual(data["invoices"][0]["customer_id"], None)
        data = self.run_transform([stock(15, "B15", 1, bill="B2", sold="2026-10-03")])
        self.assertEqual((data["invoices"][0]["completeness"], data["invoices"][0]["net_amount"]), ("UNKNOWN", D("0")))

    def test_the_shop_paying_the_customer_flips_the_direction(self):
        data = self.run_transform([stock(16, "5-261022-old", 2, bill="B3", day="2026-10-22")], cash=[("B3", "فاتورة", "0", "7000", "5")])
        self.assertEqual((data["invoices"][0]["type"], data["invoices"][0]["net_direction"], data["invoices"][0]["net_amount"]), ("BUY", "SHOP_PAYS_CUSTOMER", D("7000")))


class EconomicsTests(unittest.TestCase):
    def bill(self, net, *lines, direction="CUSTOMER_PAYS_SHOP", completeness="PARTIAL"):
        built = [{"number": n, "kind": kind, "weight": D(w), "price": D(p) if p else None} for n, (kind, w, p) in enumerate(lines, 1)]
        return {"completeness": completeness, "net_direction": direction, "net_amount": D(net), "lines": built}

    def test_the_price_of_a_gram_is_the_21k_price_scaled_to_the_rows_own_karat(self):
        self.assertEqual(imp.karat_price(D("6300"), 21), D("6300.0000"))
        self.assertEqual(imp.karat_price(D("6300"), 18), D("5400.0000"))
        self.assertEqual(imp.karat_price(D("6300"), 24), D("7200.0000"))
        self.assertIsNone(imp.karat_price(None, 21))

    def test_the_making_is_the_amount_minus_the_gold_spread_by_weight_and_adds_up_exactly(self):
        result = imp.derive_economics(self.bill("28000", ("SELL_INVENTORY", "1.580", "6340"), ("SELL_INVENTORY", "2.560", "6340")))
        gold = (D("1.580") * D("6340")).quantize(D("0.01")) + (D("2.560") * D("6340")).quantize(D("0.01"))
        self.assertEqual(sum(p["gross"] for p in result["lines"].values()), gold)
        self.assertEqual(sum(p["total"] for p in result["lines"].values()), D("28000"))                     # the lines add up to the invoice amount
        self.assertEqual((result["sell_total"], result["buy_total"]), (D("28000"), D("0")))

    def test_old_gold_taken_in_gets_the_buying_price_and_the_average_discount_and_the_sale_absorbs_it(self):
        # sold 3 g at 6000 = 18000 gold; the customer brings 2 g at 5000 = 10000, 1.5 % off = 9850; the cash book proves a net of 12000 paid by the customer
        result = imp.derive_economics(self.bill("12000", ("SELL_INVENTORY", "3", "6000"), ("BUY_OLD_GOLD", "2", "5000")))
        buy = result["lines"][2]
        self.assertEqual((buy["gross"], buy["discount"], buy["total"]), (D("10000.00"), D("150.00"), D("9850.00")))
        self.assertEqual((result["buy_total"], result["sell_total"]), (D("9850.00"), D("21850.00")))        # net = sell - buy
        self.assertEqual(result["lines"][1]["making"], D("3850.00"))                                        # 21850 - 18000

    def test_a_scrap_only_bill_gets_a_buy_total_and_no_sale_side(self):
        result = imp.derive_economics(self.bill("9000", ("BUY_OLD_GOLD", "2", "5000"), direction="SHOP_PAYS_CUSTOMER"))
        self.assertEqual((result["sell_total"], result["buy_total"]), (D("0"), D("9850.00")))

    def test_an_implausible_making_leaves_the_sold_side_unknown_but_keeps_the_old_gold(self):
        high = imp.derive_economics(self.bill("30000", ("SELL_INVENTORY", "3", "5000"), ("BUY_OLD_GOLD", "1", "5000")))        # making would be 30000 + 4925 - 15000: far above 25 % of the gold
        self.assertEqual((high["sell_total"], high["buy_total"], 2 in high["lines"], 1 in high["lines"]), (None, D("4925.00"), True, False))
        self.assertIsNone(imp.derive_economics(self.bill("30000", ("SELL_INVENTORY", "3", "5000"))))                              # no old gold to keep: nothing derived

    def test_nothing_is_invented_when_it_cannot_be_derived(self):
        self.assertIsNone(imp.derive_economics(self.bill("100", ("SELL_INVENTORY", "3", "6000"))))          # the amount is below the gold: a discount, unknown making
        self.assertIsNone(imp.derive_economics(self.bill("30000", ("SELL_INVENTORY", "3", None))))          # no price on the row
        self.assertIsNone(imp.derive_economics(self.bill("30000", ("SELL_INVENTORY", "3", "6000"), completeness="UNKNOWN")))   # no cash proof


class SqlTests(unittest.TestCase):
    def test_one_transaction_text_as_hex_with_the_tables_collation_and_the_lookup_by_number(self):
        data = imp.transform([stock(1, "26261002", 1, bill="26100301", sold="2026-10-03")], [], [], [], LOOKUPS)
        sql = imp.insert_sql(data)
        self.assertTrue(sql.startswith("SET NAMES utf8mb4;\nSTART TRANSACTION;"))
        self.assertTrue(sql.rstrip().endswith("COMMIT;"))
        self.assertIn("COLLATE utf8mb4_unicode_ci", sql)
        self.assertIn("(SELECT id FROM invoices WHERE invoice_number = ", sql)
        self.assertIn("'LEGACY_MIGRATION'", sql)
        self.assertIn("'LEGACY'", sql)
        self.assertNotIn("خاتم", sql)

    def test_apply_needs_the_explicit_target(self):
        self.assertEqual(imp.main([]), 2)
        self.assertEqual(imp.main(["apply"]), 2)
        self.assertEqual(imp.main(["apply", "dev"]), 2)
        self.assertEqual(imp.main(["apply", "test", "masgold_v2"]), 2)


if __name__ == "__main__":
    unittest.main()
