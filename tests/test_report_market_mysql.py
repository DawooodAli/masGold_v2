"""Explicit opt-in rehearsal check (settlement step 2g): the «عهدة السوق والخسائر» report on the REAL ReportRepository over masgold_rehearsal (level >= 65), READ ONLY (no write at all).

Evidence: the four SQL reads run on MySQL, every row carries its reversal state, and the report's totals equal independent aggregates computed directly in SQL over the same rows."""
from decimal import Decimal as D
import os
import unittest

from services.report_service import ReportService
from tests.test_market_trip_book_mysql import BookCoreMySqlTests


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

    def table(self, key):
        return next(t for t in self.result["tables"] if t["key"] == key)

    def totals(self):
        return {t["label"]: t["value"] for t in self.result["totals"]}

    def test_every_table_is_available_and_the_rows_carry_their_state(self):
        for key in ("melts", "losses", "returns", "adjusts", "cash"):
            table = self.table(key)
            self.assertTrue(table.get("available", True), key)
            for row in table["rows"]:
                self.assertIn(row["state"], ("فعّال", "معكوس (حالة اليوم)"))

    def test_the_melt_totals_equal_an_independent_sql_aggregate(self):
        direct = self.h.query(
            """SELECT COALESCE(SUM(m.loss_eq21),0) AS loss, COALESCE(SUM(m.theoretical_eq21),0) AS theory, COUNT(*) AS n
               FROM market_trip_settle_melts m JOIN market_trip_entries e ON e.id = m.entry_id
               WHERE NOT EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type='MARKET_TRIP_ENTRY' AND c.source_id=e.id AND c.correction_type='FULL_REVERSAL')""")[0]
        t = self.totals()
        self.assertEqual(t["السبك — الفاقد (فعّال) (مكافئ عيار 21)"], D(str(direct["loss"])))
        self.assertEqual(t["السبك — النظري (فعّال) (مكافئ عيار 21)"], D(str(direct["theory"])))
        self.assertEqual(t["عدد عمليات السبك الفعّالة"], int(direct["n"]))

    def test_the_cash_totals_equal_an_independent_sql_aggregate(self):
        direct = {r["entry_kind"]: D(str(r["s"])) for r in self.h.query(
            """SELECT e.entry_kind, COALESCE(SUM(s.amount),0) AS s FROM market_trip_settle_cash s JOIN market_trip_entries e ON e.id = s.entry_id
               WHERE NOT EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type='MARKET_TRIP_ENTRY' AND c.source_id=e.id AND c.correction_type='FULL_REVERSAL')
               GROUP BY e.entry_kind""")}
        t = self.totals()
        self.assertEqual(t["مصروف (فعّال)"], direct.get("ST_EXPENSE", D("0.00")))
        self.assertEqual(t["إعدام (فعّال)"], direct.get("ST_CASH_OFF", D("0.00")))
        self.assertEqual(t["إيداع في حساب (فعّال)"], direct.get("ST_CASH_BANK", D("0.00")))

    def test_the_loss_and_return_lines_equal_the_stored_line_count(self):
        n = int(self.h.query("SELECT COUNT(*) AS n FROM market_trip_settle_lines")[0]["n"])
        self.assertEqual(len(self.table("losses")["rows"]) + len(self.table("returns")["rows"]), n)
        adj = int(self.h.query("SELECT COUNT(*) AS n FROM market_trip_settle_adjust")[0]["n"])
        self.assertEqual(len(self.table("adjusts")["rows"]), adj)

    def test_the_report_writes_nothing(self):
        before = self.h.counts(("market_trip_entries", "market_trip_settle_cash", "market_trip_settle_lines", "market_trip_settle_adjust", "market_trip_settle_moves"))
        self.service.run("market", dict(self.RANGE))
        self.assertEqual(self.h.counts(("market_trip_entries", "market_trip_settle_cash", "market_trip_settle_lines", "market_trip_settle_adjust", "market_trip_settle_moves")), before)


if __name__ == "__main__":
    unittest.main()
