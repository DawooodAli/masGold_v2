"""Explicit opt-in rehearsal checks for legacy 051 receipt history and the disabled new-receipt path.

They connect to masgold_rehearsal ONLY through the inherited rehearsal guards. No new receipt is posted: old immutable HR/RP
rows are seeded through repository insert methods solely to test exact replay, correction and the normal whole-bar return.
"""
from datetime import date
from decimal import Decimal as D
import hashlib
import json
import threading
import unittest
from uuid import uuid4
from unittest.mock import patch

from repositories.intermediary_holding_repository import (
    MOVE_RECEIPT,
    RECEIVE,
    IntermediaryHoldingRepository,
    piece_code,
    receipt_code,
)
from repositories.correction_repository import CorrectionRepository
from repositories.market_trip_repository import MarketTripRepository
from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository
from services.correction_service import CorrectionError, CorrectionService
from services.holding_receipt_service import SOURCE, HoldingReceiptService
from services.market_trip_service import TripError
from services.supplier_rules import SupplierRuleError
from tests.test_holding_settlement_mysql import ENTITLEMENT, Denying, Recording, SettleMySqlTests
from tests.test_intermediary_holdings_mysql import DATE
from tests.test_market_trip_hardening_mysql import UnlockedAuthorization


class ReceiptMySqlTests(SettleMySqlTests):
    def setUp(self):
        super().setUp()
        trip, _, rows = self.lots("2.95", "930")
        self.origin_trip = trip
        handed = self.hand(trip, rows[0])
        self.pool = int(self.query(
            "SELECT pool_id FROM intermediary_holding_documents WHERE id=%s", (handed["document_id"],)
        )[0]["pool_id"])
        allocation = self.settle("LOT", self.pool, self.s21, "1", ENTITLEMENT)
        self.reverse(allocation["id"])
        self.receiver = HoldingReceiptService(self.session, transaction_factory=self.tx, corrections=self.correct)

    def recv(self, grams, *, pool=None, key=None, service=None):
        return (service or self.receiver).receive(
            str(pool or self.pool), grams, grams, DATE, "051 ref", "051 notes", key or str(uuid4())
        )

    def stock(self, piece_id):
        return self.query(
            "SELECT COALESCE(SUM(weight_delta),0) AS g FROM receipt_stock_movements WHERE piece_id=%s",
            (piece_id,),
        )[0]["g"]

    def pool_grams(self, pool=None):
        return self.lot_balance(pool or self.pool)

    def settled(self):
        return self.query(
            "SELECT COALESCE(SUM(-weight_delta),0) AS g FROM intermediary_holding_movements "
            "WHERE pool_id=%s AND movement_type IN ('SETTLEMENT','SETTLEMENT_REVERSAL')",
            (self.pool,),
        )[0]["g"]

    def conserved(self):
        return self.pool_grams() + self.stock(self.query(
            "SELECT id FROM receipt_pieces WHERE pool_id=%s ORDER BY id", (self.pool,)
        )[0]["id"]) + self.settled()

    def rstate(self):
        tables = ("holding_receipts", "receipt_pieces", "receipt_stock_movements")
        return {**self.state(), **{table: self.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] for table in tables}}

    def legacy_receipt(self, grams="1", *, key=None):
        """Seed an immutable pre-disable HR/RP record without using the refused posting service."""
        weight = D(grams).quantize(D("0.001"))
        receipt_date = date.fromisoformat(DATE)
        reference, notes = "051 ref", "051 notes"
        key = key or str(uuid4())
        digest = hashlib.sha256(json.dumps(
            [SOURCE, self.pool, str(weight), receipt_date.isoformat(), reference, notes],
            ensure_ascii=False,
        ).encode()).hexdigest()
        with self.tx() as connection:
            repository = IntermediaryHoldingRepository(connection)
            pool = repository.pool(self.pool)
            actor = self.session.require_authenticated()
            now = MarketTripRepository(connection).db_now()
            receipt_id = repository.insert_receipt(
                pool=pool, grams=weight, receipt_date=receipt_date, reference=reference, notes=notes,
                key=key, digest=digest, actor=actor, now=now,
            )
            piece_id = repository.insert_piece(receipt_id=receipt_id, pool=pool, grams=weight)
            repository.insert_stock_movement(
                piece_id=piece_id, receipt_id=receipt_id, movement_type=RECEIVE, weight=weight, delta=weight,
            )
            repository.insert_receipt_movement(
                pool_id=self.pool, receipt_id=receipt_id, movement_type=MOVE_RECEIPT, weight=weight, delta=-weight,
            )
            repository.audit(
                actor=actor, event_type="INTERMEDIARY_HOLDING_RECEIPT", entity_id=receipt_id,
                reference=receipt_code(receipt_id), description="legacy receipt fixture",
                metadata={"piece": piece_code(piece_id), "grams": str(weight)}, now=now,
            )
        return {"receipt_id": str(receipt_id), "piece_id": str(piece_id), "key": key}

    def undo(self, receipt_id, service=None):
        return (service or self.receiver).reverse(str(receipt_id), "051 correction")

    # ------------------------------------------------------------------ disabled posting
    def test_new_lot_receipts_are_refused_for_allocated_and_unallocated_pools_without_writes(self):
        before = self.rstate()
        self.assertFalse(self.receiver.can_receive())
        with self.assertRaises(TripError) as refused:
            self.recv("1")
        self.assertEqual(refused.exception.code, "LOT_RECEIPTS_DISABLED")
        self.assertEqual(self.rstate(), before)

        _, _, _, handed = self.lot_pool("10", "875")
        fresh_pool = int(self.query(
            "SELECT pool_id FROM intermediary_holding_documents WHERE id=%s", (handed["document_id"],)
        )[0]["pool_id"])
        before = self.rstate()
        with self.assertRaises(TripError) as fresh:
            self.recv("1", pool=fresh_pool)
        self.assertEqual(fresh.exception.code, "LOT_RECEIPTS_DISABLED")
        self.assertEqual(self.rstate(), before)

    # ------------------------------------------------------------------ preserved legacy history
    def test_exact_legacy_replay_precedes_disable_and_remains_replayable_after_reversal(self):
        receipt = self.legacy_receipt()
        before = self.rstate()
        replay = self.recv("1", key=receipt["key"])
        self.assertEqual((replay["duplicate"], replay["receipt_id"], replay["piece_id"]),
                         (True, receipt["receipt_id"], receipt["piece_id"]))
        self.assertEqual(self.rstate(), before)

        self.undo(receipt["receipt_id"])
        before = self.rstate()
        replay = self.recv("1", key=receipt["key"])
        self.assertEqual((replay["duplicate"], replay["receipt_id"], replay["piece_id"]),
                         (True, receipt["receipt_id"], receipt["piece_id"]))
        self.assertEqual(self.rstate(), before)
        with self.assertRaises(TripError) as changed:
            self.recv("1.5", key=receipt["key"])
        self.assertEqual(changed.exception.code, "REQUEST_CONFLICT")
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM holding_receipts WHERE request_key=%s", (receipt["key"],))[0]["n"], 1)

        review = self.holding.return_review(self.pool)
        self.assertEqual(review["physical_event_ids"], [f"HR:{receipt['receipt_id']}/RP:{receipt['piece_id']}"])
        with self.assertRaises(TripError) as acknowledgement:
            self.back(self.pool)
        self.assertEqual(acknowledgement.exception.code, "PHYSICAL_EVENT_DECLARATION_REQUIRED")
        self.assertEqual(self.back(self.pool, physical_confirmed=True)["kind"], "HOLD_RETURN")

    def test_legacy_receipt_reversal_keeps_its_identity_and_requires_the_canonical_rights(self):
        receipt = self.legacy_receipt()
        before = self.rstate()
        for denied in ("TRANSACTION_CORRECT", "INTERMEDIARY_HOLDING_REVERSE"):
            correction = CorrectionService(self.session, authorization=Denying(self.session, denied), transaction_factory=self.tx)
            service = HoldingReceiptService(self.session, transaction_factory=self.tx, corrections=correction)
            with self.assertRaises(PermissionError):
                self.undo(receipt["receipt_id"], service)
            self.assertEqual(self.rstate(), before)

        recording = Recording(self.session)
        correction = CorrectionService(self.session, authorization=recording, transaction_factory=self.tx)
        self.undo(receipt["receipt_id"], HoldingReceiptService(self.session, transaction_factory=self.tx, corrections=correction))
        self.assertEqual(set(recording.codes), {"TRANSACTION_CORRECT", "INTERMEDIARY_HOLDING_REVERSE"})
        self.assertEqual(self.pool_grams(), D("2.950"))
        self.assertEqual(self.stock(receipt["piece_id"]), D("0.000"))
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM holding_receipts WHERE id=%s", (receipt["receipt_id"],))[0]["n"], 1)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM receipt_pieces WHERE id=%s AND receipt_id=%s",
                                    (receipt["piece_id"], receipt["receipt_id"]))[0]["n"], 1)
        self.assertEqual(self.query(
            "SELECT COUNT(*) AS n FROM transaction_corrections WHERE source_type='HOLDING_RECEIPT' AND source_id=%s",
            (receipt["receipt_id"],),
        )[0]["n"], 1)

    def test_receipt_reversal_audit_failure_rolls_back_the_correction_and_stock_movements(self):
        receipt = self.legacy_receipt()
        before = self.rstate()
        with patch.object(CorrectionService, "_audit", side_effect=RuntimeError("audit failure")):
            with self.assertRaisesRegex(RuntimeError, "audit failure"):
                self.undo(receipt["receipt_id"])
        self.assertEqual(self.rstate(), before)
        self.assertEqual(self.pool_grams(), D("1.950"))
        self.assertEqual(self.stock(receipt["piece_id"]), D("1.000"))

    def test_the_reversal_requires_the_full_received_stock_from_a_real_sql_read(self):
        receipt = self.legacy_receipt()
        # FIXTURE LIMITATION: no supported flow consumes receipt stock and the schema admits only RECEIVE / RECEIVE_REVERSAL movements, so a future
        # consumer is simulated by removing the seeded RECEIVE row in this disposable rehearsal database (the same fixture the posting-era test used).
        # This is NOT a supported stock-consumption flow; it only makes the SQL-backed stock read (SUM(weight_delta)) return less than the receipt.
        with self.tx() as connection:
            cursor = connection.cursor()
            cursor.execute("DELETE FROM receipt_stock_movements WHERE piece_id=%s AND movement_type='RECEIVE'", (receipt["piece_id"],))
            removed = cursor.rowcount
            cursor.close()
        self.assertEqual(removed, 1)
        self.assertEqual(self.stock(receipt["piece_id"]), D("0.000"))
        before = self.rstate()
        corrections = self.query("SELECT COUNT(*) AS n FROM transaction_corrections")[0]["n"]
        with self.assertRaises(CorrectionError) as missing:
            self.undo(receipt["receipt_id"])
        self.assertEqual(missing.exception.code, "RECEIPT_STOCK_NOT_PRESENT")
        self.assertEqual(self.rstate(), before)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM transaction_corrections")[0]["n"], corrections)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM transaction_corrections WHERE source_type='HOLDING_RECEIPT' AND source_id=%s",
                                    (receipt["receipt_id"],))[0]["n"], 0)
        self.assertEqual(self.pool_grams(), D("1.950"))                                   # the receipt movement of the pool is untouched

    def test_exact_canonical_reversal_effects_keep_the_original_movements_and_the_identity(self):
        receipt = self.legacy_receipt("1")
        piece, rid = receipt["piece_id"], receipt["receipt_id"]

        def movements():
            stock = self.query("SELECT id,movement_type,weight_delta,correction_id FROM receipt_stock_movements WHERE piece_id=%s ORDER BY id", (piece,))
            pool = self.query("SELECT id,movement_type,weight_delta,correction_id FROM intermediary_holding_movements WHERE receipt_id=%s ORDER BY id", (rid,))
            return stock, pool

        def identity():
            return self.query("SELECT r.id AS receipt_id, p.id AS piece_id, r.request_key, r.request_hash, r.grams, r.pool_id, r.lot_id "
                              "FROM holding_receipts r JOIN receipt_pieces p ON p.receipt_id=r.id WHERE r.id=%s", (rid,))

        original_stock, original_pool = movements()
        original_identity = identity()
        result = self.undo(rid)
        stock, pool = movements()
        self.assertEqual([(m["movement_type"], m["weight_delta"]) for m in stock], [("RECEIVE", D("1.000")), ("RECEIVE_REVERSAL", D("-1.000"))])
        self.assertEqual([(m["movement_type"], m["weight_delta"]) for m in pool], [("RECEIPT", D("-1.000")), ("RECEIPT_REVERSAL", D("1.000"))])
        self.assertEqual({int(stock[1]["correction_id"]), int(pool[1]["correction_id"])}, {int(result["correction_id"])})
        self.assertEqual((stock[0], pool[0]), (original_stock[0], original_pool[0]))      # the originals are byte-for-byte unchanged
        self.assertEqual((stock[0]["correction_id"], pool[0]["correction_id"]), (None, None))
        self.assertEqual(identity(), original_identity)                                   # HR / RP identity, key, hash and pool link preserved
        correction = self.query("SELECT source_type, source_id, correction_type FROM transaction_corrections WHERE id=%s", (result["correction_id"],))[0]
        self.assertEqual((correction["source_type"], str(correction["source_id"]), correction["correction_type"]),
                         ("HOLDING_RECEIPT", str(rid), "FULL_REVERSAL"))
        self.assertEqual((self.pool_grams(), self.stock(piece), self.conserved()), (D("2.950"), D("0.000"), D("2.950")))
        with self.assertRaises(CorrectionError) as again:
            self.undo(rid)
        self.assertEqual(again.exception.code, "ALREADY_REVERSED")
        second = self.legacy_receipt("0.500")
        with self.assertRaises(CorrectionError) as reason:
            self.receiver.reverse(second["receipt_id"], "")
        self.assertEqual(reason.exception.code, "REASON_REQUIRED")

    def test_legacy_request_key_integrity_replay_payload_actor_and_cross_flow_in_both_directions(self):
        receipt = self.legacy_receipt("1")
        key = receipt["key"]
        # legitimate setup (a second actor and a custody trip WRITE rows) happens BEFORE the snapshot the rejections are compared with
        other, _ = self.make_session("051-replay-actor", "OWNER")
        trip = self.trip("20")
        before = self.rstate()
        again = self.recv("1", key=key)
        self.assertEqual((again["duplicate"], again["receipt_id"], again["piece_id"]), (True, receipt["receipt_id"], receipt["piece_id"]))
        self.assertEqual(self.rstate(), before)
        with self.assertRaises(TripError) as payload:
            self.recv("1.5", key=key)
        self.assertEqual(payload.exception.code, "REQUEST_CONFLICT")
        self.assertEqual(self.rstate(), before)
        with self.assertRaises(TripError) as actor:
            self.recv("1", key=key, service=HoldingReceiptService(other, transaction_factory=self.tx, authorization=UnlockedAuthorization(other)))
        self.assertEqual(actor.exception.code, "REQUEST_CONFLICT")
        self.assertEqual(self.rstate(), before)
        # a receipt key reused by an SG flow, an IG flow or the whole-bar return
        for call in (lambda: self.settle("LOT", self.pool, self.s21, "1", ENTITLEMENT, key=key),
                     lambda: self.put(trip, "1", key=key),
                     lambda: self.back(self.pool, key=key, physical_confirmed=True)):
            with self.assertRaises((TripError, SupplierRuleError)) as conflict:
                call()
            self.assertEqual(conflict.exception.code, "REQUEST_CONFLICT")
            self.assertEqual(self.rstate(), before)
        # a holding / settlement / custody key reused by the receipt: the legitimate write comes first, the snapshot right after it
        used = str(uuid4())
        self.put(trip, "1", key=used)
        before = self.rstate()
        with self.assertRaises(TripError) as ig:
            self.recv("1", key=used)
        self.assertEqual(ig.exception.code, "REQUEST_CONFLICT")
        self.assertEqual(self.rstate(), before)
        sg_key = str(uuid4())
        self.settle("LOT", self.pool, self.supplier, "0.5", ENTITLEMENT, key=sg_key)
        before = self.rstate()
        with self.assertRaises(TripError) as sg:
            self.recv("0.5", key=sg_key)
        self.assertEqual(sg.exception.code, "REQUEST_CONFLICT")
        self.assertEqual(self.rstate(), before)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM holding_receipts WHERE request_key IN (%s,%s)", (used, sg_key))[0]["n"], 0)

    def test_settlement_reversal_after_a_legacy_receipt_preserves_conservation(self):
        settlement = self.settle("LOT", self.pool, self.s21, "1", ENTITLEMENT)
        receipt = self.legacy_receipt("1.950")
        self.assertEqual((self.pool_grams(), self.stock(receipt["piece_id"]), self.settled(), self.conserved()),
                         (D("0.000"), D("1.950"), D("1.000"), D("2.950")))

        self.reverse(settlement["id"])
        self.assertEqual((self.pool_grams(), self.stock(receipt["piece_id"]), self.settled(), self.conserved()),
                         (D("1.000"), D("1.950"), D("0.000"), D("2.950")))

    def test_rr_snapshot_second_reversal_uses_the_committed_correction_state(self):
        receipt = self.legacy_receipt()
        with self.tx() as reader:
            cursor = reader.cursor()
            cursor.execute("SELECT COUNT(*) FROM transaction_corrections")
            cursor.fetchone()
            cursor.close()
            self.undo(receipt["receipt_id"])
            service = HoldingReceiptService(
                self.session,
                transaction_factory=lambda: self.on_connection(reader),
                corrections=CorrectionService(self.session, transaction_factory=lambda: self.on_connection(reader)),
            )
            with self.assertRaises(CorrectionError) as twice:
                service.reverse(receipt["receipt_id"], "محاولة عكس ثانية")
            self.assertEqual(twice.exception.code, "ALREADY_REVERSED")
            reader.rollback()
        self.assertEqual((self.pool_grams(), self.stock(receipt["piece_id"])), (D("2.950"), D("0.000")))

    def test_real_lock_order_for_receipt_reversal(self):
        receipt = self.legacy_receipt()
        order = []

        def wrap(cls, name, label, locking_only=False):
            original = getattr(cls, name)

            def wrapper(instance, *args, **kwargs):
                if not locking_only or kwargs.get("lock"):
                    order.append(label(args) if callable(label) else label)
                return original(instance, *args, **kwargs)
            return patch.object(cls, name, wrapper)

        patches = (
            wrap(MarketTripRepository, "trip", "trip", True),
            wrap(MarketTripRepository, "lock_lots", "lots"),
            wrap(SupplierGoldSettlementRepository, "get_supplier", lambda args: f"party:{int(args[0])}", True),
            wrap(IntermediaryHoldingRepository, "lock_pool", "pool"),
            wrap(IntermediaryHoldingRepository, "lock_piece", "piece"),
        )
        for item in patches:
            item.start()
        try:
            self.undo(receipt["receipt_id"])
            self.assertEqual(order, ["lots", f"party:{int(self.party)}", "pool", "piece"])
            self.assertNotIn("trip", order)
        finally:
            for item in reversed(patches):
                item.stop()

    def test_a_receipt_reversal_waiting_on_the_piece_already_holds_prior_resources(self):
        receipt = self.legacy_receipt()
        results = {}
        with self.tx() as holder:
            cursor = holder.cursor()
            cursor.execute("SELECT id FROM receipt_pieces WHERE id=%s FOR UPDATE", (receipt["piece_id"],))
            cursor.fetchall()
            cursor.close()
            service = HoldingReceiptService(
                self.session,
                transaction_factory=self.tx,
                corrections=CorrectionService(
                    self.session, transaction_factory=self.tx, authorization=UnlockedAuthorization(self.session)
                ),
            )
            worker = self.thread(results, "receipt-reversal", lambda: self.undo(receipt["receipt_id"], service=service))
            try:
                self.wait_waiting("receipt_pieces")
                with self.tx() as observer:
                    lot_lock = self.probe(observer, "SELECT id FROM market_trip_lots ORDER BY id DESC LIMIT 1 FOR UPDATE NOWAIT")
                    party_lock = self.probe(observer, "SELECT id FROM suppliers WHERE id=%s FOR UPDATE NOWAIT", (int(self.party),))
                    pool_lock = self.probe(observer, "SELECT id FROM intermediary_holding_pools WHERE id=%s FOR UPDATE NOWAIT", (self.pool,))
                    trip_lock = self.probe(observer, "SELECT id FROM market_trips WHERE id=%s FOR UPDATE NOWAIT",
                                            (self.origin_trip["trip_id"],))
            finally:
                holder.commit()
                worker.join(60)
        self.assertEqual((lot_lock, party_lock, pool_lock), (3572, 3572, 3572))
        self.assertIsNone(trip_lock)
        self.assertIsInstance(results["receipt-reversal"], dict, results["receipt-reversal"])

    def test_two_reversals_racing_on_one_legacy_receipt_reverse_it_once(self):
        receipt = self.legacy_receipt()
        sessions = [self.make_session(f"receipt-undo{i}", "OWNER")[0] for i in range(2)]
        services = [
            HoldingReceiptService(
                session,
                transaction_factory=self.tx,
                corrections=CorrectionService(
                    session, transaction_factory=self.tx, authorization=UnlockedAuthorization(session)
                ),
            )
            for session in sessions
        ]
        barrier, results = threading.Barrier(2), {}

        def race(index):
            def call():
                barrier.wait(timeout=10)
                return self.undo(receipt["receipt_id"], service=services[index])
            return call

        workers = [self.thread(results, "A", race(0)), self.thread(results, "B", race(1))]
        for worker in workers:
            worker.join(60)
        outcomes = list(results.values())
        self.assertEqual(sum(isinstance(result, dict) for result in outcomes), 1, outcomes)
        self.assertEqual(sum(isinstance(result, CorrectionError) and result.code == "ALREADY_REVERSED" for result in outcomes), 1, outcomes)
        self.assertEqual((self.pool_grams(), self.stock(receipt["piece_id"]), self.conserved()),
                         (D("2.950"), D("0.000"), D("2.950")))

    def test_rollback_after_each_persistent_stage_of_legacy_receipt_reversal(self):
        receipt = self.legacy_receipt()

        def inject_after(cls, name):
            original = getattr(cls, name)

            def fail(instance, *args, **kwargs):
                original(instance, *args, **kwargs)
                raise RuntimeError("051 reversal injected")
            return patch.object(cls, name, fail)

        stages = (
            (CorrectionRepository, "insert_correction"),
            (IntermediaryHoldingRepository, "insert_stock_movement"),
            (IntermediaryHoldingRepository, "insert_receipt_movement"),
        )
        for cls, name in stages:
            before = self.rstate()
            with self.subTest(stage=name), inject_after(cls, name), self.assertRaisesRegex(RuntimeError, "051 reversal injected"):
                self.undo(receipt["receipt_id"])
            self.assertEqual(self.rstate(), before, name)

        original_audit = CorrectionService._audit

        def fail_after_audit(*args, **kwargs):
            original_audit(*args, **kwargs)
            raise RuntimeError("051 reversal audit injected")

        before = self.rstate()
        with patch.object(CorrectionService, "_audit", staticmethod(fail_after_audit)):
            with self.assertRaisesRegex(RuntimeError, "051 reversal audit injected"):
                self.undo(receipt["receipt_id"])
        self.assertEqual(self.rstate(), before, "audit")
        self.assertEqual((self.pool_grams(), self.stock(receipt["piece_id"])), (D("1.950"), D("1.000")))


def load_tests(loader, tests, pattern):
    """Run only these legacy-receipt checks; the inherited trip/settlement cases live in their own modules."""
    suite = unittest.TestSuite()
    suite.addTests(ReceiptMySqlTests(name) for name in loader.getTestCaseNames(ReceiptMySqlTests)
                   if name in ReceiptMySqlTests.__dict__)
    return suite


if __name__ == "__main__":
    unittest.main()
