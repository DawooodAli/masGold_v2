"""R1 reconciliation preview: the PURE rules and diagnostics (services/book_reconciliation.py). No database, no float in any assertion, no service.

`Src` builds a HEALTHY stored source (entries, lines, Scrap legs, trade headers, stock records) with the project's own `book_rules.plan_line`; each integrity test then
corrupts exactly one thing. What the diagnostics cannot detect is asserted too (the documented coverage limits)."""
from decimal import Decimal as D
from fractions import Fraction as F
import random
import unittest

from repositories import market_trip_repository as repository
from services import book_reconciliation as rec
from services import book_rules as br

TRIP = 100
K = "K"


class Src:
    def __init__(self):
        self.entries, self.lines, self.scrap, self.trades, self.stock = [], [], [], [], []
        self.incomplete, self.cash = [], D("0.00")
        self._entry, self._move = 0, 0

    def add(self, kind, rows, *, reversed_=False, prices=None):
        """rows: (weight, "K", karat_value) or (weight, "F", permille). Returns the entry id."""
        self._entry += 1
        eid = self._entry
        self.entries.append({"id": eid, "trip_id": TRIP, "trip_mode": "BOOK", "entry_no": eid, "entry_kind": kind, "correction_id": 900 + eid if reversed_ else None})
        direction = br.IN if kind in br.INCOMING_KINDS else br.OUT
        total = D("0.00")
        for number, (weight, basis, key) in enumerate(rows, 1):
            master = basis == K
            line = br.plan_line(direction, D(weight), br.MASTER_KARAT if master else br.FINENESS, 1 if master else None, int(key) if master else None,
                                None if master else D(str(key)))
            price = None if prices is None else D(prices[number - 1])
            leg = None
            if master and kind in rec.SCRAP_LEG_CODE:
                self._move += 1
                leg = self._move
                self.scrap.append({"id": leg, "karat_id": 1, "weight_delta_grams": -line.weight if kind == br.BK_ISSUE else line.weight,
                                   "movement_code": rec.SCRAP_LEG_CODE[kind], "reference_id": eid})
            if kind == br.BK_RETURN and not master:
                self.stock.append({"item_entry_id": eid, "item_line_no": number, "movement_type": br.RECEIVE, "grams_delta": line.weight, "eq21_posted_delta": line.posted})
            if price is not None:
                total += price
            self.lines.append({"entry_id": eid, "line_no": number, "trip_id": TRIP, "trip_mode": "BOOK", "entry_kind": kind, "direction": direction,
                               "weight_grams": line.weight, "basis": line.basis, "karat_id": line.karat_id, "karat_value": line.karat_value,
                               "fineness_permille": line.fineness, "eq21_analytical": line.analytical_evidence, "eq21_posted": line.posted,
                               "posting_difference": line.difference, "signed_eq21_posted": line.signed, "line_price": price, "scrap_movement_id": leg})
        if kind in rec.PRICED_KINDS:
            self.trades.append({"entry_id": eid, "entry_kind": kind, "total_price": total})
        return eid

    def source(self):
        return {"trip_id": TRIP, "entries": self.entries, "lines": self.lines, "scrap": self.scrap, "trades": self.trades, "stock": self.stock,
                "cash_outstanding": self.cash, "incomplete": self.incomplete}

    def preview(self):
        return rec.reconcile_preview(self.source())

    def line(self, entry_id, number=1):
        return next(l for l in self.lines if l["entry_id"] == entry_id and l["line_no"] == number)

    def codes(self):
        return sorted(d.code for d in self.preview().diagnostics if d.category != rec.COVERAGE)


def two(issue, out, *, out_kind=br.BK_RETURN):
    s = Src()
    s.add(br.BK_ISSUE, [issue] if isinstance(issue, tuple) else issue)
    s.add(out_kind, [out] if isinstance(out, tuple) else out, prices=None if out_kind == br.BK_RETURN else ["1000.00"] * (1 if isinstance(out, tuple) else len(out)))
    return s


