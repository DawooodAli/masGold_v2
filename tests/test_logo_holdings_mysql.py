"""Explicit opt-in rehearsal checks for the 049 master-karat LOGO holdings. They connect to masgold_rehearsal ONLY (target, migration level and marker are
asserted) and never to the DEV database. Real services, real repositories, real SQL, separate REPEATABLE READ connections.

Evidence classes, kept apart on purpose: functional checks (real services over MySQL); controlled RR snapshot checks (a snapshot is taken on purpose, then
another connection commits); resource-order / contention checks that remove the production authorization locks (raw holders and UnlockedAuthorization;
production authorization X-locks the actor and the shared role / permission rows until commit and serializes every protected writer first). None of this
is a stress test and none of it proves general concurrency safety or deadlock freedom."""
from decimal import Decimal as D
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from repositories import market_trip_repository as trip_module
from repositories.correction_repository import CorrectionRepository
from repositories.intermediary_holding_repository import IntermediaryHoldingRepository
from repositories.market_trip_repository import MarketTripRepository
from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository
from services.correction_service import CorrectionError, CorrectionService
from services.intermediary_holding_service import IntermediaryHoldingService
from services.market_trip_service import MarketTripService, TripError
from services.supplier_rules import SupplierRuleError
from tests.test_intermediary_holdings_mysql import DATE, HoldingsMySqlTests
from tests.test_market_trip_hardening_mysql import Legacy, UnlockedAuthorization, snapshot_form


