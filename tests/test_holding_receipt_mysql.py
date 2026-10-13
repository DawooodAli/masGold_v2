"""Explicit opt-in rehearsal checks for the 051 physical receipts from intermediary holdings. They connect to masgold_rehearsal ONLY (target, migration level and marker are
asserted) and never to the DEV database. Real services, real repositories, real SQL, separate REPEATABLE READ connections.

Evidence classes, kept apart on purpose: functional checks (real services over MySQL); controlled RR snapshot checks (a snapshot is taken on purpose, then another
connection commits); resource-order / contention checks that remove the production authorization locks (raw holders and UnlockedAuthorization; production
authorization X-locks the actor and the shared role / permission rows until commit and serializes every protected writer first). None of this is a stress test and
none of it proves general concurrency safety or deadlock freedom."""
from decimal import Decimal as D
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from repositories.correction_repository import CorrectionRepository
from repositories.intermediary_holding_repository import IntermediaryHoldingRepository
from repositories.market_trip_repository import MarketTripRepository
from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository
from services.correction_service import CorrectionError, CorrectionService
from services.holding_receipt_service import HoldingReceiptService
from services.holding_settlement_service import HoldingSettlementService
from services.market_trip_service import TripError
from services.supplier_rules import SupplierRuleError
from tests.test_holding_settlement_mysql import ENTITLEMENT, WHOLE, Denying, Recording, SettleMySqlTests
from tests.test_intermediary_holdings_mysql import DATE
from tests.test_market_trip_hardening_mysql import UnlockedAuthorization