class ArithmeticTests(unittest.TestCase):
    def check_identities(self, p):
        self.assertEqual(p.b - p.proposed_loss - p.rounding_settlement, 0)                 # B - L - r = 0
        self.assertEqual(Fraction_of(p.rounding_settlement), p.rho - p.delta)               # r = rho - delta
        self.assertEqual(Fraction_of(p.b), p.a + p.rho)                                     # B = A + rho
        self.assertLessEqual(abs(p.delta), F(1, 2000))
        self.assertLessEqual(abs(Fraction_of(p.rounding_settlement)), p.bound)

    def test_e1_everything_returned(self):
        s = Src()
        s.add(br.BK_ISSUE, [("20.000", K, 21), ("10.000", K, 18)])
        s.add(br.BK_RETURN, [("20.000", K, 21), ("10.000", K, 18)])
        p = s.preview()
        self.assertEqual((p.a, p.b, p.proposed_loss, p.rounding_settlement, p.rho), (0, D("0.000"), D("0.000"), D("0.000"), 0))
        self.assertEqual(p.a_state, rec.ZERO)
        self.assertTrue(p.trustworthy)
        self.check_identities(p)

    def test_e2_rounding_only_is_neither_loss_nor_gain(self):
        s = Src()
        s.add(br.BK_ISSUE, [("10.000", K, 18)])
        s.add(br.BK_RETURN, [("3.333", K, 18), ("3.333", K, 18), ("3.334", K, 18)])
        p = s.preview()
        self.assertEqual((p.a, p.b, p.proposed_loss, p.rounding_settlement), (0, D("-0.001"), D("0.000"), D("-0.001")))
        self.assertEqual((p.rho, p.sum_abs), (F(-1, 1000), F(1, 1000)))
        self.assertEqual(p.a_state, rec.ZERO)
        self.assertEqual(p.effect["position"], D("0.001"))                                  # the posted position would INCREASE by 0.001 with no real gain
        self.check_identities(p)

    def test_e3_real_loss_with_weight_and_assay_change(self):
        s = two(("100.000", K, 21), ("95.000", "F", "872.5"))
        p = s.preview()
        self.assertEqual((p.a, p.b, p.proposed_loss, p.rounding_settlement, p.delta), (F(369, 70), D("5.271"), D("5.271"), D("0.000"), F(-3, 7000)))
        self.assertEqual(p.a_state, rec.POSITIVE)
        self.assertEqual(p.effect, {"position": D("-5.271"), "loss_part": D("-5.271"), "rounding_part": D("0.000")})
        self.assertEqual(F(5) + F(95) * (F(875) - F("872.5")) / 875, p.a)                  # weight effect + assay effect
        self.check_identities(p)

    def test_e4_after_an_evidenced_gain_the_book_is_zero(self):
        s = Src()
        s.add(br.BK_ISSUE, [("100.000", K, 18)])
        s.add(br.BK_BUY, [("1.230", "F", "1000")], prices=["1000.00"])                      # the surplus as an INPUT line; the 1.230 is an illustrative assumption
        s.add(br.BK_RETURN, [("77.000", "F", "990")])
        p = s.preview()
        self.assertEqual((p.a, p.b, p.proposed_loss, p.rounding_settlement), (0, D("0.000"), D("0.000"), D("0.000")))
        self.check_identities(p)

    def test_review_example_1_positive_remainder_below_posting_precision(self):
        s = two(("1.000", K, 21), ("1.000", "F", "874.9"))
        p = s.preview()
        self.assertEqual(p.a, F(1, 8750))
        self.assertEqual((p.b, p.proposed_loss, p.rounding_settlement, p.rho, p.delta), (D("0.000"), D("0.000"), D("0.000"), F(-1, 8750), F(-1, 8750)))
        self.assertEqual(p.a_state, rec.POSITIVE_BELOW)
        self.assertGreater(p.a, 0)                                                         # it did NOT move into r and was not turned into zero
        self.check_identities(p)

    def test_review_example_2_loss_and_rounding_together(self):
        s = Src()
        s.add(br.BK_ISSUE, [("10.000", K, 18)])
        s.add(br.BK_RETURN, [("3.333", K, 18)] * 3)
        p = s.preview()
        self.assertEqual((p.a, p.b, p.proposed_loss, p.rounding_settlement, p.delta), (F(3, 3500), D("0.000"), D("0.001"), D("-0.001"), F(1, 7000)))
        self.assertEqual(p.effect, {"position": D("0.000"), "loss_part": D("-0.001"), "rounding_part": D("0.001")})      # total 0 although a posted loss of 0.001 is proposed
        self.check_identities(p)

    def test_the_posting_boundary_ties_round_half_up(self):
        for weight, permille, a, loss, b, r in (("0.875", "999.5", F(1, 2000), D("0.001"), D("0.000"), D("-0.001")),      # A = 0.0005 exactly -> 0.001
                                                ("0.875", "998.5", F(3, 2000), D("0.002"), D("0.001"), D("-0.001"))):    # A = 0.0015 exactly -> 0.002
            with self.subTest(a=a):
                p = two(("1.000", K, 21), (weight, "F", permille)).preview()
                self.assertEqual((p.a, p.proposed_loss, p.b, p.rounding_settlement), (a, loss, b, r))
                self.assertEqual(p.a_state, rec.POSITIVE)
                self.check_identities(p)
        below = two(("1.000", K, 21), ("0.999", K, 21)).preview()
        self.assertEqual((below.a, below.proposed_loss, below.rounding_settlement), (F(1, 1000), D("0.001"), D("0.000")))

    def test_a_state_is_decided_by_the_exact_value_not_by_the_display(self):
        self.assertEqual(rec.classify_a(F(0)), rec.ZERO)
        self.assertEqual(rec.classify_a(F(1, 8750)), rec.POSITIVE_BELOW)
        self.assertEqual(rec.classify_a(F(1999, 4000000)), rec.POSITIVE_BELOW)           # just below 1/2000
        self.assertEqual(rec.classify_a(F(1, 2000)), rec.POSITIVE)
        self.assertEqual(rec.classify_a(F(-1, 10 ** 9)), rec.NEGATIVE)
        # smallest positive A the stored precision allows: 0.0001 / 875 = 1.14e-7 shows as 0.000000 in six decimals yet stays positive
        s = Src()
        s.add(br.BK_BUY, [("0.001", "F", "1000")], prices=["10.00"])
        s.add(br.BK_RETURN, [("0.001", "F", "999.9")])
        p = s.preview()
        self.assertEqual(p.a, F(1, 8750000))
        view = p.view()
        self.assertEqual((view["a"], view["aState"], view["aPositiveBelowDisplay"]), ("0.000000", rec.POSITIVE_BELOW, True))

    def test_a_negative_is_an_integrity_alarm_never_a_loss_or_a_gain_path(self):
        s = Src()
        s.add(br.BK_ISSUE, [("1.000", K, 21)])
        s.add(br.BK_RETURN, [("2.000", K, 21)])                                               # an outgoing line the book could never have admitted
        p = s.preview()
        self.assertEqual(p.a_state, rec.NEGATIVE)
        self.assertIsNone(p.proposed_loss)
        self.assertIsNone(p.rounding_settlement)
        self.assertIsNone(p.effect)
        self.assertFalse(p.trustworthy)
        self.assertIn("A_NEGATIVE", [d.code for d in p.diagnostics])
        message = "".join(d.message for d in p.diagnostics if d.code == "A_NEGATIVE")
        self.assertIn("لا تُسجَّل زيادة", message)
        self.assertNotIn("سجّل زيادة", message)

    def test_random_sequences_keep_every_identity(self):
        rng = random.Random(20261018)
        for case in range(300):
            s = Src()
            incoming = rng.randint(1, 4)
            for _ in range(incoming):
                s.add(br.BK_ISSUE, [(f"{rng.randint(1, 90000) / 1000:.3f}", K, rng.choice((18, 21, 24)))])
            admitted = rec.reconcile_preview(s.source()).a
            for _ in range(rng.randint(0, 5)):                                             # only outgoing documents the exact balance would have admitted
                weight, permille = f"{rng.randint(1, 30000) / 1000:.3f}", f"{rng.randint(500, 1000) / 1:.1f}"
                needed = br.analytical(D(weight), br.FINENESS, None, D(permille))
                if needed <= admitted:
                    s.add(br.BK_RETURN, [(weight, "F", permille)])
                    admitted -= needed
            p = s.preview()
            self.assertTrue(p.trustworthy, (case, [d.code for d in p.diagnostics]))
            self.assertGreaterEqual(p.a, 0)
            self.check_identities(p)


