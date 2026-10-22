"""Cleaning of the old shop's sales history: one bill = the row with the highest id of its bill number; a row WITHOUT a bill number is its own bill (never merged with the others)."""
from datetime import date
from decimal import Decimal as D
import unittest

from services.sales_history_rules import clean_sales


class SalesHistoryRulesTests(unittest.TestCase):
    def test_rows_of_one_bill_collapse_to_the_highest_id(self):
        rows = [{"id": 1, "bill_id": "7", "date": date(2025, 3, 1), "weight": "5.000"}, {"id": 2, "bill_id": "7", "date": date(2025, 3, 1), "weight": "6.000"}]
        result = clean_sales(rows)
        self.assertEqual((result["counts"]["bills"], result["daily"][0]["grams"]), (1, D("6.000")))

    def test_rows_without_a_bill_number_are_each_their_own_bill(self):
        rows = [{"id": 1, "bill_id": None, "date": date(2025, 3, 1), "weight": "2.000"}, {"id": 2, "bill_id": "NULL", "date": date(2025, 3, 1), "weight": "3.000"},
                {"id": 3, "bill_id": "", "date": date(2025, 3, 2), "weight": "4.000"}, {"id": 4, "bill_id": "9", "date": date(2025, 3, 2), "weight": "1.000"}]
        result = clean_sales(rows)
        self.assertEqual(result["counts"]["bills"], 4)
        self.assertEqual([(d["bills"], d["grams"]) for d in result["daily"]], [(2, D("5.000")), (2, D("5.000"))])


if __name__ == "__main__":
    unittest.main()
