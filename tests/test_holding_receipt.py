"""Physical receipts from an intermediary's holdings (051). Real HoldingReceiptService, HoldingSettlementService, IntermediaryHoldingService and CorrectionService over
the in-memory transactional database of tests/market_trip_fakes.py (a failed transaction restores every table; the 051 CHECK / unique / composite keys are mirrored as
asserts). NOT real-MySQL tests: the SQL, the constraints and the lock behaviour were exercised on masgold_rehearsal (tests/test_holding_receipt_mysql.py, opt-in).

The example is SG000006: a lot of 2.950 g at 930 per mille, 1.000 g allocated (GRAM_ENTITLEMENT) and the allocation reversed. Every other weight is hypothetical."""
import copy
from decimal import Decimal as D
from unittest.mock import patch
import unittest

from mysql.connector import IntegrityError

from services.correction_service import CorrectionError
from services.holding_receipt_service import HoldingReceiptService
from services.market_trip_service import TripError
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import HoldingRepo, OWNER, SettlementRepo, TripRepo, session_for
from tests.test_holding_settlement import ENTITLEMENT, S18_INACTIVE, S21, SettleCase, WHOLE, skey
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


# ====================================================================================================================== posting
class ReceiptTests(ReceiptCase):
    def test_the_sg000006_pool_is_received_in_sequential_partial_receipts_with_exact_effects(self):
        self.assertEqual((self.pool_grams(), self.db.lots[self.lot2]["weight_grams"], self.db.lots[self.lot2]["fineness_permille"]), (D("2.950"), D("2.950"), D("930.0")))
        before = self.untouched()
        first = self.recv("1")                                                                   # hypothetical figure
        self.assertEqual((first["code"], first["piece_code"], first["grams"], first["remaining"], first["duplicate"]), ("HR000001", "RP000001", "1.000", "1.950", False))
        self.assertEqual((self.pool_grams(), self.stock(), self.conserved()), (D("1.950"), D("1.000"), D("2.950")))
        self.assertEqual(self.untouched(), before)                                               # no custody, Scrap, cash, making, shop-lot or inventory movement
        second = self.recv("1.95")
        self.assertEqual((second["piece_code"], second["remaining"]), ("RP000002", "0.000"))
        self.assertEqual((self.pool_grams(), self.stock(), self.conserved()), (D("0.000"), D("2.950"), D("2.950")))
        pieces = self.db.pieces
        self.assertEqual([(p["identity_kind"], p["location"], p["assay_status"], p["fineness_permille"], p["pure_grams"]) for p in pieces.values()],
                         [("RECEIPT_PIECE", "SHOP", "UNKNOWN", None, None)] * 2)
        self.assertEqual({p["receipt_id"] for p in pieces.values()}, {1, 2})                      # one NEW identity per receipt, never the original lot
        self.assertEqual(self.db.lots[self.lot2]["weight_grams"], D("2.950"))                     # the source lot is untouched
        self.refuse("HOLDING_NOT_AVAILABLE", lambda: self.recv("0.001"))
        self.assertEqual([r["event_type"] for r in self.db.audit_rows][-1], "INTERMEDIARY_HOLDING_RECEIPT")

    def test_allocation_guards_are_preserved_and_a_receipt_never_restores_the_original_bar(self):
        self.recv("1")
        self.refuse("ALLOCATION_HISTORY_BLOCKS_RETURN", lambda: self.back(self.pool))
        entry = next(l["entry_id"] for l in self.db.holding_links.values() if l["pool_id"] == self.pool)
        self.refuse("DEPENDENT_HOLDING_SETTLEMENT_EXISTS", lambda: self.corrections.reverse("MARKET_TRIP_ENTRY", str(entry), "x"), CorrectionError)
        self.assertEqual(TripRepo(self.db).shop_lot_balance(self.lot2), D("0"))                  # the lot is not in the shop lot stock

    def test_a_pool_without_allocation_history_uses_the_normal_return(self):
        fresh = self.lot_pool(self.residue)
        self.refuse("USE_NORMAL_RETURN", lambda: self.recv("1", pool=fresh))
        whole = self.sett("LOT", fresh, S21, "1", WHOLE)                                         # a whole delivery is no allocation history
        self.reverse(whole["id"])
        self.refuse("USE_NORMAL_RETURN", lambda: self.recv("1", pool=fresh))
        self.assertEqual(self.db.receipts, {})

    def test_measured_grams_must_equal_the_consumed_holding_grams_and_nothing_is_cleared(self):
        for consumed, measured, code in (("1", "0.999", "RECEIPT_WEIGHT_MISMATCH"), ("1", "1.001", "RECEIPT_WEIGHT_MISMATCH"), ("1.0001", "1.0001", "WEIGHT_INVALID"),
                                         ("0", "0", "WEIGHT_INVALID"), ("-1", "-1", "WEIGHT_INVALID"), ("", "", "WEIGHT_INVALID"), ("2.951", "2.951", "HOLDING_EXCEEDED")):
            self.refuse(code, lambda c=consumed, m=measured: self.recv(c, m))
        self.assertEqual((self.pool_grams(), self.stock(), self.db.receipts), (D("2.950"), D("0.000"), {}))
        self.recv("1")
        self.assertEqual(self.pool_grams(), D("1.950"))                                             # the unreceived 1.950 g stay in holdings, never cleared
        self.refuse("HOLDING_NOT_FOUND", lambda: self.recv("1", pool="999"))

    def test_the_stock_is_shop_stock_visible_but_not_scrap_inventory_custody_or_shop_lots(self):
        scrap, shop_lots, available = copy.deepcopy(self.db.scrap), copy.deepcopy(self.db.shop_moves), self.db.available(1)
        self.recv("1")
        self.assertEqual((self.db.scrap, self.db.shop_moves, self.db.available(1)), (scrap, shop_lots, available))
        rows = self.holding.party_holdings(OWNER_PARTY)["receipts"]
        self.assertEqual([(r["piece_code"], r["stock"], r["in_stock"], r["assay_known"], r["source_fineness"]) for r in rows], [("RP000001", D("1.000"), True, False, D("930.0"))])
        detail = self.service.trip_detail(self.logo_trip)
        self.assertEqual(detail["custody"]["lots"], detail["custody"]["lots"])
        self.assertTrue(all(hasattr(self.db, name) for name in ("receipts", "pieces", "stock_moves")))
        for module in ("services.market_trip_service", "services.supplier_gold_settlement_service", "services.holding_settlement_service"):
            source = __import__(module, fromlist=["x"]).__file__
            self.assertNotIn("receipt_stock", open(source, encoding="utf-8").read(), module)      # no sale / settlement / issue / melt path reads receipt stock

    def test_the_panel_flags_receivable_pools_and_lists_receipts_with_their_state(self):
        panel = self.holding.party_holdings(OWNER_PARTY)
        self.assertEqual([(p["receivable"], p["allocated"]) for p in panel["pools"] if p["pool_id"] == str(self.pool)], [(True, True)])
        first = self.recv("1")
        self.receipt_reversal(first["receipt_id"])
        rows = {r["code"]: r for r in self.holding.party_holdings(OWNER_PARTY)["receipts"]}
        self.assertEqual((rows["HR000001"]["reversed"], rows["HR000001"]["stock"], rows["HR000001"]["in_stock"]), (True, D("0.000"), False))


