"""Explicit opt-in rehearsal check (settlement step 2g): the «عهدة السوق والخسائر» report (TOTALS ONLY) on the REAL ReportRepository over masgold_rehearsal (level >= 65), READ ONLY.

Evidence: the two SQL reads run on MySQL and the report's totals equal independent aggregates computed directly in SQL over the same effective rows; nothing is written."""
from decimal import Decimal as D, ROUND_HALF_UP
import os
import unittest

from services.report_service import ReportService
from tests.test_market_trip_book_mysql import BookCoreMySqlTests

NOT_REVERSED = "NOT EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type='MARKET_TRIP_ENTRY' AND c.source_id=e.id AND c.correction_type='FULL_REVERSAL')"


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class MarketReportMySqlTests(unittest.TestCase):
    RANGE = {"from": "2020-01-01", "to": "2030-12-31"}

    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        with cls.h.tx() as c:
            q = c.cursor()
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert int(q.fetchone()[0]) >= 65
            q.close()
        cls.service = ReportService(cls.h.session)
        cls.result = cls.service.run("market", dict(cls.RANGE))
        cls.summary = {r["label"]: r["grams"] for r in next(t for t in cls.result["tables"] if t["key"] == "summary")["rows"]}

    def test_the_melt_loss_equals_an_independent_sql_aggregate(self):
        row = self.h.query(f"SELECT COALESCE(SUM(m.weight_loss_eq21),0) AS w, COALESCE(SUM(m.fineness_loss_eq21),0) AS f FROM market_trip_settle_melts m "
                           f"JOIN market_trip_entries e ON e.id = m.entry_id WHERE {NOT_REVERSED}")[0]
        self.assertEqual(self.summary["فاقد السبك — وزن"], D(str(row["w"])))
        self.assertEqual(self.summary["فاقد السبك — عيار"], D(str(row["f"])))

    def test_the_losses_equal_an_independent_sql_aggregate_in_21k(self):
        rows = self.h.query(f"SELECT l.classification AS c, l.source AS s, l.weight_grams AS w, k.karat_value AS k FROM market_trip_settle_lines l JOIN market_trip_entries e ON e.id = l.entry_id "
                            f"LEFT JOIN karats k ON k.id = l.karat_id WHERE e.entry_kind = 'ST_LOSS' AND {NOT_REVERSED}")
        sums = {"CRUCIBLE": D("0.000"), "MARKET": D("0.000")}
        for r in rows:
            grams = D(str(r["w"]))
            if r["s"] != "BAR":
                grams = (grams * int(r["k"]) / 21).quantize(D("0.001"), rounding=ROUND_HALF_UP)
            sums[r["c"]] += grams
        self.assertEqual(self.summary["خسية البوتقة"], sums["CRUCIBLE"])
        self.assertEqual(self.summary["خسية السوق"], sums["MARKET"])
        self.assertEqual(self.summary["إجمالي الفقد والخسية"], self.summary["إجمالي فاقد السبك"] + sums["CRUCIBLE"] + sums["MARKET"])

    def test_the_report_writes_nothing(self):
        tables = ("market_trip_entries", "market_trip_settle_cash", "market_trip_settle_lines", "market_trip_settle_adjust", "market_trip_settle_moves")
        before = self.h.counts(tables)
        self.service.run("market", dict(self.RANGE))
        self.assertEqual(self.h.counts(tables), before)


if __name__ == "__main__":
    unittest.main()
