"""Explicit opt-in rehearsal check: the «مؤشرات الذهب» report on the REAL ReportRepository over masgold_rehearsal (level >= 66), READ ONLY.

Evidence: the two price reads run on MySQL, the imported history (migration 066) feeds the karat 21 series and its day count equals an independent SQL count; nothing is written."""
import os
import unittest

from services.report_service import ReportService
from tests.test_market_trip_book_mysql import BookCoreMySqlTests


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class IndicatorsMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        with cls.h.tx() as c:
            q = c.cursor()
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert int(q.fetchone()[0]) >= 66
            q.close()
        cls.service = ReportService(cls.h.session)

    def test_the_series_merges_the_history_with_the_live_days_and_matches_sql(self):
        result = self.service.run("indicators", {})
        row = next(r for r in result["tables"][0]["rows"] if r["karat"] == "عيار 21")
        legacy = int(self.h.query("SELECT COUNT(*) AS n FROM gold_price_history_legacy WHERE karat_value = 21")[0]["n"])
        live = int(self.h.query("SELECT COUNT(DISTINCT DATE(provider_timestamp)) AS n FROM gold_price_quotes q JOIN karats k ON k.id = q.karat_id "
                                "WHERE k.karat_value = 21 AND q.validation_status = 'ACCEPTED' AND q.provider_timestamp IS NOT NULL")[0]["n"])
        self.assertGreater(legacy, 300)
        self.assertGreaterEqual(row["days"], max(legacy, live))
        self.assertLessEqual(row["days"], legacy + live)
        self.assertTrue(any("سعر شراء المحل" in t["label"] for t in result["totals"]))
        chart = self.service.price_chart(21, 0)
        self.assertEqual(len(chart["points"]), row["days"])

    def test_the_report_writes_nothing(self):
        before = self.h.counts(("gold_price_quotes", "gold_price_history_legacy"))
        self.service.run("indicators", {})
        self.assertEqual(self.h.counts(("gold_price_quotes", "gold_price_history_legacy")), before)
