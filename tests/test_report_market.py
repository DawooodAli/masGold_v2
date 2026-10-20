"""Settlement step 2g: the «عهدة السوق والخسائر» report through the REAL ReportService over a fake repository with hand-checkable numbers (every expected figure is computed in the
comment next to it). The SQL of ReportRepository is NOT exercised here (no MySQL): the static checks at the end read the statements, the live read is in the opt-in MySQL module."""
import csv
from datetime import date, datetime
from decimal import Decimal as D
from pathlib import Path
import tempfile
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
        rows = [{"entry_id": 2, "trip_id": 1, "entry_no": 2, "doc_date": at(3), "workshop": "ورشة النور", "input_weight_grams": D("45.000"), "output_weight_grams": D("44.800"), "fineness_permille": 850,
                 "theoretical_eq21": D("44.286"), "actual_eq21": D("43.520"), "loss_eq21": D("0.766"), "weight_loss_eq21": D("0.197"), "fineness_loss_eq21": D("0.569"), "reversal_id": None},
                {"entry_id": 9, "trip_id": 2, "entry_no": 2, "doc_date": at(6), "workshop": "ورشة ثانية", "input_weight_grams": D("10.000"), "output_weight_grams": D("10.100"), "fineness_permille": 880,
                 "theoretical_eq21": D("10.000"), "actual_eq21": D("10.160"), "loss_eq21": D("-0.160"), "weight_loss_eq21": D("-0.100"), "fineness_loss_eq21": D("-0.060"), "reversal_id": None},
                {"entry_id": 11, "trip_id": 2, "entry_no": 4, "doc_date": at(7), "workshop": "ورشة ملغاة", "input_weight_grams": D("5.000"), "output_weight_grams": D("4.900"), "fineness_permille": 800,
                 "theoretical_eq21": D("5.000"), "actual_eq21": D("4.480"), "loss_eq21": D("0.520"), "weight_loss_eq21": D("0.100"), "fineness_loss_eq21": D("0.420"), "reversal_id": 5}]
        return [r for r in rows if start <= r["doc_date"].date() < end]

    def trip_settle_lines(self, start, end):
        def line(entry, trip, no, kind, day, n, source, karat, weight, cls, reversal=None):
            return {"entry_id": entry, "trip_id": trip, "entry_no": no, "entry_kind": kind, "doc_date": at(day), "line_no": n, "source": source, "karat_value": karat,
                    "weight_grams": D(weight), "classification": cls, "reversal_id": reversal}
        rows = [line(20, 1, 5, "ST_LOSS", 8, 1, "BAR", None, "0.500", "CRUCIBLE"), line(20, 1, 5, "ST_LOSS", 8, 2, "RAW", 18, "1.250", "MARKET"),
                line(21, 1, 6, "ST_LOSS", 9, 1, "BAR", None, "0.250", "CRUCIBLE"), line(22, 1, 7, "ST_LOSS", 9, 1, "BAR", None, "9.000", "MARKET", reversal=6),
                line(23, 1, 8, "ST_RET_MKT", 10, 1, "BAR", None, "20.000", None), line(23, 1, 8, "ST_RET_MKT", 10, 2, "LOGO", 21, "4.000", None),
                line(24, 1, 9, "ST_RET_MKT", 11, 1, "RAW", 21, "3.000", None)]
        return [r for r in rows if start <= r["doc_date"].date() < end]

    def trip_settle_adjusts(self, start, end):
        rows = [{"entry_id": 30, "trip_id": 1, "entry_no": 10, "doc_date": at(12), "karat_value": 21, "direction": "INCREASE", "weight_grams": D("2.500"), "reason": "وزن غلط", "reversal_id": None},
                {"entry_id": 31, "trip_id": 1, "entry_no": 11, "doc_date": at(13), "karat_value": 21, "direction": "DECREASE", "weight_grams": D("1.000"), "reason": "ميزان", "reversal_id": None}]
        return [r for r in rows if start <= r["doc_date"].date() < end]

    def trip_settle_cash(self, start, end):
        def doc(entry, no, kind, day, amount, text, bank, reversal=None):
            return {"entry_id": entry, "trip_id": 1, "entry_no": no, "entry_kind": kind, "doc_date": at(day), "amount": D(amount), "description": text, "reference": "", "bank_name": bank, "reversal_id": reversal}
        rows = [doc(40, 12, "ST_EXPENSE", 14, "1500.50", "أجرة نقل", None), doc(41, 13, "ST_EXPENSE", 14, "200.00", "ضيافة", None, reversal=8), doc(42, 14, "ST_CASH_BANK", 15, "5000.00", None, "بنك مصر"),
                doc(43, 15, "ST_CASH_OFF", 16, "250.00", "فرق عد", None)]
        return [r for r in rows if start <= r["doc_date"].date() < end]


