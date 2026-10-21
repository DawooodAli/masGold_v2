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


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class GoldMovementMySqlTests(unittest.TestCase):
    """«حركة الذهب»: the two reads on real MySQL equal independent SQL aggregates over the same rows (read only)."""
    RANGE = {"from": "2020-01-01", "to": "2030-12-31"}

    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        cls.result = ReportService(cls.h.session).run("gold", dict(cls.RANGE))

    def test_the_sold_pieces_per_karat_equal_an_independent_aggregate(self):
        rows = self.h.query("""SELECT k.karat_value AS k, COUNT(*) AS n, COALESCE(SUM(ii.weight_grams),0) AS w FROM invoice_lines il JOIN invoices iv ON iv.id = il.invoice_id
                               JOIN inventory_items ii ON ii.id = il.inventory_item_id JOIN karats k ON k.id = ii.karat_id
                               WHERE il.line_type = 'SELL_INVENTORY' AND iv.invoice_status = 'FINALIZED'
                                 AND NOT EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type='INVOICE' AND c.source_id=iv.id AND c.correction_type='FULL_REVERSAL')
                               GROUP BY k.karat_value""")
        direct = {f"مبيع عيار {r['k']}": (f"{D(str(r['w'])):,.3f} جرام", f"{r['n']} قطعة") for r in rows}
        shown = {x["label"]: (x["value"], x["note"]) for x in self.result["totals"]}
        self.assertEqual(shown, direct)

    def test_the_scrap_purchases_equal_an_independent_aggregate(self):
        rows = self.h.query("""SELECT k.karat_value AS k, COALESCE(SUM(m.weight_delta_grams),0) AS w, SUM(m.movement_code = 'CUSTOMER_BUY_IN') AS n FROM old_gold_movements m JOIN karats k ON k.id = m.karat_id
                               WHERE m.balance_effect='COUNTED' AND m.weight_delta_grams IS NOT NULL AND m.movement_code IN ('CUSTOMER_BUY_IN','CUSTOMER_BUY_IN_REVERSAL') GROUP BY k.karat_value""")
        direct = {f"عيار {r['k']}": (int(r["n"]), D(str(r["w"]))) for r in rows}
        shown = {r["karat"]: (r["count"], r["weight"]) for r in next(t for t in self.result["tables"] if t["key"] == "scrap")["rows"]}
        self.assertEqual(shown, direct)


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class CustomersReportMySqlTests(unittest.TestCase):
    """«العملاء»: the open items on real MySQL add up to the real balances (independent SQL aggregate), read only."""

    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        cls.result = ReportService(cls.h.session).run("customers", {"inactive": "1"})

    def test_the_two_cards_equal_the_sums_of_the_real_balances(self):
        rows = self.h.query("SELECT c.id, COALESCE(SUM(t.amount_delta),0) AS b FROM customers c LEFT JOIN customer_transactions t ON t.customer_id = c.id GROUP BY c.id")
        owes = sum((D(str(r["b"])) for r in rows if D(str(r["b"])) > 0), D("0.00"))
        credit = sum((-D(str(r["b"])) for r in rows if D(str(r["b"])) < 0), D("0.00"))
        cards = {x["label"]: x["value"] for x in self.result["totals"]}
        self.assertEqual(cards["إجمالي اللي عليهم (على العملاء)"], owes)
        self.assertEqual(cards["إجمالي اللي علينا (للعملاء)"], credit)

    def test_dead_cash_equals_an_independent_aggregate(self):
        row = self.h.query("SELECT COALESCE(SUM(CASE WHEN is_reversed = 0 THEN amount ELSE 0 END),0) AS active, SUM(is_reversed = 0) AS n, COUNT(*) AS everything FROM dead_cash_writeoffs")[0]
        dead = ReportService(self.h.session).run("deadcash", {"inactive": "1"})
        cards = {x["label"]: x["value"] for x in dead["totals"]}
        self.assertEqual(cards["إجمالي الديون المعدومة"], D(str(row["active"])))
        self.assertEqual(cards["عدد الديون المعدومة"], int(row["n"] or 0))
        self.assertEqual(len(next(t for t in dead["tables"] if t["key"] == "deadcash")["rows"]), int(row["everything"]))

    def test_every_customer_rows_add_up_to_the_customer_balance(self):
        per = {}
        for r in next(t for t in self.result["tables"] if t["key"] == "customers")["rows"]:
            per[r["name"]] = per.get(r["name"], D("0.00")) + (r["owes"] or D("0.00")) - (r["credit"] or D("0.00"))
        for r in self.h.query("SELECT c.name, COALESCE(SUM(t.amount_delta),0) AS b FROM customers c JOIN customer_transactions t ON t.customer_id = c.id GROUP BY c.id, c.name HAVING SUM(t.amount_delta) <> 0"):
            self.assertEqual(per.get(r["name"]), D(str(r["b"])), r["name"])


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class InventoryAlertsMySqlTests(unittest.TestCase):
    """«تنبيهات المخزون» on real MySQL: the dead stock per karat equals an independent aggregate (read only)."""

    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        cls.service = ReportService(cls.h.session)
        cls.result = cls.service.run("alerts", {})

    def test_the_dead_stock_equals_an_independent_aggregate(self):
        rows = self.h.query("""SELECT k.karat_value AS k, COUNT(*) AS n, COALESCE(SUM(ii.weight_grams),0) AS w FROM inventory_items ii JOIN karats k ON k.id = ii.karat_id
                               WHERE ii.inventory_status = 'IN_STOCK' AND ii.acquired_at <= DATE_SUB(CURDATE(), INTERVAL 180 DAY)
                                 AND NOT EXISTS (SELECT 1 FROM invoice_lines il JOIN invoices iv ON iv.id = il.invoice_id WHERE il.inventory_item_id = ii.id AND iv.invoice_status <> 'DRAFT')
                               GROUP BY k.karat_value""")
        direct = {f"راكد عيار {r['k']}": (f"{D(str(r['w'])):,.3f} جرام", f"{r['n']} قطعة") for r in rows}
        shown = {x["label"]: (x["value"], x["note"].split(" • ")[0]) for x in self.result["totals"] if x["group"] == "dead"}
        self.assertEqual(shown, direct)

    def test_the_report_writes_nothing_and_every_table_is_readable(self):
        before = self.h.counts(("inventory_items", "invoice_lines", "old_gold_movements"))
        ReportService(self.h.session).run("alerts", {})
        self.assertEqual(self.h.counts(("inventory_items", "invoice_lines", "old_gold_movements")), before)
        for t in self.result["tables"]:
            self.assertTrue(t.get("available", True), t["key"])


if __name__ == "__main__":
    unittest.main()
