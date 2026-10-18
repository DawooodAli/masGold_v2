"""BOOK-mode arithmetic (services/book_rules.py): analytical equivalents, posted equivalents, the balance, availability and the integrity bound.
Pure functions over exact fractions: no database, no float. Includes the rounding-only example of the design addendum and an exact random property test."""
from decimal import Decimal as D
from fractions import Fraction as F
import random
import unittest

from services import book_rules as br
from services.trip_errors import TripError


def line(direction, weight, basis=br.MASTER_KARAT, karat=21, fineness=None):
    return br.plan_line(direction, D(weight), basis, 1 if basis == br.MASTER_KARAT else None, karat if basis == br.MASTER_KARAT else None,
                        None if fineness is None else D(fineness))


class AnalyticalAndPostedTests(unittest.TestCase):
    def test_master_karat_and_assay_use_the_exact_formulas(self):
        self.assertEqual(br.analytical(D("10.000"), br.MASTER_KARAT, 18), F(60, 7))
        self.assertEqual(br.analytical(D("1.800"), br.FINENESS, None, D("866.7")), F(18, 10) * F(8667, 10) / 875)
        self.assertEqual(br.analytical(D("5"), br.FINENESS, None, D("875.0")), F(5))
        self.assertNotEqual(br.analytical(D("5"), br.FINENESS, None, D("916.7")), br.analytical(D("5"), br.MASTER_KARAT, 22))            # 22K is 916.666...: never mapped
        for bad in ({"basis": "X"}, {"basis": br.MASTER_KARAT}, {"basis": br.FINENESS}):
            with self.assertRaises(TripError):
                br.analytical(D("1"), bad["basis"])

    def test_posting_rounds_half_up_once_and_keeps_the_difference(self):
        self.assertEqual(br.post(F(60, 7)), D("8.571"))
        self.assertEqual(br.post(F(1, 2000)), D("0.001"))                                    # an exact tie rounds UP (0.0005 -> 0.001)
        self.assertEqual(br.post(F(1, 4000)), D("0.000"))                                    # below the tie
        self.assertEqual(br.round_half_up(F(-1, 2000), 3), D("-0.001"))                      # symmetric away from zero
        planned = line(br.IN, "10.000", karat=18)
        self.assertEqual((planned.posted, planned.analytical_evidence, planned.difference), (D("8.571"), D("8.571429"), D("-0.000429")))
        self.assertEqual(br.post(br.analytical(D("1.800"), br.FINENESS, None, D("866.7"))), D("1.783"))        # the addendum's item
        self.assertEqual(br.post(F(0)), D("0.000"))

    def test_no_float_enters_the_computation(self):
        a = br.analytical(D("0.333"), br.FINENESS, None, D("333.3"))
        self.assertIsInstance(a, F)
        self.assertIsInstance(br.post(a), D)
        self.assertEqual(br.round_half_up(F(1, 3), 6), D("0.333333"))

    def test_the_stored_line_recomputes_the_analytical_value_from_the_physical_facts(self):
        row = {"direction": br.IN, "weight_grams": D("10.000"), "basis": br.MASTER_KARAT, "karat_id": 2, "karat_value": 18, "fineness_permille": None,
               "eq21_posted": D("8.571"), "eq21_analytical": D("999999.000000")}               # a wrong stored evidence is ignored
        stored = br.stored_line(row)
        self.assertEqual(stored.analytical, F(60, 7))
        self.assertEqual((stored.posted, stored.difference), (D("8.571"), D("-0.000429")))


