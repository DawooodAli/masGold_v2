"""Market-trip melting (039): gold designation (للسبك / لوجو), mixed-karat melting from custody, assayed lots, residues, the permanent
processing analysis and dependency-safe reversal.

Real MarketTripService and the real CorrectionService over the in-memory transactional database of tests/market_trip_fakes.py: a
failed transaction restores every table, the 039 CHECK / unique constraints are mirrored as asserts. These are NOT real-MySQL
tests: the SQL, the constraints and the locking were exercised separately on the masgold_rehearsal sandbox (see the task report)."""
import copy
from decimal import Decimal as D
import unittest

from mysql.connector import IntegrityError

from services.correction_service import CorrectionError
from services.market_trip_service import (MarketTripService, TripError, analyse, custody_view, parse_designation, parse_fineness)
from tests.market_trip_fakes import MANAGER, OWNER_NO_CORRECT, TripRepo, session_for
from tests.test_market_trips import K18, K21, K24, TODAY, TripCase, gold, key

K22 = "4"


def melt_inputs(*rows):
    return [{"karat_id": k, "weight": w} for k, w in rows]


def raw(*rows):
    """Gold lines exactly as a caller supplies them (designation may be missing / invalid)."""
    return [{"karat_id": k, "weight": w, **({"designation": d} if d is not None else {})} for k, w, d in rows]


class MeltCase(TripCase):
    """OPEN trip 1 holding 21K 40 g (MELT), 21K 20 g (LOGO), 18K 10 g (MELT) and, with 22K active, 22K 15 g (MELT)."""

    def setUp(self):
        super().setUp()
        self.db.karats[4]["is_active"] = 1                                              # 22K master row (038)
        self.db.scrap.append({"id": 90, "karat_id": 4, "weight_delta": D("30.000"), "reference_type": "OPENING", "reference_id": "0",
                              "type_code": "OPENING"})
        self.db.sequences["scrap"] = 90
        self.trip = self.issue("", [(K21, "40", "MELT"), (K21, "20", "LOGO"), (K18, "10", "MELT"), (K22, "15", "MELT")])["trip_id"]

    def melt(self, inputs=None, main=("18.000", "900"), residues=(), workshop="ورشة النور", assay="A-1", gain="", notes="",
             when=TODAY, trip=None, request=None, service=None):
        self.n += 1
        return (service or self.service).melt(
            trip or self.trip, inputs if inputs is not None else melt_inputs((K21, "10"), (K22, "5"), (K18, "4")), when, workshop,
            {"weight": main[0], "fineness": main[1]}, [dict(weight=w, fineness=f, label=l) for w, f, l in residues], assay, gain,
            notes, request or key(self.n))

    def custody_gold(self, trip=None):
        return {(g["karat_value"], g["designation"]): g for g in self.service.trip_detail(trip or self.trip)["custody"]["gold"]}

    def detail(self, trip=None):
        return self.service.trip_detail(trip or self.trip)


