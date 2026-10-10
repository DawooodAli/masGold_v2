"""Logo heating (042, حمّية اللوجو): LOGO custody heated without melting. The processed grams leave, the ACTUAL resulting grams return
under the same karat and the LOGO designation; only physical weights are stored; the signed difference is permanent evidence.

Real MarketTripService and the real CorrectionService over the in-memory transactional database of tests/market_trip_fakes.py (a
failed transaction restores every table; the 042 CHECK / unique constraints are mirrored as asserts). NOT real-MySQL tests: the SQL,
the constraints and the rollback were exercised on the masgold_rehearsal sandbox, single-threaded. Concurrency is not exercised:
a heat takes only the trip lock, so same-trip writers serialize on it by design, but that is not independently proven here."""
import copy
from decimal import Decimal as D
import unittest

from mysql.connector import IntegrityError

from services.correction_service import CorrectionError
from services.market_trip_service import TripError
from tests.market_trip_fakes import MANAGER, OWNER_NO_CORRECT
from tests.test_market_trips import K18, K21, K24, TODAY, TripCase, key


class HeatCase(TripCase):
    def setUp(self):
        super().setUp()
        self.trip = self.issue("", [(K21, "10", "LOGO"), (K18, "5", "LOGO")])["trip_id"]
        self.baseline = self.untouched()

    def untouched(self):
        return copy.deepcopy((self.db.cash_moves, self.db.scrap, self.db.melts, self.db.melt_inputs, self.db.lots, self.db.lot_moves))

    def heat(self, rows, trip=None, request=None, service=None, workshop="ورشة الحمية", reference="", gain="", notes="", when=TODAY):
        self.n += 1
        lines = [{"karat_id": k, "input_weight": b, "output_weight": a} for k, b, a in rows]
        return (service or self.service).heat(trip or self.trip, lines, when, workshop, reference, gain, notes, request or key(2000 + self.n))

    def logo(self, trip=None, karat=K21):
        rows = {(g["karat_id"], g["designation"]): g for g in self.service.trip_detail(trip or self.trip)["custody"]["gold"]}
        return rows[(int(karat), "LOGO")]["outstanding"]

    def denied(self, call):
        before = self.state()
        with self.assertRaises(PermissionError):
            call()
        self.assertEqual(self.state(), before, "a denied request left a trace")

    def detail(self, trip=None):
        return self.service.trip_detail(trip or self.trip)