class BalanceAndAvailabilityTests(unittest.TestCase):
    def test_the_book_balance_separates_the_analytical_value_from_the_posting_drift(self):
        issue = line(br.IN, "10.000", karat=18)
        sells = [line(br.OUT, w, karat=18) for w in ("3.333", "3.333")]
        balance = br.balance_of([issue, *sells])
        self.assertEqual(balance.analytical_open, F(60, 7) - 2 * F(59994, 21000))
        self.assertEqual(balance.posted, D("8.571") - D("5.714"))
        self.assertEqual(balance.drift, F(balance.posted) - balance.analytical_open)
        self.assertEqual(sum((l.posted for l in sells), D("0")), D("5.714"))                 # a document total is the SUM of its posted lines

    def test_a_legitimate_final_output_is_not_refused_because_rounding_leaves_the_posted_book_negative(self):
        """The addendum's example: 10.000 g @18K out in three pieces. Analytical balance reaches exactly 0, the POSTED book -0.001."""
        lines = [line(br.IN, "10.000", karat=18), line(br.OUT, "3.333", karat=18), line(br.OUT, "3.333", karat=18)]
        third = line(br.OUT, "3.334", karat=18)
        before = br.balance_of(lines)
        self.assertEqual((before.posted, before.analytical_open), (D("2.857"), F(60012, 21000)))
        self.assertEqual(before.analytical_open, third.analytical)                           # exactly enough
        br.check_outgoing(before, [third])                                                    # accepted
        after = br.balance_of(lines + [third])
        self.assertEqual((after.posted, after.analytical_open), (D("-0.001"), F(0)))
        self.assertEqual(after.abs_difference, F(1, 1000))                                    # the drift bound equals the drift: -0.001 is exactly the bound
        self.assertEqual(after.drift, F(-1, 1000))
        with self.assertRaises(TripError) as refused:                                         # nothing is left analytically
            br.check_outgoing(after, [line(br.OUT, "0.001", karat=18)])
        self.assertEqual(refused.exception.code, "BOOK_EXCEEDS_BALANCE")

    def test_an_output_above_the_analytical_balance_is_refused_with_the_available_figure(self):
        balance = br.balance_of([line(br.IN, "10.000")])
        with self.assertRaises(TripError) as refused:
            br.check_outgoing(balance, [line(br.OUT, "10.001")])
        self.assertEqual(refused.exception.code, "BOOK_EXCEEDS_BALANCE")
        self.assertIn("10.000", str(refused.exception))
        br.check_outgoing(balance, [line(br.OUT, "10.000")])                                  # exactly the balance
        br.check_outgoing(balance, [line(br.OUT, "4.000"), line(br.OUT, "6.000")])             # a document is judged as a whole

    def test_the_integrity_check_fails_only_when_the_drift_is_not_explained_by_rounding(self):
        balance = br.balance_of([line(br.IN, "10.000")])
        tampered = br.Balance(balance.analytical_in, F(0), balance.posted_in, D("0.000"), F(0), 1)   # analytically 10.000 open, posted 10.000
        forged = br.Balance(F(10), F(0), D("0.000"), D("0.000"), F(0), 1)                               # analytically 10.000 open but posted 0.000
        br.check_outgoing(tampered, [line(br.OUT, "10.000")])
        with self.assertRaises(TripError) as refused:
            br.check_outgoing(forged, [line(br.OUT, "10.000")])                               # posted -10.000 is no rounding
        self.assertEqual(refused.exception.code, "BOOK_INTEGRITY")

    def test_reversing_incoming_gold_is_judged_on_the_remaining_lines(self):
        issue, sell = line(br.IN, "10.000"), line(br.OUT, "6.000")
        self.assertIsNone(br.check_without_incoming(br.balance_of([sell, line(br.IN, "10.000")])))
        blocker = br.check_without_incoming(br.balance_of([sell]))
        self.assertEqual(blocker[0], "DEPENDENT_BOOK_USE_EXISTS")
        self.assertIsNone(br.check_without_incoming(br.balance_of([])))
        self.assertEqual(br.balance_of([issue]).lines, 1)

    def test_view_figures(self):
        view = br.balance_of([line(br.IN, "10.000", karat=18), line(br.OUT, "10.000", karat=18)]).view()
        self.assertEqual((view["posted"], view["analytical"], view["analytical_available"]), (D("0.000"), D("0.000000"), D("0.000")))
        view = br.balance_of([line(br.IN, "1.000")]).view()
        self.assertEqual((view["posted"], view["analytical"], view["drift"]), (D("1.000"), D("1.000000"), D("0.000000")))

    def test_grouped_rows_give_the_same_balance_as_the_lines(self):
        lines = [line(br.IN, "10.000", karat=18), line(br.IN, "5.000", br.FINENESS, fineness="916.7"), line(br.OUT, "3.333", karat=18),
                 line(br.OUT, "3.334", karat=18)]
        groups = [{"direction": br.IN, "basis": br.MASTER_KARAT, "karat_value": 18, "fineness_permille": None, "weight_grams": D("10.000"),
                   "eq21_posted": lines[0].posted, "lines": 1},
                  {"direction": br.IN, "basis": br.FINENESS, "karat_value": None, "fineness_permille": D("916.7"), "weight_grams": D("5.000"),
                   "eq21_posted": lines[1].posted, "lines": 1},
                  {"direction": br.OUT, "basis": br.MASTER_KARAT, "karat_value": 18, "fineness_permille": None, "weight_grams": D("6.667"),
                   "eq21_posted": lines[2].posted + lines[3].posted, "lines": 2}]
        whole, grouped = br.balance_of(lines), br.balance_of_groups(groups)
        self.assertEqual((grouped.analytical_open, grouped.posted), (whole.analytical_open, whole.posted))