class DesignationTests(MeltCase):
    def test_every_issued_gold_line_needs_melt_or_logo(self):
        for bad in ("", None, "UNCLASSIFIED", "غير مصنف", "SALE"):
            with self.subTest(designation=bad):
                self.refused("DESIGNATION_REQUIRED", lambda b=bad: self.service.issue("", "", raw((K21, "1", b)), TODAY, "", key(70)))
        for good, expected in (("للسبك", "MELT"), ("لوجو", "LOGO"), ("MELT", "MELT"), ("LOGO", "LOGO")):
            self.assertEqual(parse_designation(good, "ISSUE"), expected)

    def test_a_missing_designation_is_refused_without_a_trace(self):
        before = self.state()
        self.refused("DESIGNATION_REQUIRED", lambda: self.service.issue(self.trip, "", raw((K21, "1", None)), TODAY, "", key(71)))
        self.assertEqual(self.state(), before)

    def test_custody_is_tracked_per_karat_and_designation_and_both_may_share_a_karat(self):
        rows = self.custody_gold()
        self.assertEqual((rows[(21, "MELT")]["outstanding"], rows[(21, "LOGO")]["outstanding"]), (D("40.000"), D("20.000")))
        self.assertEqual((rows[(18, "MELT")]["outstanding"], rows[(22, "MELT")]["outstanding"]), (D("10.000"), D("15.000")))
        self.assertNotIn((18, "LOGO"), rows)

    def test_two_designations_of_one_karat_post_two_scrap_movements_one_per_leg(self):
        entry = next(e for e in self.db.entries.values() if e["entry_no"] == 1)
        legs = [l for l in self.db.gold_legs if l["entry_id"] == entry["id"] and l["karat_id"] == 1]
        self.assertEqual(sorted(l["designation"] for l in legs), ["LOGO", "MELT"])
        self.assertEqual(len({l["scrap_movement_id"] for l in legs}), 2)                  # one-to-one movement links
        outs = {m["id"]: m for m in self.db.scrap if m["type_code"] == "TRIP_CUSTODY_OUT" and m["karat_id"] == 1}
        self.assertEqual(sorted(outs[l["scrap_movement_id"]]["weight_delta"] for l in legs), [D("-40.000"), D("-20.000")])

    def test_the_combined_issue_of_both_designations_is_checked_against_available_scrap(self):
        # 21K: 100 physical - 5 held = 95 available; 60 were already issued above -> 35 left (not 95)
        self.assertEqual(self.db.available(1), D("35.000"))
        self.refused("SCRAP_SHORTAGE", lambda: self.issue("", [(K21, "20", "MELT"), (K21, "15.001", "LOGO")], trip=self.trip))
        self.issue("", [(K21, "20", "MELT"), (K21, "15", "LOGO")], trip=self.trip)
        self.assertEqual(self.db.available(1), D("0.000"))
        self.refused("SCRAP_SHORTAGE", lambda: self.issue("", [(K21, "0.001", "LOGO")], trip=self.trip))
        self.assertEqual(self.db.holds[1], D("5.000"))                                    # the manager-review hold is untouched

    def test_shop_scrap_stays_pooled_by_karat(self):
        shop = {s["karat_value"]: s for s in self.service.context()["shop_scrap"]}
        self.assertEqual((shop[21]["physical"], shop[21]["available"]), (D("40.000"), D("35.000")))      # no designation in the shop

    def test_a_return_preserves_the_designation_and_never_exceeds_it(self):
        self.refused("RETURN_EXCEEDS_CUSTODY", lambda: self.ret(self.trip, "", [(K21, "20.001", "LOGO")]))
        self.refused("RETURN_EXCEEDS_CUSTODY", lambda: self.ret(self.trip, "", [(K18, "0.001", "LOGO")]))       # no 18K LOGO at all
        self.ret(self.trip, "", [(K21, "20", "LOGO")])
        rows = self.custody_gold()
        self.assertEqual((rows[(21, "LOGO")]["outstanding"], rows[(21, "MELT")]["outstanding"]), (D("0.000"), D("40.000")))
        self.refused("RETURN_EXCEEDS_CUSTODY", lambda: self.ret(self.trip, "", [(K21, "0.001", "LOGO")]))
        self.assertEqual(self.db.physical(1), D("60.000"))                                # 100 - 60 issued + 20 returned

    def test_a_return_needs_the_lines_designation(self):
        self.refused("DESIGNATION_REQUIRED", lambda: self.service.return_custody(self.trip, "", raw((K21, "1", None)), TODAY, "", key(72)))

    def test_legacy_gold_is_unclassified_returnable_but_never_meltable_and_never_rewritten(self):
        legacy = self.issue("", [(K18, "5", "MELT")])["trip_id"]
        for leg in self.db.gold_legs:                                                     # a line that predates the designation column
            if leg["entry_id"] == max(self.db.entries):
                leg["designation"] = "UNCLASSIFIED"
        rows = self.custody_gold(legacy)
        self.assertEqual(list(rows), [(18, "UNCLASSIFIED")])
        message = self.refused("MELT_EXCEEDS_CUSTODY", lambda: self.melt(melt_inputs((K18, "1")), main=("0.5", "900"), trip=legacy,
                                                                      gain="لا"), ).args[0]
        self.assertIn("المتاح للسبك", message)
        self.assertIn("غير مصنف", message)                                                # explained: legacy gold is return-only
        self.ret(legacy, "", [(K18, "5", "UNCLASSIFIED")])
        self.assertEqual(self.custody_gold(legacy)[(18, "UNCLASSIFIED")]["outstanding"], D("0.000"))
        self.assertEqual([l["designation"] for l in self.db.gold_legs if l["entry_id"] == max(self.db.entries) - 1], ["UNCLASSIFIED"])


class FinenessTests(unittest.TestCase):
    def test_accepted_forms_and_exact_precision(self):
        for text, expected in (("870", "870.0"), ("870.5", "870.5"), ("٨٧٠٫٥", "870.5"), ("1000", "1000.0"), ("0.1", "0.1"), (" 999.9 ", "999.9")):
            self.assertEqual(parse_fineness(text), D(expected), text)
        self.assertEqual(parse_fineness("875").as_tuple().exponent, -1)

    def test_refused_forms_are_specific(self):
        for text, code in (("", "FINENESS_REQUIRED"), ("0", "FINENESS_RANGE"), ("-5", "FINENESS_RANGE"), ("1000.1", "FINENESS_RANGE"),
                           ("1001", "FINENESS_RANGE"), ("870.55", "FINENESS_PRECISION"), ("870.05", "FINENESS_PRECISION"),
                           ("abc", "FINENESS_INVALID"), ("NaN", "FINENESS_RANGE"), ("8,70", "FINENESS_INVALID")):
            with self.subTest(text=text), self.assertRaises(TripError) as refused:
                parse_fineness(text)
            self.assertEqual(refused.exception.code, code)

    def test_an_unknown_residue_fineness_is_none_and_never_inferred(self):
        self.assertIsNone(parse_fineness("", required=False))
        self.assertIsNone(parse_fineness("  ", required=False))
        with self.assertRaises(TripError):
            parse_fineness("0", required=False)                                          # unknown is blank, not zero


