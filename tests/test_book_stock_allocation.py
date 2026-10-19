"""packet24 (F1 / F2): the posting basis of a stock reissue follows the allocation policy (plan v3, addendum 2) and the reconciliation re-derives every BK_ISS_STK
line from the item's origin and its full stored movement history (two consistent copies of a wrong figure no longer pass). Real book_rules / MarketTripBookService /
CorrectionService / book_reconciliation / book_settlement over the in-memory database of tests/market_trip_fakes.py. NOT real-MySQL tests."""
from decimal import Decimal as D
from fractions import Fraction
import unittest
from unittest.mock import patch

from services import book_rules as br
from services import book_reconciliation as rec
from services import book_settlement as bs
from services.market_trip_service import TripError
from tests.market_trip_fakes import TripRepo
from tests.test_book_settlement_service import ALL
from tests.test_market_trip_book import K21, assay, karat
from tests.test_market_trip_book_cycle import CycleCase


def basis_of(results):
    return [(r[0].posted, r[2]) for r in results]


class PolicyTests(unittest.TestCase):
    """book_rules.allocate_stock alone: q and basis for the three cases of the table."""

    def test_e8_first_part_is_independent_and_the_remainder_allocated(self):
        first = br.allocate_stock(D("0.900"), D("866.7"), D("1.800"), D("1.783"))
        self.assertEqual((first[0].posted, first[1], first[2]), (D("0.891"), D("0.891"), "INDEPENDENT"))
        second = br.allocate_stock(D("0.900"), D("866.7"), D("0.900"), D("0.892"))
        self.assertEqual((second[0].posted, second[1], second[2]), (D("0.892"), D("0.891"), "ALLOCATED"))
        self.assertEqual(second[0].difference, D("0.000537"))

    def test_three_small_parts_at_500(self):
        held, posted, out = D("0.003"), D("0.002"), []
        for _ in range(3):
            line, independent, basis = br.allocate_stock(D("0.001"), D("500"), held, posted)
            out.append((line.posted, independent, basis))
            held, posted = held - line.weight, posted - line.posted
        self.assertEqual(out, [(D("0.001"), D("0.001"), "INDEPENDENT"), (D("0.001"), D("0.001"), "INDEPENDENT"), (D("0.000"), D("0.001"), "ALLOCATED")])
        self.assertEqual((held, posted), (D("0.000"), D("0.000")))

    def test_a_capped_part_is_allocated_and_keeps_its_independent_evidence(self):
        line, independent, basis = br.allocate_stock(D("0.001"), D("500"), D("0.002"), D("0.000"))
        self.assertEqual((line.posted, independent, basis), (D("0.000"), D("0.001"), "ALLOCATED"))
        self.assertEqual(line.difference, br.round_half_up(Fraction(0) - br.analytical(D("0.001"), br.FINENESS, None, D("500")), br.EVIDENCE_PLACES))

    def test_an_independent_part_stays_within_half_a_milligram(self):
        line, _, basis = br.allocate_stock(D("0.700"), D("866.7"), D("1.800"), D("1.783"))
        self.assertEqual(basis, "INDEPENDENT")
        self.assertLessEqual(abs(line.difference), D("0.0005"))


class StockCase(CycleCase):
    def item(self, fineness, grams):
        origin = self.issue([karat(K21, "5")])["trip_id"]
        return self.ret(origin, [assay(fineness, grams)])["entry_id"]

    def take(self, trip, item, grams):
        return self.issue_stock(trip, [{"entry_id": item, "line_no": 1, "grams": grams}])

    def stored(self, entry_id):
        line = next(l for l in self.db.book_lines if l["entry_id"] == int(entry_id))
        move = next(m for m in self.db.assayed_moves if m["source_entry_id"] == int(entry_id) and m["movement_type"] == "ISSUE")
        return line, move

    def clean(self, trip):
        return [d["code"] for d in self.service.reconciliation_preview(trip)["diagnostics"]]


