"""«عهدة السوق والخسائر» report: ONLY the totals of the loss (owner request): melt loss (weight + fineness), crucible loss and market loss in 21K-equivalent grams, through the REAL
ReportService over a fake repository with hand-checkable numbers. The SQL of ReportRepository is NOT exercised here (no MySQL): the static checks read the statements, the live read is in
the opt-in MySQL module."""
from datetime import datetime
from decimal import Decimal as D
import inspect
import unittest

from repositories import report_repository
from services.report_service import ReportService
from tests import test_report_service as base
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import session_for

MONTH = {"from": "2026-10-01", "to": "2026-10-31"}


def at(day):
    return datetime(2026, 10, day, 12)


class MarketRepo(base.FakeRepo):
    missing = ()

    def has_table(self, name):
        return name not in self.missing and super().has_table(name)

    def trip_melts(self, start, end):
        def melt(entry, day, weight_loss, fineness_loss, reversal=None):
            return {"entry_id": entry, "trip_id": 1, "entry_no": entry, "doc_date": at(day), "workshop": "ورشة", "input_weight_grams": D("10.000"), "output_weight_grams": D("9.000"),
                    "fineness_permille": 850, "theoretical_eq21": D("0"), "actual_eq21": D("0"), "loss_eq21": D(weight_loss) + D(fineness_loss), "weight_loss_eq21": D(weight_loss),
                    "fineness_loss_eq21": D(fineness_loss), "reversal_id": reversal}
        rows = [melt(2, 3, "0.197", "0.569"), melt(3, 6, "-0.100", "-0.060"), melt(4, 7, "0.100", "0.420", reversal=5)]
        return [r for r in rows if start <= r["doc_date"].date() < end]

    def trip_settle_lines(self, start, end):
        def line(entry, kind, day, source, karat, weight, cls, reversal=None):
            return {"entry_id": entry, "trip_id": 1, "entry_no": entry, "entry_kind": kind, "doc_date": at(day), "line_no": 1, "source": source, "karat_value": karat,
                    "weight_grams": D(weight), "classification": cls, "reversal_id": reversal}
        rows = [line(20, "ST_LOSS", 8, "BAR", None, "0.500", "CRUCIBLE"), line(21, "ST_LOSS", 8, "RAW", 18, "1.750", "MARKET"),      # 1.750 x 18 / 21 = 1.500 (eq21)
                line(22, "ST_LOSS", 9, "LOGO", 24, "0.700", "CRUCIBLE"),                                                       # 0.700 x 24 / 21 = 0.800
                line(23, "ST_LOSS", 9, "BAR", None, "9.000", "MARKET", reversal=6),                                            # reversed: not counted
                line(24, "ST_RET_MKT", 10, "BAR", None, "20.000", None)]                                                       # a market return is not a loss
        return [r for r in rows if start <= r["doc_date"].date() < end]


class MarketCase(base.Case):
    def setUp(self):
        super().setUp()
        MarketRepo.missing = ()
        self.service = ReportService(self.session, transaction_factory=self.service._transaction, repository=MarketRepo, authorization=self.authorization, today=lambda: base.TODAY)

    def service_for(self, permissions, repo=MarketRepo):
        session = session_for(permissions)
        return ReportService(session, transaction_factory=self.service._transaction, repository=repo, authorization=FakeAuthorization(session), today=lambda: base.TODAY)

    def summary(self, result):
        return {r["label"]: r["grams"] for r in next(t for t in result["tables"] if t["key"] == "summary")["rows"]}