class MeltTests(MeltCase):
    def test_a_mixed_karat_melt_including_22k_consumes_only_melt_custody_and_creates_assayed_lots(self):
        scrap_before, cash_before = copy.deepcopy(self.db.scrap), copy.deepcopy(self.db.cash_moves)
        result = self.melt(residues=[("0.400", "", "خبث"), ("0.150", "250.5", "تراب")], main=("18.000", "900"))
        self.assertEqual((result["code"], result["kind"]), ("MT000001-2", "MELT"))
        rows = self.custody_gold()
        self.assertEqual((rows[(21, "MELT")]["melted"], rows[(22, "MELT")]["melted"], rows[(18, "MELT")]["melted"]),
                         (D("10.000"), D("5.000"), D("4.000")))
        self.assertEqual((rows[(21, "MELT")]["outstanding"], rows[(22, "MELT")]["outstanding"], rows[(18, "MELT")]["outstanding"]),
                         (D("30.000"), D("10.000"), D("6.000")))
        self.assertEqual(rows[(21, "LOGO")]["outstanding"], D("20.000"))                  # Logo gold is never consumed
        self.assertEqual((self.db.scrap, self.db.cash_moves), (scrap_before, cash_before))   # no shop Scrap, cash or supplier movement
        melt = self.detail()["melts"][0]
        self.assertEqual([(i["karat_value"], i["weight"]) for i in melt["inputs"]], [(22, D("5.000")), (21, D("10.000")), (18, D("4.000"))])
        self.assertEqual([(l["kind"], l["weight"], l["fineness"]) for l in melt["lots"]],
                         [("MAIN", D("18.000"), D("900.0")), ("RESIDUE", D("0.400"), None), ("RESIDUE", D("0.150"), D("250.5"))])
        self.assertEqual([l["label"] for l in melt["lots"]], ["", "خبث", "تراب"])
        self.assertEqual((melt["workshop"], melt["assay_reference"]), ("ورشة النور", "A-1"))
        self.assertIn("MARKET_TRIP_MELT", [a["event_type"] for a in self.db.audit_rows])

    def test_the_analysis_is_exact_and_marks_an_unknown_residue_as_incomplete(self):
        # inputs: 10 g 21K + 5 g 22K + 4 g 18K -> (210 + 110 + 72) / 24 = 16.333333 nominal pure; Eq21 = * 24 / 21
        self.melt(residues=[("0.400", "", "خبث")], main=("18.000", "900"))
        a = self.detail()["melts"][0]["analysis"]
        self.assertEqual((a["input_weight"], a["nominal_pure"], a["output_weight"]), (D("19.000"), D("16.333333"), D("18.400")))
        self.assertEqual(a["known_output_pure"], D("16.200000"))                          # only the known 18 g * 900 / 1000
        self.assertEqual((a["weight_difference"], a["pure_complete"], a["pure_difference"], a["eq21_difference"]),
                         (D("-0.600"), False, None, None))
        self.assertEqual((a["nominal_eq21"], a["known_output_eq21"]), (D("18.666667"), D("18.514286")))

    def test_the_analysis_with_every_assay_known_has_signed_differences(self):
        self.melt(residues=[("0.400", "300", "خبث")], main=("17.500", "925"), gain="")
        a = self.detail()["melts"][0]["analysis"]
        # known pure: 17.5 * .925 + .4 * .3 = 16.1875 + 0.12 = 16.3075 ; nominal 16.333333 -> -0.025833
        self.assertEqual((a["known_output_pure"], a["pure_complete"], a["pure_difference"]), (D("16.307500"), True, D("-0.025833")))
        self.assertEqual((a["weight_difference"], a["eq21_difference"]), (D("-1.100"), D("-0.029524")))
        self.assertEqual(self.service.trip_detail(self.trip)["custody"]["lots"][0]["outstanding"], D("17.500"))

    def test_raw_facts_are_stored_apart_from_the_analysis(self):
        self.melt(melt_inputs((K21, "0.001"), (K22, "0.003")), main=("0.004", "999.9"), residues=[("0.001", "0.1", "")], gain="وزن الخبث")
        melt = self.detail()["melts"][0]
        self.assertEqual([(i["weight"], i["pure"]) for i in melt["inputs"]], [(D("0.003"), D("0.002750")), (D("0.001"), D("0.000875"))])
        self.assertEqual((melt["lots"][0]["weight"], melt["lots"][0]["fineness"], melt["lots"][0]["pure"]),
                         (D("0.004"), D("999.9"), D("0.004000")))
        self.assertEqual(melt["analysis"]["input_weight"], D("0.004"))

    def test_a_weight_gain_needs_an_explanation(self):
        self.refused("GAIN_EXPLANATION_REQUIRED", lambda: self.melt(melt_inputs((K21, "1")), main=("1.001", "800")))
        self.refused("GAIN_EXPLANATION_REQUIRED", lambda: self.melt(melt_inputs((K21, "1")), main=("1.000", "800"),
                                                                  residues=[("0.001", "", "")], gain="   "))
        self.melt(melt_inputs((K21, "1")), main=("1.000", "800"))                         # equal weight needs none
        self.melt(melt_inputs((K21, "1")), main=("1.001", "800"), gain="شوائب أخف من المتوقع")
        self.assertEqual(self.detail()["melts"][1]["gain_explanation"], "شوائب أخف من المتوقع")
        self.assertEqual(self.detail()["melts"][1]["analysis"]["weight_difference"], D("0.001"))

    def test_the_inputs_are_limited_by_the_melt_designated_custody_of_each_karat(self):
        before = self.state()
        self.refused("MELT_EXCEEDS_CUSTODY", lambda: self.melt(melt_inputs((K21, "40.001")), main=("1", "900"), gain="x"))
        message = self.refused("MELT_EXCEEDS_CUSTODY", lambda: self.melt(melt_inputs((K21, "41")), main=("1", "900"), gain="x")).args[0]
        self.assertIn("عيار 21 هو 40.000", message)
        self.assertIn("لوجو 20.000", message)                                             # the Logo grams are named, not usable
        self.refused("MELT_EXCEEDS_CUSTODY", lambda: self.melt(melt_inputs((K24, "0.001")), main=("1", "900"), gain="x"))
        self.assertEqual(self.state(), before)
        self.melt(melt_inputs((K21, "40")), main=("30", "900"))                           # exactly the melt custody is fine
        self.refused("MELT_EXCEEDS_CUSTODY", lambda: self.melt(melt_inputs((K21, "0.001")), main=("0.001", "900")))

    def test_logo_gold_cannot_be_melted_even_when_it_is_the_only_gold_of_the_karat(self):
        only_logo = self.issue("", [(K18, "3", "LOGO")])["trip_id"]
        self.refused("MELT_EXCEEDS_CUSTODY", lambda: self.melt(melt_inputs((K18, "1")), main=("1", "900"), trip=only_logo, gain="x"))

    def test_field_validation_is_specific_and_nothing_is_written(self):
        before = self.state()
        cases = [
            ("MELT_INPUT_REQUIRED", dict(inputs=[])), ("MELT_INPUT_REQUIRED", dict(inputs=melt_inputs((K21, "0")))),
            ("KARAT_REQUIRED", dict(inputs=[{"karat_id": "", "weight": "1"}])),
            ("WEIGHT_INVALID", dict(inputs=melt_inputs((K21, "-1")))), ("WEIGHT_INVALID", dict(inputs=melt_inputs((K21, "1.0001")))),
            ("OUTPUT_WEIGHT_REQUIRED", dict(main=("", "900"))), ("OUTPUT_WEIGHT_REQUIRED", dict(main=("0", "900"))),
            ("WEIGHT_INVALID", dict(main=("-2", "900"))), ("FINENESS_REQUIRED", dict(main=("5", ""))),
            ("FINENESS_RANGE", dict(main=("5", "0"))), ("FINENESS_RANGE", dict(main=("5", "1000.1"))),
            ("FINENESS_PRECISION", dict(main=("5", "870.55"))), ("REASON_REQUIRED", dict(workshop="  ")),
            ("DATE_REQUIRED", dict(when="")), ("DATE_INVALID", dict(when="2026-13-40")),
            ("RESIDUE_WEIGHT_REQUIRED", dict(residues=[("0", "", "x")])), ("RESIDUE_WEIGHT_REQUIRED", dict(residues=[("", "800", "x")])),
            ("FINENESS_RANGE", dict(residues=[("1", "1001", "")])), ("FINENESS_PRECISION", dict(residues=[("1", "5.55", "")])),
            ("TEXT_LONG", dict(workshop="و" * 121)), ("TEXT_LONG", dict(assay="م" * 121)),
            ("TRIP_NOT_FOUND", dict(trip="999")), ("REQUEST_INVALID", dict(request="not-a-key")),
        ]
        for code, kwargs in cases:
            with self.subTest(code=code, kwargs=list(kwargs)):
                self.refused(code, lambda kw=kwargs: self.melt(**{"inputs": melt_inputs((K21, "1")), "main": ("1", "900"), **kw}))
        self.assertEqual(self.state(), before)

    def test_blank_residue_rows_are_ignored_and_at_most_ten_residues_are_accepted(self):
        self.melt(melt_inputs((K21, "10")), main=("9", "900"), residues=[("", "", ""), ("0.5", "", "")])
        self.assertEqual(len(self.detail()["melts"][0]["lots"]), 2)
        many = [("0.001", "", "")] * 11
        self.refused("TOO_MANY_RESIDUES", lambda: self.melt(melt_inputs((K21, "20")), main=("9", "900"), residues=many))

    def test_a_melt_needs_an_open_trip_and_the_owner_right(self):
        self.ret(self.trip, "", [(K21, "40", "MELT"), (K21, "20", "LOGO"), (K18, "10", "MELT"), (K22, "15", "MELT")])
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.melt(melt_inputs((K21, "1")), main=("1", "900")))
        with self.assertRaises(PermissionError):
            self.melt(melt_inputs((K21, "1")), main=("1", "900"), service=self.owner_like(MANAGER)[0])

    def test_melting_an_unknown_trip_or_no_trip_is_refused(self):
        self.refused("TRIP_NOT_FOUND", lambda: self.melt(trip="0"))
        self.refused("TRIP_NOT_FOUND", lambda: self.service.melt("", melt_inputs((K21, "1")), TODAY, "ورشة", {"weight": "1", "fineness": "900"}, [], "", "", "", key(80)))

    def test_the_melt_leaves_shop_cash_and_suppliers_untouched_and_creates_no_correction(self):
        self.melt()
        self.assertEqual((self.db.cash(), self.db.corrections), (D("50000.00"), []))


