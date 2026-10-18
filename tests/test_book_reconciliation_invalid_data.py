"""R1 follow-up (F2): the preview must answer INVALID / MISSING stored data with a diagnostic, never with an exception and never with a silent zero. Some of these states are
prevented by the real database constraints; they are part of the deliberate diagnostic contract of the preview (no database corruption is simulated, rows are plain
dictionaries). Covers: bad / None / missing / non-finite fields of a line (weight, karat id, evidence fields, signed value, direction, basis, price, leg id), invalid leg /
trade / stock rows, non-dict rows, failing read sections, one damaged line next to a healthy one (PARTIAL figures), UNAVAILABLE balance (never «zero»), the token that is not
produced from dropped inputs, the presentation of those states, and that an unexpected programming error is NOT swallowed."""
from decimal import Decimal as D
from fractions import Fraction as F
import unittest
from unittest.mock import patch

from app.viewmodels.market_trip_viewmodel import reconciliation_view
from services import book_reconciliation as rec
from services import book_rules as br
from tests.market_trip_fakes import AccountsRepo, HoldingRepo, SupplierPayRepo, TripRepo, build
from tests.test_book_reconciliation_rules import Src, K
from tests.test_market_trip_book import K21, PRICES, TODAY, karat, key
from unittest.mock import patch as mock_patch

NAN, INF = D("NaN"), D("Infinity")


def pair():
    """Two ISSUE documents and a RETURN: entry 1 (line 1) is the one the tests damage; the healthy remainder stays usable."""
    s = Src()
    s.add(br.BK_ISSUE, [("10.000", K, 21)])
    s.add(br.BK_ISSUE, [("5.000", K, 18)])
    s.add(br.BK_RETURN, [("4.000", K, 21)])
    return s


def codes(preview):
    return sorted({d.code for d in preview.diagnostics if d.category != rec.COVERAGE})


class InvalidFieldTests(unittest.TestCase):
    FACT_CASES = (("weight None", "weight_grams", None), ("weight 'bad'", "weight_grams", "bad"), ("weight NaN", "weight_grams", NAN), ("weight Infinity", "weight_grams", INF),
                  ("weight missing key", "weight_grams", "__delete__"), ("posted NaN", "eq21_posted", NAN), ("posted None", "eq21_posted", None),
                  ("karat_id None (master karat)", "karat_id", None), ("karat_value 'x'", "karat_value", "x"), ("direction None", "direction", None),
                  ("basis missing", "basis", "__delete__"), ("basis unknown", "basis", "OTHER"))
    EVIDENCE_CASES = (("eq21_analytical None", "eq21_analytical", None), ("eq21_analytical 'bad'", "eq21_analytical", "bad"), ("signed invalid", "signed_eq21_posted", "x"),
                      ("posting_difference None", "posting_difference", None), ("scrap_movement_id 'x'", "scrap_movement_id", "x"))

    def damaged(self, field, value):
        s = pair()
        line = s.line(1)
        if value == "__delete__":
            del line[field]
        else:
            line[field] = value
        return s

    def test_a_line_with_invalid_FACTS_is_a_diagnostic_excluded_from_the_numbers_and_the_token(self):
        for label, field, value in self.FACT_CASES:
            with self.subTest(case=label):
                s = self.damaged(field, value)
                p = s.preview()                                                          # never an exception
                self.assertFalse(p.trustworthy)
                self.assertIsNone(p.proposed_loss)
                self.assertIsNone(p.rounding_settlement)
                self.assertIsNone(p.effect)
                located = [d for d in p.diagnostics if d.entry_id == 1 and d.line_no == 1 and d.category == rec.INTEGRITY]
                self.assertTrue(located, [d.code for d in p.diagnostics])                  # tied to the entry and the line
                self.assertEqual(located[0].entry_code, "MT000100-1")
                self.assertEqual(p.balance_status, rec.PARTIAL)
                self.assertEqual(p.a_state, rec.PARTIAL)
                self.assertEqual(p.excluded_count, 1)
                self.assertIsNone(p.token)                                                # no fingerprint of silently dropped inputs
                self.assertIn("بصمة", p.token_reason)
                view = p.view()
                self.assertEqual((view["balanceStatus"], view["sourceToken"], view["proposedLoss"], view["effect"]), ("partial", None, None, None))

    def test_a_line_with_invalid_EVIDENCE_only_stays_in_the_numbers_but_is_never_trusted(self):
        for label, field, value in self.EVIDENCE_CASES:
            with self.subTest(case=label):
                p = self.damaged(field, value).preview()
                self.assertFalse(p.trustworthy)
                self.assertIsNone(p.proposed_loss)
                self.assertTrue([d for d in p.diagnostics if d.entry_id == 1 and d.line_no == 1 and d.category == rec.INTEGRITY])
                self.assertEqual(p.balance_status, rec.FULL)                               # its FACTS are valid: the whole balance is still computed
                self.assertIsNotNone(p.token)                                              # the token uses facts only, which are valid

    def test_the_partial_figures_are_exactly_those_of_the_healthy_lines(self):
        s = self.damaged("weight_grams", "bad")
        p = s.preview()
        healthy = Src()
        healthy.add(br.BK_ISSUE, [("5.000", K, 18)])
        healthy.add(br.BK_RETURN, [("4.000", K, 21)])
        expected = healthy.preview()
        self.assertEqual((p.a, p.b, p.line_count), (expected.a, expected.b, 2))
        self.assertNotIn("A_NEGATIVE", codes(p))                                           # a partial sum is not judged as a balance
        self.assertEqual(p.view()["lineCount"], 2)

    def test_non_dict_rows_and_unusable_entries_are_diagnosed(self):
        s = pair()
        s.lines.append(None)
        s.entries.append("not a row")
        s.entries.append({"id": None, "trip_id": 100, "entry_no": 9, "entry_kind": br.BK_BUY, "correction_id": None})
        s.entries.append({"id": 77, "trip_id": 100, "entry_no": 7, "entry_kind": None, "correction_id": None})
        s.scrap.append(["x"])
        p = s.preview()
        self.assertFalse(p.trustworthy)
        self.assertEqual(p.balance_status, rec.PARTIAL)
        self.assertTrue({"ENTRY_INVALID", "LINE_KEYS_INVALID", "LEG_DATA_INVALID"} <= set(codes(p)))
        self.assertIsNone(p.token)

    def test_a_line_without_usable_keys_is_a_diagnostic(self):
        for field, value in (("entry_id", None), ("line_no", "x"), ("entry_id", "__delete__")):
            with self.subTest(field=field, value=value):
                s = pair()
                row = s.line(1)
                if value == "__delete__":
                    del row[field]
                else:
                    row[field] = value
                p = s.preview()
                self.assertIn("LINE_KEYS_INVALID", codes(p))
                self.assertFalse(p.trustworthy)
                self.assertIsNone(p.token)

    def test_an_invalid_reversal_state_is_a_diagnostic_not_a_guess(self):
        s = pair()
        s.entries[2]["correction_id"] = "not a number"
        p = s.preview()
        self.assertIn("ENTRY_INVALID", codes(p))
        self.assertFalse(p.trustworthy)
        self.assertIsNone(p.token)


