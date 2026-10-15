"""Physical receipts from an intermediary's holdings (051). Real HoldingReceiptService, HoldingSettlementService, IntermediaryHoldingService and CorrectionService over
the in-memory transactional database of tests/market_trip_fakes.py (a failed transaction restores every table; the 051 CHECK / unique / composite keys are mirrored as
asserts). NOT real-MySQL tests: the SQL, the constraints and the lock behaviour were exercised on masgold_rehearsal (tests/test_holding_receipt_mysql.py, opt-in).

The example is SG000006: a lot of 2.950 g at 930 per mille, 1.000 g allocated (GRAM_ENTITLEMENT) and the allocation reversed. Every other weight is hypothetical."""
import copy
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal as D
from unittest.mock import patch
import unittest

from mysql.connector import IntegrityError

from services.correction_service import CorrectionError
from repositories.intermediary_holding_repository import CONFIRMATION_CODE, MOVE_RECEIPT, PHYSICAL_EVENTS_DECLARATION_CODE, RECEIVE
from services.holding_receipt_service import SOURCE as HOLDING_SOURCE, HoldingReceiptService
from services.market_trip_service import TripError
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import HoldingRepo, OWNER, SettlementRepo, TripRepo, session_for
from tests.test_holding_settlement import DIVIDED, ENTITLEMENT, S18_INACTIVE, S21, SettleCase, WHOLE, skey
from tests.test_intermediary_holdings import OWNER_PARTY, hkey
from tests.test_market_trips import K21, TODAY, key


class ReceiptCase(SettleCase):
    """SG000006 setup: a 2.950 g lot at 930 handed to the intermediary, 1.000 g allocated and the allocation reversed (balance back to 2.950)."""

    def setUp(self):
        super().setUp()
        self.receiver = HoldingReceiptService(self.session, transaction_factory=self.db.transaction, repository=TripRepo, holdings=HoldingRepo, parties=SettlementRepo,
                                              authorization=self.authorization, corrections=self.corrections)
        self.n += 1
        melt = self.service.melt(self.logo_trip, [{"karat_id": K21, "weight": "3"}], TODAY, "ورشة", {"weight": "2.95", "fineness": "930"}, [], "", "", "",
                                 key(960 + self.n))
        self.lot2 = next(l["id"] for l in self.db.lots.values() if l["entry_id"] == int(melt["entry_id"]))
        self.pool = int(self.hand(self.lot2, trip=self.logo_trip)["pool_id"])
        self.sg = self.sett("LOT", self.pool, S21, "1", ENTITLEMENT)
        self.reverse(self.sg["id"])
        self.baseline = self.untouched()
        self.r = 0

    # ------------------------------------------------------------ helpers
    def recv(self, grams, measured=None, pool=None, request=None, service=None, when=TODAY, reference="", notes=""):
        self.r += 1
        return (service or self.receiver).receive(str(pool or self.pool), grams, grams if measured is None else measured, when, reference, notes,
                                                  request or "55555555-5555-4555-8555-%012d" % self.r)

    def legacy_receipt(self, grams="1", pool=None, request=None, when=TODAY, reference="", notes=""):
        """Seed an immutable pre-disable HR/RP record without exercising the disabled posting path."""
        self.r += 1
        pool_id = int(pool or self.pool)
        request = request or "55555555-5555-4555-8555-%012d" % self.r
        weight = D(grams).quantize(D("0.001"))
        receipt_date = date.fromisoformat(when)
        reference, notes = reference.strip() or None, notes.strip() or None
        digest = hashlib.sha256(json.dumps(
            [HOLDING_SOURCE, pool_id, str(weight), receipt_date.isoformat(), reference, notes],
            ensure_ascii=False,
        ).encode()).hexdigest()
        repository = HoldingRepo(self.db)
        actor = self.session.require_authenticated()
        now = datetime.now()
        pool_row = repository.pool(pool_id)
        receipt_id = repository.insert_receipt(pool=pool_row, grams=weight, receipt_date=receipt_date, reference=reference,
                                               notes=notes, key=request, digest=digest, actor=actor, now=now)
        piece_id = repository.insert_piece(receipt_id=receipt_id, pool=pool_row, grams=weight)
        repository.insert_stock_movement(piece_id=piece_id, receipt_id=receipt_id, movement_type=RECEIVE, weight=weight, delta=weight)
        repository.insert_receipt_movement(pool_id=pool_id, receipt_id=receipt_id, movement_type=MOVE_RECEIPT,
                                            weight=weight, delta=-weight)
        return {"receipt_id": str(receipt_id), "code": f"HR{receipt_id:06d}", "piece_id": str(piece_id),
                "piece_code": f"RP{piece_id:06d}", "pool_id": str(pool_id), "grams": str(weight),
                "remaining": str(repository.pool_balance(pool_id)), "duplicate": False}

    def stock(self, piece=None):
        repo = HoldingRepo(self.db)
        if piece is not None:
            return repo.piece_stock(piece)
        return sum((m["weight_delta"] for m in self.db.stock_moves), D("0.000"))

    def pool_grams(self, pool=None):
        return HoldingRepo(self.db).pool_balance(pool or self.pool)

    def effective_settled(self, pool=None):
        total = D("0.000")
        for s in self.db.settlements.values():
            if s.get("lot_pool_id") == (pool or self.pool) and not any(c["source_type"] == "SUPPLIER_GOLD_SETTLEMENT" and c["source_id"] == str(s["id"])
                                                                         for c in self.db.corrections):
                total += s["delivered_weight_grams"]
        return total

    def conserved(self):
        """holding + shop receipt stock + effective supplier settlements == what was handed over: nothing is created or lost."""
        return self.pool_grams() + self.stock() + self.effective_settled()

    def receipt_reversal(self, receipt_id, reason="تصحيح"):
        return self.receiver.reverse(receipt_id, reason)


