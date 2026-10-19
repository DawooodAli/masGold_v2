"""R1: the view-model side of the reconciliation preview - the real MarketTripViewModel over the real service and the fake database, with the inline worker of
tests/test_market_trip_book_viewmodel.py. Covers the presentation of the states, the refresh after a movement / reversal, nothing of the previous trip surviving a
selection change, LEGACY keeping its screen, and the closure staying unavailable. No database."""
from decimal import Decimal as D
import unittest
from unittest.mock import patch

from app.viewmodels.market_trip_viewmodel import BOOK_CLOSE_HINT, MarketTripViewModel, reconciliation_view
from services import book_reconciliation as rec
from tests.market_trip_fakes import AccountsRepo, HoldingRepo, SupplierPayRepo, TripRepo, build
from tests.test_market_trip_book import K18, K21, PRICES, TODAY, assay, karat, key
from tests.test_market_trip_book_viewmodel import inline_vm, row
from tests.test_book_reconciliation_rules import Src, K
from services import book_rules as br


def view_of(preview):
    return reconciliation_view(preview.view())


class PresentationTests(unittest.TestCase):
    def test_a_sound_loss_proposal(self):
        s = Src()
        s.add(br.BK_ISSUE, [("100.000", K, 21)])
        s.add(br.BK_RETURN, [("95.000", "F", "872.5")])
        v = view_of(s.preview())
        self.assertTrue(v["lossAvailable"] and v["trustworthy"])
        self.assertIn("5.271", v["lossText"])
        self.assertIn("تقدير مشروط باكتمال تسجيل الحركات واعتماد المالك", v["lossNote"])
        self.assertIn("ليس فاقدًا ولا زيادة", v["roundingNote"])
        self.assertIn("معاينة فقط", v["effectText"])
        self.assertIn("لم تُكتشف مشكلة ضمن الفحوص المتاحة", v["statusText"])
        self.assertIn("ليس ضمانًا", v["statusText"])
        self.assertEqual(v["closureNote"], BOOK_CLOSE_HINT)

    def test_a_positive_remainder_below_the_posting_precision_stays_visible(self):
        s = Src()
        s.add(br.BK_ISSUE, [("1.000", K, 21)])
        s.add(br.BK_RETURN, [("1.000", "F", "874.9")])
        v = view_of(s.preview())
        self.assertEqual(v["aState"], rec.POSITIVE_BELOW)
        self.assertIn("0.000114", v["aText"])
        self.assertIn("أقل من دقة الترحيل", v["aNote"])
        self.assertIn("0.000", v["lossText"])

    def test_a_positive_value_below_the_display_precision_is_flagged_not_shown_as_zero(self):
        s = Src()
        s.add(br.BK_BUY, [("0.001", "F", "1000")], prices=["10.00"])
        s.add(br.BK_RETURN, [("0.001", "F", "999.9")])
        v = view_of(s.preview())
        self.assertIn("0.000000", v["aText"])
        self.assertIn("أقل من دقة العرض", v["aNote"])
        self.assertEqual(v["aState"], rec.POSITIVE_BELOW)

    def test_a_negative_balance_offers_no_loss_and_no_gain(self):
        s = Src()
        s.add(br.BK_ISSUE, [("1.000", K, 21)])
        s.add(br.BK_RETURN, [("2.000", K, 21)])
        v = view_of(s.preview())
        self.assertFalse(v["lossAvailable"])
        self.assertEqual(v["lossText"], "—")
        self.assertEqual(v["effectText"], "")
        self.assertIn("اختلال في السجل", v["aNote"])
        self.assertFalse(v["trustworthy"])
        self.assertIn("غير موثوقة", v["statusText"])

    def test_an_incomplete_preview_shows_no_estimate(self):
        s = Src()
        s.add(br.BK_ISSUE, [("1.000", K, 21)])
        s.incomplete.append(("SOURCE_LINES_UNREADABLE", "OperationalError"))
        v = view_of(s.preview())
        self.assertIn("غير مكتملة", v["statusText"])
        self.assertFalse(v["lossAvailable"])
        self.assertEqual(v["roundingText"], "—")

    def test_diagnostics_and_the_coverage_notes_are_carried(self):
        s = Src()
        s.add(br.BK_ISSUE, [("1.000", K, 21)])
        s.lines.clear()
        v = view_of(s.preview())
        self.assertEqual(sorted(d["code"] for d in v["diagnostics"]), ["ENTRY_WITHOUT_LINES", "LEG_ORPHAN"])         # the leg left behind is an orphan too
        self.assertEqual(v["diagnostics"][0]["entryCode"], "MT000100-1")
        self.assertEqual(len(v["coverage"]), len(rec.COVERAGE_NOTES))

    def test_the_exact_internals_never_reach_the_screen(self):
        s = Src()
        s.add(br.BK_ISSUE, [("1.000", K, 21)])
        v = view_of(s.preview())
        self.assertNotIn("sourceToken", v)
        self.assertNotIn("aExact", v)
        self.assertFalse(any(isinstance(value, str) and "/" in value and value.replace("/", "").isdigit() for value in v.values()))