class MarketCase(base.Case):
    def setUp(self):
        super().setUp()
        MarketRepo.missing = ()
        self.service = ReportService(self.session, transaction_factory=self.service._transaction, repository=MarketRepo, authorization=self.authorization, today=lambda: base.TODAY)

    def service_for(self, permissions, repo=MarketRepo):
        session = session_for(permissions)
        return ReportService(session, transaction_factory=self.service._transaction, repository=repo, authorization=FakeAuthorization(session), today=lambda: base.TODAY)


class MeltTests(MarketCase):
    def test_the_melt_differences_are_totalled_over_effective_melts_only(self):
        result = self.service.run("market", MONTH)
        t = self.totals(result)
        self.assertEqual(len(self.rows(result, "melts")), 3)                                       # the reversed melt stays listed
        self.assertEqual(t["السبك — النظري (فعّال) (مكافئ عيار 21)"], D("54.286"))                  # 44.286 + 10.000
        self.assertEqual(t["السبك — الفعلي (فعّال) (مكافئ عيار 21)"], D("53.680"))                  # 43.520 + 10.160
        self.assertEqual(t["السبك — الفاقد (فعّال) (مكافئ عيار 21)"], D("0.606"))                   # 0.766 - 0.160 (a gain nets against a loss)
        self.assertEqual(t["السبك — فاقد الوزن (فعّال) (مكافئ عيار 21)"], D("0.097"))                # 0.197 - 0.100
        self.assertEqual(t["السبك — فاقد العيار (فعّال) (مكافئ عيار 21)"], D("0.509"))               # 0.569 - 0.060
        self.assertEqual((t["عدد عمليات السبك الفعّالة"], t["عمليات سبك معكوسة (عددها)"]), (2, 1))
        self.assertEqual([r["state"] for r in self.rows(result, "melts")][-1], "معكوس (حالة اليوم)")

    def test_a_search_narrows_the_rows_and_their_totals(self):
        result = self.service.run("market", {**MONTH, "text": "النور"})
        self.assertEqual(len(self.rows(result, "melts")), 1)
        self.assertEqual(self.totals(result)["عدد عمليات السبك الفعّالة"], 1)


class LossAndReturnTests(MarketCase):
    def test_losses_are_grouped_by_classification_and_unit_and_never_added_across_karats(self):
        t = self.totals(self.service.run("market", MONTH))
        self.assertEqual(t["خسية بوتقة — سبيكة (مكافئ عيار 21) (فعّال)"], D("0.750"))               # 0.500 + 0.250
        self.assertEqual(t["خسية سوق — عيار 18 (بدون وجهة) (فعّال)"], D("1.250"))
        self.assertNotIn("خسية سوق — سبيكة (مكافئ عيار 21) (فعّال)", t)                            # the only market loss of the bar is reversed
        self.assertEqual(t["خسائر / مرتجعات معكوسة (عدد السطور)"], 1)

    def test_the_market_return_is_apart_from_the_loss_and_the_bar_is_in_eq21(self):
        t = self.totals(self.service.run("market", MONTH))
        self.assertEqual(t["مرتجع سوق — سبيكة (مكافئ عيار 21) (فعّال)"], D("20.000"))
        self.assertEqual(t["مرتجع سوق — عيار 21 (لوجو) (فعّال)"], D("4.000"))
        self.assertEqual(t["مرتجع سوق — عيار 21 (بدون وجهة) (فعّال)"], D("3.000"))                  # a different unit: not merged with the logo of the same karat

    def test_reversed_lines_keep_their_reference_and_are_marked(self):
        rows = self.rows(self.service.run("market", MONTH), "losses")
        reversed_row = next(r for r in rows if r["state"] == "معكوس (حالة اليوم)")
        self.assertEqual((reversed_row["weight"], reversed_row["ref"]), (D("9.000"), {"type": "TRIP", "id": "1"}))