# ====================================================================================================================== disabled new posting
class ReceiptTests(ReceiptCase):
    def test_new_lot_receipts_are_disabled_without_writing(self):
        before = self.state()
        self.assertFalse(self.receiver.can_receive())
        self.refuse("LOT_RECEIPTS_DISABLED", lambda: self.recv("1"))
        self.assertEqual(self.state(), before)

    def test_new_lot_receipts_are_disabled_even_without_allocation_history(self):
        fresh = self.lot_pool(self.residue)
        self.refuse("LOT_RECEIPTS_DISABLED", lambda: self.recv("1", pool=fresh))
        self.assertEqual(self.db.receipts, {})

    def test_exact_legacy_receipt_replay_survives_reversal_and_new_payloads_conflict(self):
        request = "55555555-5555-4555-8555-000000000100"
        original = self.legacy_receipt("1", request=request)
        before = self.state()
        replay = self.recv("1", request=request)
        self.assertEqual((replay["duplicate"], replay["receipt_id"], replay["piece_id"]),
                         (True, original["receipt_id"], original["piece_id"]))
        self.assertEqual(self.state(), before)
        self.receipt_reversal(original["receipt_id"])
        replay_after_reverse = self.recv("1", request=request)
        self.assertEqual((replay_after_reverse["duplicate"], replay_after_reverse["receipt_id"]),
                         (True, original["receipt_id"]))
        self.assertEqual(len(self.db.receipts), 1)
        self.refuse("REQUEST_CONFLICT", lambda: self.recv("1.5", request=request))

    def test_reversed_physical_division_requires_an_event_specific_declaration_and_is_audited(self):
        divided = self.sett("LOT", self.pool, S21, "2.950", DIVIDED)
        self.reverse(divided["id"])
        review = self.holding.return_review(self.pool)
        self.assertTrue(review["requires_physical_declaration"])
        self.refuse("PHYSICAL_EVENT_DECLARATION_REQUIRED",
                    lambda: self.back(self.pool, review_token=review["token"]))
        before = self.state()
        for step in ("audit", "holding_audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.back(self.pool, physical_confirmed=True, review_token=review["token"])
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)
        result = self.back(self.pool, physical_confirmed=True, review_token=review["token"])
        metadata = self.db.audit_rows[-1]["metadata"]["physical_event_review"]
        self.assertEqual(result["kind"], "HOLD_RETURN")
        self.assertEqual(metadata["physical_event_ids"], [f"SG:{divided['id']}"])
        self.assertTrue(metadata["same_original_bar_intact_and_undivided"])
        self.assertTrue(metadata["physical_events_recorded_in_error_and_did_not_occur"])
        self.assertEqual(metadata["events"], review["events"])

    def test_reversed_receipt_requires_event_declaration_and_active_receipt_cannot_return(self):
        receipt = self.legacy_receipt("0.500")
        self.refuse("HOLDING_PARTIAL", lambda: self.back(self.pool))
        self.receipt_reversal(receipt["receipt_id"])
        review = self.holding.return_review(self.pool)
        self.refuse("PHYSICAL_EVENT_DECLARATION_REQUIRED",
                    lambda: self.back(self.pool, review_token=review["token"]))
        result = self.back(self.pool, physical_confirmed=True, review_token=review["token"])
        metadata = self.db.audit_rows[-1]["metadata"]["physical_event_review"]
        self.assertEqual(result["kind"], "HOLD_RETURN")
        self.assertEqual(metadata["physical_event_ids"], [f"HR:{receipt['receipt_id']}/RP:{receipt['piece_id']}"])
        self.assertEqual(len(self.db.receipts), 1)

    def test_changed_event_list_requires_a_new_owner_confirmation(self):
        review = self.holding.return_review(self.pool)
        new_settlement = self.sett("LOT", self.pool, S21, "1", ENTITLEMENT)
        self.reverse(new_settlement["id"])
        self.refuse("RETURN_REVIEW_CHANGED",
                    lambda: self.back(self.pool, review_token=review["token"]))
        fresh = self.holding.return_review(self.pool)
        self.assertNotEqual(fresh["token"], review["token"])
        self.assertEqual(self.back(self.pool, review_token=fresh["token"])["kind"], "HOLD_RETURN")