class InvalidSideRecordTests(unittest.TestCase):
    def test_an_unreadable_leg_row_is_reported_and_not_also_called_missing(self):
        s = pair()
        del s.scrap[0]["karat_id"]
        p = s.preview()
        self.assertIn("LEG_DATA_INVALID", codes(p))
        self.assertNotIn("LEG_MISSING", codes(p))
        self.assertNotIn("LEG_ORPHAN", codes(p))
        self.assertFalse(p.trustworthy)

    def test_an_unreadable_trade_row_is_reported_and_not_also_called_missing_or_mismatched(self):
        s = Src()
        s.add(br.BK_BUY, [("3.000", "F", "900")], prices=["100.00"])
        s.trades[0]["total_price"] = None
        p = s.preview()
        self.assertEqual(codes(p), ["TRADE_DATA_INVALID"])
        self.assertFalse(p.trustworthy)

    def test_an_unreadable_stock_row_is_reported_and_not_also_called_missing(self):
        s = Src()
        s.add(br.BK_BUY, [("5.000", "F", "866.7")], prices=["100.00"])
        s.add(br.BK_RETURN, [("1.800", "F", "866.7")])
        s.stock[0]["grams_delta"] = "bad"
        p = s.preview()
        self.assertEqual(codes(p), ["STOCK_DATA_INVALID"])


class SectionFailureTests(unittest.TestCase):
    def test_unreadable_lines_or_entries_mean_NO_balance_never_zero(self):
        for section in ("SOURCE_LINES_UNREADABLE", "SOURCE_ENTRIES_UNREADABLE"):
            with self.subTest(section=section):
                s = pair()
                s.incomplete.append((section, "OperationalError"))
                if section == "SOURCE_LINES_UNREADABLE":
                    s.lines = []
                else:
                    s.entries = []
                p = s.preview()
                self.assertEqual(codes(p), [section])                                      # ONLY the unreadable section is reported: «entry without lines» etc. would be artefacts of the failed read
                self.assertEqual((p.balance_status, p.a_state), (rec.UNAVAILABLE, rec.UNAVAILABLE))
                self.assertIsNone(p.proposed_loss)
                self.assertIsNone(p.effect)
                self.assertIsNone(p.token)
                view = p.view()
                for name in ("a", "b", "aIn", "aOut", "bIn", "bOut", "rho", "sumAbs", "bound", "aExact"):
                    self.assertIsNone(view[name], name)
                self.assertEqual((view["balanceStatus"], view["aState"], view["complete"], view["trustworthy"]), ("unavailable", "unavailable", False, False))
                self.assertFalse(view["aPositiveBelowDisplay"])

    def test_another_unreadable_section_keeps_the_balance_but_withdraws_the_proposal_and_reports_only_itself(self):
        def with_trade_and_stock():
            s = Src()
            s.add(br.BK_BUY, [("5.000", "F", "866.7")], prices=["100.00"])
            s.add(br.BK_RETURN, [("1.800", "F", "866.7")])
            return s
        for section, field, build in (("SOURCE_LEGS_UNREADABLE", "scrap", pair), ("SOURCE_TRADES_UNREADABLE", "trades", with_trade_and_stock),
                                      ("SOURCE_STOCK_UNREADABLE", "stock", with_trade_and_stock)):
            with self.subTest(section=section):
                s = build()
                s.incomplete = [(section, "OperationalError")]
                setattr(s, field, [])
                p = s.preview()
                self.assertEqual(codes(p), [section])                                      # no LEG_MISSING / TRADE_MISSING / STOCK_RECORD_MISSING artefacts
                self.assertEqual(p.balance_status, rec.FULL)                               # A and B come from the lines, which were read
                self.assertFalse(p.trustworthy)
                self.assertIsNone(p.proposed_loss)


