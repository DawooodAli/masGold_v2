"""Explicit opt-in rehearsal check: the «تكلفة الكسر» report on the REAL ReportRepository over masgold_rehearsal, READ ONLY. The cumulative grams and value per karat equal an independent SQL
aggregate over the same FINALIZED, not fully reversed scrap-purchase lines; nothing is written."""
from datetime import date
from decimal import Decimal as D
import os
import unittest

from services.report_service import ReportService
from tests.test_market_trip_book_mysql import BookCoreMySqlTests


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class ScrapCostMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        cls.service = ReportService(cls.h.session, today=date.today)

    def test_cumulative_grams_and_average_equal_an_independent_sql_aggregate(self):
        result = self.service.run("scrapcost", {"from": "2020-01-01", "to": "2030-12-31"})
        reported = {int(r["karat"].split()[-1]): r for r in result["tables"][0]["rows"]}
        rows = self.h.query(
            "SELECT k.karat_value, SUM(il.weight_grams) AS grams, SUM(il.line_total) AS paid FROM invoice_lines il JOIN invoices iv ON iv.id = il.invoice_id JOIN karats k ON k.id = il.karat_id "
            "WHERE il.line_type = 'BUY_OLD_GOLD' AND iv.invoice_status = 'FINALIZED' "
            "AND NOT EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type = 'INVOICE' AND c.source_id = iv.id AND c.correction_type = 'FULL_REVERSAL') GROUP BY k.karat_value")
        self.assertEqual({int(r["karat_value"]) for r in rows}, set(reported))
        for r in rows:
            grams, paid = D(str(r["grams"])), D(str(r["paid"]))
            row = reported[int(r["karat_value"])]
            self.assertEqual(row["c_grams"], grams)
            self.assertEqual(row["c_avg"], (paid / grams).quantize(D("0.01")))

    def test_the_report_writes_nothing(self):
        before = self.h.counts(("invoices", "invoice_lines", "gold_price_quotes"))
        self.service.run("scrapcost", {"from": "2020-01-01", "to": "2030-12-31"})
        self.assertEqual(self.h.counts(("invoices", "invoice_lines", "gold_price_quotes")), before)


if __name__ == "__main__":
    unittest.main()
