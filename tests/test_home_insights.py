from datetime import date, timedelta
from decimal import Decimal as D
import unittest

from services.dashboard_service import DashboardService
from services.price_alert import PriceAlertSettings, evaluate, parse_price


class MemoryStore:
    def __init__(self):
        self.values = {}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def sync(self):
        pass


class InsightTests(unittest.TestCase):
    def test_week_compare_uses_the_same_weekday_last_week(self):
        days = [{"day": f"d{i}", "total": str(i * 100)} for i in range(14)]
        days[-1]["total"], days[-8]["total"] = "1500.00", "1000.00"
        found = DashboardService.week_compare(days)
        self.assertEqual((found["today"], found["before"], found["change"]), ("1500.00", "1000.00", "50"))
        days[-8]["total"] = "0"
        self.assertIsNone(DashboardService.week_compare(days)["change"])
        self.assertIsNone(DashboardService.week_compare(days[:5]))

    def test_year_range_position(self):
        start = date(2026, 1, 1)
        series = [(start + timedelta(days=i), D(p), D(p) + 25) for i, p in enumerate((6000, 6200, 6100, 6150))]
        found = DashboardService.year_range(series)
        self.assertEqual((found["low"], found["high"], found["now"], found["position"], found["days"]), ("6000", "6200", "6150", 75, 4))
        self.assertIsNone(DashboardService.year_range(series[:1]))

    def test_scrap_vs_sold_is_per_karat(self):
        found = DashboardService.scrap_vs_sold([{"karat_value": 21, "grams": "10.5", "paid": "1"}], [{"karat_value": 21, "grams": "4"}, {"karat_value": 18, "grams": "2"}])
        self.assertEqual([(r["karat"], r["bought"], r["sold"]) for r in found], [(21, "10.500", "4.000"), (18, "0.000", "2.000")])


class PriceAlertTests(unittest.TestCase):
    def test_parse_and_evaluate(self):
        self.assertIsNone(parse_price(" "))
        self.assertEqual(parse_price("6,150"), D("6150.00"))
        with self.assertRaises(ValueError):
            parse_price("abc")
        with self.assertRaises(ValueError):
            parse_price("-5")
        self.assertEqual(evaluate(D("6000"), None, "5990", "6100")[0]["kind"], "buy")
        self.assertEqual(evaluate(None, D("6400"), "6300", "6400")[0]["kind"], "sell")
        self.assertEqual(evaluate(D("6000"), D("6400"), "6100", "6200"), [])
        self.assertEqual(evaluate(D("6000"), D("6400"), None, None), [])

    def test_settings_round_trip_and_clear(self):
        settings = PriceAlertSettings(MemoryStore())
        settings.save("6000", "6400.5")
        self.assertEqual((settings.buy_below(), settings.sell_above()), (D("6000.00"), D("6400.50")))
        settings.save("", "")
        self.assertEqual((settings.buy_below(), settings.sell_above()), (None, None))
        with self.assertRaises(ValueError):
            settings.save("x", "")


if __name__ == "__main__":
    unittest.main()