# ====================================================================================================================== reversal
class ReversalTests(ReceiptCase):
    def test_a_reversal_removes_the_stock_and_restores_the_stored_holding_grams_atomically_under_one_correction(self):
        done = self.legacy_receipt("1")
        result = self.receipt_reversal(done["receipt_id"])
        self.assertEqual((self.pool_grams(), self.stock(), self.conserved()), (D("2.950"), D("0.000"), D("2.950")))
        moves = [m for m in self.db.holding_moves if m.get("receipt_id")]
        stock = [m for m in self.db.stock_moves]
        self.assertEqual([(m["movement_type"], m["weight_delta"]) for m in moves], [("RECEIPT", D("-1.000")), ("RECEIPT_REVERSAL", D("1.000"))])
        self.assertEqual([(m["movement_type"], m["weight_delta"]) for m in stock], [("RECEIVE", D("1.000")), ("RECEIVE_REVERSAL", D("-1.000"))])
        self.assertEqual({moves[1]["correction_id"], stock[1]["correction_id"]}, {int(result["correction_id"])})       # both histories share ONE record
        self.assertEqual((moves[0]["correction_id"], stock[0]["correction_id"]), (None, None))                          # originals stay counted, unrewritten
        self.assertEqual(self.db.corrections[-1]["source_type"], "HOLDING_RECEIPT")
        self.assertEqual(len(self.db.receipts), 1)                                                                      # the HR document and the piece stay
        self.refuse("PHYSICAL_EVENT_DECLARATION_REQUIRED", lambda: self.back(self.pool))

    def test_double_reversal_reason_and_the_stored_grams_are_enforced(self):
        done = self.legacy_receipt("1")
        self.refuse("REASON_REQUIRED", lambda: self.receipt_reversal(done["receipt_id"], ""), CorrectionError)
        self.receipt_reversal(done["receipt_id"])
        self.refuse("ALREADY_REVERSED", lambda: self.receipt_reversal(done["receipt_id"]), CorrectionError)
        self.assertEqual(self.conserved(), D("2.950"))
        again = self.legacy_receipt("1")                                                        # legacy documents preserve each distinct receipt-piece identity
        self.assertEqual((again["piece_code"], again["code"]), ("RP000002", "HR000002"))

    def test_the_reversal_requires_the_full_received_stock_and_locks_the_piece_before_reading_it(self):
        done = self.legacy_receipt("1")
        self.db.stock_moves.append({"id": 900, "piece_id": int(done["piece_id"]), "receipt_id": int(done["receipt_id"]), "movement_type": "FUTURE_USE",
                                    "weight_grams": D("0.400"), "weight_delta": D("-0.400"), "correction_id": None})      # what a future consumer would post
        self.db.lock_log.clear()
        error = self.refuse("RECEIPT_STOCK_NOT_PRESENT", lambda: self.receipt_reversal(done["receipt_id"]), CorrectionError)
        self.assertIn("0.600", str(error))
        self.assertEqual(self.db.lock_log, [("lots", (self.lot2,)), ("party", 20), ("pool", self.pool), ("piece", int(done["piece_id"]))])      # lot -> party -> pool -> piece
        self.assertEqual(self.pool_grams(), D("1.950"))
        self.db.stock_moves.pop()
        self.receipt_reversal(done["receipt_id"])                                                # once the stock is whole again it reverses

    def test_settlement_reversal_after_receipts_keeps_conservation_and_the_pool_inside_its_bounds(self):
        live = self.sett("LOT", self.pool, S21, "1", ENTITLEMENT)                                # 1.000 g effective with the supplier, balance 1.950
        self.legacy_receipt("1.95")                                                              # legacy receipt consumes the remainder
        self.assertEqual((self.pool_grams(), self.stock(), self.effective_settled(), self.conserved()), (D("0.000"), D("1.950"), D("1.000"), D("2.950")))
        self.reverse(live["id"])                                                                 # the supplier credit is undone: entitlement 1.000 is back
        self.assertEqual((self.pool_grams(), self.stock(), self.effective_settled(), self.conserved()), (D("1.000"), D("1.950"), D("0.000"), D("2.950")))
        self.legacy_receipt("1")
        self.assertEqual((self.pool_grams(), self.stock()), (D("0.000"), D("2.950")))
        for receipt in sorted(self.db.receipts, reverse=True):
            self.receipt_reversal(receipt)
        self.assertEqual((self.pool_grams(), self.stock(), self.conserved()), (D("2.950"), D("0.000"), D("2.950")))

    def test_a_reversal_that_would_push_the_pool_above_its_initial_quantity_is_refused(self):
        done = self.legacy_receipt("1")
        self.db.holding_moves.append({"id": 901, "pool_id": self.pool, "document_id": None, "doc_kind": None, "settlement_id": None, "receipt_id": None,
                                      "movement_type": "RETURN_REVERSAL", "weight_grams": D("2.000"), "weight_delta": D("2.000"), "correction_id": None})
        self.refuse("HOLDING_BALANCE_INVALID", lambda: self.receipt_reversal(done["receipt_id"]), CorrectionError)

    def test_a_closed_origin_custody_neither_blocks_nor_is_reopened(self):
        self.db.trips[int(self.logo_trip)]["status"] = "CLOSED"
        self.db.lock_log.clear()
        done = self.legacy_receipt("1")
        self.receipt_reversal(done["receipt_id"])
        self.assertNotIn(("trip",), self.db.lock_log)
        self.assertEqual(self.db.trips[int(self.logo_trip)]["status"], "CLOSED")
        self.assertEqual(self.pool_grams(), D("2.950"))

    def test_the_failure_of_any_reversal_stage_leaves_no_trace(self):
        done = self.legacy_receipt("1")
        for step in ("stock_movement", "receipt_movement"):
            self.db.fail_at = step
            before = self.state()
            with self.assertRaises(RuntimeError):
                self.receipt_reversal(done["receipt_id"])
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)
        self.assertEqual(self.conserved(), D("2.950"))


