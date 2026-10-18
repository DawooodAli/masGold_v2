"""R2-S1 review regressions. Real services with transactional fakes and SQL text only.

No connection, migration execution or claim about actual MySQL locking/constraints.
"""
import ast
from decimal import Decimal
import re
import unittest
from unittest.mock import patch

from mysql.connector import IntegrityError
from services import book_settlement as bs
from services import book_rules as br
from services.market_trip_service import TripError
from tests import test_book_settlement_rules as rules
from tests import test_book_settlement_schema as schema
from tests.test_book_settlement_service import ReconCase


class ReplayReviewTests(ReconCase):
    def posted(self):
        trip = self.healthy_trip()
        digest, request = self.digest(trip), self.k()
        first = self.reconcile(trip, source_digest=digest, request_key=request)
        return trip, digest, request, first

    def test_expired_session_on_exact_replay_is_refused(self):
        trip, digest, request, _ = self.posted()
        before = self.state()
        with patch.object(type(self.session), "is_current", side_effect=[True, False]):
            with self.assertRaises(PermissionError):
                self.reconcile(trip, source_digest=digest, request_key=request)
        self.assertEqual(self.state(), before)

    def test_expired_session_on_duplicate_key_recovery_is_refused(self):
        trip, digest, request, _ = self.posted()
        before = self.state()
        collision = IntegrityError(msg="Duplicate entry for key 'uq_mte_request_key'", errno=1062)
        with patch.object(self.service, "_post_reconciliation", side_effect=collision):
            with patch.object(type(self.session), "is_current", side_effect=[True, False]):
                with self.assertRaises(PermissionError):
                    self.reconcile(trip, source_digest=digest, request_key=request)
        self.assertEqual(self.state(), before)

    def test_duplicate_key_recovery_locks_trip_and_replays_before_closed_state(self):
        trip, digest, request, first = self.posted()
        self.db.trips[int(trip)]["status"] = "CLOSED"
        self.db.lock_log.clear()
        collision = IntegrityError(msg="Duplicate entry for key 'uq_mte_request_key'", errno=1062)
        with patch.object(self.service, "_post_reconciliation", side_effect=collision):
            again = self.reconcile(trip, source_digest=digest, request_key=request)
        self.assertEqual(again["entry_id"], first["entry_id"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(self.db.lock_log[0], ("trip",))

    def test_original_one_gram_example_and_exact_confirmation_units(self):
        from tests.test_market_trip_book import assay, karat, K21
        trip = self.issue([karat(K21, "1")])["trip_id"]
        self.ret(trip, [assay("874.9", "1")])
        first = self.reconcile(trip, reason_code=None)
        self.assertEqual(first["a_star_units"], "3000")
        self.assertEqual(first["loss_posted"], "0.000")
        saved = self.db.audit_rows[-1]["metadata"]["confirmation_texts"]["loss_recognised_as_posted"]
        self.assertIn("3000 وحدة", saved)
        text = bs.confirmation_texts("0.000000", "0.000", 1)["loss_recognised_as_posted"]
        self.assertIn("1 وحدة", text)
        self.assertIn("1/26250000", text)

    def test_corrupt_stored_record_is_refused_on_replay(self):
        for column, bad in (("snapshot", "{invalid"), ("source_digest", "a" * 63),
                            ("a_star_units", 1), ("source_algorithm", "wrong"),
                            ("snapshot_format", "wrong"), ("trip_id", 999),
                            ("confirmed_by_user_id", 999)):
            with self.subTest(column=column):
                trip, digest, request, first = self.posted()
                self.db.reconciliations[int(first["entry_id"])][column] = bad
                before = self.state()
                with self.assertRaises(TripError) as caught:
                    self.reconcile(trip, source_digest=digest, request_key=request)
                self.assertEqual(caught.exception.code, "RECONCILIATION_MISMATCH")
                self.assertEqual(self.state(), before)

    def test_driver_json_key_order_is_accepted_on_replay(self):
        trip, digest, request, first = self.posted()
        row = self.db.reconciliations[int(first["entry_id"])]
        row["snapshot"] = rules.SnapshotTests.mysql_like(row["snapshot"])
        self.assertTrue(self.reconcile(trip, source_digest=digest, request_key=request)["duplicate"])

    def test_unrepresentable_source_numbers_are_a_diagnostic_not_an_unhandled_error(self):
        trip = self.healthy_trip()
        digest = self.digest(trip)
        self.db.book_lines[0]["weight_grams"] = Decimal("1e50")
        before = self.state()
        with self.assertRaises(TripError) as caught:
            self.reconcile(trip, source_digest=digest)
        self.assertEqual(caught.exception.code, "RECONCILIATION_NOT_READY")
        self.assertEqual(self.state(), before)

    def test_reversed_source_without_lines_is_refused(self):
        trip = self.healthy_trip()
        from tests.test_market_trip_book import karat, K21
        self.cash(trip)
        extra = self.buy(trip, [karat(K21, "1", "6000")])
        self.service.reverse(extra["entry_id"], "خطأ")
        digest = self.digest(trip)
        self.db.book_lines[:] = [line for line in self.db.book_lines if line["entry_id"] != int(extra["entry_id"])]
        before = self.state()
        with self.assertRaises(TripError) as caught:
            self.reconcile(trip, source_digest=digest)
        self.assertEqual(caught.exception.code, "RECONCILIATION_NOT_READY")
        self.assertEqual(self.state(), before)


class RecordReviewTests(unittest.TestCase):
    def test_reversed_history_requires_its_original_lines(self):
        source = rules.healthy()
        source.add(br.BK_BUY, [("1.000", rules.K, 21)], prices=["6000.00"], reversed_=True)
        stored = source.source()
        last = stored["entries"][-1]["id"]
        stored["lines"] = [line for line in stored["lines"] if line["entry_id"] != last]
        with self.assertRaises(bs.SettlementDataError):
            bs.build(stored)

    def test_identity_and_version_columns_are_checked_individually(self):
        fixture = rules.SnapshotTests()
        fixture.setUp()
        for column, bad, code in (("trip_id", fixture.trip + 1, "COLUMN_IDENTITY_MISMATCH"),
                                  ("trip_mode", "LEGACY", "COLUMN_IDENTITY_MISMATCH"),
                                  ("entry_kind", "BK_ISSUE", "COLUMN_IDENTITY_MISMATCH"),
                                  ("source_algorithm", "wrong", "SOURCE_ALGORITHM_MISMATCH"),
                                  ("snapshot_format", "wrong", "SNAPSHOT_FORMAT_MISMATCH")):
            row = fixture.row()
            row[column] = bad
            self.assertIn(code, bs.verify_record(row), column)


def sql_truth(body, row):
    """Evaluate this CHECK text using SQL UNKNOWN, without a SQL engine.

    Only the grammar needed by chk_mtr_reason is supported. CHECK rejects FALSE;
    UNKNOWN is accepted, which is exactly the distinction this regression tests.
    """
    text = re.sub(r"\bIS NOT NULL\b", "is not None", body)
    text = re.sub(r"\bIS NULL\b", "is None", text)
    for old, new in (("AND", "and"), ("OR", "or"), ("IN", "in")):
        text = re.sub(rf"\b{old}\b", new, text)
    text = re.sub(r"(?<![<>=!])=(?!=)", "==", text)
    def visit(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return row[node.id]
        if isinstance(node, ast.Tuple):
            return tuple(visit(v) for v in node.elts)
        if isinstance(node, ast.BoolOp):
            values = [visit(v) for v in node.values]
            if isinstance(node.op, ast.And):
                return False if False in values else None if None in values else True
            return True if True in values else None if None in values else False
        if isinstance(node, ast.Compare):
            left, right, op = visit(node.left), visit(node.comparators[0]), node.ops[0]
            if isinstance(op, ast.Is):
                return left is right
            if isinstance(op, ast.IsNot):
                return left is not right
            if left is None or right is None:
                return None
            if isinstance(op, ast.In):
                return left in right
            if isinstance(op, ast.Eq):
                return left == right
            if isinstance(op, ast.Gt):
                return left > right
        if isinstance(node, ast.Call):
            value = visit(node.args[0])
            if value is None:
                return None
            return {"TRIM": lambda v: v.strip(" "), "CHAR_LENGTH": len}[node.func.id](value)
        raise AssertionError(ast.dump(node))
    return visit(ast.parse(" ".join(text.split()), mode="eval").body)


class ReasonSqlReviewTests(unittest.TestCase):
    def test_null_reason_with_nonzero_loss_is_false_not_unknown(self):
        for note in (None, "", " ", "شرح"):
            self.assertIs(sql_truth(schema.check_body("chk_mtr_reason"),
                                    {"reason_code": None, "loss_posted": Decimal("0.001"), "note": note}), False)

    def test_reason_check_text_matches_policy_in_three_valued_logic(self):
        for code in (None, *bs.REASONS, "INVALID"):
            for note in (None, "", " ", "ش"):
                for loss in (Decimal("0.000"), Decimal("0.001")):
                    expected = ((code is None and loss == 0) or code in bs.REASONS)
                    expected = expected and (note is None or bool(note.strip(" ")))
                    if code in bs.NOTE_REQUIRED_REASONS:
                        expected = expected and note is not None
                    actual = sql_truth(schema.check_body("chk_mtr_reason"),
                                       {"reason_code": code, "note": note, "loss_posted": loss})
                    self.assertIs(actual, expected, (code, note, loss))


if __name__ == "__main__":
    unittest.main()


class ReverseCurrentHistoryTests(ReconCase):
    def test_locked_reversal_uses_committed_history_after_trip_lock(self):
        from services.correction_service import CorrectionError, MTE
        from tests.market_trip_fakes import Corrections
        trip = self.healthy_trip()
        entry = self.reconcile(trip)["entry_id"]
        self.service.reverse(entry, "first reversal")
        original = Corrections.corrections_for_source
        calls = []
        self.db.lock_log.clear()
        before = self.state()

        def history(repo, source_type, source_id, current=False):
            calls.append(current)
            self.assertIn(("trip",), self.db.lock_log)
            return original(repo, source_type, source_id, current=True) if current else []

        with patch.object(Corrections, "corrections_for_source", history):
            with patch.object(Corrections, "insert_correction", side_effect=AssertionError("must refuse before insert")):
                with self.assertRaises(CorrectionError) as caught:
                    self.corrections.reverse(MTE, entry, "second reversal")
        self.assertEqual(caught.exception.code, "ALREADY_REVERSED")
        self.assertEqual(calls, [True])
        self.assertEqual(self.state(), before)

    def test_preview_keeps_nonlocking_history(self):
        from services.correction_service import MTE
        from tests.market_trip_fakes import Corrections
        trip = self.healthy_trip()
        entry = self.reconcile(trip)["entry_id"]
        original = Corrections.corrections_for_source
        calls = []

        def history(repo, source_type, source_id, current=False):
            calls.append(current)
            return original(repo, source_type, source_id, current=current)

        before = self.state()
        self.db.lock_log.clear()
        with patch.object(Corrections, "corrections_for_source", history):
            self.corrections.preview_reversal(MTE, entry)
        self.assertEqual(calls, [False])
        self.assertEqual(self.db.lock_log, [])
        self.assertEqual(self.state(), before)