class TotalsTests(MarketCase):
    def test_the_melt_loss_is_the_sum_of_the_effective_weight_and_fineness_losses(self):
        s = self.summary(self.service.run("market", MONTH))
        self.assertEqual(s["فاقد السبك — وزن"], D("0.097"))                                      # 0.197 - 0.100 (the reversed melt is not counted)
        self.assertEqual(s["فاقد السبك — عيار"], D("0.509"))                                    # 0.569 - 0.060
        self.assertEqual(s["إجمالي فاقد السبك"], D("0.606"))

    def test_crucible_and_market_losses_are_converted_to_21k_and_added(self):
        s = self.summary(self.service.run("market", MONTH))
        self.assertEqual(s["خسية البوتقة"], D("1.300"))                                         # bar 0.500 + logo 24K 0.700 x 24 / 21 = 0.800
        self.assertEqual(s["خسية السوق"], D("1.500"))                                           # raw 18K 1.750 x 18 / 21; the reversed bar loss is excluded

    def test_the_grand_total_and_the_reversed_count(self):
        result = self.service.run("market", MONTH)
        cards = {x["label"]: (x["value"], x["note"], x["group"]) for x in result["totals"]}
        self.assertEqual(cards["فاقد السبك"][0], "0.606 جرام")                                    # 0.197 - 0.100 + 0.569 - 0.060
        self.assertEqual(cards["خسية البوتقة"][0], "1.300 جرام")
        self.assertEqual(cards["خسية السوق"][0], "1.500 جرام")
        self.assertEqual(cards["إجمالي الفقد والخسية"][0], "3.406 جرام")                          # 0.606 + 1.300 + 1.500
        self.assertIn("2 مستند معكوس", cards["إجمالي الفقد والخسية"][1])                          # one melt + one loss line, not counted
        self.assertEqual([x["group"] for x in result["totals"]], ["losses", "losses", "losses", "grand"])   # each loss alone in its card, the grand total in its own row
        self.assertEqual(self.summary(result)["إجمالي الفقد والخسية"], D("3.406"))                 # the table stays in the result for the CSV

    def test_a_market_return_is_not_a_loss(self):
        s = self.summary(self.service.run("market", {"from": "2026-10-10", "to": "2026-10-10"}))
        self.assertEqual(s["إجمالي الفقد والخسية"], D("0.000"))

    def test_the_period_is_by_the_documents_own_date(self):
        s = self.summary(self.service.run("market", {"from": "2026-10-09", "to": "2026-10-09"}))
        self.assertEqual((s["خسية البوتقة"], s["خسية السوق"], s["إجمالي فاقد السبك"]), (D("0.800"), D("0.000"), D("0.000")))


class SafetyTests(MarketCase):
    def test_a_missing_table_marks_the_total_incomplete_instead_of_hiding_it(self):
        MarketRepo.missing = ("market_trip_settle_lines",)
        result = self.service.run("market", MONTH)
        self.assertTrue(any(t["key"] == "missing" and not t["available"] for t in result["tables"]))
        self.assertIn("ناقص", next(t["note"] for t in result["totals"] if t["label"] == "إجمالي الفقد والخسية"))
        self.assertEqual(self.summary(result)["إجمالي فاقد السبك"], D("0.606"))                  # the melt part is still real

    def test_the_trip_right_is_required_and_rechecked(self):
        with self.assertRaises(PermissionError):
            self.service_for(base.ALL - {"MARKET_TRIP_MANAGE"}).run("market", MONTH)
        with self.assertRaises(PermissionError):
            self.service_for(base.ALL - {"REPORTS_VIEW"}).run("market", MONTH)

    def test_the_report_is_listed_only_with_the_right(self):
        self.assertIn("market", [r["key"] for r in self.service.available_reports()])
        self.assertNotIn("market", [r["key"] for r in self.service_for(base.ALL - {"MARKET_TRIP_MANAGE"}).available_reports()])

    def test_one_snapshot(self):
        self.service.run("market", MONTH)
        self.assertEqual(self.opened, 1)


class StaticSqlTests(unittest.TestCase):
    def test_the_reads_are_plain_selects_with_no_lock_and_no_write(self):
        repo = report_repository.ReportRepository
        for name in ("trip_melts", "trip_settle_lines"):
            upper = inspect.getsource(getattr(repo, name)).upper()
            for forbidden in ("FOR UPDATE", "FOR SHARE", "LOCK IN SHARE", "INSERT ", "UPDATE ", "DELETE "):
                self.assertNotIn(forbidden, upper, f"{name}: {forbidden}")
            self.assertEqual(upper.count("SELECT"), 1, f"{name} must be ONE statement (no query per row)")
        self.assertIn("FULL_REVERSAL", repo._TRIP_REVERSAL)


if __name__ == "__main__":
    unittest.main()