# ====================================================================================================================== rights, parties, replay, locks
class RightsAndPartyTests(ReceiptCase):
    def test_every_right_is_rechecked_in_the_transaction_and_the_reversal_needs_exactly_the_agreed_set(self):
        base = self.state()
        self.authorization.permissions = OWNER - {"INTERMEDIARY_HOLDING_MANAGE"}                  # the session still shows the right: only the database says no
        with self.assertRaises(PermissionError):
            self.recv("1")
        self.assertEqual(self.state(), base)
        self.authorization.permissions = OWNER
        self.refuse("LOT_RECEIPTS_DISABLED", lambda: self.recv("1"))
        done = self.legacy_receipt("1")
        base = self.state()
        for missing in ("TRANSACTION_CORRECT", "INTERMEDIARY_HOLDING_REVERSE"):
            self.authorization.permissions = OWNER - {missing}
            with self.assertRaises(PermissionError):
                self.receipt_reversal(done["receipt_id"])
            self.assertEqual(self.state(), base, missing)
        self.authorization.permissions = OWNER
        self.assertEqual(self.writes(lambda: self.receipt_reversal(done["receipt_id"])), {"TRANSACTION_CORRECT", "INTERMEDIARY_HOLDING_REVERSE"})
        weak = session_for(frozenset({"APP_LOGIN", "SUPPLIER_GOLD_SETTLE", "INTERMEDIARY_HOLDING_VIEW"}))                          # no holding MANAGE right
        service = HoldingReceiptService(weak, transaction_factory=self.db.transaction, repository=TripRepo, holdings=HoldingRepo, parties=SettlementRepo,
                                        authorization=FakeAuthorization(weak), corrections=self.corrections)
        with self.assertRaises(PermissionError):
            service.receive(str(self.pool), "1", "1", TODAY, "", "", "55555555-5555-4555-8555-999999999999")
        with self.assertRaises(PermissionError):
            service.reverse(done["receipt_id"], "x")
        self.assertFalse(service.can_receive() or service.can_reverse())

    def test_no_new_permission_exists_in_the_receipt_flow(self):
        import services.holding_receipt_service as module
        from pathlib import Path
        self.assertEqual({module.MANAGE_PERMISSION, module.REVERSE_PERMISSION, module.VIEW_PERMISSION, module.CORRECT_PERMISSION},
                         {"INTERMEDIARY_HOLDING_MANAGE", "INTERMEDIARY_HOLDING_REVERSE", "INTERMEDIARY_HOLDING_VIEW", "TRANSACTION_CORRECT"})
        migration = (Path(module.__file__).resolve().parents[1] / "database" / "schema" / "051_intermediary_physical_receipts.sql").read_text(encoding="utf-8")
        self.assertNotIn("INSERT INTO permissions", migration)

    def test_an_inactive_holder_is_resolved_and_the_history_keeps_the_party_and_its_role(self):
        self.db.suppliers[20]["is_active"] = 0
        self.legacy_receipt("1")
        self.assertGreater(HoldingRepo(self.db).history_count(20), 3)
        self.db.suppliers[20]["is_active"] = 1

    def test_exact_replay_returns_the_original_records_even_after_reversal_and_conflicts_are_refused(self):
        first = self.legacy_receipt("1", request="55555555-5555-4555-8555-000000000100")
        state = self.state()
        again = self.recv("1", request="55555555-5555-4555-8555-000000000100")
        self.assertEqual((again["duplicate"], again["receipt_id"], again["piece_id"]), (True, first["receipt_id"], first["piece_id"]))
        self.assertEqual(self.state(), state)
        self.receipt_reversal(first["receipt_id"])
        after = self.recv("1", request="55555555-5555-4555-8555-000000000100")
        self.assertEqual((after["duplicate"], after["receipt_id"], len(self.db.receipts)), (True, first["receipt_id"], 1))
        self.refuse("REQUEST_CONFLICT", lambda: self.recv("1.5", request="55555555-5555-4555-8555-000000000100"))             # another payload
        other = self.owner_like(OWNER, user_id=9)
        service = HoldingReceiptService(other[0]._session, transaction_factory=self.db.transaction, repository=TripRepo, holdings=HoldingRepo, parties=SettlementRepo,
                                        authorization=other[1], corrections=self.corrections)
        self.refuse("REQUEST_CONFLICT", lambda: self.recv("1", request="55555555-5555-4555-8555-000000000100", service=service))   # another actor

    def test_cross_flow_key_conflicts_are_refused_in_both_directions(self):
        done = self.legacy_receipt("1", request=hkey(70))
        # a receipt key reused by other flows ...
        self.refuse("REQUEST_CONFLICT", lambda: self.sett("LOT", self.pool, S21, "1", ENTITLEMENT, request=hkey(70)))
        self.refuse("REQUEST_CONFLICT", lambda: self.hand(self.residue, request=hkey(70)))
        self.refuse("REQUEST_CONFLICT", lambda: self.put("1", request=hkey(70)))
        logo = int(self.put("2", request=hkey(74))["pool_id"])
        self.refuse("REQUEST_CONFLICT", lambda: self.take(logo, "1", request=hkey(70)))
        # ... and an SG / holding / custody key reused by the receipt
        sg = self.sett("LOT", self.pool, S18_INACTIVE, "0.5", ENTITLEMENT, request=skey(71))
        self.refuse("REQUEST_CONFLICT", lambda: self.recv("0.5", request=skey(71)))
        self.hand(self.residue, request=hkey(72))
        self.refuse("REQUEST_CONFLICT", lambda: self.recv("0.5", request=hkey(72)))
        put = self.put("1", request=hkey(73))
        self.refuse("REQUEST_CONFLICT", lambda: self.recv("0.5", request=hkey(73)))
        self.assertTrue(done["piece_code"] and sg["code"] and put["document_code"])

    def test_the_losing_side_of_a_concurrent_same_key_request_replays_without_writing(self):
        first = self.legacy_receipt("1", request="55555555-5555-4555-8555-000000000200")
        error = IntegrityError(msg="Duplicate entry 'x' for key 'holding_receipts.uq_hr_request'", errno=1062)
        state = self.state()
        with patch.object(HoldingReceiptService, "_post", side_effect=error):
            replay = self.recv("1", request="55555555-5555-4555-8555-000000000200")
            self.assertEqual((replay["duplicate"], replay["receipt_id"]), (True, first["receipt_id"]))
            with self.assertRaises(IntegrityError):
                self.recv("1", request="55555555-5555-4555-8555-000000000201")
        self.assertEqual(self.state(), state)

    def test_failure_at_every_posting_stage_leaves_no_trace(self):
        before = self.state()
        self.refuse("LOT_RECEIPTS_DISABLED", lambda: self.recv("1"))
        self.assertEqual(self.state(), before)
        self.assertEqual(self.db.receipts, {})

    def test_a_session_that_ended_during_the_post_rolls_everything_back(self):
        before = self.state()
        actor = self.session.require_authenticated()
        with patch.object(self.session, "is_current", return_value=False), self.assertRaises(PermissionError):
            self.receiver.receive(str(self.pool), "1", "1", TODAY, "", "", "55555555-5555-4555-8555-999999999999",
                                 _expected_actor=actor)
        self.assertEqual(self.state(), before)

    def test_the_lock_order_is_lot_then_holder_then_pool_and_never_the_trip(self):
        self.db.lock_log.clear()
        self.refuse("LOT_RECEIPTS_DISABLED", lambda: self.recv("1"))
        self.assertEqual(self.db.lock_log, [("lots", (self.lot2,)), ("party", 20), ("pool", self.pool)])

    def test_the_dialog_context_is_a_display_read(self):
        before = self.untouched()
        context = self.receiver.context(str(self.pool))
        self.assertEqual((context["balance"], context["allocated"], context["receivable"], context["source_fineness"]),
                         (D("2.950"), True, False, D("930.0")))
        self.assertEqual(self.untouched(), before)
        weak = session_for(frozenset({"APP_LOGIN", "INTERMEDIARY_HOLDING_VIEW"}))
        with self.assertRaises(PermissionError):
            HoldingReceiptService(weak, transaction_factory=self.db.transaction, repository=TripRepo, holdings=HoldingRepo, parties=SettlementRepo,
                                  authorization=FakeAuthorization(weak)).context(str(self.pool))