class ModeRuleTests(unittest.TestCase):
    def test_each_kind_exists_only_in_its_mode(self):
        for kind in br.BOOK_KINDS:
            self.assertTrue(br.kind_allowed_in_mode(kind, br.BOOK))
            self.assertFalse(br.kind_allowed_in_mode(kind, br.LEGACY))
        for kind in br.LEGACY_ONLY_KINDS:
            self.assertTrue(br.kind_allowed_in_mode(kind, br.LEGACY))
            self.assertFalse(br.kind_allowed_in_mode(kind, br.BOOK))
        for kind in br.SHARED_CASH_KINDS:                                                     # the cash ledger is shared, never duplicated
            self.assertTrue(br.kind_allowed_in_mode(kind, br.LEGACY) and br.kind_allowed_in_mode(kind, br.BOOK))
        with self.assertRaises(TripError) as refused:
            br.require_kind_in_mode("MELT", br.BOOK)
        self.assertEqual(refused.exception.code, "TRIP_MODE_MISMATCH")

    def test_a_row_without_the_column_is_legacy(self):
        self.assertEqual(br.trip_mode({"id": 1}), br.LEGACY)
        self.assertEqual(br.trip_mode(None), br.LEGACY)
        br.require_legacy_trip({"id": 1})
        with self.assertRaises(TripError):
            br.require_legacy_trip({"accounting_mode": "BOOK"})
        with self.assertRaises(TripError):
            br.require_book_trip({"id": 1})


class RandomSequenceProperties(unittest.TestCase):
    def test_exact_identities_hold_over_random_sequences(self):
        """Bk = Lambda_open + rho, |rho| <= sum|d|, an analytically available output is never refused, an unavailable one always is."""
        rng = random.Random(56)
        for _ in range(400):
            lines = []
            for _ in range(rng.randint(1, 8)):
                weight = D(rng.randint(1, 30000)) / 1000
                if rng.random() < 0.5:
                    karat = rng.choice((18, 21, 22, 24))
                    lines.append(line(br.IN, weight, karat=karat))
                else:
                    lines.append(line(br.IN, weight, br.FINENESS, fineness=D(rng.randint(100, 1000)) / 10))
            for _ in range(rng.randint(0, 12)):
                weight = D(rng.randint(1, 8000)) / 1000
                candidate = line(br.OUT, weight, karat=rng.choice((18, 21, 22, 24))) if rng.random() < 0.5 \
                    else line(br.OUT, weight, br.FINENESS, fineness=D(rng.randint(100, 1000)) / 10)
                balance = br.balance_of(lines)
                self.assertEqual(F(balance.posted), balance.analytical_open + balance.drift)
                self.assertLessEqual(abs(balance.drift), balance.abs_difference)
                if candidate.analytical <= balance.analytical_open:
                    br.check_outgoing(balance, [candidate])                                   # never refused: availability is analytical
                    lines.append(candidate)
                else:
                    with self.assertRaises(TripError) as refused:
                        br.check_outgoing(balance, [candidate])
                    self.assertEqual(refused.exception.code, "BOOK_EXCEEDS_BALANCE")


if __name__ == "__main__":
    unittest.main()
