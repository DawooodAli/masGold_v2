"""Explicit opt-in rehearsal check: the KPI report on the REAL ReportRepository over masgold_rehearsal, READ ONLY (the SELECT of `kpi_lines` is executed and the report is built; nothing is written)."""
from datetime import date
import os
import unittest

from services.report_service import ReportService
from tests.test_market_trip_book_mysql import BookCoreMySqlTests


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class KpiMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        cls.service = ReportService(cls.h.session, today=date.today)

    def test_the_report_runs_on_the_real_schema_and_has_five_items(self):
        result = self.service.run("kpi", {"from": "2020-01-01", "to": "2030-12-31"})
        self.assertEqual([r["key"] for r in result["tables"][0]["rows"]], ["sales", "making", "scrap", "profit", "expenses"])

    def test_the_report_writes_nothing(self):
        before = self.h.counts(("invoices", "invoice_lines", "gold_price_quotes"))
        self.service.run("kpi", {"from": "2020-01-01", "to": "2030-12-31"})
        self.assertEqual(self.h.counts(("invoices", "invoice_lines", "gold_price_quotes")), before)


if __name__ == "__main__":
    unittest.main()
