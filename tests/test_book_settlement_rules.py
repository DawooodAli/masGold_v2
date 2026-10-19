"""R2-S1 reconciliation SETTLEMENT: the PURE module services/book_settlement.py. No database, no float in any assertion, no service.

`DatedSrc` extends the R1 test builder (`Src`: a healthy stored source made with the project's own `book_rules.plan_line`) with entry dates, the reversal type and the stock
record identity the settlement read adds. Every boundary of the owner's policy is asserted with exact integers on the 1/26,250,000 g lattice; the arithmetic is cross-checked
against the R1 preview (`match_preview`) on seeded random sources. Nothing here proves a SQL CHECK, the MySQL JSON type or a lock: the key re-ordering below is a SIMULATION
of the documented JSON normalisation."""
import copy
from datetime import date, datetime
from decimal import Decimal as D
from fractions import Fraction as F
import json
import random
import unittest
import unittest.mock

from services import book_reconciliation as rec
from services import book_rules as br
from services import book_settlement as bs
from tests.test_book_reconciliation_rules import K, Src, TRIP

DAY = date(2026, 10, 10)
ALL = {k: True for k in bs.CONFIRMATION_KEYS}


class DatedSrc(Src):
    def add(self, kind, rows, *, reversed_=False, prices=None, entry_date=DAY):
        eid = super().add(kind, rows, reversed_=reversed_, prices=prices)
        entry = self.entries[-1]
        entry["entry_date"], entry["correction_type"] = entry_date, "FULL_REVERSAL" if reversed_ else None
        return eid

    def source(self):
        for number, row in enumerate(self.stock, 1):
            row.setdefault("id", number)
        return super().source()

    def built(self):
        return bs.build(self.source())


def sell_buy(buy, sell):
    """A BOOK source from one purchase and one sale (both priced) given as (weight, "F", permille) rows."""
    s = DatedSrc()
    s.add(br.BK_BUY, [buy], prices=["1000.00"])
    s.add(br.BK_SELL, [sell], prices=["1000.00"])
    return s


def healthy():
    """A healthy BOOK source with every document kind (issue, purchase, return to Scrap + assayed stock, sale)."""
    s = DatedSrc()
    s.add(br.BK_ISSUE, [("10.000", K, 21), ("4.000", K, 18)])
    s.add(br.BK_BUY, [("2.000", "F", "900")], prices=["500.00"])
    s.add(br.BK_RETURN, [("3.000", K, 21), ("1.000", "F", "900")])
    s.add(br.BK_SELL, [("2.000", K, 18)], prices=["900.00"])
    return s