class ServiceBasisTests(StockCase):
    def test_e8_line_and_leg_carry_the_same_policy_basis(self):
        item, target = self.item("866.7", "1.8"), self.issue([karat(K21, "5")])["trip_id"]
        first, second = self.take(target, item, "0.9"), self.take(target, item, "0.9")
        for result, posted, basis, independent in ((first, D("0.891"), "INDEPENDENT", D("0.891")), (second, D("0.892"), "ALLOCATED", D("0.891"))):
            line, move = self.stored(result["entry_id"])
            self.assertEqual((line["eq21_posted"], line["posting_basis"]), (posted, basis))
            self.assertEqual((-move["eq21_posted_delta"], move["posting_basis"], move["eq21_independent"]), (posted, basis, independent))
        self.assertEqual(self.clean(target), [])

    def test_three_small_parts_then_capped_and_whole(self):
        item, target = self.item("500", "0.004"), self.issue([karat(K21, "5")])["trip_id"]
        got = []
        for _ in range(4):
            line, move = self.stored(self.take(target, item, "0.001")["entry_id"])
            got.append((line["eq21_posted"], line["posting_basis"], move["eq21_independent"]))
        self.assertEqual(got, [(D("0.001"), "INDEPENDENT", D("0.001")), (D("0.001"), "INDEPENDENT", D("0.001")),
                               (D("0.000"), "ALLOCATED", D("0.001")), (D("0.000"), "ALLOCATED", D("0.001"))])     # third = capped part, fourth = whole remainder
        self.assertEqual(TripRepo(self.db).stock_balance(item, 1), (D("0.000"), D("0.000")))
        self.assertEqual(self.clean(target), [])

    def test_out_of_order_reversal_then_reissue_keeps_weight_and_posted(self):
        item, target = self.item("500", "0.004"), self.issue([karat(K21, "5")])["trip_id"]
        a = self.take(target, item, "0.001")
        b = self.take(target, item, "0.001")
        self.service.reverse(a["entry_id"], "خطأ")                                              # the OLDER issue first: never blocked by rounding
        rev = next(m for m in self.db.assayed_moves if m["movement_type"] == "ISSUE_REVERSAL")
        self.assertEqual((rev["posting_basis"], rev["eq21_posted_delta"], rev["eq21_independent"]), ("INDEPENDENT", D("0.001"), D("0.001")))
        self.assertEqual(TripRepo(self.db).stock_balance(item, 1), (D("0.003"), D("0.001")))
        c = self.take(target, item, "0.003")                                                    # the whole remainder
        self.assertEqual(self.stored(c["entry_id"])[0]["eq21_posted"], D("0.001"))
        self.assertEqual(TripRepo(self.db).stock_balance(item, 1), (D("0.000"), D("0.000")))
        self.assertEqual(self.clean(target), [])
        self.service.reverse(c["entry_id"], "خطأ")
        self.service.reverse(b["entry_id"], "خطأ")
        self.assertEqual(TripRepo(self.db).stock_balance(item, 1), (D("0.004"), D("0.002")))

    def test_a_positive_weight_with_a_zero_share_is_accepted(self):
        item, target = self.item("400", "0.001"), self.issue([karat(K21, "1")])["trip_id"]
        self.take(target, item, "0.001")
        self.assertEqual(self.clean(target), [])