class AtomicityAndIdempotencyTests(MeltCase):
    def test_a_failure_at_any_step_leaves_nothing_behind(self):
        before = self.state()
        for step in ("entry", "melt", "melt_input", "lot", "lot_movement", "audit"):
            with self.subTest(step=step):
                self.db.fail_at = step
                with self.assertRaises(RuntimeError):
                    self.melt()
                self.db.fail_at = None
                self.assertEqual(self.state(), before, f"a failure at {step} left a trace")
        self.melt()                                                                       # and the same request succeeds afterwards
        self.assertEqual(len(self.db.melts), 1)

    def test_the_same_request_replays_without_a_second_melt(self):
        request = key(900)
        first = self.melt(request=request)
        again = self.melt(request=request)
        self.assertEqual((first["duplicate"], again["duplicate"], again["code"]), (False, True, first["code"]))
        self.assertEqual((len(self.db.melts), len(self.db.lots), len(self.db.melt_inputs)), (1, 1, 3))
        self.assertEqual(self.custody_gold()[(21, "MELT")]["outstanding"], D("30.000"))        # consumed once

    def test_the_same_key_with_a_different_payload_is_a_conflict(self):
        request = key(901)
        self.melt(request=request)
        self.refused("REQUEST_CONFLICT", lambda: self.melt(request=request, main=("18.000", "899.9")))
        self.refused("REQUEST_CONFLICT", lambda: self.melt(request=request, workshop="ورشة أخرى"))
        self.assertEqual(len(self.db.melts), 1)

    def test_a_request_key_collision_inside_the_transaction_is_resolved_from_a_fresh_read(self):
        request = key(902)
        original = TripRepo.insert_entry

        def twin_commits_first(repo, **kw):
            TripRepo.insert_entry = original
            raise IntegrityError("1062 (23000): Duplicate entry 'x' for key 'market_trip_entries.uq_mte_request'", errno=1062)
        TripRepo.insert_entry = twin_commits_first
        try:
            with self.assertRaises(IntegrityError):
                self.melt(request=request)                                                # no twin exists -> the real defect surfaces
        finally:
            TripRepo.insert_entry = original
        self.assertEqual(len(self.db.melts), 0)


