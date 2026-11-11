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

    def test_a_supplier_piece_keeps_its_real_cost(self):
        self.assertEqual(kpi_rules.line_cost({"line_type": "SELL_INVENTORY", "real_cost": D("240"), "category_cost": D("200")}), D("240"))


if __name__ == "__main__":
    unittest.main()