# ====================================================================================================================== reversal
class ReversalTests(ReceiptCase):
    def test_a_reversal_removes_the_stock_and_restores_the_stored_holding_grams_atomically_under_one_correction(self):
        done = self.recv("1")
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
        self.refuse("ALLOCATION_HISTORY_BLOCKS_RETURN", lambda: self.back(self.pool))                                   # a reversed receipt never reopens the whole return

    def test_double_reversal_reason_and_the_stored_grams_are_enforced(self):
        done = self.recv("1")
        self.refuse("REASON_REQUIRED", lambda: self.receipt_reversal(done["receipt_id"], ""), CorrectionError)
        self.receipt_reversal(done["receipt_id"])
        self.refuse("ALREADY_REVERSED", lambda: self.receipt_reversal(done["receipt_id"]), CorrectionError)
        self.assertEqual(self.conserved(), D("2.950"))
        again = self.recv("1")                                                                   # the same grams received again are a NEW piece identity
        self.assertEqual((again["piece_code"], again["code"]), ("RP000002", "HR000002"))

    def test_the_reversal_requires_the_full_received_stock_and_locks_the_piece_before_reading_it(self):
        done = self.recv("1")
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
        self.recv("1.95")                                                                        # everything else is received: balance 0
        self.assertEqual((self.pool_grams(), self.stock(), self.effective_settled(), self.conserved()), (D("0.000"), D("1.950"), D("1.000"), D("2.950")))
        self.reverse(live["id"])                                                                 # the supplier credit is undone: entitlement 1.000 is back
        self.assertEqual((self.pool_grams(), self.stock(), self.effective_settled(), self.conserved()), (D("1.000"), D("1.950"), D("0.000"), D("2.950")))
        self.recv("1")
        self.assertEqual((self.pool_grams(), self.stock()), (D("0.000"), D("2.950")))
        for receipt in sorted(self.db.receipts, reverse=True):
            self.receipt_reversal(receipt)
        self.assertEqual((self.pool_grams(), self.stock(), self.conserved()), (D("2.950"), D("0.000"), D("2.950")))

    def test_a_reversal_that_would_push_the_pool_above_its_initial_quantity_is_refused(self):
        done = self.recv("1")
        self.db.holding_moves.append({"id": 901, "pool_id": self.pool, "document_id": None, "doc_kind": None, "settlement_id": None, "receipt_id": None,
                                      "movement_type": "RETURN_REVERSAL", "weight_grams": D("2.000"), "weight_delta": D("2.000"), "correction_id": None})
        self.refuse("HOLDING_BALANCE_INVALID", lambda: self.receipt_reversal(done["receipt_id"]), CorrectionError)

    def test_a_closed_origin_custody_neither_blocks_nor_is_reopened(self):
        self.db.trips[int(self.logo_trip)]["status"] = "CLOSED"
        self.db.lock_log.clear()
        done = self.recv("1")
        self.receipt_reversal(done["receipt_id"])
        self.assertNotIn(("trip",), self.db.lock_log)
        self.assertEqual(self.db.trips[int(self.logo_trip)]["status"], "CLOSED")
        self.assertEqual(self.pool_grams(), D("2.950"))

    def test_the_failure_of_any_reversal_stage_leaves_no_trace(self):
        done = self.recv("1")
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
        done = self.recv("1")
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
        self.recv("1")
        self.assertGreater(HoldingRepo(self.db).history_count(20), 3)
        self.db.suppliers[20]["is_active"] = 1

    def test_exact_replay_returns_the_original_records_even_after_reversal_and_conflicts_are_refused(self):
        first = self.recv("1", request="55555555-5555-4555-8555-000000000100")
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
        done = self.recv("1", request=hkey(70))
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
        first = self.recv("1", request="55555555-5555-4555-8555-000000000200")
        error = IntegrityError(msg="Duplicate entry 'x' for key 'holding_receipts.uq_hr_request'", errno=1062)
        state = self.state()
        with patch.object(HoldingReceiptService, "_post", side_effect=error):
            replay = self.recv("1", request="55555555-5555-4555-8555-000000000200")
            self.assertEqual((replay["duplicate"], replay["receipt_id"]), (True, first["receipt_id"]))
            with self.assertRaises(IntegrityError):
                self.recv("1", request="55555555-5555-4555-8555-000000000201")
        self.assertEqual(self.state(), state)

    def test_failure_at_every_posting_stage_leaves_no_trace(self):
        for step in ("receipt", "piece", "stock_movement", "receipt_movement", "holding_audit"):
            self.db.fail_at = step
            before = self.state()
            with self.assertRaises(RuntimeError):
                self.recv("1")
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)
        self.assertEqual(self.db.receipts, {})
        self.recv("1")

    def test_a_session_that_ended_during_the_post_rolls_everything_back(self):
        before = self.state()
        with patch.object(self.session, "is_current", return_value=False), self.assertRaises(PermissionError):
            self.recv("1")
        self.assertEqual(self.state(), before)

    def test_the_lock_order_is_lot_then_holder_then_pool_and_never_the_trip(self):
        self.db.lock_log.clear()
        self.recv("1")
        self.assertEqual(self.db.lock_log, [("lots", (self.lot2,)), ("party", 20), ("pool", self.pool)])

    def test_the_dialog_context_is_a_display_read(self):
        before = self.untouched()
        context = self.receiver.context(str(self.pool))
        self.assertEqual((context["balance"], context["allocated"], context["receivable"], context["source_fineness"]), (D("2.950"), True, True, D("930.0")))
        self.assertEqual(self.untouched(), before)
        weak = session_for(frozenset({"APP_LOGIN", "INTERMEDIARY_HOLDING_VIEW"}))
        with self.assertRaises(PermissionError):
            HoldingReceiptService(weak, transaction_factory=self.db.transaction, repository=TripRepo, holdings=HoldingRepo, parties=SettlementRepo,
                                  authorization=FakeAuthorization(weak)).context(str(self.pool))


if __name__ == "__main__":
    unittest.main()