class AllocationEvidenceTests(StockCase):
    """F2: these tampered records pass the line <-> leg comparison of packet23; only the re-derivation from the origin and history catches them."""

    def e8(self):
        item, target = self.item("866.7", "1.8"), self.issue([karat(K21, "5")])["trip_id"]
        first = self.take(target, item, "0.9")
        return item, target, first

    def forge(self, entry_id, posted, basis):
        """Rewrite the line AND its leg consistently (posted, basis, signed value, difference fields): the leg comparison cannot see it."""
        line, move = self.stored(entry_id)
        exact = br.analytical(line["weight_grams"], br.FINENESS, None, line["fineness_permille"])
        difference = br.round_half_up(Fraction(posted) - exact, br.EVIDENCE_PLACES)
        line.update(eq21_posted=posted, signed_eq21_posted=posted, posting_basis=basis, posting_difference=difference)
        move.update(eq21_posted_delta=-posted, posting_basis=basis, posting_difference=difference)

    def codes(self, trip):
        return {d["code"] for d in self.service.reconciliation_preview(trip)["diagnostics"]}

    def test_a_consistent_wrong_share_in_line_and_leg_is_detected(self):
        _, target, first = self.e8()
        self.forge(first["entry_id"], D("0.890"), "ALLOCATED")
        codes = self.codes(target)
        self.assertIn("STOCK_ALLOCATION_MISMATCH", codes)
        self.assertNotIn("STOCK_ISSUE_MISMATCH", codes)                                         # the two copies agree: only the re-derivation sees it

    def test_the_approval_refuses_a_forged_allocation(self):
        _, target, first = self.e8()
        self.forge(first["entry_id"], D("0.890"), "ALLOCATED")
        digest = bs.build(TripRepo(self.db).reconciliation_source_current(target))[3]
        with self.assertRaises(TripError):
            self.service.reconcile(target, digest, None, "OPERATING_LOSS", None, dict(ALL), dict(bs.CONTRACT_VERSIONS_V2), self.k())
        self.assertEqual(self.db.reconciliations, {})

    def test_a_relabelled_basis_is_detected(self):
        _, target, first = self.e8()
        self.forge(first["entry_id"], D("0.891"), "ALLOCATED")                                   # right figure, wrong basis
        self.assertIn("STOCK_ALLOCATION_MISMATCH", self.codes(target))

    def test_changed_independent_evidence_is_detected(self):
        _, target, first = self.e8()
        self.stored(first["entry_id"])[1]["eq21_independent"] = D("0.892")
        self.assertIn("STOCK_ALLOCATION_MISMATCH", self.codes(target))

    def test_a_fineness_different_from_the_origin_is_detected(self):
        item, target, _ = self.e8()
        origin = next(l for l in self.db.book_lines if l["entry_id"] == int(item))
        origin["fineness_permille"] = D("866.8")
        self.assertIn("STOCK_ORIGIN_MISMATCH", self.codes(target))

    def test_a_receipt_that_differs_from_the_origin_is_detected(self):
        item, target, _ = self.e8()
        next(m for m in self.db.assayed_moves if m["movement_type"] == "RECEIVE")["eq21_posted_delta"] = D("1.784")
        self.assertIn("STOCK_ALLOCATION_MISMATCH", self.codes(target))

    def test_a_tampered_compensation_is_detected(self):
        item, target, first = self.e8()
        self.service.reverse(first["entry_id"], "خطأ")
        self.take(target, item, "0.5")
        next(m for m in self.db.assayed_moves if m["movement_type"] == "ISSUE_REVERSAL")["posting_basis"] = "ALLOCATED"
        self.assertIn("STOCK_ALLOCATION_MISMATCH", self.codes(target))

    def test_a_missing_origin_or_unreadable_history_blocks(self):
        item, target, _ = self.e8()
        source = TripRepo(self.db).reconciliation_source(target)
        for broken, code in (({**source, "stock_origins": []}, "STOCK_ORIGIN_MISSING"),
                             ({**source, "stock_history": [{**source["stock_history"][0], "eq21_independent": None}] + source["stock_history"][1:]},
                              "STOCK_HISTORY_INVALID")):
            preview = rec.reconcile_preview(broken)
            self.assertIn(code, {d.code for d in preview.diagnostics})
            self.assertFalse(preview.trustworthy)

    def test_incomplete_history_never_yields_a_trusted_preview(self):
        """packet25: an absent / empty / [None] / receipt-only history used to pass silently while an effective issue exists."""
        _, target, _ = self.e8()
        source = TripRepo(self.db).reconciliation_source(target)
        self.assertTrue(rec.reconcile_preview(source).trustworthy)
        receipt_only = [r for r in source["stock_history"] if r["movement_type"] == "RECEIVE"]
        dup = source["stock_history"] + [dict(source["stock_history"][-1])]
        without = {k: v for k, v in source.items() if k != "stock_history"}
        for name, broken in (("absent", without), ("empty", {**source, "stock_history": []}), ("[None]", {**source, "stock_history": [None]}),
                             ("receipt only", {**source, "stock_history": receipt_only}), ("duplicate id", {**source, "stock_history": dup}),
                             ("origins absent", {k: v for k, v in source.items() if k != "stock_origins"})):
            with self.subTest(name):
                preview = rec.reconcile_preview(broken)
                self.assertFalse(preview.trustworthy, name)
                self.assertTrue({d.code for d in preview.diagnostics} & {"STOCK_HISTORY_INVALID", "STOCK_ALLOCATION_MISMATCH", "STOCK_ORIGIN_MISSING"}, name)

    def test_a_valid_history_of_another_issue_does_not_cover_this_line(self):
        item, target, first = self.e8()
        source = TripRepo(self.db).reconciliation_source(target)
        mine = next(r["id"] for r in source["stock_history"] if r["movement_type"] == "ISSUE")
        foreign = [r for r in source["stock_history"] if r["id"] != mine]                    # a valid prefix (receipt only): the issue of this line is gone
        preview = rec.reconcile_preview({**source, "stock_history": foreign})
        self.assertFalse(preview.trustworthy)

    def test_the_approval_refuses_when_the_history_is_incomplete(self):
        _, target, _ = self.e8()
        original = TripRepo.phase2_source

        def without_history(repo, trip_id, *, current):
            out = original(repo, trip_id, current=current)
            return {**out, "stock_history": [r for r in out["stock_history"] if r["movement_type"] == "RECEIVE"]}
        digest = bs.build(TripRepo(self.db).reconciliation_source_current(target))[3]
        with patch.object(TripRepo, "phase2_source", without_history):
            with self.assertRaises(TripError):
                self.service.reconcile(target, digest, None, "OPERATING_LOSS", None, dict(ALL), dict(bs.CONTRACT_VERSIONS_V2), self.k())
        self.assertEqual(self.db.reconciliations, {})

    def test_a_trip_without_stock_needs_no_history(self):
        target = self.issue([karat(K21, "5")])["trip_id"]
        source = TripRepo(self.db).reconciliation_source(target)
        preview = rec.reconcile_preview({k: v for k, v in source.items() if k not in ("stock_history", "stock_origins")})
        self.assertTrue(preview.trustworthy)

    def test_an_allocated_share_above_half_a_milligram_is_accepted(self):
        _, target, _ = self.e8()
        item = next(l for l in self.db.book_lines if l["entry_kind"] == "BK_ISS_STK")["stock_item_entry_id"]
        self.take(target, item, "0.9")
        self.assertEqual(self.codes(target), set())