class BoundaryTests(unittest.TestCase):
    """The owner's policy boundaries: the reason is mandatory exactly when the posted loss is not zero (A >= 0.0005 g = 13,125 units)."""

    def settle(self, s):
        _, _, st, *_ = s.built()
        bs.match_preview(st, s.preview())                                     # the integer computation agrees with R1
        return st

    def test_zero_balance(self):
        s = DatedSrc()
        s.add(br.BK_ISSUE, [("10.000", K, 21)])
        s.add(br.BK_RETURN, [("10.000", K, 21)])
        st = self.settle(s)
        self.assertEqual((st.a_star_units, st.loss_milli, st.rounding_milli, st.a_state), (0, 0, 0, rec.ZERO))
        bs.require_reason(None, st.loss_milli)                                # no reason needed

    def test_one_eight_thousand_seven_hundred_fiftieth_stays_positive_with_zero_loss(self):
        s = DatedSrc()
        s.add(br.BK_BUY, [("0.001", "F", "100")], prices=["1.00"])             # A = 1/8750 g = 3,000 units
        st = self.settle(s)
        self.assertEqual((st.a_star_units, st.b_star_milli, st.loss_milli, st.rounding_milli), (3000, 0, 0, 0))
        self.assertEqual(st.a_state, rec.POSITIVE_BELOW)
        bs.require_reason(None, st.loss_milli)                                # optional while L == 0 (the residual is stored and confirmed)
        self.assertEqual(st.delta_units, -3000)

    def test_just_below_the_half_milligram_keeps_loss_zero_and_a_positive_rounding_settlement(self):
        s = DatedSrc()
        s.add(br.BK_BUY, [("0.028", "F", "81.1")], prices=["1.00"])             # 68,124 units in
        s.add(br.BK_SELL, [("0.002", K, 22)], prices=["1.00"])                   # 55,000 units out
        st = self.settle(s)
        self.assertEqual((st.a_star_units, st.b_star_milli, st.loss_milli, st.rounding_milli), (13124, 1, 0, 1))
        self.assertEqual(st.a_state, rec.POSITIVE_BELOW)
        bs.require_reason(None, st.loss_milli)

    def test_exactly_half_a_milligram_rounds_up_and_needs_a_reason(self):
        s = DatedSrc()
        s.add(br.BK_BUY, [("0.001", "F", "437.5")], prices=["1.00"])             # A = 0.0005 g = 13,125 units
        st = self.settle(s)
        self.assertEqual((st.a_star_units, st.loss_milli, st.b_star_milli, st.rounding_milli), (13125, 1, 1, 0))
        self.assertEqual(st.a_state, rec.POSITIVE)
        with self.assertRaises(br.BookError) as refused:
            bs.require_reason(None, st.loss_milli)
        self.assertEqual(refused.exception.code, "REASON_REQUIRED")
        bs.require_reason("TRADING_LOSS", st.loss_milli)

    def test_reviewers_example_b_zero_loss_one_milligram_rounding_minus_one(self):
        st = self.settle(sell_buy(("0.014", "F", "87.5"), ("0.001", "F", "437.5")))        # A = 0.0009 g, B = 0
        self.assertEqual((st.a_star_units, st.b_star_milli, st.loss_milli, st.rounding_milli), (23625, 0, 1, -1))
        self.assertEqual((st.delta_units, st.rho_units), (26250 - 23625, -23625))
        self.assertEqual(st.rounding_milli * bs.MILLI_UNITS, st.rho_units - st.delta_units)

    def test_three_over_three_thousand_five_hundred_needs_a_reason(self):
        s = DatedSrc()
        s.add(br.BK_BUY, [("0.001", K, 18)], prices=["1.00"])                    # A = 0.001 g x 18/21 = 3/3500 g = 22,500 units
        st = self.settle(s)
        self.assertEqual((st.a_star_units, st.loss_milli, st.b_star_milli, st.rounding_milli), (22500, 1, 1, 0))
        with self.assertRaises(br.BookError):
            bs.require_reason(None, st.loss_milli)

    def test_loss_flips_exactly_between_13124_and_13125_units_over_the_low_range(self):
        for units in range(0, 40_000):
            self.assertEqual((bs.loss_milli_of(units) != 0), units >= bs.HALF_MILLI_UNITS)
            self.assertEqual(bs.loss_milli_of(units), int(br.post(F(units, bs.UNITS_PER_GRAM)) * 1000))

    def test_every_quantity_is_a_whole_number_of_units(self):
        rng = random.Random(57)
        for _ in range(400):
            weight, karat = D(rng.randint(1, 999_999)).scaleb(-3), rng.choice((18, 21, 22, 24))
            permille = D(rng.randint(1, 10_000)).scaleb(-1)
            self.assertEqual(F(br.analytical(weight, br.MASTER_KARAT, karat, None)) * bs.UNITS_PER_GRAM % 1, 0)
            self.assertEqual(F(br.analytical(weight, br.FINENESS, None, permille)) * bs.UNITS_PER_GRAM % 1, 0)