def Fraction_of(value):
    return F(value)


class IntegrityTests(unittest.TestCase):
    def healthy(self):
        s = Src()
        s.add(br.BK_ISSUE, [("10.000", K, 21), ("5.000", K, 18)])
        s.add(br.BK_BUY, [("3.000", "F", "900"), ("2.000", "F", "800")], prices=["100.00", "50.00"])
        s.add(br.BK_SELL, [("1.000", K, 21)], prices=["6000.00"])
        s.add(br.BK_RETURN, [("4.000", K, 21), ("1.800", "F", "866.7")])
        return s

    def test_a_healthy_source_has_no_blocking_diagnostic(self):
        s = self.healthy()
        self.assertEqual(s.codes(), [])
        p = s.preview()
        self.assertTrue(p.trustworthy and p.complete)
        self.assertIsNotNone(p.proposed_loss)

    def test_a_forged_posted_value_passes_the_inequality_but_not_the_independent_check(self):
        s = Src()
        s.add(br.BK_BUY, [("1.000", K, 21)], prices=["10.00"])
        line = s.line(1)
        line["eq21_posted"] = D("2.000")
        line["signed_eq21_posted"] = D("2.000")
        p = s.preview()
        self.assertEqual((p.a, p.b, p.sum_abs), (F(1), D("2.000"), F(1)))
        self.assertLessEqual(abs(F(p.b - br.post(p.a))), p.bound)                           # L = 1, r = 1, bound = 1.0005: the inequality still holds
        self.assertIn("LINE_POSTED_MISMATCH", s.codes())
        self.assertFalse(p.trustworthy)
        self.assertIsNone(p.proposed_loss)

    def test_the_evidence_fields_are_two_different_equations(self):
        s = self.healthy()
        s.line(1)["eq21_analytical"] = D("10.000001")
        self.assertEqual(s.codes(), ["LINE_EVIDENCE_MISMATCH"])
        s = self.healthy()
        s.line(1)["posting_difference"] = D("0.000123")
        self.assertEqual(s.codes(), ["LINE_DIFFERENCE_MISMATCH"])
        s = self.healthy()
        s.line(1)["signed_eq21_posted"] = D("-10.000")
        self.assertEqual(s.codes(), ["LINE_SIGN_MISMATCH"])

    def test_shape_and_links(self):
        for field, value, code in (("direction", br.OUT, "LINE_DIRECTION_INVALID"), ("trip_id", 999, "LINE_LINK_MISMATCH"), ("entry_kind", br.BK_BUY, "LINE_LINK_MISMATCH"),
                                   ("weight_grams", D("0.000"), "LINE_FACTS_INVALID"), ("basis", "OTHER", "LINE_FACTS_INVALID")):
            with self.subTest(field=field):
                s = self.healthy()
                s.line(1)[field] = value
                self.assertIn(code, s.codes())
        s = self.healthy()
        s.entries[0]["trip_mode"] = "LEGACY"
        self.assertIn("ENTRY_MODE_MISMATCH", s.codes())
        s = self.healthy()
        s.line(1)["line_price"] = D("1.00")
        self.assertIn("LINE_PRICE_INVALID", s.codes())

    def test_a_document_without_lines_is_detected(self):
        s = self.healthy()
        s.lines = [l for l in s.lines if l["entry_id"] != 3]
        self.assertIn("ENTRY_WITHOUT_LINES", s.codes())

    def test_a_reversed_document_is_not_judged_but_its_state_is_part_of_the_numbers(self):
        s = self.healthy()
        s.entries[3]["correction_id"] = 777                                                # the return is reversed
        p = s.preview()
        self.assertEqual(p.reversed_count, 1)
        self.assertEqual(s.codes(), [])
        self.assertGreater(p.a, rec.reconcile_preview(self.healthy().source()).a)

    def test_the_scrap_leg_is_checked_in_both_directions(self):
        s = self.healthy()
        s.scrap[0]["weight_delta_grams"] = D("-9.999")
        self.assertIn("LEG_MISMATCH", s.codes())
        s = self.healthy()
        s.line(1)["scrap_movement_id"] = None
        self.assertIn("LEG_MISSING", s.codes())
        self.assertIn("LEG_ORPHAN", s.codes())                                              # the leg is still there but no line claims it
        s = self.healthy()
        s.line(1, 2)["scrap_movement_id"] = 9999
        self.assertIn("LEG_MISSING", s.codes())
        s = self.healthy()
        s.scrap.append({"id": 50, "karat_id": 1, "weight_delta_grams": D("-1.000"), "movement_code": rec.SCRAP_LEG_CODE[br.BK_ISSUE], "reference_id": 1})
        self.assertEqual(s.codes(), ["LEG_ORPHAN"])
        s = self.healthy()
        s.line(4, 2)["scrap_movement_id"] = 1                                                  # an assayed line must not carry a Scrap leg
        self.assertTrue({"LEG_UNEXPECTED"} <= set(s.codes()))

    def test_trade_header_and_total(self):
        s = self.healthy()
        s.trades[0]["total_price"] = D("149.99")
        self.assertEqual(s.codes(), ["TRADE_TOTAL_MISMATCH"])
        s = self.healthy()
        s.trades = []
        self.assertEqual(s.codes(), ["TRADE_MISSING", "TRADE_MISSING"])
        s = self.healthy()
        s.trades.append({"entry_id": 1, "entry_kind": br.BK_BUY, "total_price": D("1.00")})
        self.assertIn("TRADE_ORPHAN", s.codes())

    def test_the_original_stock_record_is_checked_not_the_items_future_balance(self):
        s = self.healthy()
        s.stock[0]["grams_delta"] = D("1.700")
        self.assertEqual(s.codes(), ["STOCK_RECORD_MISMATCH"])
        s = self.healthy()
        s.stock = []
        self.assertEqual(s.codes(), ["STOCK_RECORD_MISSING"])
        s = self.healthy()
        s.stock.append({"item_entry_id": 4, "item_line_no": 2, "movement_type": "ISSUE", "grams_delta": D("-1.800"), "eq21_posted_delta": D("-1.783")})
        self.assertEqual(s.codes(), [])                                                     # a later ISSUE (Phase 2) is not this preview's business: the RECEIVE record is what is verified

    def test_unknown_unsupported_orphan_and_duplicate_are_never_dropped(self):
        for kind in ("BK_NEXT_KIND", "MELT", "NOT_A_KIND"):                          # BK_ISS_STK (058) and BK_GAIN (059) are supported kinds
            with self.subTest(kind=kind):
                s = self.healthy()
                s.entries.append({"id": 50, "trip_id": TRIP, "trip_mode": "BOOK", "entry_no": 50, "entry_kind": kind, "correction_id": None})
                p = s.preview()
                self.assertEqual(s.codes(), ["UNSUPPORTED_KIND"])
                self.assertFalse(p.trustworthy)
                self.assertIsNone(p.proposed_loss)
        s = self.healthy()
        s.lines.append({**s.line(1), "entry_id": 77})
        self.assertIn("LINE_ORPHAN", s.codes())
        s = self.healthy()
        s.lines.append(dict(s.line(1)))
        self.assertIn("LINE_DUPLICATE", s.codes())

    def test_known_excluded_kinds_are_not_unsupported(self):
        s = self.healthy()
        for number, kind in enumerate(("ISSUE", "RETURN", "SUPPLIER_PAY", "BK_RECON"), 60):
            s.entries.append({"id": number, "trip_id": TRIP, "trip_mode": "BOOK", "entry_no": number, "entry_kind": kind, "correction_id": None})
        self.assertEqual(s.codes(), [])                                                     # cash kinds and the FUTURE reconciliation document are excluded, not rejected

    def test_an_unreadable_part_is_incomplete_never_zero(self):
        s = self.healthy()
        s.incomplete.append(("SOURCE_LINES_UNREADABLE", "OperationalError"))
        s.lines = []                                                                       # what a failed read would leave
        p = s.preview()
        self.assertFalse(p.complete)
        self.assertFalse(p.trustworthy)
        self.assertIsNone(p.proposed_loss)
        self.assertIsNone(p.effect)
        self.assertIn("SOURCE_LINES_UNREADABLE", [d.code for d in p.diagnostics])