class HeatPostingTests(HeatCase):
    def test_partial_processing_records_the_difference_and_keeps_the_karat_and_designation(self):
        done = self.heat([(K21, "4", "3.9")], reference="ح-17", notes="فرن 2")
        self.assertEqual((done["kind"], done["difference"]), ("HEAT", D("-0.100")))
        self.assertEqual(self.logo(), D("9.900"))                                              # 10 - 4 + 3.9
        self.assertEqual(self.logo(karat=K18), D("5.000"))                                     # untouched karat
        heat = self.detail()["heats"][0]
        self.assertEqual((heat["workshop"], heat["reference"], heat["reversed"]), ("ورشة الحمية", "ح-17", False))
        self.assertEqual([(l["karat_value"], l["input"], l["output"], l["difference"]) for l in heat["lines"]],
                         [(21, D("4.000"), D("3.900"), D("-0.100"))])
        self.assertEqual((heat["total_input"], heat["total_output"], heat["total_difference"]), (D("4.000"), D("3.900"), D("-0.100")))
        self.assertEqual(self.untouched(), self.baseline)                                       # no cash / Scrap / supplier / melt / lot effect
        self.assertEqual(len(self.db.heat_lines), 1)

    def test_the_actual_remaining_grams_return_through_the_normal_logo_path_and_the_trip_closes(self):
        self.heat([(K21, "4", "3.9"), (K18, "5", "4.95")])
        self.refused("RETURN_EXCEEDS_CUSTODY", lambda: self.ret(self.trip, "", [(K21, "10", "LOGO")]))   # the issued 10 g no longer exists
        self.ret(self.trip, "", [(K21, "9.9", "LOGO"), (K18, "4.95", "LOGO")])
        self.assertEqual(self.service.trip_detail(self.trip)["custody"]["settled"], True)
        self.assertEqual(self.service.close(self.trip)["status"], "CLOSED")
        self.assertEqual(self.db.physical(1), D("100.000") - D("0.100"))                        # the shop Scrap reflects the real weight

    def test_repeated_heating_sums_the_documents_without_double_counting(self):
        self.heat([(K21, "4", "3.9")])
        self.heat([(K21, "3.9", "3.85")])                                                       # pooled custody: independent evidence
        self.assertEqual(self.logo(), D("9.850"))
        totals = self.detail()["heat_totals"]
        self.assertEqual([(t["karat_value"], t["input"], t["output"], t["difference"]) for t in totals], [(21, D("7.900"), D("7.750"), D("-0.150"))])
        self.refused("HEAT_EXCEEDS_CUSTODY", lambda: self.heat([(K21, "9.851", "9")]))

    def test_several_karats_in_one_document_one_line_per_karat(self):
        done = self.heat([(K21, "2", "1.99"), (K18, "1", "0.98")])
        heat = self.detail()["heats"][0]
        self.assertEqual({l["karat_value"]: l["difference"] for l in heat["lines"]}, {21: D("-0.010"), 18: D("-0.020")})
        self.assertEqual(heat["total_difference"], D("-0.030"))
        self.assertEqual(len([e for e in self.db.entries.values() if e["entry_kind"] == "HEAT"]), 1)
        self.refused("KARAT_DUPLICATE", lambda: self.heat([(K21, "1", "0.9"), (K21, "1", "0.9")]))
        self.assertTrue(done["entry_id"])

    def test_a_weight_gain_needs_an_explanation(self):
        self.refused("GAIN_EXPLANATION_REQUIRED", lambda: self.heat([(K21, "4", "4.1")]))
        self.heat([(K21, "4", "4.1")], gain="كوّن زائد من الشوائب")
        self.assertEqual(self.logo(), D("10.100"))
        self.assertEqual(self.detail()["heats"][0]["gain_explanation"], "كوّن زائد من الشوائب")

    def test_refusals_leave_no_trace(self):
        self.refused("HEAT_LINES_REQUIRED", lambda: self.heat([]))
        self.refused("HEAT_INPUT_REQUIRED", lambda: self.heat([(K21, "0", "1")]))
        self.refused("HEAT_OUTPUT_REQUIRED", lambda: self.heat([(K21, "1", "0")]))
        self.refused("WEIGHT_INVALID", lambda: self.heat([(K21, "-1", "1")]))
        self.refused("WEIGHT_INVALID", lambda: self.heat([(K21, "1", "0.9999")]))
        self.refused("KARAT_REQUIRED", lambda: self.heat([("", "1", "1")]))
        self.refused("REASON_REQUIRED", lambda: self.heat([(K21, "1", "0.9")], workshop=" "))                 # the operator is mandatory
        self.refused("HEAT_EXCEEDS_CUSTODY", lambda: self.heat([(K21, "10.001", "10")]))
        self.refused("HEAT_EXCEEDS_CUSTODY", lambda: self.heat([(K24, "1", "0.9")]))                          # no such custody
        self.refused("TRIP_NOT_FOUND", lambda: self.heat([(K21, "1", "0.9")], trip="999"))
        self.refused("REQUEST_INVALID", lambda: self.heat([(K21, "1", "0.9")], request="nope"))
        self.denied(lambda: self.heat([(K21, "1", "0.9")], service=self.owner_like(MANAGER)[0]))
        self.assertEqual(self.db.heats, {})

    def test_only_logo_gold_can_be_heated(self):
        melt_trip = self.issue("", [(K21, "5", "MELT")])["trip_id"]
        error = self.refused("HEAT_EXCEEDS_CUSTODY", lambda: self.heat([(K21, "1", "0.9")], trip=melt_trip))
        self.assertIn("لا يُحمَّى", str(error))

    def test_a_closed_trip_refuses_until_it_is_reopened(self):
        self.ret(self.trip, "", [(K21, "10", "LOGO"), (K18, "5", "LOGO")])
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.heat([(K21, "1", "0.9")]))

    def test_the_same_key_replays_the_original_and_a_different_payload_is_refused(self):
        request = key(2900)
        first = self.heat([(K21, "4", "3.9")], request=request)
        after = self.state()
        again = self.heat([(K21, "4", "3.9")], request=request)
        self.assertEqual((again["duplicate"], again["entry_id"]), (True, first["entry_id"]))
        self.assertEqual(self.state(), after)
        self.refused("REQUEST_CONFLICT", lambda: self.heat([(K21, "4", "3.8")], request=request))
        self.assertEqual(len(self.db.heat_lines), 1)

    def test_a_failure_at_any_posting_step_persists_nothing(self):
        before = self.state()
        for step in ("entry", "heat", "heat_line", "audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.heat([(K21, "4", "3.9"), (K18, "1", "0.9")])
            self.db.fail_at = None
            self.assertEqual(self.state(), before, f"a failure at {step} left a trace")
        self.heat([(K21, "4", "3.9")])
        self.assertEqual(len(self.db.heat_lines), 1)

    def test_a_twin_that_committed_first_is_decided_from_a_fresh_read(self):
        request = key(2950)
        first = self.heat([(K21, "4", "3.9")], request=request)
        original = IntegrityError(msg="Duplicate entry for key 'uq_mte_request'", errno=1062)
        replay = self.service._replay(self.service._actor(None), request, self.db.entries[int(first["entry_id"])]["request_hash"], original)
        self.assertEqual(replay["entry_id"], first["entry_id"])


class HeatMeltIndependenceTests(HeatCase):
    def test_heating_never_enters_a_melt_analysis_or_melt_custody(self):
        melt_trip = self.issue("", [(K21, "10", "MELT"), (K21, "4", "LOGO")])["trip_id"]
        self.heat([(K21, "4", "3.9")], trip=melt_trip)
        melted = self.service.melt(melt_trip, [{"karat_id": K21, "weight": "10"}], TODAY, "ورشة", {"weight": "9.5", "fineness": "930"}, [], "", "", "", key(3100))
        self.assertEqual(melted["analysis"]["input_weight"], D("10.000"))                      # the heated Logo grams are not an input
        detail = self.service.trip_detail(melt_trip)
        self.assertEqual({(g["designation"], g["outstanding"]) for g in detail["custody"]["gold"] if g["karat_value"] == 21},
                         {("MELT", D("0.000")), ("LOGO", D("3.900"))})
        self.assertEqual(len(detail["melts"]), 1)
        self.assertEqual(len(detail["heats"]), 1)


class HeatReversalTests(HeatCase):
    def reverse(self, code_or_result, reason="تصحيح"):
        entry = code_or_result["entry_id"] if isinstance(code_or_result, dict) else code_or_result
        return self.service.reverse(entry, reason)

    def test_a_heat_cannot_be_reversed_while_a_later_logo_return_exists_and_no_phantom_gram_appears(self):
        trip = self.issue("", [(K21, "10", "LOGO")])["trip_id"]                                # issue 10
        heat = self.heat([(K21, "10", "9.8")], trip=trip)                                       # heat to 9.8
        returned = self.ret(trip, "", [(K21, "9.8", "LOGO")])                                   # return 9.8
        before = self.state()
        self.refused("DEPENDENT_LOGO_RETURN_EXISTS", lambda: self.reverse(heat), error=CorrectionError)
        self.assertEqual(self.state(), before)                                                  # nothing moved: no phantom 0.2 g
        self.assertEqual(self.logo(trip=trip), D("0.000"))
        self.reverse(returned)                                                                  # newest first
        self.reverse(heat)
        self.assertEqual(self.logo(trip=trip), D("10.000"))

    def test_a_heat_cannot_be_reversed_while_a_later_heat_of_the_same_karat_exists(self):
        first, second = self.heat([(K21, "4", "3.9")]), self.heat([(K21, "3.9", "3.85")])
        self.refused("DEPENDENT_HEAT_EXISTS", lambda: self.reverse(first), error=CorrectionError)
        self.reverse(second)
        self.reverse(first)
        self.assertEqual(self.logo(), D("10.000"))
        self.assertEqual(self.detail()["heat_totals"], [])                                      # nothing effective remains

    def test_a_later_heat_of_another_karat_does_not_block(self):
        first = self.heat([(K21, "4", "3.9")])
        self.heat([(K18, "1", "0.9")])
        self.reverse(first)
        self.assertEqual(self.logo(), D("10.000"))
        self.assertEqual(self.logo(karat=K18), D("4.900"))

    def test_an_issue_cannot_be_reversed_while_a_later_heat_of_its_logo_karat_exists(self):
        trip = self.issue("", [(K21, "10", "LOGO")])["trip_id"]
        issue_entry = next(e["id"] for e in self.db.entries.values() if e["trip_id"] == int(trip) and e["entry_kind"] == "ISSUE")
        heat = self.heat([(K21, "10", "9.9")], trip=trip)
        before = self.state()
        error = self.refused_any(lambda: self.reverse(str(issue_entry)))
        self.assertIn(error.code, ("DEPENDENT_HEAT_EXISTS", "DEPENDENT_RETURN_EXISTS"))
        self.assertEqual(self.state(), before)
        self.reverse(heat)
        self.reverse(str(issue_entry))

    def refused_any(self, call):
        with self.assertRaises(CorrectionError) as context:
            call()
        return context.exception

    def test_a_reversed_heat_stays_readable_with_its_measurements_and_leaves_the_totals(self):
        heat = self.heat([(K21, "4", "3.9")], reference="ح-1")
        self.reverse(heat, "وزن خاطئ")
        row = self.detail()["heats"][0]
        self.assertTrue(row["reversed"])
        self.assertEqual((row["lines"][0]["input"], row["lines"][0]["output"], row["lines"][0]["difference"]), (D("4.000"), D("3.900"), D("-0.100")))
        self.assertEqual((row["reversal_reason"], row["reversal_code"].startswith("RV")), ("وزن خاطئ", True))
        self.assertEqual(self.detail()["heat_totals"], [])                                      # the cumulative loss counts effective heats only
        self.assertEqual(len(self.db.heat_lines), 1)                                            # the measurement row is never deleted
        self.assertEqual(self.logo(), D("10.000"))
        self.refused("ALREADY_REVERSED", lambda: self.reverse(heat), error=CorrectionError)

    def test_a_heat_reversal_needs_the_owner_rights_and_a_reason_and_is_atomic(self):
        heat = self.heat([(K21, "4", "3.9")])
        self.denied(lambda: self.owner_like(MANAGER)[0].reverse(heat["entry_id"], "x"))
        self.denied(lambda: self.owner_like(OWNER_NO_CORRECT)[0].reverse(heat["entry_id"], "x"))
        before = self.state()
        for step in ("correction", "audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.reverse(heat)
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)


if __name__ == "__main__":
    unittest.main()
