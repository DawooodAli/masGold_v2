"""«تنبيهات المخزون» (owner request, 2026-10-21): dead stock (six months, never on an invoice) and items about to run out (sold at least 3 times in 90 days and the stock covers fewer than
14 days of sales), per karat. Through the REAL ReportService over a fake repository with hand-checkable numbers (the arithmetic is in the comment next to each expectation)."""
from datetime import datetime
from decimal import Decimal as D
import unittest

from services import report_service as rs
from services.report_service import ReportError, ReportService
from tests import test_report_service as base
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import session_for

TODAY = base.TODAY                                                 # 2026-10-20 ; six months back = 2026-04-23


def old(days):
    return datetime(2026, 10, 20, 12) - __import__("datetime").timedelta(days=days)


class AlertsRepo(base.FakeRepo):
    def stock_items(self, statuses):
        def item(i, karat, kind, factory, size, weight, acquired, invoice=None):
            return {"item_id": i, "weight_grams": D(weight), "size": size, "stock_classification": "NEW_STOCK", "inventory_status": "IN_STOCK", "acquisition_source": "SUPPLIER_PURCHASE",
                    "acquired_at": acquired, "karat_id": 1, "karat_value": karat, "item_type": kind, "category": "ذهب", "factory": factory, "barcode": f"B{i}", "invoice_id": invoice,
                    "invoice_number": None, "invoice_line_type": None}
        return [item(1, 21, "دبلة", "النور", "18", "5.000", old(200)),                  # dead: 200 days
                item(2, 21, "دبلة", "النور", "18", "5.500", old(181)),                  # dead: 181 days
                item(3, 21, "خاتم", "الأمل", "", "4.000", old(180)),                    # dead: exactly 180 days (the limit is included)
                item(4, 21, "خاتم", "الأمل", "", "4.000", old(179)),                    # NOT dead: 179 days
                item(5, 18, "غويشة", "النور", "", "10.000", old(400)),                  # dead 18K
                item(6, 21, "دبلة", "النور", "19", "6.000", old(300), invoice=77),      # old but it came back from an invoice: not dead
                item(7, 21, "سلسلة", "النور", "", "8.000", old(10)),                    # recent
                item(8, 21, "دبلة", "النور", "20", "5.000", old(10))]                   # recent, another size

    def gold_sold_pieces(self, start, end):
        def sold(i, kind, factory, karat, size, days_ago, reversal=None):
            return {"item_id": 100 + i, "item_type": kind, "category": "ذهب", "factory_id": 1, "factory": factory, "karat_value": karat, "weight_grams": D("5.000"), "size": size,
                    "invoice_id": 200 + i, "invoice_number": f"INV{i}", "doc_date": old(days_ago), "reversal_id": reversal}
        rows = [sold(i, "دبلة", "النور", 21, "18", 1 + i * 6) for i in range(13)]                                                  # A: 13 sales in 90 days (days 1,7,...,73); 5 of them in the last 30 days
        rows += [sold(20 + i, "دبلة", "النور", 21, "20", d) for i, d in enumerate((2, 30, 60))]                                         # B: 3 sales, stock 1
        rows += [sold(30 + i, "خاتم", "الأمل", 21, "", d) for i, d in enumerate((3, 40, 70))] + [sold(33, "خاتم", "الأمل", 21, "", 10, reversal=3)]       # C: 3 sales (+1 reversed, not counted), stock 2
        rows += [sold(40, "سلسلة", "النور", 21, "", 30), sold(41, "سلسلة", "النور", 21, "", 31)]                                          # D: only 2 sales: below the minimum
        rows += [sold(50 + i, "غويشة", "النور", 18, "", d) for i, d in enumerate((1, 2, 3))]                                            # E: 3 sales, stock 1
        rows += [sold(60 + i, "سوار", "النور", 24, "", d) for i, d in enumerate((4, 5, 6, 7))]                                          # F: 4 sales, NO stock
        return [r for r in rows if start <= r["doc_date"] < end]


class AlertsCase(base.Case):
    def setUp(self):
        super().setUp()
        self.service = ReportService(self.session, transaction_factory=self.service._transaction, repository=AlertsRepo, authorization=self.authorization, today=lambda: TODAY)

    def run_alerts(self, **filters):
        return self.service.run("alerts", filters)

    def table(self, result, key):
        return next(t for t in result["tables"] if t["key"] == key)