class CoverageLimitTests(unittest.TestCase):
    """What the available checks CAN and CANNOT see (documented in the preview itself)."""

    def test_a_deleted_line_of_a_multi_line_buy_is_seen_through_the_trade_total(self):
        s = Src()
        s.add(br.BK_BUY, [("3.000", "F", "900"), ("2.000", "F", "800")], prices=["100.00", "50.00"])
        s.lines = [l for l in s.lines if l["line_no"] != 2]
        self.assertEqual(s.codes(), ["TRADE_TOTAL_MISMATCH"])

    def test_a_deleted_line_that_left_its_scrap_leg_is_seen_as_an_orphan_leg(self):
        s = Src()
        s.add(br.BK_ISSUE, [("10.000", K, 21), ("5.000", K, 18)])
        s.lines = [l for l in s.lines if l["line_no"] != 2]
        s.scrap[1]["karat_id"] = 1
        self.assertEqual(s.codes(), ["LEG_ORPHAN"])

    def test_a_deleted_line_together_with_its_leg_is_NOT_detectable(self):
        s = Src()
        s.add(br.BK_ISSUE, [("10.000", K, 21), ("5.000", K, 18)])
        s.add(br.BK_RETURN, [("4.000", K, 21)])
        s.lines = [l for l in s.lines if not (l["entry_id"] == 1 and l["line_no"] == 2)]
        s.scrap = [m for m in s.scrap if m["id"] != 2]
        p = s.preview()
        self.assertEqual(s.codes(), [])                                                     # nothing independent remains: the limit the preview states openly
        self.assertTrue(p.trustworthy)
        self.assertIn("COVERAGE_LINE_DELETION", [c for c, _ in rec.COVERAGE_NOTES])

    def test_numbering_gaps_alone_are_not_evidence(self):
        s = Src()
        s.add(br.BK_ISSUE, [("10.000", K, 21), ("5.000", K, 18), ("2.000", K, 24)])
        s.lines = [l for l in s.lines if l["line_no"] != 2]
        s.scrap = [m for m in s.scrap if m["id"] != 2]
        self.assertEqual(s.codes(), [])
        self.assertIn("COVERAGE_NUMBERING", [c for c, _ in rec.COVERAGE_NOTES])

    def test_the_view_carries_the_coverage_notes_and_never_claims_soundness(self):
        view = Src().preview().view()
        self.assertEqual(len(view["coverage"]), len(rec.COVERAGE_NOTES))