class ViewModelTests(unittest.TestCase):
    def setUp(self):
        self.db, self.session, self.authorization, self.service, self.corrections = build(book=True)
        self.service._price_reference = lambda: dict(PRICES)
        for target, value in (("repositories.market_trip_repository.MarketTripRepository", TripRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo),
                              ("repositories.supplier_payment_repository.SupplierPaymentRepository", SupplierPayRepo),
                              ("repositories.intermediary_holding_repository.IntermediaryHoldingRepository", HoldingRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)
        self.vm, self.posted = inline_vm(self.session, self.service)
        self.n = 0

    def k(self):
        self.n += 1
        return key(self.n + 700)

    def book_trip(self, weight="20"):
        trip = self.service.book_issue("", [karat(K21, weight)], TODAY, "", self.k())["trip_id"]
        self.vm.refresh()
        self.vm.selectTrip(trip)
        return trip

    def test_selecting_a_book_trip_loads_its_preview(self):
        self.book_trip("20")
        r = self.vm.reconciliation
        self.assertIn("20.000000", r["aText"])
        self.assertIn("20.000", r["bText"])
        self.assertFalse(r["loading"])

    def test_a_movement_and_a_reversal_refresh_the_preview_through_the_existing_path(self):
        trip = self.book_trip("20")
        self.vm.bookReturn(trip, [row("MASTER_KARAT", K21, weight="5")], TODAY, "", self.k())
        self.assertIn("15.000000", self.vm.reconciliation["aText"])
        entry = next(e for e in self.db.entries.values() if e["entry_kind"] == "BK_RETURN")
        self.vm.reverseEntry(str(entry["id"]), "خطأ إدخال")
        self.assertIn("20.000000", self.vm.reconciliation["aText"])

    def test_nothing_of_the_previous_trip_stays_while_the_next_loads(self):
        first = self.book_trip("20")
        second = self.service.book_issue("", [karat(K18, "10")], TODAY, "", self.k())["trip_id"]
        self.assertIn("20.000000", self.vm.reconciliation["aText"])
        with patch.object(self.vm, "_start", lambda operation, call: None):           # the answers are still on their way
            self.vm.selectTrip(second)
            self.assertEqual({k: v for k, v in self.vm.reconciliation.items() if k != "loading"}, {})
        self.vm.selectTrip(second)
        self.assertIn("8.571429", self.vm.reconciliation["aText"])
        self.assertNotEqual(first, second)

    def test_a_late_answer_for_another_trip_is_ignored(self):
        first = self.book_trip("20")
        stale = self.service.reconciliation_preview(first)
        second = self.service.book_issue("", [karat(K18, "10")], TODAY, "", self.k())["trip_id"]
        self.vm.selectTrip(second)
        self.vm._on_recon(stale, "")
        self.assertIn("8.571429", self.vm.reconciliation["aText"])

    def test_a_legacy_trip_has_no_card_and_keeps_its_closure_logic(self):
        legacy = self.service.issue("", "500", [], TODAY, "", self.k())["trip_id"]
        self.vm.refresh()
        self.vm.selectTrip(legacy)
        self.assertEqual({k: v for k, v in self.vm.reconciliation.items() if k != "loading"}, {})
        self.assertFalse(self.vm.detail["isBook"])

    def test_a_good_preview_never_makes_a_book_trip_closable(self):
        trip = self.book_trip("5")
        self.service.book_return(trip, [karat(K21, "5")], TODAY, "", self.k())
        self.vm.refresh()                                                             # A = 0, B = 0, cash = 0: the most favourable state there is
        r = self.vm.reconciliation
        self.assertTrue(r["trustworthy"])
        self.assertIs(self.vm.detail["closable"], False)
        self.assertEqual(self.vm.detail["closeHint"], BOOK_CLOSE_HINT)
        self.vm.closeTrip(trip)
        self.assertEqual(self.vm.errorCode, "RECONCILIATION_REQUIRED")

    def test_a_failing_preview_shows_a_message_not_stale_numbers(self):
        trip = self.book_trip("5")
        with patch.object(type(self.service), "reconciliation_preview", side_effect=PermissionError("لا صلاحية")):
            self.vm.refresh()
        r = self.vm.reconciliation
        self.assertIn("تعذّر تحميل مطابقة العهدة", r["error"])
        self.assertNotIn("aText", r)
        self.assertEqual(trip, self.vm.detail["id"])


if __name__ == "__main__":
    unittest.main()