# ====================================================================================================================== return policy: legacy replay, declaration code, independent guards
class ReturnPolicyTests(ReceiptCase):
    """The whole-bar return after the review-token / declaration changes. Tests marked DEFENSIVE seed an inconsistent state directly in the fake tables
    (no production flow creates it) to prove that a full balance alone never bypasses an independent return guard."""

    def snapshot(self):
        d = self.db
        return copy.deepcopy((d.entries, d.holding_docs, d.holding_moves, d.holding_links, d.stock_moves, d.receipts, d.pieces, d.settlements,
                              d.corrections, d.audit_rows, d.lot_moves))

    def pre_token_record(self, request, pool, when=TODAY):
        """Fixture: rewrite the stored hash of ONE successful return to the digest format used before the review token existed."""
        legacy = hashlib.sha256(json.dumps(["HOLD_RETURN", int(pool), when, None, None, True], ensure_ascii=False).encode()).hexdigest()
        entry = next(e for e in self.db.entries.values() if e["request_key"] == request)
        document = next(d for d in self.db.holding_docs.values() if d["request_key"] == request)
        self.assertNotEqual(entry["request_hash"], legacy)
        entry["request_hash"] = document["request_hash"] = legacy
        return legacy

    def seed_move(self, pool, delta, movement_type, receipt_id=None):
        self.db.holding_moves.append({"id": self.db.next("hmove"), "pool_id": int(pool), "document_id": None, "doc_kind": None, "settlement_id": None,
                                      "receipt_id": receipt_id, "movement_type": movement_type, "weight_grams": abs(delta), "weight_delta": delta,
                                      "correction_id": None})

    # ------------------------------------------------------------------ legacy HOLD_RETURN replay
    def test_a_return_recorded_with_the_pre_token_digest_replays_exactly_and_nothing_else_does(self):
        request = hkey(3000)
        first = self.back(self.pool, request=request)
        self.pre_token_record(request, self.pool)
        after = self.snapshot()
        again = self.back(self.pool, request=request)                                    # the current-format payload of the same business request
        self.assertEqual((again["duplicate"], again["document_id"], again["entry_id"]), (True, first["document_id"], first["entry_id"]))
        no_token = self.holding.return_holding(str(self.pool), True, "", False, TODAY, "", "", request)       # replay precedes the token check
        self.assertEqual((no_token["duplicate"], no_token["document_id"]), (True, first["document_id"]))
        self.assertEqual(self.snapshot(), after, "a replay wrote")
        # any changed business payload, another actor or another flow stays a conflict
        self.refuse("REQUEST_CONFLICT", lambda: self.back(self.pool, request=request, when="2020-01-02"))
        self.refuse("REQUEST_CONFLICT", lambda: self.holding.return_holding(str(self.pool), True, "", False, TODAY, "", "different", request))
        _, other = self.as_user(OWNER, user_id=9)
        self.refuse("REQUEST_CONFLICT", lambda: self.back(self.pool, request=request, service=other))
        self.refuse("REQUEST_CONFLICT", lambda: self.hand(request=request))
        self.assertEqual(self.snapshot(), after)

    def test_the_legacy_digest_never_lets_a_new_posting_skip_the_review_or_the_confirmation(self):
        request = hkey(3001)
        self.back(self.pool, request=request)
        self.pre_token_record(request, self.pool)
        pool = int(self.hand(self.lot2, trip=self.logo_trip)["pool_id"])                 # the same bar handed over again: a NEW pool and a NEW key
        before = self.snapshot()
        self.refuse("RETURN_REVIEW_CHANGED", lambda: self.holding.return_holding(str(pool), True, "", False, TODAY, "", "", hkey(3002)))
        self.refuse("RETURN_REVIEW_CHANGED", lambda: self.holding.return_holding(str(pool), True, "0" * 64, False, TODAY, "", "", hkey(3002)))
        review = self.holding.return_review(pool)
        self.refuse("INTACT_CONFIRMATION_REQUIRED",
                    lambda: self.holding.return_holding(str(pool), False, review["token"], False, TODAY, "", "", hkey(3002)))
        self.assertEqual(self.snapshot(), before)
        # a changed history after the review still needs a renewed confirmation
        settled = self.sett("LOT", pool, S21, "1", ENTITLEMENT)
        self.reverse(settled["id"])
        self.refuse("RETURN_REVIEW_CHANGED",
                    lambda: self.holding.return_holding(str(pool), True, review["token"], False, TODAY, "", "", hkey(3002)))
        fresh = self.holding.return_review(pool)
        self.assertEqual(self.holding.return_holding(str(pool), True, fresh["token"], False, TODAY, "", "", hkey(3002))["kind"], "HOLD_RETURN")

    # ------------------------------------------------------------------ the explicit declaration code
    def test_the_audit_carries_the_declaration_code_only_when_physical_events_were_declared(self):
        settled = self.sett("LOT", self.pool, S21, "1", ENTITLEMENT)
        self.reverse(settled["id"])
        self.back(self.pool, physical_confirmed=True)             # a supplied declaration is ignored: nothing physical was listed
        review = self.db.audit_rows[-1]["metadata"]["physical_event_review"]
        self.assertIsNone(review["physical_event_declaration_code"])
        self.assertFalse(review["physical_events_recorded_in_error_and_did_not_occur"])
        self.assertEqual(review["physical_event_ids"], [])
        self.assertEqual(review["same_original_bar_confirmation_code"], CONFIRMATION_CODE)
        self.assertIn(f"SG{int(settled['id']):06d}", review["references"]["settlements"])
        self.assertEqual(len(review["references"]["correction_ids"]), 2)                 # the setUp allocation and this one, both reversed
        self.assertEqual((review["references"]["receipts"], review["references"]["pieces"]), ([], []))

    def test_a_declared_physical_division_is_coded_and_its_audit_is_atomic_with_the_return(self):
        divided = self.sett("LOT", self.pool, S21, "2.950", DIVIDED)
        self.reverse(divided["id"])
        self.refuse("PHYSICAL_EVENT_DECLARATION_REQUIRED", lambda: self.back(self.pool))
        for step in ("audit", "holding_audit"):                                          # the audit is atomic with the return
            before = self.snapshot()
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.back(self.pool, physical_confirmed=True)
            self.db.fail_at = None
            self.assertEqual(self.snapshot(), before, step)
        self.back(self.pool, physical_confirmed=True)
        review = self.db.audit_rows[-1]["metadata"]["physical_event_review"]
        self.assertEqual(review["physical_event_declaration_code"], PHYSICAL_EVENTS_DECLARATION_CODE)
        self.assertTrue(review["physical_events_recorded_in_error_and_did_not_occur"])
        self.assertEqual(review["physical_event_ids"], [f"SG:{divided['id']}"])
        self.assertEqual(review["same_original_bar_confirmation_code"], CONFIRMATION_CODE)
        self.assertIn("snapshot_sha256", review)
        self.assertEqual((review["confirmed_by_user_id"], bool(review["confirmed_at"])), (7, True))

    def test_a_reversed_receipt_is_listed_with_hr_rp_and_its_correction(self):
        receipt = self.legacy_receipt("0.500")
        self.receipt_reversal(receipt["receipt_id"])
        self.back(self.pool, physical_confirmed=True)
        review = self.db.audit_rows[-1]["metadata"]["physical_event_review"]
        self.assertEqual(review["physical_event_declaration_code"], PHYSICAL_EVENTS_DECLARATION_CODE)
        self.assertEqual(review["physical_event_ids"], [f"HR:{receipt['receipt_id']}/RP:{receipt['piece_id']}"])
        self.assertEqual((review["references"]["receipts"], review["references"]["pieces"]),
                         ([receipt["code"]], [receipt["piece_code"]]))
        self.assertEqual(len(review["references"]["correction_ids"]), 2)                 # the setUp allocation reversal + the receipt reversal
        self.assertEqual(len(self.db.receipts), 1)

    # ------------------------------------------------------------------ independent guards (DEFENSIVE states)
    def test_defensive_a_full_balance_alone_never_bypasses_an_effective_settlement(self):
        for declaration in (ENTITLEMENT, DIVIDED):
            with self.subTest(declaration=declaration):
                pool = self.lot_pool(self.residue if declaration == ENTITLEMENT else self.main)
                settled = self.sett("LOT", pool, S21, "1", declaration)
                self.seed_move(pool, D("1.000"), "SETTLEMENT_REVERSAL")                 # balance full again, but NO canonical reversal exists
                self.assertEqual(self.pool_grams(pool), D(self.db.holding_pools[pool]["weight_grams"]))
                before = self.snapshot()
                error = self.refuse("SETTLEMENT_NOT_REVERSED", lambda: self.back(pool, physical_confirmed=True))
                self.assertIn(f"SG{int(settled['id']):06d}", str(error))
                self.assertEqual(self.snapshot(), before)

    def test_defensive_a_full_balance_alone_never_bypasses_an_unreversed_receipt_or_its_stock(self):
        # (1) an unreversed receipt with the holding grams "restored" directly
        receipt = self.legacy_receipt("0.500")
        self.seed_move(self.pool, D("0.500"), "RECEIPT_REVERSAL", receipt_id=int(receipt["receipt_id"]))
        self.assertEqual(self.pool_grams(), D("2.950"))
        before = self.snapshot()
        error = self.refuse("RECEIPT_NOT_REVERSED", lambda: self.back(self.pool, physical_confirmed=True))
        self.assertIn(f"{receipt['code']}/{receipt['piece_code']}", str(error))
        self.assertEqual(self.snapshot(), before)

    def test_defensive_outstanding_receipt_stock_after_a_canonical_reversal_blocks_the_return(self):
        receipt = self.legacy_receipt("0.500")
        self.receipt_reversal(receipt["receipt_id"])
        self.assertEqual(self.pool_grams(), D("2.950"))
        self.db.stock_moves.append({"id": self.db.next("smove"), "piece_id": int(receipt["piece_id"]), "receipt_id": int(receipt["receipt_id"]),
                                    "movement_type": "RECEIVE", "weight_grams": D("0.500"), "weight_delta": D("0.250"), "correction_id": None})
        before = self.snapshot()
        self.refuse("RECEIPT_NOT_REVERSED", lambda: self.back(self.pool, physical_confirmed=True))
        self.assertEqual(self.snapshot(), before)

    def test_defensive_a_dependent_use_of_receipt_stock_blocks_the_return_even_at_zero_stock(self):
        receipt = self.legacy_receipt("0.500")
        self.receipt_reversal(receipt["receipt_id"])
        piece, rid = int(receipt["piece_id"]), int(receipt["receipt_id"])
        for delta in (D("0.100"), D("-0.100")):                                          # a consumer took and returned stock: net zero, but a use exists
            self.db.stock_moves.append({"id": self.db.next("smove"), "piece_id": piece, "receipt_id": rid, "movement_type": "CONSUMED",
                                        "weight_grams": abs(delta), "weight_delta": delta, "correction_id": None})
        self.assertEqual(HoldingRepo(self.db).piece_stock(piece), D("0.000"))
        before = self.snapshot()
        self.refuse("RECEIPT_NOT_REVERSED", lambda: self.back(self.pool, physical_confirmed=True))
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