class TokenContractTests(unittest.TestCase):
    def source(self):
        s = Src()
        s.add(br.BK_ISSUE, [("10.000", K, 21)])
        s.add(br.BK_RETURN, [("4.000", K, 21)])
        return s

    def token(self, s):
        return rec.source_token(s.entries, s.lines)

    def test_stable_and_order_independent(self):
        s = self.source()
        first = self.token(s)
        s.entries.reverse()
        s.lines.reverse()
        self.assertEqual(self.token(s), first)
        self.assertEqual(len(first), 64)

    def test_a_posted_or_reversed_source_document_changes_it(self):
        base = self.token(self.source())
        s = self.source()
        s.add(br.BK_RETURN, [("1.000", K, 21)])
        self.assertNotEqual(self.token(s), base)
        s = self.source()
        s.entries[1]["correction_id"] = 5
        self.assertNotEqual(self.token(s), base)
        s = self.source()
        s.line(1)["weight_grams"] = D("10.001")
        self.assertNotEqual(self.token(s), base)
        s = self.source()
        s.line(2)["eq21_posted"] = D("4.001")
        self.assertNotEqual(self.token(s), base)

    def test_cash_events_legs_and_the_future_reconciliation_do_not_change_it(self):
        base = self.token(self.source())
        s = self.source()
        for number, kind in enumerate(("ISSUE", "RETURN", "SUPPLIER_PAY", "BK_RECON"), 40):
            s.entries.append({"id": number, "trip_id": TRIP, "trip_mode": "BOOK", "entry_no": number, "entry_kind": kind, "correction_id": None})
        s.entries.append({"id": 90, "trip_id": TRIP, "trip_mode": "BOOK", "entry_no": 90, "entry_kind": "BK_RECON", "correction_id": 12})            # its reversal too
        s.scrap.append({"id": 77, "karat_id": 1, "weight_delta_grams": D("-1.000"), "movement_code": "TRIP_CUSTODY_OUT", "reference_id": 1})
        s.cash = D("123.45")
        self.assertEqual(self.token(s), base)

    def test_unknown_kinds_are_not_hashed_but_are_diagnosed(self):
        s = self.source()
        base = self.token(s)
        s.entries.append({"id": 50, "trip_id": TRIP, "trip_mode": "BOOK", "entry_no": 50, "entry_kind": "BK_NEXT_KIND", "correction_id": None})
        self.assertEqual(self.token(s), base)
        self.assertIn("UNSUPPORTED_KIND", s.codes())

    def test_it_is_a_source_token_not_a_snapshot_hash(self):
        self.assertEqual(rec.ALGORITHM, "book-source-v1")
        self.assertEqual(self.source().preview().view()["sourceToken"], self.token(self.source()))
        self.assertEqual(rec.SOURCE_KINDS, (br.BK_ISSUE, br.BK_BUY, br.BK_SELL, br.BK_RETURN, br.BK_ISS_STK, br.BK_RECEIVE, br.BK_DLV_SUP, br.BK_DLV_MKT, br.BK_GAIN))   # explicit, never a prefix


class ConstantsPinnedTests(unittest.TestCase):
    def test_the_scrap_leg_codes_are_the_repositorys(self):
        self.assertEqual(rec.SCRAP_LEG_CODE, {br.BK_ISSUE: repository.SCRAP_TYPE[repository.ISSUE], br.BK_RETURN: repository.SCRAP_TYPE[repository.RETURN]})

    def test_the_source_kinds_are_the_current_book_kinds_and_a_new_kind_must_be_classified(self):
        self.assertEqual(set(rec.SOURCE_KINDS), set(br.BOOK_KINDS))                          # when book_rules gains a kind this fails: the preview must classify it explicitly


if __name__ == "__main__":
    unittest.main()