class ClosureTests(MeltCase):
    def finish_melt(self):
        result = self.melt(melt_inputs((K21, "40"), (K18, "10"), (K22, "15")), main=("60", "900"), gain="")
        self.ret(self.trip, "", [(K21, "20", "LOGO")])
        return result

    def test_assayed_lots_prevent_closure_and_the_reason_says_why(self):
        self.finish_melt()
        custody = self.detail()["custody"]
        self.assertTrue(all(g["outstanding"] == 0 for g in custody["gold"]))
        self.assertEqual((custody["settled"], len(custody["lots"])), (False, 1))
        message = self.refused("OUTSTANDING_CUSTODY", lambda: self.service.close(self.trip)).args[0]
        self.assertIn("شحنة سبك 1", message)
        self.assertIn("60.000 جرام", message)
        self.assertIn("رجوع ناتج السبك", message)                                         # the way out is named

    def test_master_karat_custody_and_assayed_lots_are_reported_separately(self):
        self.melt()
        custody = self.detail()["custody"]
        self.assertEqual(sorted({g["designation"] for g in custody["gold"]}), ["LOGO", "MELT"])
        self.assertEqual([l["kind"] for l in custody["lots"]], ["MAIN"])

    def test_unreturned_logo_gold_also_prevents_closure(self):
        self.melt(melt_inputs((K21, "40"), (K18, "10"), (K22, "15")), main=("60", "900"))
        message = self.refused("OUTSTANDING_CUSTODY", lambda: self.service.close(self.trip)).args[0]
        self.assertIn("عيار 21 (لوجو): 20.000", message)
        self.assertEqual(custody_view((D(0), D(0)), [], [])["settled"], True)

    def test_a_trip_closes_after_the_only_lot_melt_is_reversed_and_everything_returned(self):
        melt = self.finish_melt()
        self.service.reverse(melt["entry_id"], "خطأ")
        self.ret(self.trip, "", [(K21, "40", "MELT"), (K18, "10", "MELT"), (K22, "15", "MELT")])
        self.assertEqual(self.service.close(self.trip)["status"], "CLOSED")