class AdjustAndCashTests(MarketCase):
    def test_issue_weight_errors_are_signed_and_netted_per_karat(self):
        result = self.service.run("market", MONTH)
        self.assertEqual([r["weight"] for r in self.rows(result, "adjusts")], [D("2.500"), D("-1.000")])
        self.assertEqual(self.totals(result)["تصحيح وزن الإخراج — عيار 21 (صافي فعّال)"], D("1.500"))

    def test_cash_settlements_are_totalled_by_kind_over_effective_documents(self):
        result = self.service.run("market", MONTH)
        t = self.totals(result)
        self.assertEqual((t["مصروف (فعّال)"], t["إعدام (فعّال)"], t["إيداع في حساب (فعّال)"]), (D("1500.50"), D("250.00"), D("5000.00")))   # the reversed 200.00 is excluded
        self.assertEqual(len(self.rows(result, "cash")), 4)
        self.assertEqual(self.rows(result, "cash")[2]["what"], "بنك مصر")

    def test_the_period_is_by_the_documents_own_date(self):
        result = self.service.run("market", {"from": "2026-10-14", "to": "2026-10-15"})
        self.assertEqual(len(self.rows(result, "cash")), 3)
        self.assertEqual((self.rows(result, "melts"), self.rows(result, "adjusts")), ([], []))


class SafetyTests(MarketCase):
    def test_a_missing_table_is_unavailable_not_zero(self):
        MarketRepo.missing = ("market_trip_settle_cash", "market_trip_settle_lines")
        result = self.service.run("market", MONTH)
        by_key = {t["key"]: t for t in result["tables"]}
        self.assertFalse(by_key["cash"]["available"])
        self.assertFalse(by_key["losses"]["available"])
        self.assertFalse(by_key["returns"]["available"])
        self.assertTrue(by_key["melts"].get("available", True))
        self.assertNotIn("مصروف (فعّال)", self.totals(result))

    def test_the_trip_right_is_required_and_rechecked(self):
        with self.assertRaises(PermissionError):
            self.service_for(base.ALL - {"MARKET_TRIP_MANAGE"}).run("market", MONTH)
        with self.assertRaises(PermissionError):
            self.service_for(base.ALL - {"REPORTS_VIEW"}).run("market", MONTH)

    def test_the_report_is_listed_only_with_the_right(self):
        self.assertIn("market", [r["key"] for r in self.service.available_reports()])
        self.assertNotIn("market", [r["key"] for r in self.service_for(base.ALL - {"MARKET_TRIP_MANAGE"}).available_reports()])

    def test_one_snapshot_and_the_csv_has_every_row(self):
        self.service.run("market", MONTH)
        self.assertEqual(self.opened, 1)
        with tempfile.TemporaryDirectory() as folder:
            path = self.service.export_csv("market", MONTH, folder)
            text = Path(path).read_text(encoding="utf-8-sig")
        for needle in ("فرق الوزن والعيار (السبك)", "الخسية (بوتقة / سوق)", "مرتجع السوق للمحل", "أخطاء وزن الإخراج", "المصروفات والإيداع والإعدام", "أجرة نقل", "بنك مصر"):
            self.assertIn(needle, text)


class StaticSqlTests(unittest.TestCase):
    def test_the_reads_are_plain_selects_with_no_lock_and_no_write(self):
        repo = report_repository.ReportRepository
        import inspect
        for name in ("trip_melts", "trip_settle_lines", "trip_settle_adjusts", "trip_settle_cash"):
            source = inspect.getsource(getattr(repo, name))
            upper = source.upper()
            for forbidden in ("FOR UPDATE", "FOR SHARE", "LOCK IN SHARE", "INSERT ", "UPDATE ", "DELETE "):
                self.assertNotIn(forbidden, upper, f"{name}: {forbidden}")
            self.assertEqual(upper.count("SELECT"), 1, f"{name} must be ONE statement (no query per row)")
        self.assertIn("FULL_REVERSAL", repo._TRIP_REVERSAL)


if __name__ == "__main__":
    unittest.main()
