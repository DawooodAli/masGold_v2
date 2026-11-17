from decimal import Decimal as D
import unittest

from services import kpi_rules


def line(kind, grams, making, item_type=""):
    return {"line_type": kind, "weight_grams": D(grams), "making_total": D(making), "item_type": item_type}


class MakingKindsTests(unittest.TestCase):
    def test_pieces_scrap_and_bullion_are_averaged_apart(self):
        lines = [line("SELL_INVENTORY", "10", "4000", "خاتم"), line("SELL_OLD_GOLD", "20", "1000"), line("SELL_INVENTORY", "10", "200", "سبيكة")]
        self.assertEqual(kpi_rules.making_item(lines, lines, 0, 0)["value"], "400.00 ج")
        self.assertEqual(kpi_rules.making_info_item("s", "t", kpi_rules.scrap_sales(lines), [])["value"], "50.00 ج")
        self.assertEqual(kpi_rules.making_info_item("b", "t", kpi_rules.bullion(lines), [])["value"], "20.00 ج")
        self.assertEqual(kpi_rules.making_info_item("b", "t", [], [])["status"], kpi_rules.GRAY)

    def test_scrap_sale_costs_nothing_in_the_kpi(self):
        self.assertEqual(kpi_rules.line_cost({"line_type": "SELL_OLD_GOLD", "category_cost": D("150")}), D("0"))
        self.assertEqual(kpi_rules.line_cost({"line_type": "SELL_INVENTORY", "category_cost": D("150")}), D("150"))

    def test_profit_percent_covers_crafted_pieces_only(self):
        def sale(kind, grams, making, cost, gross, item_type=""):
            return {**line(kind, grams, making, item_type), "category_cost": D(cost) if cost is not None else None, "gross_total": D(gross)}
        lines = [sale("SELL_INVENTORY", "10", "4000", "300", "50000", "خاتم"),      # 4000 - 3000 = 1000 on 50,000
                 sale("SELL_INVENTORY", "2", "900", "300", "10000", "سلسلة"),        # 900 - 600 = 300 on 10,000
                 sale("SELL_OLD_GOLD", "20", "1000", "999", "100000"),               # scrap sold by weight: not a crafted piece
                 sale("SELL_INVENTORY", "10", "200", "0", "60000", "سبيكة"),         # bar: not a crafted piece
                 sale("SELL_INVENTORY", "5", "500", None, "25000", "خاتم"),          # no cost: left out
                 {"line_type": "BUY_OLD_GOLD", "weight_grams": D("50"), "making_total": None}]   # a purchase is no sale
        item = kpi_rules.profit_pct_item(lines, [], days=7)
        self.assertEqual(item["value"], "2.17%")                            # 1300 / 60,000
        self.assertIn("إجمالي الربح 1,300.00 ج على 12.000 جرام (قيمة ذهبها 60,000.00 ج)", item["advice"])
        self.assertIn("108.33 ج للجرام", item["advice"])
        self.assertIn("المتوسط اليومي 185.71 ج", item["advice"])
        self.assertIn("1 بند بدون", item["advice"])
        self.assertEqual(item["status"], kpi_rules.GRAY)
        self.assertEqual(item["compare"], "—")
        self.assertEqual(kpi_rules.profit_pct_item([], lines)["value"], "—")
        self.assertEqual(kpi_rules.profit_pct_item(lines, lines)["compare"], "2.17% (+0.00 نقطة)")

    def test_a_supplier_piece_keeps_its_real_cost(self):
        self.assertEqual(kpi_rules.line_cost({"line_type": "SELL_INVENTORY", "real_cost": D("240"), "category_cost": D("200")}), D("240"))


if __name__ == "__main__":
    unittest.main()