class ReversalTests(MeltCase):
    def setUp(self):
        super().setUp()
        self.melt_result = self.melt(residues=[("0.400", "", "خبث")])
        self.melt_entry = int(self.melt_result["entry_id"])

    def reverse(self, entry=None, reason="تصحيح", service=None):
        return (service or self.service).reverse(entry or self.melt_entry, reason)

    def test_reversing_a_melt_restores_input_custody_and_removes_its_lots_but_keeps_the_analysis(self):
        analysis_before = copy.deepcopy(self.detail()["melts"][0]["analysis"])
        scrap_before, cash_before = copy.deepcopy(self.db.scrap), copy.deepcopy(self.db.cash_moves)
        result = self.reverse()
        self.assertTrue(result["code"].startswith("RV"))
        rows = self.custody_gold()
        self.assertEqual((rows[(21, "MELT")]["outstanding"], rows[(22, "MELT")]["outstanding"], rows[(18, "MELT")]["outstanding"]),
                         (D("40.000"), D("15.000"), D("10.000")))
        self.assertEqual(self.detail()["custody"]["lots"], [])
        self.assertEqual((self.db.scrap, self.db.cash_moves), (scrap_before, cash_before))   # a melt moved no Scrap or cash: neither does its reversal
        melt = self.detail()["melts"][0]
        self.assertTrue(melt["reversed"])
        self.assertEqual(melt["analysis"], analysis_before)                               # the processing difference is permanent
        self.assertEqual(self.db.melts[self.melt_entry]["workshop"], "ورشة النور")
        self.assertTrue(self.detail()["entries"][1]["reversed"])

    def test_a_blank_reason_and_the_missing_rights_are_refused(self):
        before = self.state()
        for reason in ("", "   ", None):
            with self.assertRaises(CorrectionError) as refused:
                self.reverse(reason=reason)
            self.assertEqual(refused.exception.code, "REASON_REQUIRED")
        with self.assertRaises(PermissionError):
            self.reverse(service=self.owner_like(MANAGER)[0])
        with self.assertRaises(PermissionError):
            self.reverse(service=self.owner_like(OWNER_NO_CORRECT)[0])
        self.assertEqual(self.state(), before)

    def test_double_reversal_is_refused(self):
        self.reverse()
        with self.assertRaises(CorrectionError) as refused:
            self.reverse()
        self.assertEqual(refused.exception.code, "ALREADY_REVERSED")
        self.assertEqual(len(self.db.corrections), 1)

    def test_a_closed_trip_must_be_reopened_before_a_melt_can_be_reversed(self):
        # M1 itself cannot close a trip that still holds an unreversed lot, so the closed state is forced to prove the guard.
        self.db.trips[int(self.trip)]["status"] = "CLOSED"
        with self.assertRaises(CorrectionError) as refused:
            self.reverse()
        self.assertEqual(refused.exception.code, "TRIP_CLOSED")
        self.assertEqual(len(self.db.corrections), 0)
        self.db.trips[int(self.trip)]["status"] = "OPEN"                                  # the audited reopening
        self.reverse()
        self.assertEqual(len(self.db.corrections), 1)

    def test_the_issue_cannot_be_reversed_while_a_melt_depends_on_it_and_can_afterwards(self):
        issue_entry = min(self.db.entries)
        with self.assertRaises(CorrectionError) as refused:
            self.reverse(issue_entry)
        self.assertEqual(refused.exception.code, "DEPENDENT_RETURN_EXISTS")
        self.assertIn("سبكًا", refused.exception.args[0])
        self.reverse()                                                                    # undo the melt first
        self.ret(self.trip, "", [(K21, "20", "LOGO")])                                    # LOGO returned: the issue still has returns
        with self.assertRaises(CorrectionError):
            self.reverse(issue_entry)                                                     # the return depends on the issue
        self.service.reverse(max(self.db.entries), "رجوع خاطئ")
        self.reverse(issue_entry)
        self.assertEqual(self.detail()["custody"]["gold"], [])

    def test_a_return_of_melt_gold_cannot_leave_negative_custody_after_a_melt(self):
        self.refused("RETURN_EXCEEDS_CUSTODY", lambda: self.ret(self.trip, "", [(K21, "31", "MELT")]))     # 40 - 10 melted = 30 left
        self.ret(self.trip, "", [(K21, "30", "MELT")])
        self.assertEqual(self.custody_gold()[(21, "MELT")]["outstanding"], D("0.000"))
        self.reverse()                                                                    # undoing the melt gives its 10 g back
        self.assertEqual(self.custody_gold()[(21, "MELT")]["outstanding"], D("10.000"))

    def test_a_melt_whose_lot_was_used_downstream_cannot_be_reversed_but_its_own_creation_is_no_dependency(self):
        lot_id = next(l["id"] for l in self.db.lots.values() if l["entry_id"] == self.melt_entry and l["lot_kind"] == "MAIN")
        self.assertEqual(self.db.lot_moves[0]["movement_type"], "CREATE")                  # born by the melt itself
        self.assertEqual(self.service._repository(self.db).lot_downstream(self.melt_entry), [])
        later = self.service.return_lots(self.trip, [{"lot_id": str(lot_id)}], TODAY, "", key(95))   # an effective lot return
        with self.assertRaises(CorrectionError) as refused:
            self.reverse()
        self.assertEqual(refused.exception.code, "DEPENDENT_LOT_USE_EXISTS")
        self.assertIn("MT000001-3", refused.exception.args[0])
        self.service.reverse(later["entry_id"], "إلغاء الاستخدام")                         # reversing the dependant releases the melt
        self.reverse()
        self.assertTrue(self.detail()["melts"][0]["reversed"])

    def test_a_failure_inside_the_reversal_leaves_everything_unchanged(self):
        before = self.state()
        self.db.fail_at = "correction"
        with self.assertRaises(RuntimeError):
            self.reverse()
        self.db.fail_at = None
        self.assertEqual(self.state(), before)


