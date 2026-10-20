"""Market-custody settlement step 2a (060, owner 2026-10-20): the melt recorded AFTER the owner returns from the market. Gold left WITHOUT a destination (UNCLASSIFIED); the melt
takes grams of several karats out of their RAW pool and puts ONE bar into the BAR pool by its 21K equivalent. The bar's weight and INTEGER fineness are kept for the reports and the
loss is split in weight loss + fineness loss (negative = gain).

Real MarketTripService and CorrectionService over the in-memory transactional database of tests/market_trip_fakes.py (a failed transaction restores every table; the 060 CHECK
constraints are mirrored as asserts). NOT real-MySQL tests."""
from decimal import Decimal as D
import unittest

from services import settlement_rules
from services.market_trip_service import TripError
from tests.test_market_trips import K18, K21, K24, TODAY, TripCase, key


def lines(*rows):
    return [{"karat_id": k, "weight": w} for k, w in rows]


class FiguresTests(unittest.TestCase):
    """The owner's worked example, by hand: 40 g of 21K + 5 g of 18K melt into a bar of 44.8 g at fineness 850."""

    def test_the_worked_example(self):
        f = settlement_rules.melt_figures([(21, D("40")), (18, D("5"))], D("44.8"), 850)
        self.assertEqual((f["input_weight"], f["theoretical_eq21"], f["actual_eq21"]), (D("45.000"), D("44.286"), D("43.520")))
        self.assertEqual((f["loss_eq21"], f["weight_loss_eq21"], f["fineness_loss_eq21"]), (D("0.766"), D("0.197"), D("0.569")))
        self.assertEqual(f["weight_loss_eq21"] + f["fineness_loss_eq21"], f["loss_eq21"])                    # the split always adds up exactly
        self.assertEqual(f["difference_eq21"], D("-0.766"))

    def test_a_melt_with_no_loss_and_a_gain(self):
        none = settlement_rules.melt_figures([(21, D("10"))], D("10"), 875)
        self.assertEqual((none["loss_eq21"], none["weight_loss_eq21"], none["fineness_loss_eq21"]), (D("0.000"), D("0.000"), D("0.000")))
        gain = settlement_rules.melt_figures([(21, D("10"))], D("10"), 900)                                      # finer than promised: a GAIN (negative loss)
        self.assertEqual((gain["loss_eq21"], gain["weight_loss_eq21"], gain["fineness_loss_eq21"]), (D("-0.286"), D("0.000"), D("-0.286")))

    def test_the_mixed_karat_example_of_the_owner(self):
        f = settlement_rules.melt_figures([(21, D("10")), (18, D("5")), (24, D("5"))], D("19"), 875)           # theoretically 20.000, the bar is worth 19.000
        self.assertEqual((f["theoretical_eq21"], f["actual_eq21"], f["loss_eq21"]), (D("20.000"), D("19.000"), D("1.000")))

    def test_fineness_is_an_integer_per_mille(self):
        self.assertEqual(settlement_rules.parse_fineness("789"), 789)
        for bad, code in (("", "FINENESS_REQUIRED"), ("850.5", "FINENESS_INVALID"), ("abc", "FINENESS_INVALID"), ("0", "FINENESS_RANGE"), ("1001", "FINENESS_RANGE"),
                          ("-5", "FINENESS_INVALID")):
            with self.assertRaises(TripError) as raised:
                settlement_rules.parse_fineness(bad)
            self.assertEqual(raised.exception.code, code, bad)


class SettleMeltCase(TripCase):
    """OPEN trip 1: 21K 50 g and 18K 10 g left WITHOUT a destination (the stage 1 issue)."""

    def setUp(self):
        super().setUp()
        self.db.safe_allowance = D("0.00")
        self.trip = self.service.issue("", "", lines((K21, "50"), (K18, "10")), TODAY, "", key(1))["trip_id"]           # NO designation: stage 1

    def melt(self, rows=None, weight="44.8", fineness="850", workshop="ورشة النور", notes="", trip=None, request=None, when=TODAY):
        self.n += 1
        return self.service.settle_melt(trip or self.trip, rows if rows is not None else lines((K21, "40"), (K18, "5")), weight, fineness, workshop, when, notes,
                                        request or key(900 + self.n))

    def gold(self, trip=None):
        return {(g["karat_value"], g["designation"]): g for g in self.service.trip_detail(trip or self.trip)["custody"]["gold"]}

    def back(self, rows, trip=None, request=None):
        """Return gold WITHOUT a destination to the shop (a return keeps the line's own designation: UNCLASSIFIED)."""
        self.n += 1
        return self.service.return_custody(trip or self.trip, "", [{"karat_id": k, "weight": w, "designation": "UNCLASSIFIED"} for k, w in rows], TODAY, "",
                                           request or key(800 + self.n))

    def bar(self, trip=None):
        return self.service.trip_detail(trip or self.trip)["custody"]["bar"]