class TokenAndErrorTests(unittest.TestCase):
    def test_the_token_function_refuses_invalid_inputs_instead_of_hashing_what_is_left(self):
        s = pair()
        ok = rec.source_token(s.entries, s.lines)
        self.assertEqual(len(ok), 64)
        s.line(1)["weight_grams"] = "bad"
        with self.assertRaises(rec.SourceTokenError):
            rec.source_token(s.entries, s.lines)
        s = pair()
        s.lines.append({"entry_id": None})
        with self.assertRaises(rec.SourceTokenError):
            rec.source_token(s.entries, s.lines)
        s = pair()
        s.entries[0]["correction_id"] = "x"
        with self.assertRaises(rec.SourceTokenError):
            rec.source_token(s.entries, s.lines)

    def test_a_healthy_source_still_has_its_token(self):
        self.assertEqual(len(pair().preview().token), 64)
        self.assertEqual(pair().preview().token_reason, "")

    def test_an_unexpected_programming_error_is_not_swallowed(self):
        with mock_patch.object(br, "analytical", side_effect=RuntimeError("a bug, not stored data")):
            with self.assertRaises(RuntimeError):
                pair().preview()
        with mock_patch.object(rec, "classify_a", side_effect=RuntimeError("another bug")):
            with self.assertRaises(RuntimeError):
                pair().preview()


class ServiceAndPresentationTests(unittest.TestCase):
    def setUp(self):
        self.db, self.session, self.authorization, self.service, self.corrections = build(book=True)
        self.service._price_reference = lambda: dict(PRICES)
        for target, value in (("repositories.market_trip_repository.MarketTripRepository", TripRepo),
                              ("repositories.supplier_payment_repository.SupplierPaymentRepository", SupplierPayRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo),
                              ("repositories.intermediary_holding_repository.IntermediaryHoldingRepository", HoldingRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)
        self.n = 0
        self.trip = self.service.book_issue("", [karat(K21, "10")], TODAY, "", self.k())["trip_id"]
        self.service.book_return(self.trip, [karat(K21, "4")], TODAY, "", self.k())

    def k(self):
        self.n += 1
        return key(self.n + 1500)

    def test_a_damaged_stored_line_comes_back_as_a_diagnostic_through_the_service(self):
        self.db.book_lines[0]["weight_grams"] = None
        view = self.service.reconciliation_preview(self.trip)                              # no exception
        self.assertEqual((view["balanceStatus"], view["proposedLoss"], view["effect"], view["trustworthy"]), ("partial", None, None, False))
        self.assertIn("LINE_FIELD_INVALID", [d["code"] for d in view["diagnostics"]])
        self.assertEqual(view["lineCount"], 1)
        self.assertIsNone(view["sourceToken"])

    def test_the_presentation_of_a_partial_balance(self):
        self.db.book_lines[0]["weight_grams"] = "bad"
        card = reconciliation_view(self.service.reconciliation_preview(self.trip))
        self.assertIn("(جزئي)", card["aText"])
        self.assertIn("أرقام جزئية", card["aNote"])
        self.assertIn("1 عنصرًا", card["aNote"])
        self.assertFalse(card["lossAvailable"])
        self.assertEqual(card["lossText"], "—")
        self.assertEqual(card["effectText"], "")
        self.assertIn("غير موثوقة", card["statusText"])
        self.assertEqual(card["balanceStatus"], "partial")

    def test_the_presentation_of_an_unavailable_balance_never_reads_as_zero(self):
        self.db.unreadable = {"lines"}
        card = reconciliation_view(self.service.reconciliation_preview(self.trip))
        self.assertEqual(card["aText"], "غير متاح")
        self.assertEqual(card["bText"], "غير متاح")
        self.assertNotIn("0.000000", card["aText"])
        self.assertIn("ليس رصيدًا صفرًا", card["aNote"])
        self.assertNotIn("لا رصيد تحليلي غير مسوّى", card["aNote"])
        self.assertEqual((card["lossText"], card["roundingText"], card["effectText"], card["detailsText"], card["countsText"]), ("—", "—", "", "—", ""))
        self.assertIn("غير مكتملة", card["statusText"])
        self.assertEqual(card["balanceStatus"], "unavailable")


if __name__ == "__main__":
    unittest.main()