class PropertyTests(unittest.TestCase):
    def test_seeded_random_sources_agree_with_the_r1_preview(self):
        rng = random.Random(20261018)
        for case in range(250):
            s = DatedSrc()
            s.add(br.BK_ISSUE, [(f"{rng.randint(1, 300)}.{rng.randint(0, 999):03d}", K, rng.choice((18, 21, 22, 24))) for _ in range(rng.randint(1, 3))])
            for _ in range(rng.randint(0, 3)):
                s.add(br.BK_BUY, [(f"{rng.randint(0, 30)}.{rng.randint(1, 999):03d}", "F", str(D(rng.randint(10, 10_000)).scaleb(-1)))], prices=["10.00"])
            outs = rng.randint(0, 3)
            for _ in range(outs):
                kind = rng.choice((br.BK_RETURN, br.BK_SELL))
                row = (f"{rng.randint(0, 2)}.{rng.randint(1, 999):03d}", K, rng.choice((18, 21, 22, 24)))
                s.add(kind, [row], prices=None if kind == br.BK_RETURN else ["10.00"])
            preview = s.preview()
            if preview.a_state not in (rec.ZERO, rec.POSITIVE_BELOW, rec.POSITIVE) or not preview.trustworthy:
                continue
            _, entries, st, source_digest, snapshot, snapshot_digest = s.built()
            bs.match_preview(st, preview)
            st.check_identities()
            self.assertEqual(bs.verify_snapshot(snapshot), [], case)
            self.assertEqual(st.rounding_milli * bs.MILLI_UNITS, st.rho_units - st.delta_units)
            self.assertEqual(st.b_star_milli, st.loss_milli + st.rounding_milli)

    def test_no_float_or_decimal_arithmetic_on_wide_values(self):
        big = 10 ** 29 + 1
        self.assertEqual(bs.loss_milli_of(big) * bs.MILLI_UNITS - big, ((big + 13125) // 26250) * 26250 - big)
        self.assertEqual(bs.istr(big), "100000000000000000000000000001")


class CanonicalTests(unittest.TestCase):
    def test_key_order_and_whitespace_do_not_change_the_bytes(self):
        a = {"b": "1", "a": ["x", None, True], "z": {"y": "2", "x": "3"}}
        b = {"z": {"x": "3", "y": "2"}, "a": ["x", None, True], "b": "1"}
        self.assertEqual(bs.canonical_bytes(a), bs.canonical_bytes(b))
        self.assertEqual(bs.canonical_bytes(a), b'{"a":["x",null,true],"b":"1","z":{"x":"3","y":"2"}}')

    def test_numbers_must_travel_as_strings(self):
        for bad in (1, 1.5, D("1"), float("nan"), (1,), {"a": 2}):
            with self.assertRaises(bs.CanonicalError):
                bs.canonical_bytes({"v": bad})
        with self.assertRaises(bs.CanonicalError):
            bs.canonical_bytes({1: "a"})

    def test_unicode_is_utf8_not_escaped(self):
        self.assertEqual(bs.canonical_bytes({"k": "فاقد"}), '{"k":"فاقد"}'.encode("utf-8"))

    def test_fixed_scale_is_lossless_and_never_negative_zero(self):
        self.assertEqual(bs.fixed(D("6"), 3), "6.000")
        self.assertEqual(bs.fixed(D("-0.000"), 3), "0.000")
        self.assertEqual(bs.fixed(D("-0E-8"), 6), "0.000000")
        self.assertEqual(bs.fixed(D("-0.001"), 3), "-0.001")
        self.assertEqual(bs.fixed(D("1E+3"), 1), "1000.0")
        for lossy in (D("0.0005"), D("1.2345678")):
            with self.assertRaises(bs.CanonicalError):
                bs.fixed(lossy, 3)
        with self.assertRaises(bs.CanonicalError):
            bs.fixed(D("NaN"), 3)
        with self.assertRaises(bs.CanonicalError):
            bs.fixed(1.5, 3)
        self.assertEqual(bs.milli_text(0), "0.000")
        self.assertEqual(bs.milli_text(-1), "-0.001")
        self.assertEqual(bs.milli_text(-1500), "-1.500")
        self.assertEqual(bs.milli_text(10 ** 18), "1000000000000000.000")

    def test_integer_strings(self):
        self.assertEqual((bs.istr(0), bs.istr(-7), bs.istr(10)), ("0", "-7", "10"))
        for bad in (True, 1.0, "1", D(1)):
            with self.assertRaises(bs.CanonicalError):
                bs.istr(bad)

    def test_digest_text_is_lowercase_ascii_hex_of_exact_length(self):
        good = "a" * 64
        self.assertTrue(bs.is_digest(good))
        for bad in ("A" * 64, "a" * 63, "a" * 65, "g" * 64, " " + "a" * 63, 5, None, "ａ" * 64):
            self.assertFalse(bs.is_digest(bad))


class DigestTests(unittest.TestCase):
    def base(self):
        return healthy()

    def digests(self, s):
        _, _, _, source_digest, _, snapshot_digest = s.built()
        return source_digest, snapshot_digest

    def test_row_and_key_order_do_not_change_either_digest(self):
        s = self.base()
        reference = self.digests(s)
        shuffled = copy.deepcopy(s)
        random.Random(1).shuffle(shuffled.entries)
        random.Random(2).shuffle(shuffled.lines)
        random.Random(3).shuffle(shuffled.scrap)
        shuffled.entries = [dict(reversed(list(e.items()))) for e in shuffled.entries]
        shuffled.lines = [dict(reversed(list(l.items()))) for l in shuffled.lines]
        self.assertEqual(self.digests(shuffled), reference)

    def test_ids_are_ordered_numerically_not_as_text(self):
        s = DatedSrc()
        for _ in range(12):                                                     # entry ids 1..12: "10" < "2" as text
            s.add(br.BK_BUY, [("1.000", "F", "900")], prices=["10.00"])
        _, entries, *_ = s.built()
        self.assertEqual([int(e["id"]) for e in entries], list(range(1, 13)))
        self.assertEqual(bs.source_payload(TRIP, entries)["entries"][9]["id"], "10")

    def test_trip_mode_and_versions_are_part_of_the_source_digest(self):
        s = self.base()
        _, entries, *_ = s.built()
        self.assertNotEqual(bs.source_digest(TRIP, entries), bs.source_digest(TRIP + 1, entries))
        payload = bs.source_payload(TRIP, entries)
        self.assertEqual((payload["algorithm"], payload["accounting_mode"], payload["source_kinds"]), (bs.SOURCE_ALGORITHM, "BOOK", list(bs.V1_KINDS)))        # a trip without a Phase 2 kind keeps the v1 payload
        changed = copy.deepcopy(payload)
        changed["algorithm"] = "book-recon-source-v2"
        self.assertNotEqual(bs.digest_of(payload), bs.digest_of(changed))

    def test_a_changed_fact_changes_both_digests(self):
        s = self.base()
        before = self.digests(s)
        line = s.line(1, 1)
        line["weight_grams"] = D("10.001")
        line["eq21_posted"] = br.post(br.analytical(D("10.001"), br.MASTER_KARAT, 21, None))
        line["eq21_analytical"] = br.evidence(br.analytical(D("10.001"), br.MASTER_KARAT, 21, None))
        line["posting_difference"] = br.round_half_up(F(line["eq21_posted"]) - br.analytical(D("10.001"), br.MASTER_KARAT, 21, None), 6)
        line["signed_eq21_posted"] = line["eq21_posted"]
        after = self.digests(s)
        self.assertNotEqual(after[0], before[0])
        self.assertNotEqual(after[1], before[1])

    def test_reversal_changes_the_source_digest(self):
        s = self.base()
        before = self.digests(s)
        s.entries[3]["correction_id"], s.entries[3]["correction_type"] = 950, "FULL_REVERSAL"
        after = self.digests(s)
        self.assertNotEqual(after[0], before[0])
        self.assertNotEqual(after[1], before[1])

    def test_cash_entries_and_the_reconciliation_document_itself_are_excluded(self):
        s = self.base()
        before = self.digests(s)
        s.entries.append({"id": 90, "trip_id": TRIP, "trip_mode": "BOOK", "entry_no": 90, "entry_kind": "ISSUE", "entry_date": DAY, "correction_id": None, "correction_type": None})
        s.entries.append({"id": 91, "trip_id": TRIP, "trip_mode": "BOOK", "entry_no": 91, "entry_kind": "BK_RECON", "entry_date": DAY, "correction_id": None, "correction_type": None})
        s.entries.append({"id": 92, "trip_id": TRIP, "trip_mode": "BOOK", "entry_no": 92, "entry_kind": "BK_RECON", "entry_date": DAY, "correction_id": 960,
                          "correction_type": "FULL_REVERSAL"})
        self.assertEqual(self.digests(s), before)                                 # creating or reversing the reconciliation, or a cash entry, changes neither digest

    def test_side_evidence_changes_the_snapshot_digest_only(self):
        s = self.base()
        before = self.digests(s)
        s.scrap[0]["movement_code"] = "TRIP_CUSTODY_OUT_X"                         # a leg is side evidence (R1's diagnostics judge it; the digest of the SOURCE ignores it)
        after = self.digests(s)
        self.assertEqual(after[0], before[0])
        self.assertNotEqual(after[1], before[1])
        t = self.base()
        t.trades[0]["total_price"] = D("501.00")
        self.assertEqual(self.digests(t)[0], before[0])
        self.assertNotEqual(self.digests(t)[1], before[1])
        u = self.base()
        u.stock[0]["grams_delta"] = D("1.001")
        self.assertEqual(self.digests(u)[0], before[0])
        self.assertNotEqual(self.digests(u)[1], before[1])

    def test_the_stock_record_identity_is_required_and_kept(self):
        s = self.base()
        _, entries, *_ = s.built()
        stock = [r for e in entries for r in e["stock"]]
        self.assertEqual([r["id"] for r in stock], ["1"])                       # the assayed-return stock record of the BK_RETURN FINENESS line
        self.assertEqual(stock[0]["item_line_no"], "2")
        source = s.source()
        source["stock"] = [{k: v for k, v in r.items() if k != "id"} for r in source["stock"]]
        with self.assertRaises(bs.SettlementDataError):                       # a read that does not select s.id cannot be snapshotted
            bs.build(source)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.s = healthy()
        self.trip, self.entries, self.st, self.source_digest, self.snapshot, self.snapshot_digest = self.s.built()

    def row(self):
        st = self.st
        return {"algorithm": bs.RECON_ALGORITHM, "source_algorithm": bs.SOURCE_ALGORITHM, "snapshot_format": bs.SNAPSHOT_FORMAT,
                "trip_id": self.trip, "trip_mode": br.BOOK, "entry_kind": bs.BK_RECON,
                "unit_denominator": bs.UNITS_PER_GRAM, "a_star_units": st.a_star_units, "a_star": bs.a_star_evidence(st),
                "b_star": bs.milli_decimal(st.b_star_milli), "loss_posted": bs.milli_decimal(st.loss_milli), "rounding_settlement": bs.milli_decimal(st.rounding_milli),
                "delta_units": st.delta_units, "rho_units": st.rho_units, "sum_abs_units": st.sum_abs_units, "entry_count": st.entry_count, "line_count": st.line_count,
                "reversed_count": st.reversed_count, "source_digest": self.source_digest, "snapshot_digest": self.snapshot_digest,
                "snapshot": bs.canonical_bytes(self.snapshot).decode("utf-8")}

    @staticmethod
    def mysql_like(text):
        """SIMULATED JSON-column behaviour: keys re-ordered (length then bytes) and different whitespace. Not a MySQL test."""
        def norm(v):
            if isinstance(v, dict):
                return {k: norm(v[k]) for k in sorted(v, key=lambda k: (len(k.encode()), k.encode()))}
            return [norm(x) for x in v] if isinstance(v, list) else v
        return json.dumps(norm(json.loads(text)), ensure_ascii=False, separators=(", ", ": "))

    def test_the_snapshot_is_made_of_strings_and_verifies(self):
        self.assertEqual(bs.verify_snapshot(self.snapshot), [])
        self.assertEqual(self.snapshot["totals"], self.st.totals())
        self.assertEqual({k: self.snapshot[k] for k in ("snapshot_version", "recon_algorithm", "source_algorithm")},
                         {"snapshot_version": bs.SNAPSHOT_FORMAT, "recon_algorithm": bs.RECON_ALGORITHM, "source_algorithm": bs.SOURCE_ALGORITHM})
        self.assertEqual(bs.source_digest(self.trip, self.snapshot["entries"]), self.source_digest)       # the digest is re-derivable from the stored snapshot alone

    def test_a_driver_that_reorders_keys_and_whitespace_does_not_matter(self):
        stored_text = self.mysql_like(bs.canonical_bytes(self.snapshot).decode("utf-8"))
        self.assertNotEqual(stored_text.encode("utf-8"), bs.canonical_bytes(self.snapshot))              # the stored TEXT differs from the hashed bytes ...
        row = self.row()
        row["snapshot"] = stored_text
        self.assertEqual(bs.verify_record(row), [])                                                      # ... and verification re-serialises, so it still matches
        for form in (stored_text, stored_text.encode("utf-8"), json.loads(stored_text)):
            row["snapshot"] = form
            self.assertEqual(bs.verify_record(row), [])

    def test_the_record_matches_its_columns(self):
        self.assertEqual(bs.verify_record(self.row()), [])

    def test_tampering_is_detected(self):
        cases = {}
        row = self.row(); row["loss_posted"] = D("0.001"); cases["COLUMN_LOSS_POSTED_MISMATCH"] = row
        row = self.row(); row["a_star_units"] = 1; cases["COLUMN_A_STAR_UNITS_MISMATCH"] = row
        row = self.row(); row["entry_count"] = 99; cases["COLUMN_ENTRY_COUNT_MISMATCH"] = row
        row = self.row(); row["snapshot_digest"] = "0" * 64; cases["SNAPSHOT_DIGEST_MISMATCH"] = row
        row = self.row(); row["source_digest"] = "1" * 64; cases["SOURCE_DIGEST_MISMATCH"] = row
        row = self.row(); row["source_digest"] = row["source_digest"].upper(); cases["DIGEST_FORMAT"] = row
        row = self.row(); row["algorithm"] = "book-recon-v2"; cases["ALGORITHM_MISMATCH"] = row
        row = self.row(); row["unit_denominator"] = 1000; cases["COLUMN_UNIT_DENOMINATOR_MISMATCH"] = row
        row = self.row(); row["a_star"] = D("0.000001"); cases["COLUMN_A_STAR_MISMATCH"] = row
        for expected, row in cases.items():
            self.assertIn(expected, bs.verify_record(row), expected)
        changed = copy.deepcopy(self.snapshot)
        changed["entries"][0]["lines"][0]["weight_grams"] = "10.001"
        problems = bs.verify_snapshot(changed)
        self.assertTrue({"LINE_UNITS_MISMATCH", "LINE_EVIDENCE_MISMATCH", "TOTALS_MISMATCH"} & set(problems))
        row = self.row(); row["snapshot"] = bs.canonical_bytes(changed).decode("utf-8")
        self.assertTrue(bs.verify_record(row))
        numeric = copy.deepcopy(self.snapshot); numeric["totals"]["entry_count"] = 4
        self.assertEqual(bs.verify_snapshot(numeric), ["SNAPSHOT_UNREADABLE"])                          # a JSON number is never accepted
        wrong_version = copy.deepcopy(self.snapshot); wrong_version["snapshot_version"] = "book-recon-snapshot-v2"
        self.assertEqual(bs.verify_snapshot(wrong_version), ["VERSION_MISMATCH"])
        self.assertEqual(bs.verify_record({**self.row(), "snapshot": "{not json"}), ["SNAPSHOT_UNREADABLE"])

    def test_reading_and_rewriting_is_a_fixed_point(self):
        text = bs.canonical_bytes(self.snapshot).decode("utf-8")
        again = bs.canonical_bytes(json.loads(self.mysql_like(text))).decode("utf-8")
        self.assertEqual(text, again)
        self.assertEqual(bs.digest_of(json.loads(again)), self.snapshot_digest)


class PolicyTests(unittest.TestCase):
    def test_reasons_and_notes(self):
        self.assertEqual(bs.clean_reason(None, None), (None, None))
        self.assertEqual(bs.clean_reason("  ", "  "), (None, None))
        self.assertEqual(bs.clean_reason("OPERATING_LOSS", "  ملاحظة "), ("OPERATING_LOSS", "ملاحظة"))
        self.assertEqual(sorted(bs.REASONS), ["OPERATING_LOSS", "OTHER", "PURITY_DIFFERENCE", "TRADING_LOSS", "UNEXPLAINED"])
        self.assertEqual(bs.REASONS["OPERATING_LOSS"], "فاقد تشغيل (سبك/تنظيف)")
        for code in ("OTHER", "UNEXPLAINED"):
            for blank in (None, "", "   "):
                with self.assertRaises(br.BookError) as refused:
                    bs.clean_reason(code, blank)
                self.assertEqual(refused.exception.code, "NOTE_REQUIRED")
            self.assertEqual(bs.clean_reason(code, "x"), (code, "x"))                                       # no invented minimum length: non-blank is enough
        with self.assertRaises(br.BookError) as refused:
            bs.clean_reason("NOT_A_REASON", "x")
        self.assertEqual(refused.exception.code, "REASON_INVALID")
        with self.assertRaises(br.BookError) as refused:
            bs.clean_reason("OPERATING_LOSS", "x" * 1001)
        self.assertEqual(refused.exception.code, "TEXT_LONG")
        self.assertEqual(bs.clean_reason("OPERATING_LOSS", "x" * 1000)[1], "x" * 1000)

    def test_confirmations_are_exactly_three_true_booleans(self):
        self.assertEqual(bs.clean_confirmations(dict(ALL)), ALL)
        for bad in (None, {}, {**ALL, "recording_complete": False}, {**ALL, "extra": True}, {k: "true" for k in ALL}, {k: 1 for k in ALL},
                    {k: True for k in list(ALL)[:2]}):
            with self.assertRaises(br.BookError) as refused:
                bs.clean_confirmations(bad)
            self.assertEqual(refused.exception.code, "RECONCILIATION_CONFIRM_REQUIRED")

    def test_the_kind_constants_of_the_modules_agree(self):
        self.assertEqual((rec.BK_RECON, bs.BK_RECON), (br.BK_RECON, br.BK_RECON))
        self.assertEqual(bs.SOURCE_KINDS, rec.SOURCE_KINDS)
        self.assertNotIn(br.BK_RECON, rec.SOURCE_KINDS)                                  # the reconciliation is never a gold source of itself
        self.assertIn(br.BK_RECON, rec.EXCLUDED_KNOWN_KINDS)
        self.assertEqual(br.RECON_KINDS, (br.BK_RECON,))

    def test_the_loss_confirmation_covers_the_analytical_balance_itself(self):
        texts = bs.confirmation_texts("0.000114", "0.000")
        self.assertIn("الرصيد التحليلي غير المسوّى A = 0.000114", texts["loss_recognised_as_posted"])
        self.assertIn("حتى لو كان L صفرًا", texts["loss_recognised_as_posted"])
        self.assertEqual(set(texts), set(bs.CONFIRMATION_KEYS))

    def test_contract_versions_must_match(self):
        self.assertEqual(bs.clean_versions(dict(bs.CONTRACT_VERSIONS)), bs.CONTRACT_VERSIONS)
        for bad in (None, {}, {**bs.CONTRACT_VERSIONS, "snapshot_format": "x"}, {**bs.CONTRACT_VERSIONS, "extra": "x"}):
            with self.assertRaises(br.BookError) as refused:
                bs.clean_versions(bad)
            self.assertEqual(refused.exception.code, "RECONCILIATION_CONTRACT_MISMATCH")

    def test_the_date_input_marker_and_formats(self):
        self.assertEqual(bs.clean_date_input(None), bs.DATE_TODAY)
        self.assertEqual(bs.clean_date_input("  "), bs.DATE_TODAY)
        self.assertEqual(bs.clean_date_input("2026-10-09"), "2026-10-09")
        self.assertEqual(bs.clean_date_input(date(2026, 10, 9)), "2026-10-09")
        self.assertEqual(bs.clean_date_input(datetime(2026, 10, 9, 23, 59)), "2026-10-09")
        self.assertEqual(bs.clean_date_input("٢٠٢٦-١٠-٠٩"), "2026-10-09")
        for bad in ("yesterday", "2026-13-01", "10/10/2026", 5):
            with self.assertRaises(br.BookError) as refused:
                bs.clean_date_input(bad)
            self.assertEqual(refused.exception.code, "DATE_INVALID")

    def test_the_date_window(self):
        s = DatedSrc()
        s.add(br.BK_ISSUE, [("1.000", K, 21)], entry_date=date(2026, 10, 3))
        s.add(br.BK_RETURN, [("1.000", K, 21)], entry_date=date(2026, 10, 7), reversed_=True)             # a reversed document still counts
        s.entries.append({"id": 50, "trip_id": TRIP, "trip_mode": "BOOK", "entry_no": 50, "entry_kind": "ISSUE", "entry_date": date(2026, 10, 9), "correction_id": None})        # cash: ignored
        s.entries.append({"id": 51, "trip_id": TRIP, "trip_mode": "BOOK", "entry_no": 51, "entry_kind": "BK_RECON", "entry_date": date(2026, 10, 9), "correction_id": None})    # itself: ignored
        floor, source = bs.date_window(s.source(), datetime(2026, 9, 1, 8, 0))
        self.assertEqual((floor, source), (date(2026, 10, 7), bs.FLOOR_LAST_GOLD_ENTRY))
        empty = DatedSrc().source()
        self.assertEqual(bs.date_window(empty, datetime(2026, 9, 1, 8, 0)), (date(2026, 9, 1), bs.FLOOR_TRIP_CREATED))
        with self.assertRaises(bs.SettlementDataError):                                                      # no invented date
            bs.date_window({"entries": [{"id": 1, "entry_kind": br.BK_ISSUE, "entry_date": None}]}, datetime(2026, 9, 1))
        with self.assertRaises(bs.SettlementDataError):
            bs.date_window(empty, None)

    def test_the_effective_date_is_inside_the_window(self):
        floor, today = date(2026, 10, 7), date(2026, 10, 10)
        self.assertEqual(bs.resolve_effective_date(bs.DATE_TODAY, floor, today), today)
        self.assertEqual(bs.resolve_effective_date("2026-10-07", floor, today), floor)
        self.assertEqual(bs.resolve_effective_date("2026-10-10", floor, today), today)
        for bad in ("2026-10-06", "2026-10-11"):
            with self.assertRaises(br.BookError) as refused:
                bs.resolve_effective_date(bad, floor, today)
            self.assertEqual(refused.exception.code, "EFFECTIVE_DATE_RANGE")
        with self.assertRaises(br.BookError) as refused:                                                     # the last movement is dated after the server's today
            bs.resolve_effective_date(bs.DATE_TODAY, date(2026, 10, 12), today)
        self.assertEqual(refused.exception.code, "EFFECTIVE_DATE_RANGE")

    def test_the_request_hash(self):
        base = ("5", "a" * 64, bs.DATE_TODAY, "OTHER", "ملاحظة", ALL)
        reference = bs.request_hash(5, *base[1:])
        self.assertEqual(reference, bs.request_hash(5, *base[1:]))
        self.assertTrue(bs.is_digest(reference))
        self.assertEqual(bs.request_hash(5, "a" * 64, bs.DATE_TODAY, "OTHER", "ملاحظة", dict(reversed(list(ALL.items())))), reference)       # key order is irrelevant
        variants = [bs.request_hash(6, *base[1:]), bs.request_hash(5, "b" * 64, *base[2:]), bs.request_hash(5, base[1], "2026-10-10", *base[3:]),
                    bs.request_hash(5, *base[1:3], "UNEXPLAINED", *base[4:]), bs.request_hash(5, *base[1:4], "غيره", base[5]),
                    bs.request_hash(5, *base[1:3], None, None, ALL)]
        self.assertEqual(len(set(variants + [reference])), len(variants) + 1)
        payload = bs.request_payload(5, *base[1:])
        self.assertEqual(set(payload), {"kind", "trip_id", "source_digest", "effective_date", "reason_code", "note", "confirmations", "contract_versions"})
        self.assertNotIn("request_key", payload)                                                             # the key and the executor are verified separately, never hashed
        self.assertNotIn("a_star", json.dumps(payload))                                                      # no financial figure travels from the client


class CapacityTests(unittest.TestCase):
    @staticmethod
    def st(a_in=0, a_out=0, p_in=0, p_out=0, bound=0, entries=1, lines=1, reversed_=0):
        return bs.Settlement(a_in, a_out, p_in, p_out, bound, entries, lines, reversed_)

    def refused(self, settlement, generation=None, code="RECONCILIATION_CAPACITY"):
        with self.assertRaises(br.BookError) as refused:
            bs.check_capacity(settlement, generation)
        self.assertEqual(refused.exception.code, code)

    def test_values_below_the_limit_pass(self):
        limit = bs.GRAM_LIMIT * bs.UNITS_PER_GRAM
        bs.check_capacity(self.st(a_in=limit - bs.UNITS_PER_GRAM, p_in=(bs.GRAM_LIMIT - 1) * 1000), 1)

    def test_the_rounded_evidence_and_the_rounded_loss_are_checked_not_only_the_raw_value(self):
        limit = bs.GRAM_LIMIT * bs.UNITS_PER_GRAM
        self.refused(self.st(a_in=limit - 10))                      # A < 10^15 but its six-decimal evidence rounds UP to 10^15
        self.refused(self.st(a_in=limit))
        self.refused(self.st(a_in=limit - 14))                      # evidence 10^15 - 0.000001, but round3(A) = 10^15 does not fit DECIMAL(18,3)

    def test_signed_figures_are_checked_by_absolute_value(self):
        self.refused(self.st(p_out=bs.GRAM_LIMIT * 1000))                           # B = -10^15 g
        self.refused(self.st(p_in=bs.GRAM_LIMIT * 1000))
        self.refused(self.st(a_out=bs.GRAM_LIMIT * bs.UNITS_PER_GRAM))              # A is hugely negative (an integrity alarm elsewhere); the capacity guard still refuses
        self.refused(self.st(bound=bs.GRAM_LIMIT * bs.UNITS_PER_GRAM))

    def test_unit_columns_stay_below_ten_to_the_thirtieth(self):
        self.assertLess(bs.GRAM_LIMIT * bs.UNITS_PER_GRAM, bs.UNIT_LIMIT)
        self.assertEqual(bs.GRAM_LIMIT * bs.UNITS_PER_GRAM, 26_250_000 * 10 ** 15)

    def test_counters_and_generation(self):
        self.refused(self.st(entries=bs.COUNTER_LIMIT + 1))
        self.refused(self.st(lines=bs.COUNTER_LIMIT + 1))
        bs.check_capacity(self.st(entries=bs.COUNTER_LIMIT, lines=bs.COUNTER_LIMIT), 1)
        bs.check_capacity(self.st(), bs.GENERATION_LIMIT)
        self.refused(self.st(), bs.GENERATION_LIMIT + 1, "RECONCILIATION_GENERATION_EXHAUSTED")           # no wrap-around
        self.refused(self.st(), 0, "RECONCILIATION_GENERATION_EXHAUSTED")
        self.refused(self.st(), -1, "RECONCILIATION_GENERATION_EXHAUSTED")

    def test_a_legitimate_extreme_line_is_fine_and_build_refuses_before_serialising_when_it_does_not_fit(self):
        s = DatedSrc()
        s.add(br.BK_BUY, [("999999999.999", "F", "1000")], prices=["1.00"])
        _, _, st, *_ = s.built()
        self.assertEqual(st.a_state, rec.POSITIVE)
        with unittest.mock.patch.object(bs, "GRAM_LIMIT", 10):             # a limit smaller than the figure: refused inside build, no snapshot is formed
            with self.assertRaises(br.BookError) as refused:
                s.built()
        self.assertEqual(refused.exception.code, "RECONCILIATION_CAPACITY")


class SourceValidationTests(unittest.TestCase):
    def test_missing_or_invalid_facts_refuse_instead_of_inventing(self):
        mutations = {
            "weight missing": lambda s: s.line(1).__setitem__("weight_grams", None),
            "posted NaN": lambda s: s.line(1).__setitem__("eq21_posted", D("NaN")),
            "basis unknown": lambda s: s.line(1).__setitem__("basis", "WRONG"),
            "direction wrong": lambda s: s.line(1).__setitem__("direction", "OUT"),
            "evidence missing": lambda s: s.line(1).__setitem__("eq21_analytical", None),
            "difference wrong": lambda s: s.line(1).__setitem__("posting_difference", D("0.4")),
            "entry date missing": lambda s: s.entries[0].__setitem__("entry_date", None),
            "reversal state garbage": lambda s: s.entries[0].__setitem__("correction_id", "abc"),
            "entry not BOOK": lambda s: s.entries[0].__setitem__("trip_mode", "LEGACY"),
            "entry without an id": lambda s: s.entries.append({"id": None, "entry_kind": br.BK_ISSUE}),
        }
        for name, mutate in mutations.items():
            s = DatedSrc()
            s.add(br.BK_ISSUE, [("5.000", K, 21)])
            s.add(br.BK_RETURN, [("1.000", K, 21)])
            mutate(s)
            with self.assertRaises(bs.SettlementDataError, msg=name):
                s.built()

    def test_a_reversed_documents_lines_are_validated_too(self):
        s = DatedSrc()
        s.add(br.BK_ISSUE, [("5.000", K, 21)], reversed_=True)
        s.line(1)["eq21_posted"] = D("9.999")                          # R1 does not judge a reversed entry; the digest cannot fingerprint an invalid line
        with self.assertRaises(bs.SettlementDataError):
            s.built()

    def test_the_integer_computation_and_r1_must_agree(self):
        s = DatedSrc()
        s.add(br.BK_ISSUE, [("5.000", K, 21)])
        _, _, st, *_ = s.built()
        preview = s.preview()
        bs.match_preview(st, preview)
        preview.rounding_settlement = D("0.001")
        with self.assertRaises(bs.SettlementIntegrityError):
            bs.match_preview(st, preview)

    def test_readiness_problems(self):
        ready = DatedSrc()
        ready.add(br.BK_ISSUE, [("5.000", K, 21)])
        self.assertEqual(bs.readiness_problems(ready.preview()), [])
        negative = DatedSrc()
        negative.add(br.BK_RETURN, [("5.000", K, 21)])
        codes = [c for c, _ in bs.readiness_problems(negative.preview())]
        self.assertIn("A_STATE_NOT_SETTLEABLE", codes)
        partial = DatedSrc()
        partial.add(br.BK_ISSUE, [("5.000", K, 21)])
        partial.line(1)["weight_grams"] = None
        codes = [c for c, _ in bs.readiness_problems(partial.preview())]
        self.assertIn("BALANCE_NOT_FULL", codes)
        unreadable = DatedSrc()
        unreadable.add(br.BK_ISSUE, [("5.000", K, 21)])
        unreadable.incomplete.append(("SOURCE_LINES_UNREADABLE", "OperationalError"))
        self.assertIn("BALANCE_NOT_FULL", [c for c, _ in bs.readiness_problems(unreadable.preview())])
        unsupported = DatedSrc()
        unsupported.add(br.BK_ISSUE, [("5.000", K, 21)])
        unsupported.entries.append({"id": 99, "trip_id": TRIP, "trip_mode": "BOOK", "entry_no": 99, "entry_kind": "BK_NEXT_KIND", "correction_id": None})
        self.assertIn("UNSUPPORTED_KIND", [c for c, _ in bs.readiness_problems(unsupported.preview())])


if __name__ == "__main__":
    unittest.main()