class PostingTests(SettleMeltCase):
    def test_the_melt_moves_raw_grams_into_the_bar_pool_by_its_equivalent(self):
        done = self.melt()
        self.assertEqual((done["kind"], done["code"], done["figures"]["actual_eq21"], done["figures"]["loss_eq21"]), ("ST_MELT", "MT000001-2", "43.520", "0.766"))
        gold = self.gold()
        self.assertEqual((gold[(21, "UNCLASSIFIED")]["outstanding"], gold[(18, "UNCLASSIFIED")]["outstanding"]), (D("10.000"), D("5.000")))     # 50-40 and 10-5 remain
        self.assertEqual(self.bar(), D("43.520"))
        entry = self.db.entries[int(done["entry_id"])]
        self.assertEqual(entry["entry_kind"], "ST_MELT")
        moves = [(m["pool"], m["karat_id"], m["delta"]) for m in self.db.settle_moves if m["entry_id"] == entry["id"]]
        self.assertEqual(sorted(moves, key=str), sorted([("RAW", 1, D("-40.000")), ("RAW", 2, D("-5.000")), ("BAR", None, D("43.520"))], key=str))

    def test_only_the_settlement_ledger_moves_no_cash_scrap_supplier_or_lot(self):
        cash, scrap, lots = self.db.cash(), len(self.db.scrap), len(self.db.lots)
        self.melt()
        self.assertEqual((self.db.cash(), len(self.db.scrap), len(self.db.lots)), (cash, scrap, lots))
        melt = self.db.settle_melts[max(self.db.settle_melts)]
        self.assertEqual((melt["output_weight_grams"], melt["fineness_permille"], melt["workshop"]), (D("44.800"), 850, "ورشة النور"))        # kept for the reports

    def test_the_custody_cannot_close_while_the_bar_is_undistributed(self):
        self.melt()
        self.back([(K21, "10"), (K18, "5")])                                                                # everything not melted is back in the shop
        refused = self.refused("OUTSTANDING_CUSTODY", lambda: self.service.close(self.trip))
        self.assertIn("رصيد السبيكة", str(refused.args[0]))

    def test_the_gold_not_melted_still_returns_to_the_shop_and_the_melted_grams_cannot(self):
        self.melt()
        self.refused("RETURN_EXCEEDS_CUSTODY", lambda: self.back([(K21, "10.001")]))
        self.back([(K21, "10"), (K18, "5")])
        self.assertEqual((self.gold()[(21, "UNCLASSIFIED")]["outstanding"], self.gold()[(18, "UNCLASSIFIED")]["outstanding"]), (D("0.000"), D("0.000")))

    def test_refusals_leave_no_trace(self):
        self.refused("SETTLE_MELT_EXCEEDS_CUSTODY", lambda: self.melt(lines((K21, "50.001"))))
        self.refused("SETTLE_MELT_EXCEEDS_CUSTODY", lambda: self.melt(lines((K24, "1"))))                  # no 24K left the shop
        self.refused("MELT_INPUT_REQUIRED", lambda: self.melt([]))
        self.refused("OUTPUT_WEIGHT_REQUIRED", lambda: self.melt(weight="0"))
        self.refused("FINENESS_INVALID", lambda: self.melt(fineness="850.5"))
        self.refused("FINENESS_RANGE", lambda: self.melt(fineness="1500"))
        self.refused("REASON_REQUIRED", lambda: self.melt(workshop="  "))

    def test_a_trip_that_was_issued_with_a_destination_cannot_be_melted_here(self):
        legacy = self.service.issue("", "", [{"karat_id": K21, "weight": "5", "designation": "MELT"}], TODAY, "", key(50))["trip_id"]
        self.refused("SETTLE_MELT_EXCEEDS_CUSTODY", lambda: self.melt(lines((K21, "1")), trip=legacy))        # that gold is MELT-designated: the old flow melts it

    def test_exact_replay_and_a_changed_payload(self):
        first = self.melt(request=key(5))
        again = self.melt(request=key(5))
        self.assertEqual((again["entry_id"], again["duplicate"]), (first["entry_id"], True))
        self.assertEqual(self.bar(), D("43.520"))                                                         # posted once
        self.refused("REQUEST_CONFLICT", lambda: self.melt(weight="44.7", request=key(5)))

    def test_a_failure_in_any_step_rolls_everything_back(self):
        for step in ("settle_melt", "settle_melt_input", "settle_move"):
            self.db.fail_at = step
            before = self.state()
            with self.assertRaises(RuntimeError):
                self.melt()
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)

    def test_a_closed_trip_and_a_missing_right_are_refused(self):
        self.back([(K21, "50"), (K18, "10")])
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.melt())


class ReversalTests(SettleMeltCase):
    def reverse(self, entry, reason="خطأ"):
        return self.corrections.reverse("MARKET_TRIP_ENTRY", entry, reason)

    def test_a_reversal_gives_the_grams_back_and_empties_the_bar_pool(self):
        done = self.melt()
        self.reverse(done["entry_id"])
        gold = self.gold()
        self.assertEqual((gold[(21, "UNCLASSIFIED")]["outstanding"], gold[(18, "UNCLASSIFIED")]["outstanding"]), (D("50.000"), D("10.000")))
        self.assertEqual(self.bar(), D("0.000"))
        self.assertIn(int(done["entry_id"]), self.db.settle_melts)                                          # the document stays as history

    def test_the_issue_cannot_be_reversed_while_its_gold_is_melted(self):
        self.melt()
        from services.correction_service import CorrectionError
        with self.assertRaises(CorrectionError) as raised:
            self.reverse("1")
        self.assertEqual(raised.exception.code, "DEPENDENT_RETURN_EXISTS")


if __name__ == "__main__":
    unittest.main()