class MeltViewModelTests(MeltCase):
    """MarketTripViewModel over the same services (worker run synchronously): what the page shows, never what it decides."""

    def setUp(self):
        super().setUp()
        from app.viewmodels.market_trip_viewmodel import MarketTripViewModel
        self.vm = MarketTripViewModel(self.session, self.service)
        actor = self.session.current_actor()

        def start(operation, call):
            try:
                result, error = call(_expected_actor=actor), ""
            except (ValueError, PermissionError) as exc:
                result, error = None, str(exc)
            getattr(self.vm, f"_on_{operation}")(result, error)
            self.vm.changed.emit()
        self.vm._start = start
        self.signals = {"custody": 0, "entry": []}
        self.vm.custodyChanged.connect(lambda: self.signals.__setitem__("custody", self.signals["custody"] + 1))
        self.vm.entryPosted.connect(self.signals["entry"].append)
        self.vm.selectTrip(self.trip)

    def melt_vm(self, main_weight="17.500", fineness="925", residues=(("0.400", "", "خبث"),), request=None, inputs=None):
        self.vm.meltCustody(self.trip, inputs or [{"karatId": K21, "weight": "10"}, {"karatId": K22, "weight": "5"},
                                                  {"karatId": K18, "weight": "4"}], TODAY, "ورشة النور", main_weight, fineness,
                            [{"weight": w, "fineness": f, "label": l} for w, f, l in residues], "A-1", "", "", request or self.vm.newRequestKey())

    def test_the_detail_shows_designation_per_karat_lots_apart_and_the_incomplete_marker(self):
        detail = self.vm.detail
        self.assertEqual([(g["karat"], g["designationText"]) for g in detail["gold"]],
                         [(22, "للسبك"), (21, "للسبك"), (21, "لوجو"), (18, "للسبك")])
        self.assertEqual({m["karatId"] for m in detail["meltStock"]}, {K21, K22, K18})                # LOGO gold is not offered for melting
        self.melt_vm()
        self.assertEqual((self.signals["entry"], self.signals["custody"]), (["MT000001-2"], 0))        # a melt moves no shop balance
        self.assertEqual(self.vm.message, "تم تسجيل السبك MT000001-2")
        detail = self.vm.detail
        self.assertEqual([l["title"] for l in detail["lots"]], ["الناتج الرئيسي — الحركة 2", "رواسب — الحركة 2 • خبث"])
        self.assertIn("رجوع ناتج السبك", detail["lotNote"])
        melt = detail["melts"][0]
        values = {r["label"]: r["value"] for r in melt["analysis"]}
        self.assertEqual(values["فرق الذهب الصافي"], "غير مكتمل — عيار بعض الرواسب غير معروف، فلا يُعرض فرق نهائي للذهب الصافي.")
        self.assertFalse(melt["complete"])
        self.assertIn("عيار 22: 5.000 جرام", melt["inputsText"])
        self.assertIn("غير معلومة", melt["lots"][1]["text"])
        self.assertIn("925.0", melt["lots"][0]["text"])
        self.assertIn("في الألف", melt["lots"][0]["text"])

    def test_known_assays_show_signed_differences_and_a_loss_is_not_called_a_write_off(self):
        self.melt_vm(residues=(("0.400", "300", "خبث"),))
        values = {r["label"]: r["value"] for r in self.vm.detail["melts"][0]["analysis"]}
        self.assertEqual(values["فرق الذهب الصافي (النواتج − المدخلات)"], "⁦−0.025833⁩ جرام")
        self.assertEqual(values["فرق الوزن (النواتج − المدخلات)"], "⁦−1.100⁩ جرام")
        self.assertTrue(self.vm.detail["melts"][0]["complete"])

    def test_a_refusal_keeps_the_error_and_posts_nothing_and_the_request_key_replays(self):
        before = self.state()
        self.melt_vm(fineness="870.55")
        self.assertIn("خانة عشرية واحدة", self.vm.errorMessage)
        self.assertEqual(self.state(), before)
        request = self.vm.newRequestKey()
        self.melt_vm(request=request)
        self.melt_vm(request=request)
        self.assertIn("مسجلة من قبل", self.vm.message)
        self.assertEqual(len(self.db.melts), 1)

    def test_a_reversed_melt_keeps_every_original_number_and_shows_the_notice_and_the_rv_reference(self):
        self.melt_vm(residues=(("0.400", "300", "خبث"), ("0.150", "", "تراب")))
        entry_id = str(next(iter(self.db.melts)))
        live = copy.deepcopy(self.vm.detail["melts"][0])
        self.assertEqual((live["reversed"], live["notice"], live["state"]), (False, "", ""))
        self.vm.reverseEntry(entry_id, "وزن خاطئ")
        detail = self.vm.detail
        dead = detail["melts"][0]
        for field in ("analysis", "lots", "inputsText", "workshop", "assay", "code", "date", "complete"):
            self.assertEqual(dead[field], live[field], field)                          # every weight, assay and difference is preserved
        self.assertTrue(dead["reversed"])
        self.assertEqual(dead["notice"], "عملية معكوسة — الأرقام للتاريخ فقط ولا تؤثر على الرصيد الحالي")
        self.assertIn("RV", dead["state"])
        self.assertIn("وزن خاطئ", dead["reason"])
        # the effective balances exclude it: inputs are back in custody, the lots are gone
        self.assertEqual(detail["lots"], [])
        stock = {m["karatId"]: m["text"] for m in detail["meltStock"]}
        self.assertIn("40.000", stock[K21])
        self.assertEqual({g["label"]: g["melted"] for g in detail["gold"]}["عيار 21 (للسبك)"], "0.000")

    def test_the_return_dialog_choices_are_the_outstanding_lines_with_their_designation(self):
        lines = {l["key"]: l for l in self.vm.detail["returnLines"]}
        self.assertEqual(sorted(lines), sorted([f"{K18}:MELT", f"{K21}:LOGO", f"{K21}:MELT", f"{K22}:MELT"]))
        self.assertIn("(لوجو)", lines[f"{K21}:LOGO"]["text"])
        self.vm.returnCustody(self.trip, "", [{"karatId": K21, "designation": "LOGO", "weight": "20"}], TODAY, "", self.vm.newRequestKey())
        self.assertNotIn(f"{K21}:LOGO", [l["key"] for l in self.vm.detail["returnLines"]])
        self.assertEqual(self.signals["custody"], 1)