class AllocationReadTests(unittest.TestCase):
    """The two new sections: one statement each (never one per line), all items of the trip at once, locking only in the decision form, never a LIMIT."""

    def statements(self, current):
        from repositories.market_trip_repository import MarketTripRepository
        from tests.test_book_reconciliation_service import Recorder
        connection = Recorder()
        repository = MarketTripRepository(connection)
        repository.has_phase2_schema = lambda: True
        repository.phase2_source(9, current=current)
        return [" ".join((s if isinstance(s, str) else s[0]).split()) for s in connection.statements]

    def test_origins_and_history_are_single_reads_locked_only_for_a_decision(self):
        for current in (True, False):
            sql = self.statements(current)
            origins = [s for s in sql if "SELECT o.entry_id" in s]
            history = [s for s in sql if "FROM shop_assayed_stock_movements h" in s]
            self.assertEqual((len(origins), len(history)), (1, 1))
            for s in origins + history:
                self.assertIn("b.entry_kind = 'BK_ISS_STK'", s)
                self.assertNotRegex(s, r"\bLIMIT\b")
            self.assertIn("ORDER BY h.id", history[0])
            self.assertEqual("FOR SHARE OF o" in origins[0] and "FOR SHARE OF h" in history[0], current)
            self.assertEqual(any("FOR SHARE" in s or "FOR UPDATE" in s for s in sql), current)


if __name__ == "__main__":
    unittest.main()