class LogoMySqlTests(HoldingsMySqlTests):
    def trip(self, grams='20', karat=21, designation='LOGO'):
        return self.service.issue('', '', [{'karat_id': self.karats[karat], 'weight': grams, 'designation': designation}], DATE, '049', str(uuid4()))

    def logo(self, trip, karat=21, service=None):
        rows = {(g['karat_id'], g['designation']): g for g in (service or self.service).trip_detail(trip['trip_id'])['custody']['gold']}
        return rows[(self.karats[karat], 'LOGO')]

    def put(self, trip, grams, party=None, karat=21, key=None, service=None):
        return (service or self.holding).hand_over_logo(trip['trip_id'], party or self.party, self.karats[karat], grams, DATE, '049 ref', '049 notes', key or str(uuid4()))

    def take(self, pool, grams, key=None, service=None):
        return (service or self.holding).return_logo(str(pool), grams, DATE, '', '', key or str(uuid4()))

    def pool_of(self, document_id):
        return int(self.query('SELECT pool_id FROM logo_holding_documents WHERE id=%s', (document_id,))[0]['pool_id'])

    def balance(self, pool):
        return self.query('SELECT COALESCE(SUM(weight_delta),0) AS g FROM logo_holding_movements WHERE pool_id=%s', (pool,))[0]['g']

    def snapshot(self):
        tables = ('logo_holding_pools', 'logo_holding_documents', 'logo_holding_movements', 'market_trip_logo_hold_links', 'market_trip_entries',
                  'transaction_corrections', 'audit_log', 'intermediary_holding_pools')
        return {t: self.query(f'SELECT COUNT(*) AS n FROM {t}')[0]['n'] for t in tables}

    # ------------------------------------------------------------------ functional
    def test_exact_effects_partial_returns_and_both_valid_reversal_orders(self):
        trip = self.trip()
        unchanged = self.untouched()
        handed = self.put(trip, '8')
        self.assertEqual((handed['kind'], handed['weight'], handed['duplicate']), ('LOGO_HOLD', '8.000', False))
        pool = self.pool_of(handed['document_id'])
        self.assertEqual((self.logo(trip)['outstanding'], self.balance(pool)), (D('12.000'), D('8.000')))
        self.assertEqual(self.untouched(), unchanged)                                        # no Scrap, cash, making, supplier, shop-lot or inventory movement
        r1, r2 = self.take(pool, '3'), self.take(pool, '2')
        self.assertEqual((self.logo(trip)['outstanding'], self.balance(pool)), (D('17.000'), D('3.000')))
        with self.assertRaises(TripError) as over:
            self.take(pool, '3.001')
        self.assertEqual(over.exception.code, 'LOGO_RETURN_EXCEEDS_HOLDING')
        with self.assertRaises(CorrectionError) as blocked:
            self.correct.reverse('MARKET_TRIP_ENTRY', handed['entry_id'], '049')
        self.assertEqual(blocked.exception.code, 'DEPENDENT_HOLDING_RETURN_EXISTS')
        self.correct.reverse('MARKET_TRIP_ENTRY', r1['entry_id'], '049 older return first')          # either order, where arithmetic allows it
        self.assertEqual((self.logo(trip)['outstanding'], self.balance(pool)), (D('14.000'), D('6.000')))
        self.correct.reverse('MARKET_TRIP_ENTRY', r2['entry_id'], '049')
        self.correct.reverse('MARKET_TRIP_ENTRY', handed['entry_id'], '049')
        self.assertEqual((self.logo(trip)['outstanding'], self.balance(pool)), (D('20.000'), D('0')))
        moves = self.query('SELECT movement_type, weight_delta, correction_id FROM logo_holding_movements WHERE pool_id=%s ORDER BY id', (pool,))
        self.assertEqual(sorted(m['movement_type'] for m in moves), ['HANDOVER', 'HANDOVER_REVERSAL', 'RETURN', 'RETURN', 'RETURN_REVERSAL', 'RETURN_REVERSAL'])
        self.assertEqual(sum((m['weight_delta'] for m in moves), D('0')), D('0'))              # compensations counted once, originals stay
        with self.assertRaises(CorrectionError) as again:
            self.correct.reverse('MARKET_TRIP_ENTRY', handed['entry_id'], 'x')
        self.assertEqual(again.exception.code, 'ALREADY_REVERSED')
        self.assertEqual(self.untouched(), unchanged)

    def test_reused_returned_grams_block_the_return_reversal_and_the_other_return_order_works(self):
        trip = self.trip()
        handed = self.put(trip, '10')
        pool = self.pool_of(handed['document_id'])
        r1, r2 = self.take(pool, '4'), self.take(pool, '6')
        self.correct.reverse('MARKET_TRIP_ENTRY', r2['entry_id'], '049 newer first')
        self.correct.reverse('MARKET_TRIP_ENTRY', r1['entry_id'], '049')
        r3 = self.take(pool, '10')
        self.service.return_custody(trip['trip_id'], '', [{'karat_id': self.karats[21], 'weight': '20', 'designation': 'LOGO'}], DATE, '', str(uuid4()))   # the grams are reused
        with self.assertRaises(CorrectionError) as used:
            self.correct.reverse('MARKET_TRIP_ENTRY', r3['entry_id'], '049')
        self.assertEqual(used.exception.code, 'DEPENDENT_CUSTODY_USE_EXISTS')
        self.assertEqual(self.balance(pool), D('0'))

    def test_heating_before_and_after_a_handover_for_loss_and_gain(self):
        trip = self.trip()
        heat = lambda before, after, note='': self.service.heat(trip['trip_id'], [{'karat_id': self.karats[21], 'input_weight': before, 'output_weight': after}], DATE, 'ws', '', note, '', str(uuid4()))
        loss = heat('5', '4.5')                                                                 # 20 -> 19.5
        handed = self.put(trip, '19.5')
        with self.assertRaises(TripError) as unheatable:
            heat('1', '1')
        self.assertEqual(unheatable.exception.code, 'HEAT_EXCEEDS_CUSTODY')                    # the handed-over grams are outside the custody
        pool = self.pool_of(handed['document_id'])
        self.take(pool, '19.5')
        gain = heat('19.5', '20', 'كسب')
        self.assertEqual(self.logo(trip)['outstanding'], D('20.000'))
        self.put(trip, '20')
        with self.assertRaises(CorrectionError):                                                # reversing the gain would make custody negative
            self.correct.reverse('MARKET_TRIP_ENTRY', gain['entry_id'], '049')
        self.assertEqual(self.logo(trip)['outstanding'], D('0.000'))
        self.assertIsNotNone(loss)

    def test_multiple_sources_closure_reopening_and_dual_role_account_separation(self):
        trip = self.trip('10')
        first, second = self.put(trip, '4'), self.put(trip, '6', party=self.dual)             # a dual-role (21K supplier) party holds 21K LOGO grams
        pools = {self.pool_of(first['document_id']), self.pool_of(second['document_id'])}
        self.assertEqual(len(pools), 2)
        self.assertEqual(self.query('SELECT COUNT(*) AS n FROM supplier_gold_movements WHERE supplier_id=%s', (self.dual,))[0]['n'], 0)
        with self.assertRaises(TripError) as cross:
            self.take(self.pool_of(first['document_id']), '5')                                 # no cross-pool allocation
        self.assertEqual(cross.exception.code, 'LOGO_RETURN_EXCEEDS_HOLDING')
        self.assertEqual(self.service.close(trip['trip_id'])['status'], 'CLOSED')             # its own outstanding is zero
        detail = self.service.trip_detail(trip['trip_id'])
        self.assertEqual(sorted(p['balance'] for p in detail['external_holdings'] if p.get('logo')), [D('4.000'), D('6.000')])
        with self.assertRaises(TripError) as closed:
            self.take(self.pool_of(first['document_id']), '1')
        self.assertEqual(closed.exception.code, 'TRIP_CLOSED')
        self.service.reopen(trip['trip_id'], '049 explicit')
        self.take(self.pool_of(first['document_id']), '4')
        self.assertEqual(self.logo(trip)['outstanding'], D('4.000'))

    def test_party_rules_permissions_deactivation_and_history_guards(self):
        trip = self.trip()
        for party, code in ((self.supplier, 'INTERMEDIARY_ROLE_REQUIRED'), ('999999999', 'PARTY_NOT_FOUND')):
            with self.assertRaises(TripError) as refusal:
                self.put(trip, '1', party=party)
            self.assertEqual(refusal.exception.code, code)
        manager, _ = self.make_session('logo-manager', 'MANAGER')
        before = self.snapshot()
        with self.assertRaises(PermissionError):
            self.put(trip, '1', service=IntermediaryHoldingService(manager, transaction_factory=self.tx))
        self.assertEqual(before, self.snapshot())
        for bad in ('0', '-1', '1.0001', 'x'):
            with self.assertRaises(TripError):
                self.put(trip, bad)
        with self.assertRaises(TripError) as exceeds:
            self.put(trip, '20.001')
        self.assertEqual(exceeds.exception.code, 'LOGO_EXCEEDS_CUSTODY')
        done = self.put(trip, '5', party=self.party2)
        pool = self.pool_of(done['document_id'])
        self.suppliers.set_active(self.party2, False)
        try:
            with self.assertRaises(TripError) as inactive:
                self.put(trip, '1', party=self.party2)
            self.assertEqual(inactive.exception.code, 'PARTY_INACTIVE')
            self.assertEqual(self.take(pool, '5')['kind'], 'HOLD_BACK')                        # existing holdings stay resolvable
        finally:
            self.suppliers.set_active(self.party2, True)
        with self.assertRaises(SupplierRuleError) as role:
            self.suppliers.update_supplier(self.party2, 'x ' + uuid4().hex[:8], is_supplier=False, is_intermediary=False)
        self.assertIn(role.exception.code, ('ROLE_REQUIRED', 'ROLE_HISTORY_EXISTS'))
        other = self.suppliers.create_supplier('049 only-logo ' + uuid4().hex[:8], is_supplier=False, is_intermediary=True)
        self.put(trip, '1', party=other)
        with self.assertRaises(SupplierRuleError) as delete:
            self.suppliers.delete_unused_supplier(other)
        self.assertEqual(delete.exception.code, 'SUPPLIER_IN_USE')
        detail = self.suppliers.supplier_detail(other)
        self.assertEqual([p['balance'] for p in detail['holdings']['logo_pools']], [D('1.000')])

    def test_replay_and_cross_flow_key_conflicts(self):
        trip = self.trip()
        key = str(uuid4())
        first = self.put(trip, '20', key=key)
        before = self.snapshot()
        again = self.put(trip, '20', key=key)                                                  # nothing is left: no availability is derived
        self.assertEqual((again['duplicate'], again['document_id']), (True, first['document_id']))
        self.assertEqual(before, self.snapshot())
        for call in (lambda: self.put(trip, '19', key=key), lambda: self.service.issue('', '1', [], DATE, '', key),
                     lambda: self.take(self.pool_of(first['document_id']), '1', key=key)):
            with self.assertRaises(TripError) as conflict:
                call()
            self.assertEqual(conflict.exception.code, 'REQUEST_CONFLICT')
        from services.supplier_gold_settlement_service import SupplierGoldSettlementService
        with self.assertRaises(SupplierRuleError) as direct:
            SupplierGoldSettlementService(self.session, transaction_factory=self.tx).settle(self.supplier, self.karats[21], '1', DATE, '', '', key)
        self.assertEqual(direct.exception.code, 'REQUEST_CONFLICT')
        lot_trip, melt, lots = self.lots()
        before = self.snapshot()
        with self.assertRaises(TripError) as lot_flow:
            self.hand(lot_trip, lots[0], key=key)                                              # a 048 whole-lot handover cannot reuse a LOGO key
        self.assertEqual(lot_flow.exception.code, 'REQUEST_CONFLICT')
        self.assertEqual(before, self.snapshot())

    def test_rollback_after_each_persistent_stage_of_handover_return_and_reversal(self):
        trip = self.trip()
        stages = ((IntermediaryHoldingRepository, 'insert_logo_pool'), (MarketTripRepository, 'insert_entry'), (IntermediaryHoldingRepository, 'insert_logo_document'),
                  (IntermediaryHoldingRepository, 'insert_logo_movement'), (IntermediaryHoldingRepository, 'insert_logo_link'),
                  (MarketTripRepository, 'audit'), (IntermediaryHoldingRepository, 'audit'))

        def failing(cls, name):
            original = getattr(cls, name)

            def fail(instance, *a, _original=original, **kw):
                _original(instance, *a, **kw)
                raise RuntimeError('049 injected')
            return patch.object(cls, name, fail)
        for cls, name in stages:
            before = self.snapshot()
            with self.subTest(stage=f'hand/{name}'), failing(cls, name), self.assertRaisesRegex(RuntimeError, '049 injected'):
                self.put(trip, '5')
            self.assertEqual(before, self.snapshot(), name)
        done = self.put(trip, '5')
        pool = self.pool_of(done['document_id'])
        for cls, name in stages[1:]:
            if name == 'insert_logo_pool':
                continue
            before = self.snapshot()
            with self.subTest(stage=f'return/{name}'), failing(cls, name), self.assertRaisesRegex(RuntimeError, '049 injected'):
                self.take(pool, '2')
            self.assertEqual(before, self.snapshot(), name)
        for cls, name in ((CorrectionRepository, 'insert_correction'), (IntermediaryHoldingRepository, 'insert_logo_movement')):
            before = self.snapshot()
            with self.subTest(stage=f'reverse/{name}'), failing(cls, name), self.assertRaisesRegex(RuntimeError, '049 injected'):
                self.correct.reverse('MARKET_TRIP_ENTRY', done['entry_id'], '049')
            self.assertEqual(before, self.snapshot(), name)
        self.assertEqual(self.balance(pool), D('5'))

    def test_existing_048_whole_lot_behaviour_is_unchanged(self):
        trip, melt, lots = self.lots()
        done = self.hand(trip, lots[0])
        self.assertEqual(done['kind'], 'LOT_HOLD')
        returned = self.back(self.query('SELECT pool_id FROM intermediary_holding_documents WHERE id=%s', (done['document_id'],))[0]['pool_id'])
        self.assertEqual(returned['kind'], 'HOLD_RETURN')
        self.correct.reverse('MARKET_TRIP_ENTRY', returned['entry_id'], '048 still works')

    # ------------------------------------------------------------------ controlled RR snapshots
    def test_rr_snapshot_before_a_reversed_handover_the_current_terms_restore_the_custody_availability(self):
        """Controlled: the snapshot is taken while 8 g are out; another connection reverses the handover. The pre-hardening query shape still counts
        the handover (a false refusal of a 20 g handover); the current form and the service see the restored 20 g."""
        trip = self.trip()
        handed = self.put(trip, '8')
        with self.tx() as reader:
            q = reader.cursor()
            q.execute('SELECT COUNT(*) FROM transaction_corrections')
            q.fetchone()
            q.close()
            self.correct.reverse('MARKET_TRIP_ENTRY', handed['entry_id'], '049 RR')
            current, legacy = MarketTripRepository(reader), MarketTripRepository(Legacy(reader))
            held = lambda repo: next(r for r in repo.custody_gold(int(trip['trip_id']), current=True) if r['designation'] == 'LOGO')
            self.assertEqual(held(legacy)['held_out'], D('8.000'))                             # control: stale
            self.assertEqual(held(current).get('held_out', 0), 0)                              # hardened: the reversed handover no longer counts
            service = IntermediaryHoldingService(self.session, transaction_factory=lambda: self.on_connection(reader))
            with patch.object(trip_module, '_not_reversed', snapshot_form):
                with self.assertRaises(TripError) as stale:
                    service.hand_over_logo(trip['trip_id'], self.party, self.karats[21], '20', DATE, '', '', str(uuid4()))
            self.assertEqual(stale.exception.code, 'LOGO_EXCEEDS_CUSTODY')                     # without the current nested read: a false refusal
            posted = service.hand_over_logo(trip['trip_id'], self.party, self.karats[21], '20', DATE, '', '', str(uuid4()))
            self.assertEqual(posted['weight'], '20.000')                                       # with it: the real availability
            reader.rollback()
        self.assertEqual(self.logo(trip)['outstanding'], D('20.000'))

    def test_rr_snapshot_before_a_reversed_return_the_reversal_decisions_see_current_balances(self):
        trip = self.trip()
        handed = self.put(trip, '10')
        pool = self.pool_of(handed['document_id'])
        returned = self.take(pool, '4')
        with self.tx() as reader:
            q = reader.cursor()
            q.execute('SELECT COUNT(*) FROM logo_holding_movements')
            q.fetchone()
            q.close()
            self.correct.reverse('MARKET_TRIP_ENTRY', returned['entry_id'], '049 RR')
            self.assertEqual(IntermediaryHoldingRepository(reader).logo_pool_balance(pool), D('10'))      # a CURRENT read sees the compensation
            reversing = CorrectionService(self.session, transaction_factory=lambda: self.on_connection(reader))
            reversing.reverse('MARKET_TRIP_ENTRY', handed['entry_id'], '049 RR handover')        # decided on the committed state, not the snapshot
        self.assertEqual((self.logo(trip)['outstanding'], self.balance(pool)), (D('20.000'), D('0')))

    # ------------------------------------------------------------------ resource order and contention (no production authorization locks)
    def test_real_acquisition_order_of_handover_return_and_reversal(self):
        trip = self.trip()
        order = []

        def wrap(cls, name, label, only_locking=False):
            original = getattr(cls, name)

            def wrapper(instance, *args, **kwargs):
                if not only_locking or kwargs.get('lock'):
                    order.append(label)
                return original(instance, *args, **kwargs)
            return patch.object(cls, name, wrapper)
        stack = (wrap(MarketTripRepository, 'trip', 'trip', True), wrap(SupplierGoldSettlementRepository, 'get_supplier', 'party', True),
                 wrap(IntermediaryHoldingRepository, 'lock_logo_pool', 'pool'), wrap(MarketTripRepository, 'lock_lots', 'lots'))
        for p in stack:
            p.start()
        try:
            done = self.put(trip, '6')
            self.assertEqual(order, ['trip', 'party'])
            order.clear()
            returned = self.take(self.pool_of(done['document_id']), '2')
            self.assertEqual(order, ['trip', 'party', 'pool'])
            order.clear()
            self.correct.reverse('MARKET_TRIP_ENTRY', returned['entry_id'], '049')
            self.assertEqual([o for o in order if o != 'lots'][:3], ['trip', 'party', 'pool'])
        finally:
            for p in reversed(stack):
                p.stop()

    def test_a_return_waiting_for_the_pool_holds_the_trip_and_the_party(self):
        trip = self.trip()
        done = self.put(trip, '6')
        pool = self.pool_of(done['document_id'])
        results = {}
        with self.tx() as holder:                                                              # raw holder: no authorization locks involved
            q = holder.cursor()
            q.execute('SELECT id FROM logo_holding_pools WHERE id=%s FOR UPDATE', (pool,))
            q.fetchall()
            q.close()
            worker = self.thread(results, 'R', lambda: self.take(pool, '2'))
            try:
                self.wait_waiting('logo_holding_pools')
                with self.tx() as observer:
                    trip_lock = self.probe(observer, 'SELECT id FROM market_trips WHERE id=%s FOR UPDATE NOWAIT', (trip['trip_id'],))
                    party_lock = self.probe(observer, 'SELECT id FROM suppliers WHERE id=%s FOR UPDATE NOWAIT', (int(self.party),))
            finally:
                holder.commit()
                worker.join(60)
        self.assertEqual((trip_lock, party_lock), (3572, 3572))                               # trip -> party -> pool
        self.assertIsInstance(results['R'], dict, results['R'])

    def test_two_returns_racing_on_one_source_never_exceed_its_balance(self):
        """Controlled schedule with UnlockedAuthorization (the trip lock serializes them): 6 g held, two returns of 4 g."""
        trip = self.trip()
        done = self.put(trip, '6')
        pool = self.pool_of(done['document_id'])
        sessions = [self.make_session(f'logo-racer{i}', 'OWNER')[0] for i in range(2)]
        services = [IntermediaryHoldingService(s, transaction_factory=self.tx, authorization=UnlockedAuthorization(s)) for s in sessions]
        barrier, results = threading.Barrier(2), {}

        def race(index):
            def call():
                barrier.wait(timeout=10)
                return services[index].return_logo(str(pool), '4', DATE, '', '', str(uuid4()))
            return call
        workers = [self.thread(results, 'A', race(0)), self.thread(results, 'B', race(1))]
        for worker in workers:
            worker.join(60)
        outcomes = list(results.values())
        self.assertEqual(sum(isinstance(r, dict) for r in outcomes), 1, outcomes)
        self.assertEqual(sum(isinstance(r, TripError) and r.code == 'LOGO_RETURN_EXCEEDS_HOLDING' for r in outcomes), 1, outcomes)
        self.assertEqual((self.balance(pool), self.logo(trip)['outstanding']), (D('2.000'), D('18.000')))

    def test_a_handover_and_a_custody_return_race_on_the_same_karat_stay_conserved(self):
        trip = self.trip()
        sessions = [self.make_session(f'logo-duel{i}', 'OWNER')[0] for i in range(2)]
        holding = IntermediaryHoldingService(sessions[0], transaction_factory=self.tx, authorization=UnlockedAuthorization(sessions[0]))
        custody = MarketTripService(sessions[1], transaction_factory=self.tx, authorization=UnlockedAuthorization(sessions[1]))
        barrier, results = threading.Barrier(2), {}

        def hand():
            barrier.wait(timeout=10)
            return holding.hand_over_logo(trip['trip_id'], self.party, self.karats[21], '15', DATE, '', '', str(uuid4()))

        def give_back():
            barrier.wait(timeout=10)
            return custody.return_custody(trip['trip_id'], '', [{'karat_id': self.karats[21], 'weight': '10', 'designation': 'LOGO'}], DATE, '', str(uuid4()))
        workers = [self.thread(results, 'H', hand), self.thread(results, 'C', give_back)]
        for worker in workers:
            worker.join(60)
        handed = D('15') if isinstance(results['H'], dict) else D('0')
        returned = D('10') if isinstance(results['C'], dict) else D('0')
        self.assertGreaterEqual(self.logo(trip)['outstanding'], 0)
        self.assertEqual(self.logo(trip)['outstanding'], D('20') - handed - returned)           # whichever order won, nothing is created or lost
        if not isinstance(results['H'], dict):
            self.assertEqual(results['H'].code, 'LOGO_EXCEEDS_CUSTODY')
        if not isinstance(results['C'], dict):
            self.assertEqual(results['C'].code, 'RETURN_EXCEEDS_CUSTODY')


def load_tests(loader, tests, pattern):
    """Only the tests defined here: the class inherits the 048 helpers and tests, which their own module runs."""
    suite = unittest.TestSuite()
    suite.addTests(LogoMySqlTests(name) for name in loader.getTestCaseNames(LogoMySqlTests) if name in LogoMySqlTests.__dict__)
    return suite


if __name__ == '__main__':
    unittest.main()
