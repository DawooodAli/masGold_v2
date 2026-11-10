from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest

from services import market_reading
from services.currency_rates import CurrencyRatesService, FxSettings
from services.fx_history import FxHistory

END = date(2026, 11, 10)


def series(values):
    start = END - timedelta(days=len(values) - 1)
    return [(start + timedelta(days=i), D(str(v))) for i, v in enumerate(values)]


class ReadingTests(unittest.TestCase):
    def test_too_little_history_is_not_guessed(self):
        self.assertFalse(market_reading.gold_reading(series([6000] * 5))["available"])
        self.assertFalse(market_reading.fx_reading("الدولار", series([54] * 9))["available"])
        self.assertFalse(market_reading.fx_reading("اليورو", None)["available"])

    def test_gold_high_in_the_range_says_sell_and_mentions_the_scrap(self):
        values = [6000 + i * 10 for i in range(40)]                       # climbing, today at the top of the range
        found = market_reading.gold_reading(series(values), D("40.5"))
        self.assertEqual(found["tone"], "sell")
        self.assertIn("40.500", found["text"])
        self.assertIn("وقت مناسب للبيع", found["text"])
        self.assertIn("ليس توصية", found["text"])

    def test_gold_low_in_the_range_says_buy(self):
        values = [6400 - i * 10 for i in range(40)]
        found = market_reading.gold_reading(series(values))
        self.assertEqual(found["tone"], "buy")
        self.assertIn("شراء الكسر", found["text"])

    def test_middle_of_the_range_has_no_strong_signal(self):
        values = [6000 + (i % 2) * 100 for i in range(20)] + [6050]
        self.assertEqual(market_reading.gold_reading(series(values))["tone"], "muted")

    def test_dollar_near_the_top_pressures_gold_up(self):
        found = market_reading.fx_reading("الدولار", series([50 + i * 0.1 for i in range(40)]))
        self.assertTrue(found["available"])
        self.assertIn("لرفع سعر الذهب", found["text"])

    def test_window_is_ninety_days(self):
        found = market_reading.stats(series([100] * 30 + [50] * 5 + list(range(60, 160))))
        self.assertLessEqual(found["days"], market_reading.WINDOW_DAYS)


class HistoryTests(unittest.TestCase):
    def test_record_keeps_one_value_per_day_and_only_usd_eur(self):
        with tempfile.TemporaryDirectory() as folder:
            history = FxHistory(Path(folder) / "h.json", today=lambda: END)
            history.record([{"code": "USD", "price": "54.07"}, {"code": "EUR", "price": "60.59"}, {"code": "SAR", "price": "14.42"}])
            history.record([{"code": "USD", "price": "54.10"}])
            self.assertEqual(history.series("USD"), [(END, D("54.10"))])
            self.assertEqual(history.series("EUR"), [(END, D("60.59"))])
            self.assertEqual(history.series("SAR"), [])

    def test_a_broken_file_or_path_never_raises(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "h.json"
            path.write_text("{not json", encoding="utf-8")
            history = FxHistory(path, today=lambda: END)
            self.assertEqual(history.series("USD"), [])
            history.record([{"code": "USD", "price": "1"}])                  # overwrites the broken file
            self.assertEqual(history.series("USD"), [(END, D("1"))])
            FxHistory(Path(folder) / "file.txt" / "x" / "h.json").record([{"code": "USD", "price": "1"}])

    def test_the_rates_service_records_after_a_successful_fetch(self):
        class Provider:
            def fetch_rates(self):
                from datetime import datetime
                return {"rates": {"USD": 1, "EGP": 54, "EUR": 0.9}, "fetched_at": datetime(2026, 11, 10, 12, 0)}

        class Store:
            def value(self, key, default=None):
                return default

            def setValue(self, *a):
                pass

            def sync(self):
                pass

        with tempfile.TemporaryDirectory() as folder:
            history = FxHistory(Path(folder) / "h.json", today=lambda: END)
            service = CurrencyRatesService(FxSettings(Store()), Provider, history=history)
            service.snapshot(force=True)
            self.assertEqual(history.series("USD")[0][1], D("54.00"))
            self.assertEqual(history.series("EUR")[0][1], D("60.00"))


if __name__ == "__main__":
    unittest.main()