class RepositoryMergeTests(unittest.TestCase):
    """The real repository's merge of the two custody queries (the SQL itself is exercised on the rehearsal sandbox)."""

    def test_melt_consumption_lands_on_the_melt_designation_only_and_rows_are_ordered(self):
        from repositories.market_trip_repository import MarketTripRepository
        repo = MarketTripRepository.__new__(MarketTripRepository)
        answers = iter([[{"karat_id": 1, "karat_value": 21, "designation": "LOGO", "issued": D("20"), "returned": D("0")},
                         {"karat_id": 1, "karat_value": 21, "designation": "MELT", "issued": D("40"), "returned": D("5")},
                         {"karat_id": 3, "karat_value": 24, "designation": "UNCLASSIFIED", "issued": D("2"), "returned": D("0")}],
                        [{"karat_id": 1, "karat_value": 21, "melted": D("10")}],
                        [],                                                               # no Logo heating (042)
                        [],                                                               # no supplier settlements (046)
                        [],                                                               # no intermediary holdings (049)
                        []])                                                              # no custody gold sales (053)
        repo._fetch_all = lambda *args, **kwargs: next(answers)
        rows = repo.custody_gold(1)
        self.assertEqual([(r["karat_value"], r["designation"], r["melted"]) for r in rows],
                         [(24, "UNCLASSIFIED", 0), (21, "MELT", D("10")), (21, "LOGO", 0)])
        view = custody_view((D(0), D(0)), rows)
        self.assertEqual({(g["karat_value"], g["designation"]): g["outstanding"] for g in view["gold"]},
                         {(24, "UNCLASSIFIED"): D("2"), (21, "MELT"): D("25"), (21, "LOGO"): D("20")})


    def test_effective_supplier_settlements_are_merged_onto_their_row_and_consumed_by_the_one_shared_calculation(self):
        """046: the settled grams land on their (karat, designation) row - even when that row has no leg of its own in the sums - and
        custody_view and services.custody_rules agree on what is outstanding."""
        from repositories.market_trip_repository import MarketTripRepository
        from services import custody_rules
        repo = MarketTripRepository.__new__(MarketTripRepository)
        answers = iter([[{"karat_id": 1, "karat_value": 21, "designation": "LOGO", "issued": D("20"), "returned": D("2")}],
                        [],
                        [{"karat_id": 1, "karat_value": 21, "heat_delta": D("-0.500")}],
                        [{"karat_id": 1, "karat_value": 21, "designation": "LOGO", "supplier_settled": D("6.250")},
                         {"karat_id": 3, "karat_value": 24, "designation": "LOGO", "supplier_settled": D("1")}],
                        [],                                                               # no intermediary holdings (049)
                        []])                                                              # no custody gold sales (053)
        repo._fetch_all = lambda *args, **kwargs: next(answers)
        rows = repo.custody_gold(1)
        by_key = {(r["karat_value"], r["designation"]): r for r in rows}
        self.assertEqual(by_key[(21, "LOGO")]["supplier_settled"], D("6.250"))
        self.assertEqual(by_key[(24, "LOGO")]["issued"], 0)                                   # a row with no issue leg is still present (and negative)
        self.assertEqual(custody_rules.gold_outstanding(by_key[(21, "LOGO")]), D("20") - D("2") - D("0.500") - D("6.250"))
        self.assertEqual(custody_rules.gold_outstanding(by_key[(24, "LOGO")]), D("-1"))
        view = custody_view((D(0), D(0)), rows)
        self.assertEqual({(g["karat_value"], g["designation"]): g["outstanding"] for g in view["gold"]},
                         {(21, "LOGO"): D("11.250"), (24, "LOGO"): D("-1")})
        self.assertTrue(custody_rules.any_negative((D(0), D(0)), rows))


class HeldTermTests(unittest.TestCase):
    def test_handed_over_and_taken_back_grams_land_on_their_logo_row_and_the_one_shared_calculation_uses_them(self):
        """049: held_out / held_back merge onto their (karat, LOGO) row and gold_outstanding subtracts the net handed-over grams."""
        from repositories.market_trip_repository import MarketTripRepository
        from services import custody_rules
        repo = MarketTripRepository.__new__(MarketTripRepository)
        answers = iter([[{"karat_id": 1, "karat_value": 21, "designation": "LOGO", "issued": D("20"), "returned": D("2")}], [], [], [],
                        [{"karat_id": 1, "karat_value": 21, "designation": "LOGO", "held_out": D("9"), "held_back": D("4")}], []])
        repo._fetch_all = lambda *args, **kwargs: next(answers)
        row = repo.custody_gold(1)[0]
        self.assertEqual((row["held_out"], row["held_back"]), (D("9"), D("4")))
        self.assertEqual(custody_rules.gold_outstanding(row), D("20") - D("2") - D("9") + D("4"))
        self.assertEqual(custody_view((D(0), D(0)), [row])["gold"][0]["outstanding"], D("13"))
        over = {**row, "held_out": D("30")}
        self.assertTrue(custody_rules.any_negative((D(0), D(0)), [over], designation="LOGO"))


class AnalysisFunctionTests(unittest.TestCase):
    def test_pure_analysis_matches_the_hand_calculation(self):
        a = analyse([(21, D("10.000")), (22, D("5.000"))], (D("14.500"), D("875.5")), [(D("0.400"), D("100"))])
        self.assertEqual(a["nominal_pure"], D("13.333333"))
        self.assertEqual(a["known_output_pure"], D("12.734750"))                           # 14.5 * .8755 + .4 * .1
        self.assertEqual((a["weight_difference"], a["pure_difference"], a["eq21_difference"]), (D("-0.100"), D("-0.598583"), D("-0.684095")))

    def test_the_total_is_quantized_once_not_summed_from_rounded_parts(self):
        a = analyse([(21, D("0.001")), (18, D("0.001")), (22, D("0.001"))], (D("0.003"), D("1000")), [])
        self.assertEqual(a["nominal_pure"], D("0.002542"))                                 # (21 + 18 + 22) * .001 / 24 = .0025416..

    def test_signs_follow_outputs_minus_inputs(self):
        gain = analyse([(24, D("1.000"))], (D("1.200"), D("1000")), [])
        self.assertEqual((gain["weight_difference"], gain["pure_difference"]), (D("0.200"), D("0.200000")))
        loss = analyse([(24, D("1.000"))], (D("0.900"), D("1000")), [])
        self.assertEqual((loss["weight_difference"], loss["pure_difference"]), (D("-0.100"), D("-0.100000")))


if __name__ == "__main__":
    unittest.main()