class DeadStockTests(AlertsCase):
    def test_six_months_never_on_an_invoice_and_the_limit_day_is_included(self):
        result = self.run_alerts()
        rows21 = {(r["type"], r["factory"]): r for r in self.table(result, "dead_21")["rows"]}
        self.assertEqual(set(rows21), {("دبلة", "النور"), ("خاتم", "الأمل")})                       # the 179-day ring, the invoice-linked and the recent pieces are not dead
        self.assertEqual((rows21[("دبلة", "النور")]["count"], rows21[("دبلة", "النور")]["weight"], rows21[("دبلة", "النور")]["oldest"]), (2, D("10.500"), 200))      # 5.000 + 5.500
        self.assertEqual((rows21[("خاتم", "الأمل")]["count"], rows21[("خاتم", "الأمل")]["weight"], rows21[("خاتم", "الأمل")]["oldest"]), (1, D("4.000"), 180))
        self.assertEqual([(r["type"], r["count"], r["weight"]) for r in self.table(result, "dead_18")["rows"]], [("غويشة", 1, D("10.000"))])      # a table per karat

    def test_cards_per_karat_with_the_weight_on_top_and_the_count_under_it(self):
        cards = {x["label"]: (x["value"], x["note"], x["group"], x["karat"]) for x in self.run_alerts()["totals"] if x["group"] == "dead"}
        self.assertEqual(cards["راكد عيار 21"], ("14.500 جرام", "3 قطعة • أقدمها 200 يوم", "dead", 21))                 # 10.500 + 4.000
        self.assertEqual(cards["راكد عيار 18"][0], "10.000 جرام")
        self.assertNotIn("24.500", " ".join(v[0] for v in cards.values()))                                         # karats are never added

    def test_the_tables_are_tagged_with_their_karat_for_the_colors(self):
        self.assertEqual({t["key"]: t["karat"] for t in self.run_alerts()["tables"] if t["key"].startswith("dead_")}, {"dead_21": 21, "dead_18": 18})

    def test_filters(self):
        self.assertEqual([t["key"] for t in self.run_alerts(karat="18")["tables"] if t["key"].startswith("dead_")], ["dead_18"])
        self.assertEqual({r["type"] for r in self.table(self.run_alerts(text="خاتم"), "dead_21")["rows"]}, {"خاتم"})
        with self.assertRaises(ReportError):
            self.run_alerts(karat="19")


class WeightBandsTests(AlertsCase):
    """The weights (owner request): one-gram bands PER KARAT AND PER ITEM TYPE with the stock, the sales and the days covered, as INFORMATION (the owner decides; nothing is flagged)."""

    def bands(self, result, karat):
        return {(r["type"], r["band"]): r for r in self.table(result, f"weights_{karat}")["rows"]}

    def test_the_bands_are_per_type_inside_each_karat(self):
        rows = self.bands(self.run_alerts(), 21)
        self.assertEqual(list(rows), [("خاتم", "4 – 5 جرام"), ("خاتم", "5 – 6 جرام"), ("دبلة", "5 – 6 جرام"), ("دبلة", "6 – 7 جرام"), ("سلسلة", "5 – 6 جرام"), ("سلسلة", "8 – 9 جرام")])   # type, then weight
        ring = rows[("دبلة", "5 – 6 جرام")]
        self.assertEqual((ring["stock"], ring["weight"], ring["sold30"], ring["sold90"], ring["cover"]), (3, D("15.500"), 7, 16, "16"))     # 5.000 + 5.500 + 5.000; 16 sold in 90 days: 3 x 90 / 16 = 16.9
        self.assertEqual(rows[("دبلة", "6 – 7 جرام")]["stock"], 1)                                                                        # exactly 6.000 g is in 6 – 7 (the lower limit is inside)

    def test_a_type_is_never_mixed_with_another_type_of_the_same_weight(self):
        rows = self.bands(self.run_alerts(), 21)
        self.assertEqual((rows[("خاتم", "5 – 6 جرام")]["stock"], rows[("خاتم", "5 – 6 جرام")]["sold90"]), (0, 3))                         # the 5 g ring sales are not the 5 g dabla stock
        self.assertEqual((rows[("خاتم", "4 – 5 جرام")]["stock"], rows[("خاتم", "4 – 5 جرام")]["weight"], rows[("خاتم", "4 – 5 جرام")]["cover"]), (2, D("8.000"), "—"))

    def test_a_band_with_sales_but_no_stock_and_the_last_band(self):
        result = self.run_alerts()
        self.assertEqual(self.bands(result, 21)[("خاتم", "5 – 6 جرام")]["cover"], "نفد")
        self.assertEqual(self.bands(result, 18)[("غويشة", "10 جرام فأكثر")]["stock"], 1)                                                  # the 10.000 g piece
        self.assertEqual(self.bands(result, 18)[("غويشة", "5 – 6 جرام")]["cover"], "نفد")
        self.assertEqual(self.bands(result, 24)[("سوار", "5 – 6 جرام")]["cover"], "نفد")

    def test_a_table_per_karat_tagged_for_the_colors_and_filters_apply(self):
        result = self.run_alerts()
        self.assertEqual({t["key"]: t["karat"] for t in result["tables"] if t["key"].startswith("weights_")}, {"weights_24": 24, "weights_21": 21, "weights_18": 18})
        self.assertEqual({t["key"] for t in self.run_alerts(karat="18")["tables"] if t["key"].startswith("weights_")}, {"weights_18"})
        self.assertEqual({r["type"] for r in self.table(self.run_alerts(text="خاتم"), "weights_21")["rows"]}, {"خاتم"})

    def test_without_the_invoice_right_only_the_stock_columns_are_shown(self):
        session = session_for(ALL_RIGHTS - {"INVOICE_VIEW"})
        limited = ReportService(session, transaction_factory=self.service._transaction, repository=AlertsRepo, authorization=FakeAuthorization(session), today=lambda: TODAY)
        row = next(r for r in next(t for t in limited.run("alerts", {})["tables"] if t["key"] == "weights_21")["rows"] if (r["type"], r["band"]) == ("دبلة", "5 – 6 جرام"))
        self.assertEqual((row["stock"], row["sold30"], row["sold90"], row["cover"]), (3, None, None, "—"))


