"""Explicit opt-in rehearsal check (migration 071): the «الموسمية والطلب» report and its line chart on the REAL ReportRepository over masgold_rehearsal, READ ONLY. The monthly grams equal an
independent SQL aggregate over the imported history; nothing is written."""
from datetime import date
from decimal import Decimal as D
import os
import unittest

from services.report_service import ReportService
from tests.test_market_trip_book_mysql import BookCoreMySqlTests


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class DemandMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        with cls.h.tx() as c:
            q = c.cursor()
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert int(q.fetchone()[0]) >= 71
            q.close()
        cls.service = ReportService(cls.h.session, today=date.today)

    def test_complete_months_equal_an_independent_sql_aggregate_before_the_opening_day(self):
        result = self.service.run("demand", {})
        monthly = next(t for t in result["tables"] if t["key"] == "monthly")["rows"]
        self.assertTrue(monthly, "the imported history feeds the report")
        opening = self.h.query("SELECT DATE(taken_at) AS day FROM position_snapshots WHERE snapshot_kind = 'OPENING' LIMIT 1")
        if opening:
            self.skipTest("an initial balance exists: the new system's own sales are also part of the series")
        sql = {f"{r['y']}-{int(r['m']):02d}": D(str(r["grams"])) for r in self.h.query(
            "SELECT YEAR(sale_date) AS y, MONTH(sale_date) AS m, SUM(grams) AS grams, SUM(bills_with_weight) AS w FROM sales_history_legacy GROUP BY y, m HAVING w > 0")}
        months_ar = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")
        reported = {}
        for r in monthly:
            name, year = r["month"].replace(" (لم ينتهِ)", "").rsplit(" ", 1)
            if isinstance(r["grams"], D):
                reported[f"{year}-{months_ar.index(name) + 1:02d}"] = r["grams"]
        self.assertEqual(reported, {k: v.quantize(D("0.001")) for k, v in sql.items()})

    def test_the_chart_reads_the_same_history_and_writes_nothing(self):
        before = self.h.counts(("sales_history_legacy", "invoices"))
        chart = self.service.demand_chart("M", "")
        self.assertTrue(chart["has_data"])
        self.assertEqual(len(chart["lines"]), 2)
        self.assertEqual(len(self.service.demand_chart("W", "2025")["lines"][0]["values"]), 53)
        self.assertEqual(self.h.counts(("sales_history_legacy", "invoices")), before)


if __name__ == "__main__":
    unittest.main()