class ReceiptMySqlTests(SettleMySqlTests):
    def setUp(self):
        super().setUp()
        self.receiver = HoldingReceiptService(self.session, transaction_factory=self.tx, corrections=self.correct)
        trip, melt, rows = self.lots('2.95', '930')                                           # the SG000006 lot: 2.950 g at 930 per mille
        done = self.hand(trip, rows[0])
        self.origin_trip = trip
        self.pool = int(self.query('SELECT pool_id FROM intermediary_holding_documents WHERE id=%s', (done['document_id'],))[0]['pool_id'])
        self.handover_entry = int(self.entry_of(done['document_id']))
        first = self.settle('LOT', self.pool, self.s21, '1', ENTITLEMENT)                      # 1.000 g allocated ...
        self.reverse(first['id'])                                                              # ... and reversed: the balance is 2.950 g again

    # ------------------------------------------------------------------ helpers
    def recv(self, grams, measured=None, pool=None, key=None, service=None):
        return (service or self.receiver).receive(str(pool or self.pool), grams, grams if measured is None else measured, DATE, '051 ref', '051 notes', key or str(uuid4()))

    def stock(self, piece=None):
        """The receipt stock of ONE piece, or of this test's pool (the rehearsal database is shared, so never a global sum)."""
        if piece is not None:
            return self.query('SELECT COALESCE(SUM(weight_delta),0) AS g FROM receipt_stock_movements WHERE piece_id=%s', (piece,))[0]['g']
        return self.query('SELECT COALESCE(SUM(m.weight_delta),0) AS g FROM receipt_stock_movements m JOIN receipt_pieces p ON p.id=m.piece_id WHERE p.pool_id=%s', (self.pool,))[0]['g']

    def pool_grams(self):
        return self.lot_balance(self.pool)

    def settled(self):
        return self.query("SELECT COALESCE(SUM(g.delivered_weight_grams),0) AS g FROM supplier_gold_settlements g WHERE g.lot_pool_id=%s AND NOT EXISTS "
                          "(SELECT 1 FROM transaction_corrections c WHERE c.source_type='SUPPLIER_GOLD_SETTLEMENT' AND c.source_id=g.id)", (self.pool,))[0]['g']

    def conserved(self):
        return self.pool_grams() + self.stock() + self.settled()

    def rstate(self):
        tables = ('holding_receipts', 'receipt_pieces', 'receipt_stock_movements')
        return {**self.state(), **{t: self.query(f'SELECT COUNT(*) AS n FROM {t}')[0]['n'] for t in tables}}

    def undo(self, receipt, service=None, reason='051'):
        return (service or self.receiver).reverse(str(receipt), reason)

    # ------------------------------------------------------------------ functional
    def test_sg000006_pool_receipts_conservation_and_exact_effects(self):
        self.assertEqual((self.pool_grams(), self.conserved()), (D('2.950'), D('2.950')))
        before = self.others()
        first = self.recv('1')
        self.assertEqual((first['grams'], first['remaining'], first['duplicate']), ('1.000', '1.950', False))
        self.assertEqual((self.pool_grams(), self.stock(), self.conserved()), (D('1.950'), D('1.000'), D('2.950')))
        self.assertEqual(self.others(), before)                                               # no custody, Scrap, cash, making, shop-lot or inventory movement
        piece = self.query('SELECT * FROM receipt_pieces WHERE id=%s', (first['piece_id'],))[0]
        self.assertEqual((piece['identity_kind'], piece['location'], piece['assay_status'], piece['fineness_permille'], piece['pure_grams'], piece['grams']),
                         ('RECEIPT_PIECE', 'SHOP', 'UNKNOWN', None, None, D('1.000')))
        second = self.recv('1.95')
        self.assertNotEqual(first['piece_id'], second['piece_id'])
        self.assertEqual((self.pool_grams(), self.stock(), self.conserved()), (D('0'), D('2.950'), D('2.950')))
        with self.assertRaises(TripError) as drained:
            self.recv('0.001')
        self.assertEqual(drained.exception.code, 'HOLDING_NOT_AVAILABLE')
        self.assertEqual(self.query('SELECT weight_grams,fineness_permille FROM market_trip_lots ORDER BY id DESC LIMIT 1')[0]['fineness_permille'], D('930.0'))

    def test_allocation_guards_hold_and_a_pool_without_allocation_history_is_refused(self):
        self.recv('1')
        with self.assertRaises(TripError) as blocked:
            self.back(self.pool)
        self.assertEqual(blocked.exception.code, 'ALLOCATION_HISTORY_BLOCKS_RETURN')
        with self.assertRaises(CorrectionError) as handover:
            self.correct.reverse('MARKET_TRIP_ENTRY', self.handover_entry, '051')
        self.assertEqual(handover.exception.code, 'DEPENDENT_HOLDING_SETTLEMENT_EXISTS')
        _, _, fresh, _ = self.lot_pool('10', '875')
        before = self.rstate()
        with self.assertRaises(TripError) as normal:
            self.recv('1', pool=fresh)
        self.assertEqual(normal.exception.code, 'USE_NORMAL_RETURN')
        self.assertEqual(self.rstate(), before)
        whole = self.settle('LOT', fresh, self.s21, '10', WHOLE)
        self.reverse(whole['id'])
        before = self.rstate()
        with self.assertRaises(TripError) as still:
            self.recv('1', pool=fresh)
        self.assertEqual(still.exception.code, 'USE_NORMAL_RETURN')
        self.assertEqual(self.rstate(), before)

    def test_weight_rules_refuse_atomically_and_unreceived_grams_stay_in_holdings(self):
        before = self.rstate()
        for consumed, measured, error, code in (('1', '0.999', TripError, 'RECEIPT_WEIGHT_MISMATCH'), ('1.0001', '1.0001', TripError, 'WEIGHT_INVALID'),
                                                ('0', '0', TripError, 'WEIGHT_INVALID'), ('-1', '-1', TripError, 'WEIGHT_INVALID'),
                                                ('2.951', '2.951', TripError, 'HOLDING_EXCEEDED')):
            with self.assertRaises(error) as refused:
                self.recv(consumed, measured)
            self.assertEqual(refused.exception.code, code, consumed)
        self.assertEqual(self.rstate(), before)
        self.recv('1')
        self.assertEqual(self.pool_grams(), D('1.950'))

    def test_reversal_removes_the_stock_and_restores_the_stored_grams_under_one_correction(self):
        done = self.recv('1')
        result = self.undo(done['receipt_id'])
        self.assertEqual((self.pool_grams(), self.stock(), self.conserved()), (D('2.950'), D('0'), D('2.950')))
        stock = self.query('SELECT movement_type,weight_delta,correction_id FROM receipt_stock_movements WHERE piece_id=%s ORDER BY id', (done['piece_id'],))
        pool = self.query("SELECT movement_type,weight_delta,correction_id FROM intermediary_holding_movements WHERE receipt_id=%s ORDER BY id", (done['receipt_id'],))
        self.assertEqual([(m['movement_type'], m['weight_delta']) for m in stock], [('RECEIVE', D('1.000')), ('RECEIVE_REVERSAL', D('-1.000'))])
        self.assertEqual([(m['movement_type'], m['weight_delta']) for m in pool], [('RECEIPT', D('-1.000')), ('RECEIPT_REVERSAL', D('1.000'))])
        self.assertEqual({int(stock[1]['correction_id']), int(pool[1]['correction_id'])}, {int(result['correction_id'])})
        self.assertEqual((stock[0]['correction_id'], pool[0]['correction_id']), (None, None))
        with self.assertRaises(CorrectionError) as again:
            self.undo(done['receipt_id'])
        self.assertEqual(again.exception.code, 'ALREADY_REVERSED')
        with self.assertRaises(TripError) as still:
            self.back(self.pool)
        self.assertEqual(still.exception.code, 'ALLOCATION_HISTORY_BLOCKS_RETURN')
        with self.assertRaises(CorrectionError) as reason:
            self.undo(self.recv('1')['receipt_id'], reason='')
        self.assertEqual(reason.exception.code, 'REASON_REQUIRED')

    def test_the_reversal_needs_the_full_received_stock(self):
        done = self.recv('1')
        with self.tx() as c:                                                                   # simulate a consumer having taken the stock: the receive row goes
            q = c.cursor()
            q.execute("DELETE FROM receipt_stock_movements WHERE piece_id=%s", (done['piece_id'],))
            q.close()
        before = self.rstate()
        with self.assertRaises(CorrectionError) as missing:
            self.undo(done['receipt_id'])
        self.assertEqual(missing.exception.code, 'RECEIPT_STOCK_NOT_PRESENT')
        self.assertEqual(self.rstate(), before)
        with self.tx() as c:
            q = c.cursor()
            q.execute("INSERT INTO receipt_stock_movements (piece_id,receipt_id,movement_type,weight_grams,weight_delta) VALUES (%s,%s,'RECEIVE',1,1)",
                      (done['piece_id'], done['receipt_id']))
            q.close()
        self.undo(done['receipt_id'])
        self.assertEqual(self.conserved(), D('2.950'))

    def test_settlement_reversal_after_receipts_keeps_conservation(self):
        live = self.settle('LOT', self.pool, self.s21, '1', ENTITLEMENT)
        self.recv('1.95')
        self.assertEqual((self.pool_grams(), self.stock(), self.settled(), self.conserved()), (D('0'), D('1.950'), D('1.000'), D('2.950')))
        self.reverse(live['id'])
        self.assertEqual((self.pool_grams(), self.stock(), self.settled(), self.conserved()), (D('1.000'), D('1.950'), D('0'), D('2.950')))
        self.recv('1')
        self.assertEqual(self.pool_grams(), D('0'))

    # ------------------------------------------------------------------ rights, replay, conflicts, rollback
    def test_each_right_is_checked_in_the_transaction_and_the_reversal_set_is_exact(self):
        base = self.rstate()
        service = HoldingReceiptService(self.session, transaction_factory=self.tx, authorization=Denying(self.session, 'INTERMEDIARY_HOLDING_MANAGE'), corrections=self.correct)
        with self.assertRaises(PermissionError):
            self.recv('1', service=service)
        manager, _ = self.make_session('051-manager', 'MANAGER')
        with self.assertRaises(PermissionError):
            self.recv('1', service=HoldingReceiptService(manager, transaction_factory=self.tx))
        self.assertEqual(self.rstate(), base)
        done = self.recv('1')
        for denied in ('TRANSACTION_CORRECT', 'INTERMEDIARY_HOLDING_REVERSE'):
            correcting = CorrectionService(self.session, authorization=Denying(self.session, denied), transaction_factory=self.tx)
            before = self.rstate()
            with self.assertRaises(PermissionError):
                self.undo(done['receipt_id'], service=HoldingReceiptService(self.session, transaction_factory=self.tx, corrections=correcting))
            self.assertEqual(self.rstate(), before, denied)
        with self.assertRaises(PermissionError):
            self.undo(done['receipt_id'], service=HoldingReceiptService(manager, transaction_factory=self.tx))
        recording = Recording(self.session)
        recorded = HoldingReceiptService(self.session, transaction_factory=self.tx, corrections=CorrectionService(self.session, authorization=recording, transaction_factory=self.tx))
        self.undo(done['receipt_id'], service=recorded)
        self.assertEqual(set(recording.codes), {'TRANSACTION_CORRECT', 'INTERMEDIARY_HOLDING_REVERSE'})

    def test_replay_after_reversal_and_cross_flow_conflicts_are_refused(self):
        key = str(uuid4())
        first = self.recv('1', key=key)
        again = self.recv('1', key=key)
        self.assertEqual((again['duplicate'], again['receipt_id']), (True, first['receipt_id']))
        self.undo(first['receipt_id'])
        after = self.recv('1', key=key)
        self.assertEqual((after['duplicate'], after['receipt_id']), (True, first['receipt_id']))
        self.assertEqual(self.query('SELECT COUNT(*) AS n FROM holding_receipts WHERE request_key=%s', (key,))[0]['n'], 1)
        with self.assertRaises(TripError) as payload:
            self.recv('1.5', key=key)
        self.assertEqual(payload.exception.code, 'REQUEST_CONFLICT')
        other, _ = self.make_session('051-other', 'OWNER')
        with self.assertRaises(TripError) as actor:
            self.recv('1', key=key, service=HoldingReceiptService(other, transaction_factory=self.tx, authorization=UnlockedAuthorization(other)))
        self.assertEqual(actor.exception.code, 'REQUEST_CONFLICT')
        trip = self.trip('20')
        for call in (lambda: self.settle('LOT', self.pool, self.s21, '1', ENTITLEMENT, key=key), lambda: self.put(trip, '1', key=key)):
            with self.assertRaises((TripError, SupplierRuleError)) as conflict:                  # a receipt key reused by an SG or an IG flow
                call()
            self.assertEqual(conflict.exception.code, 'REQUEST_CONFLICT')
        # a holding / settlement / custody key reused by the receipt
        used = str(uuid4())
        self.put(trip, '1', key=used)
        with self.assertRaises(TripError) as ig:
            self.recv('1', key=used)
        self.assertEqual(ig.exception.code, 'REQUEST_CONFLICT')
        sg_key = str(uuid4())
        self.settle('LOT', self.pool, self.supplier, '0.5', ENTITLEMENT, key=sg_key)
        with self.assertRaises(TripError) as sg:
            self.recv('0.5', key=sg_key)
        self.assertEqual(sg.exception.code, 'REQUEST_CONFLICT')
        with self.assertRaises(TripError) as reverse_direction:
            self.settle('LOT', self.pool, self.supplier, '0.5', ENTITLEMENT, key=key)
        self.assertEqual(reverse_direction.exception.code, 'REQUEST_CONFLICT')

    def test_rollback_after_each_persistent_stage_of_posting_and_reversal(self):
        def failing(cls, name):
            original = getattr(cls, name)

            def fail(instance, *a, _original=original, **kw):
                _original(instance, *a, **kw)
                raise RuntimeError('051 injected')
            return patch.object(cls, name, fail)
        for cls, name in ((IntermediaryHoldingRepository, 'insert_receipt'), (IntermediaryHoldingRepository, 'insert_piece'),
                          (IntermediaryHoldingRepository, 'insert_stock_movement'), (IntermediaryHoldingRepository, 'insert_receipt_movement'),
                          (IntermediaryHoldingRepository, 'audit')):
            before = self.rstate()
            with self.subTest(stage=f'receive/{name}'), failing(cls, name), self.assertRaisesRegex(RuntimeError, '051 injected'):
                self.recv('1')
            self.assertEqual(self.rstate(), before, name)
        done = self.recv('1')
        for cls, name in ((CorrectionRepository, 'insert_correction'), (IntermediaryHoldingRepository, 'insert_stock_movement'),
                          (IntermediaryHoldingRepository, 'insert_receipt_movement')):
            before = self.rstate()
            with self.subTest(stage=f'reverse/{name}'), failing(cls, name), self.assertRaisesRegex(RuntimeError, '051 injected'):
                self.undo(done['receipt_id'])
            self.assertEqual(self.rstate(), before, name)
        self.assertEqual(self.conserved(), D('2.950'))

    # ------------------------------------------------------------------ controlled RR snapshots
    def test_rr_snapshot_the_balance_is_a_current_read(self):
        with self.tx() as reader:
            q = reader.cursor()
            q.execute('SELECT COUNT(*) FROM holding_receipts')
            q.fetchone()
            q.close()
            self.recv('2')                                                                     # committed elsewhere: 0.950 g left
            service = HoldingReceiptService(self.session, transaction_factory=lambda: self.on_connection(reader))
            with self.assertRaises(TripError) as exceeded:
                service.receive(str(self.pool), '2', '2', DATE, '', '', str(uuid4()))
            self.assertEqual(exceeded.exception.code, 'HOLDING_EXCEEDED')
            self.assertIn('0.950', str(exceeded.exception))
            reader.rollback()

    def test_rr_snapshot_a_second_reversal_decides_on_the_committed_state(self):
        done = self.recv('1')
        with self.tx() as reader:
            q = reader.cursor()
            q.execute('SELECT COUNT(*) FROM transaction_corrections')
            q.fetchone()
            q.close()
            self.undo(done['receipt_id'])
            reversing = HoldingReceiptService(self.session, transaction_factory=lambda: self.on_connection(reader),
                                              corrections=CorrectionService(self.session, transaction_factory=lambda: self.on_connection(reader)))
            with self.assertRaises(CorrectionError) as twice:
                reversing.reverse(str(done['receipt_id']), 'stale')
            self.assertEqual(twice.exception.code, 'ALREADY_REVERSED')
            reader.rollback()
        self.assertEqual(self.conserved(), D('2.950'))

    # ------------------------------------------------------------------ resource order and contention (no production authorization locks)
    def test_real_acquisition_order_for_posting_and_for_the_reversal(self):
        order = []

        def wrap(cls, name, label, locking_only=False):
            original = getattr(cls, name)

            def wrapper(instance, *args, **kwargs):
                if not locking_only or kwargs.get('lock'):
                    order.append(label(args) if callable(label) else label)
                return original(instance, *args, **kwargs)
            return patch.object(cls, name, wrapper)
        stack = (wrap(MarketTripRepository, 'trip', 'trip', True), wrap(MarketTripRepository, 'lock_lots', 'lots'),
                 wrap(SupplierGoldSettlementRepository, 'get_supplier', lambda a: f'party:{int(a[0])}', True),
                 wrap(IntermediaryHoldingRepository, 'lock_pool', 'pool'), wrap(IntermediaryHoldingRepository, 'lock_piece', 'piece'))
        for p in stack:
            p.start()
        try:
            done = self.recv('1')
            self.assertEqual(order, ['lots', f'party:{int(self.party)}', 'pool'])
            order.clear()
            self.undo(done['receipt_id'])
            self.assertEqual(order[:4], ['lots', f'party:{int(self.party)}', 'pool', 'piece'])
            self.assertNotIn('trip', order)
        finally:
            for p in reversed(stack):
                p.stop()

    def test_a_reversal_waiting_for_the_piece_already_holds_the_lot_the_holder_and_the_pool_but_not_the_trip(self):
        done = self.recv('1')
        results = {}
        with self.tx() as holder:                                                              # raw holder of the piece: a future consumer in the lock protocol
            q = holder.cursor()
            q.execute('SELECT id FROM receipt_pieces WHERE id=%s FOR UPDATE', (done['piece_id'],))
            q.fetchall()
            q.close()
            service = HoldingReceiptService(self.session, transaction_factory=self.tx, corrections=CorrectionService(
                self.session, transaction_factory=self.tx, authorization=UnlockedAuthorization(self.session)))
            worker = self.thread(results, 'R', lambda: self.undo(done['receipt_id'], service=service))
            try:
                self.wait_waiting('receipt_pieces')
                with self.tx() as observer:
                    lot_lock = self.probe(observer, 'SELECT id FROM market_trip_lots ORDER BY id DESC LIMIT 1 FOR UPDATE NOWAIT')
                    party_lock = self.probe(observer, 'SELECT id FROM suppliers WHERE id=%s FOR UPDATE NOWAIT', (int(self.party),))
                    pool_lock = self.probe(observer, 'SELECT id FROM intermediary_holding_pools WHERE id=%s FOR UPDATE NOWAIT', (self.pool,))
                    trip_lock = self.probe(observer, 'SELECT id FROM market_trips WHERE id=%s FOR UPDATE NOWAIT', (self.origin_trip['trip_id'],))
            finally:
                holder.commit()
                worker.join(60)
        self.assertEqual((lot_lock, party_lock, pool_lock), (3572, 3572, 3572))                # lot -> holder -> pool, then the piece
        self.assertIsNone(trip_lock)
        self.assertIsInstance(results['R'], dict, results['R'])

    def test_two_receipts_racing_on_one_pool_never_exceed_its_balance(self):
        sessions = [self.make_session(f'receipt-racer{i}', 'OWNER')[0] for i in range(2)]
        services = [HoldingReceiptService(s, transaction_factory=self.tx, authorization=UnlockedAuthorization(s)) for s in sessions]
        barrier, results = threading.Barrier(2), {}

        def race(index):
            def call():
                barrier.wait(timeout=10)
                return self.recv('2', service=services[index])
            return call
        workers = [self.thread(results, 'A', race(0)), self.thread(results, 'B', race(1))]
        for worker in workers:
            worker.join(60)
        outcomes = list(results.values())
        self.assertEqual(sum(isinstance(r, dict) for r in outcomes), 1, outcomes)
        self.assertEqual(sum(isinstance(r, TripError) and r.code == 'HOLDING_EXCEEDED' for r in outcomes), 1, outcomes)
        self.assertEqual((self.pool_grams(), self.stock(), self.conserved()), (D('0.950'), D('2.000'), D('2.950')))

    def test_a_receipt_and_a_settlement_racing_on_one_pool_conserve_the_grams(self):
        sessions = [self.make_session(f'receipt-settle{i}', 'OWNER')[0] for i in range(2)]
        receive = HoldingReceiptService(sessions[0], transaction_factory=self.tx, authorization=UnlockedAuthorization(sessions[0]))
        settle = HoldingSettlementService(sessions[1], transaction_factory=self.tx, authorization=UnlockedAuthorization(sessions[1]))
        barrier, results = threading.Barrier(2), {}

        def take():
            barrier.wait(timeout=10)
            return self.recv('2', service=receive)

        def give():
            barrier.wait(timeout=10)
            return self.settle('LOT', self.pool, self.s21, '2', ENTITLEMENT, service=settle)
        workers = [self.thread(results, 'R', take), self.thread(results, 'S', give)]
        for worker in workers:
            worker.join(60)
        self.assertEqual(sum(isinstance(r, dict) for r in results.values()), 1, results)
        self.assertEqual(self.conserved(), D('2.950'))
        self.assertEqual(self.pool_grams(), D('0.950'))

    def test_two_reversals_racing_on_one_receipt_reverse_it_once(self):
        done = self.recv('1')
        sessions = [self.make_session(f'receipt-undo{i}', 'OWNER')[0] for i in range(2)]
        services = [HoldingReceiptService(s, transaction_factory=self.tx, corrections=CorrectionService(
            s, transaction_factory=self.tx, authorization=UnlockedAuthorization(s))) for s in sessions]
        barrier, results = threading.Barrier(2), {}

        def race(index):
            def call():
                barrier.wait(timeout=10)
                return self.undo(done['receipt_id'], service=services[index])
            return call
        workers = [self.thread(results, 'A', race(0)), self.thread(results, 'B', race(1))]
        for worker in workers:
            worker.join(60)
        outcomes = list(results.values())
        self.assertEqual(sum(isinstance(r, dict) for r in outcomes), 1, outcomes)
        self.assertEqual(sum(isinstance(r, CorrectionError) and r.code == 'ALREADY_REVERSED' for r in outcomes), 1, outcomes)
        self.assertEqual((self.pool_grams(), self.stock(), self.conserved()), (D('2.950'), D('0'), D('2.950')))


def load_tests(loader, tests, pattern):
    """Only the tests defined here: the class inherits the 048 / 049 / 050 helpers and tests, which their own modules run."""
    suite = unittest.TestSuite()
    suite.addTests(ReceiptMySqlTests(name) for name in loader.getTestCaseNames(ReceiptMySqlTests) if name in ReceiptMySqlTests.__dict__)
    return suite


if __name__ == '__main__':
    unittest.main()