class RunningOutTests(AlertsCase):
    def low(self, result, karat):
        return {(r["type"], r["factory"], r["size"]): r for r in self.table(result, f"low_{karat}")["rows"]}

    def test_stock_divided_by_the_daily_sales_rate_gives_the_days_it_covers(self):
        rows = self.low(self.run_alerts(), 21)
        self.assertEqual(set(rows), {("دبلة", "النور", "18")})                                       # only A is an alert; B (30 days), C (60 days) and D (too few sales) are not
        a = rows[("دبلة", "النور", "18")]
        self.assertEqual((a["sold30"], a["sold90"], a["stock"], a["cover"]), (5, 13, 2, "13"))      # 2 in stock x 90 / 13 sales = 13.8 days
        self.assertEqual([r["cover"] for r in self.table(self.run_alerts(), "low_21")["rows"]], ["13"])

    def test_an_item_with_no_stock_left_is_marked_finished_and_a_covered_one_is_not_listed(self):
        result = self.run_alerts()
        self.assertEqual(self.low(result, 24)[("سوار", "النور", "—")]["cover"], "نفد")             # 4 sales, none in stock
        self.assertNotIn("low_18", {t["key"] for t in result["tables"]})                          # E: 1 x 90 / 3 = 30 days: no alert, no table
        self.assertNotIn(("خاتم", "الأمل", "—"), self.low(result, 21))                            # the reversed sale is not counted: 3 sales, covered for 60 days

    def test_the_size_separates_items_and_cards_count_the_alert_items_per_karat(self):
        result = self.run_alerts()
        cards = {x["label"]: (x["value"], x["group"], x["karat"]) for x in result["totals"] if x["group"] == "low"}
        self.assertEqual(cards["قارب النفاد عيار 21"], ("1 صنف", "low", 21))
        self.assertEqual(cards["قارب النفاد عيار 24"], ("1 صنف", "low", 24))
        self.assertEqual(set(self.table(result, "low_21")["rows"][0]), {"type", "factory", "size", "sold30", "sold90", "stock", "cover"})

    def test_the_invoice_right_is_needed_for_the_sales_rate_and_dead_stock_stays(self):
        session = session_for(ALL_RIGHTS - {"INVOICE_VIEW"})
        limited = ReportService(session, transaction_factory=self.service._transaction, repository=AlertsRepo, authorization=FakeAuthorization(session), today=lambda: TODAY)
        result = limited.run("alerts", {})
        self.assertFalse(next(t for t in result["tables"] if t["key"] == "low")["available"])
        self.assertTrue(any(t["key"] == "dead_21" for t in result["tables"]))
        self.assertFalse(any(x["group"] == "low" for x in result["totals"]))

    def test_the_inventory_right_is_required_and_the_period_is_not_used(self):
        session = session_for(ALL_RIGHTS - {"INVENTORY_VIEW"})
        with self.assertRaises(PermissionError):
            ReportService(session, transaction_factory=self.service._transaction, repository=AlertsRepo, authorization=FakeAuthorization(session), today=lambda: TODAY).run("alerts", {})
        self.assertFalse(any(line.startswith("الفترة") for line in self.run_alerts()["applied"]))


ALL_RIGHTS = base.ALL


if __name__ == "__main__":
    unittest.main()
