"""Explicit opt-in rehearsal checks for the 048 intermediary lot holdings. They connect to masgold_rehearsal ONLY (target, migration level and marker are
asserted) and never to the DEV database. Real services, real repositories, real SQL, separate REPEATABLE READ connections.

Evidence classes (kept apart on purpose):
* functional checks: the real services over MySQL (exact effects, replay, permissions, rollback, dependencies);
* controlled RR snapshot checks: a snapshot is taken on purpose, then another connection commits;
* resource-order / contention checks: raw holders and UnlockedAuthorization remove the production authorization locks (which X-lock the actor and the
  shared role / permission rows until commit and would serialize every protected writer before it reaches a resource). They are NOT a stress test
  and prove nothing about general concurrency safety or deadlock freedom."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal as D
import os
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.session import AppSession
from config.database_config import DatabaseConfig
from database.connection import DatabaseConnection
from repositories import market_trip_repository as trip_module
from repositories.authentication_repository import AuthenticationRepository
from repositories.correction_repository import CorrectionRepository
from repositories.intermediary_holding_repository import IntermediaryHoldingRepository
from repositories.market_trip_repository import MarketTripRepository
from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.correction_service import CorrectionError, CorrectionService
from services.holding_settlement_service import HoldingSettlementService
from services.intermediary_holding_service import IntermediaryHoldingService
from services.market_trip_service import MarketTripService, TripError
from services.supplier_rules import SupplierRuleError
from services.supplier_service import SupplierService
from tests.test_market_trip_hardening_mysql import Legacy, UnlockedAuthorization, snapshot_form

TARGET = 'masgold_rehearsal'
DATE = '2026-10-12'


@unittest.skipUnless(os.environ.get('MASGOLD_HOLDINGS_REHEARSAL') == '1', 'explicit rehearsal-only opt-in')
class HoldingsMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = replace(DatabaseConfig.from_env(), database=TARGET)
        with cls.tx() as c:
            q = c.cursor(dictionary=True)
            q.execute('SELECT DATABASE() AS db, MAX(version) AS v FROM schema_migrations')
            row = q.fetchone()
            assert row['db'] == TARGET and row['v'] in (48, 49, 50, 51, 52, 53), row
            q.execute("SELECT target_database FROM masgold_rehearsal_marker WHERE marker_key='MASGOLD_SCHEMA_REHEARSAL'")
            assert q.fetchone()['target_database'] == TARGET
            q.execute('SELECT id,karat_value FROM karats')
            cls.karats = {r['karat_value']: r['id'] for r in q.fetchall()}
            q.close()
        cls.session, cls.user = cls.make_session('owner', 'OWNER')
        with cls.tx() as c:
            q = c.cursor()
            q.execute("INSERT INTO old_gold_movements(movement_type_id,karat_id,weight_delta_grams,created_by_user_id) "
                      "SELECT id,%s,10000,%s FROM old_gold_movement_types WHERE code='OPENING_BALANCE'", (cls.karats[21], cls.user))
            assert q.rowcount == 1
            q.close()
        suppliers = SupplierService(cls.session, transaction_factory=cls.tx)
        cls.party = suppliers.create_supplier('048 مسوقجي ' + uuid4().hex[:10], is_supplier=False, is_intermediary=True)
        cls.party2 = suppliers.create_supplier('048 مسوقجي ثان ' + uuid4().hex[:10], is_supplier=False, is_intermediary=True)
        cls.dual = suppliers.create_supplier('048 مورد ومسوقجي ' + uuid4().hex[:10], karat_id=cls.karats[21], is_supplier=True, is_intermediary=True)
        cls.supplier = suppliers.create_supplier('048 مورد ' + uuid4().hex[:10], karat_id=cls.karats[18])
        cls.suppliers = suppliers

    @classmethod
    def make_session(cls, label, role):
        with cls.tx() as c:
            q = c.cursor()
            q.execute('INSERT INTO employees(name) VALUES(%s)', ('048 rehearsal ' + label,))
            emp = q.lastrowid
            q.execute("INSERT INTO users(employee_id,username,password_hash,display_name) VALUES(%s,%s,'unusable-rehearsal-hash',%s)",
                      (emp, f'rehearsal-048-{label}-' + uuid4().hex[:12], '048 ' + label))
            user = q.lastrowid
            q.execute("INSERT INTO user_roles(user_id,role_id) SELECT %s,id FROM roles WHERE code=%s", (user, role))
            assert q.rowcount == 1
            q.close()
            auth = AuthenticationRepository(c)
            identity, permissions = auth.get_identity(user), auth.effective_permissions(user)
        session = AppSession()
        session._establish(AuthenticationResult(user, emp, identity['display_name'], permissions, _VERIFIED), int(session.generation))
        return session, user

    @classmethod
    @contextmanager
    def tx(cls):
        assert cls.config.database == TARGET
        c = DatabaseConnection(cls.config).connect()
        try:
            q = c.cursor()
            q.execute('SELECT DATABASE()')
            assert q.fetchone() == (TARGET,)
            q.close()
            c.rollback()
            c.start_transaction(isolation_level='REPEATABLE READ')
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    @contextmanager
    def on_connection(connection):
        yield connection

    def setUp(self):
        self.correct = CorrectionService(self.session, transaction_factory=self.tx)
        self.service = MarketTripService(self.session, transaction_factory=self.tx, corrections=self.correct)
        self.holding = IntermediaryHoldingService(self.session, transaction_factory=self.tx)

    # ------------------------------------------------------------------ helpers
    def query(self, sql, params=()):
        with self.tx() as c:
            q = c.cursor(dictionary=True)
            q.execute(sql, params)
            rows = q.fetchall()
            q.close()
            return rows

    def lots(self, weight='10', fineness='875', residues=()):
        trip = self.service.issue('', '', [{'karat_id': self.karats[21], 'weight': '20', 'designation': 'MELT'}], DATE, '048', str(uuid4()))
        melt = self.service.melt(trip['trip_id'], [{'karat_id': self.karats[21], 'weight': '20'}], DATE, 'ws', {'weight': weight, 'fineness': fineness},
                                 [{'weight': w, 'fineness': f} for w, f in residues], 'assay', '', '', str(uuid4()))
        rows = self.query('SELECT * FROM market_trip_lots WHERE entry_id=%s ORDER BY id', (melt['entry_id'],))
        return trip, melt, rows

    def hand(self, trip, lot, party=None, key=None, service=None, **kw):
        return (service or self.holding).hand_over_lot(trip['trip_id'], party or self.party, str(lot['id']), DATE, '048 ref', '048 notes', key or str(uuid4()), **kw)

    def back(self, pool, confirmed=True, key=None, service=None, physical_confirmed=False):
        service = service or self.holding
        review = service.return_review(str(pool))
        return service.return_holding(str(pool), confirmed, review['token'], physical_confirmed, DATE, '', '', key or str(uuid4()))

    def pool_of(self, document_id):
        return int(self.query('SELECT pool_id FROM intermediary_holding_documents WHERE id=%s', (document_id,))[0]['pool_id'])

    def balance(self, pool):
        return self.query('SELECT COALESCE(SUM(weight_delta),0) AS g FROM intermediary_holding_movements WHERE pool_id=%s', (pool,))[0]['g']

    def places(self, lot):
        """(custody grams, shop grams, held grams): the bar must be in exactly one place."""
        custody = self.query("SELECT COALESCE(SUM(m.weight_delta),0) AS g FROM market_trip_lot_movements m JOIN market_trip_entries e ON e.id=m.entry_id "
                             "WHERE m.lot_id=%s AND NOT EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type='MARKET_TRIP_ENTRY' "
                             "AND c.source_id=e.id AND c.correction_type='FULL_REVERSAL')", (lot['id'],))[0]['g']
        shop = self.query('SELECT COALESCE(SUM(weight_delta),0) AS g FROM market_trip_lot_shop_movements WHERE lot_id=%s', (lot['id'],))[0]['g']
        held = self.query('SELECT COALESCE(SUM(m.weight_delta),0) AS g FROM intermediary_holding_movements m JOIN intermediary_holding_pools p ON p.id=m.pool_id '
                          'WHERE p.lot_id=%s', (lot['id'],))[0]['g']
        self.assertEqual(custody + shop + held, lot['weight_grams'], 'the bar must be in exactly one place')
        return custody, shop, held

    def snapshot(self):
        tables = ('intermediary_holding_pools', 'intermediary_holding_documents', 'intermediary_holding_movements', 'market_trip_holding_links',
                  'market_trip_entries', 'market_trip_lot_movements', 'transaction_corrections', 'audit_log')
        return {t: self.query(f'SELECT COUNT(*) AS n FROM {t}')[0]['n'] for t in tables}

    def untouched(self):
        tables = ('old_gold_movements', 'cash_movements', 'bank_movements', 'supplier_making_movements', 'supplier_gold_movements',
                  'market_trip_lot_shop_movements', 'inventory_items', 'supplier_gold_settlements')
        return {t: self.query(f'SELECT COUNT(*) AS n FROM {t}')[0]['n'] for t in tables}

    def wait_waiting(self, table, timeout=20):
        deadline = time.time() + timeout
        seen = []
        while time.time() < deadline:
            seen = [r['object_name'] for r in self.query(
                "SELECT l.object_name FROM performance_schema.data_lock_waits w JOIN performance_schema.data_locks l "
                "ON l.engine_lock_id = w.requesting_engine_lock_id WHERE l.object_schema = %s", (TARGET,))]
            if table in seen:
                return seen
            time.sleep(0.1)
        self.fail(f'nothing waited on {table}: {seen}')

    @staticmethod
    def probe(connection, sql, params=()):
        q = connection.cursor()
        try:
            q.execute(sql, params)
            q.fetchall()
            return None
        except Exception as error:                                   # noqa: BLE001 - the errno is the observation
            return getattr(error, 'errno', None)
        finally:
            q.close()

    @staticmethod
    def thread(results, name, call):
        def run():
            try:
                results[name] = call()
            except BaseException as error:                           # noqa: BLE001
                results[name] = error
        worker = threading.Thread(target=run, name=name)
        worker.start()
        return worker

    # ------------------------------------------------------------------ functional
    def test_exact_effects_return_reversal_compensation_and_history(self):
        trip, melt, lots = self.lots('10', '875', residues=[('1', '500'), ('2', '')])
        main, residue, unknown = lots
        unchanged = self.untouched()
        done = self.hand(trip, main)
        self.assertEqual((done['kind'], done['weight'], done['duplicate']), ('LOT_HOLD', '10.000', False))
        self.assertEqual(self.places(main), (D('0'), D('0'), D('10')))
        self.assertEqual(self.untouched(), unchanged)                                  # no cash, Scrap, making, supplier, shop-lot or inventory movement
        pool = self.pool_of(done['document_id'])
        self.assertEqual(self.balance(pool), D('10'))
        self.assertEqual(self.service.trip_detail(trip['trip_id'])['external_holdings'][0]['balance'], D('10'))
        self.assertEqual(self.query('SELECT COUNT(*) AS n FROM market_trip_lots WHERE id=%s AND weight_grams=10 AND fineness_permille=875 AND entry_id=%s',
                                    (main['id'], melt['entry_id']))[0]['n'], 1)          # identity, weight, assay and origin are untouched
        with self.assertRaises(TripError) as unknown_assay:
            self.hand(trip, unknown)
        self.assertEqual(unknown_assay.exception.code, 'LOT_ASSAY_UNKNOWN')
        self.assertEqual(self.hand(trip, residue)['weight'], '1.000')                  # a known-assay residue hands over too
        with self.assertRaises(TripError):
            self.back(pool, confirmed=False)                                            # a full balance alone certifies nothing
        self.assertEqual(self.places(main), (D('0'), D('0'), D('10')))
        returned = self.back(pool)
        self.assertEqual((returned['kind'], returned['trip_id']), ('HOLD_RETURN', trip['trip_id']))
        self.assertEqual(self.places(main), (D('10'), D('0'), D('0')))
        document = self.query('SELECT * FROM intermediary_holding_documents WHERE id=%s', (returned['document_id'],))[0]
        self.assertEqual((document['intact_confirmation_code'], document['confirmed_by_user_id']), ('SAME_BAR_WHOLE_UNDIVIDED', self.user))
        self.assertIsNotNone(document['confirmed_at'])
        with self.assertRaises(TripError) as twice:
            self.back(pool)
        self.assertEqual(twice.exception.code, 'HOLDING_NOT_AVAILABLE')
        # reversals: the return first (newest first), then the handover, each with an explicit compensation and no exclusion of the original
        with self.assertRaises(CorrectionError) as blocked:
            self.correct.reverse('MARKET_TRIP_ENTRY', done['entry_id'], '048')
        self.assertEqual(blocked.exception.code, 'DEPENDENT_HOLDING_RETURN_EXISTS')
        self.correct.reverse('MARKET_TRIP_ENTRY', returned['entry_id'], '048 return')
        self.assertEqual(self.places(main), (D('0'), D('0'), D('10')))
        self.correct.reverse('MARKET_TRIP_ENTRY', done['entry_id'], '048 handover')
        self.assertEqual(self.places(main), (D('10'), D('0'), D('0')))
        moves = self.query('SELECT movement_type, weight_delta, correction_id FROM intermediary_holding_movements WHERE pool_id=%s ORDER BY id', (pool,))
        self.assertEqual([m['movement_type'] for m in moves], ['HANDOVER', 'RETURN', 'RETURN_REVERSAL', 'HANDOVER_REVERSAL'])
        self.assertEqual(sum((m['weight_delta'] for m in moves), D('0')), D('0'))
        self.assertEqual([m['correction_id'] is None for m in moves], [True, True, False, False])
        with self.assertRaises(CorrectionError) as again:
            self.correct.reverse('MARKET_TRIP_ENTRY', done['entry_id'], 'x')
        self.assertEqual(again.exception.code, 'ALREADY_REVERSED')
        self.assertEqual(self.untouched(), unchanged)

    def test_replay_and_cross_flow_key_conflicts(self):
        trip, melt, lots = self.lots()
        main = lots[0]
        key = str(uuid4())
        first = self.hand(trip, main, key=key)
        before = self.snapshot()
        again = self.hand(trip, main, key=key)
        self.assertEqual((again['duplicate'], again['document_id']), (True, first['document_id']))
        self.assertEqual(before, self.snapshot())
        self.correct.reverse('MARKET_TRIP_ENTRY', first['entry_id'], '048')
        self.assertEqual(self.hand(trip, main, key=key)['document_id'], first['document_id'])        # replay survives the reversal; no new availability
        for call in (lambda: self.hand(trip, main, party=self.party2, key=key),
                     lambda: self.service.issue('', '1', [], DATE, '', key),
                     lambda: self.service.settle_supplier_lot(trip['trip_id'], self.supplier, str(main['id']), DATE, '', '', key)):
            with self.assertRaises(TripError) as conflict:
                call()
            self.assertEqual(conflict.exception.code, 'REQUEST_CONFLICT')
        from services.supplier_gold_settlement_service import SupplierGoldSettlementService
        direct = SupplierGoldSettlementService(self.session, transaction_factory=self.tx)
        with self.assertRaises(SupplierRuleError) as direct_conflict:
            direct.settle(self.supplier, self.karats[21], '1', DATE, '', '', key)
        self.assertEqual(direct_conflict.exception.code, 'REQUEST_CONFLICT')
        self.assertEqual(before['intermediary_holding_pools'], self.snapshot()['intermediary_holding_pools'])
        other = str(uuid4())
        self.service.issue('', '1', [], DATE, '', other)
        with self.assertRaises(TripError) as taken:
            self.hand(trip, main, key=other)
        self.assertEqual(taken.exception.code, 'REQUEST_CONFLICT')

    def test_party_rules_permissions_and_deactivation(self):
        trip, melt, lots = self.lots()
        main = lots[0]
        for party, code in ((self.supplier, 'INTERMEDIARY_ROLE_REQUIRED'), ('999999999', 'PARTY_NOT_FOUND')):
            with self.assertRaises(TripError) as refusal:
                self.hand(trip, main, party=party)
            self.assertEqual(refusal.exception.code, code)
        manager, _ = self.make_session('manager', 'MANAGER')
        before = self.snapshot()
        with self.assertRaises(PermissionError):
            self.hand(trip, main, service=IntermediaryHoldingService(manager, transaction_factory=self.tx))
        self.assertEqual(before, self.snapshot())
        revoked, _ = self.make_session('viewer', 'MANAGER')
        with self.assertRaises(PermissionError):
            IntermediaryHoldingService(revoked, transaction_factory=self.tx).party_holdings(self.party)
        done = self.hand(trip, main, party=self.dual)                                      # a dual-role party: the supplier account stays separate
        self.assertEqual(self.query('SELECT COUNT(*) AS n FROM supplier_gold_movements WHERE supplier_id=%s', (self.dual,))[0]['n'], 0)
        pool = self.pool_of(done['document_id'])
        self.suppliers.set_active(self.dual, False)
        try:
            lots2 = self.lots()[2][0]
            with self.assertRaises(TripError) as inactive:
                self.hand(trip, lots2, party=self.dual)                                     # no new handover to a deactivated party ...
            self.assertIn(inactive.exception.code, ('PARTY_INACTIVE', 'LOT_NOT_IN_CUSTODY'))
            self.assertEqual(self.back(pool)['kind'], 'HOLD_RETURN')                         # ... but what it holds can still be resolved
        finally:
            self.suppliers.set_active(self.dual, True)
        # any holding history, reversed or not, keeps the intermediary role and the party row
        with self.assertRaises(SupplierRuleError) as role:
            self.suppliers.update_supplier(self.dual, 'x ' + uuid4().hex[:8], karat_id=self.karats[21], is_supplier=True, is_intermediary=False)
        self.assertEqual(role.exception.code, 'ROLE_HISTORY_EXISTS')
        with self.assertRaises(SupplierRuleError) as delete:
            self.suppliers.delete_unused_supplier(self.dual)
        self.assertEqual(delete.exception.code, 'SUPPLIER_IN_USE')
        detail = self.suppliers.supplier_detail(self.dual)
        self.assertEqual(detail['balances']['gold_status'], 'RESOLVED')
        self.assertEqual(len(detail['holdings']['history']), 2)
        self.assertEqual(detail['holdings']['pools'], [])

    def test_closure_with_external_holdings_reopening_and_dependencies(self):
        trip, melt, lots = self.lots('10', '875', residues=[('1', '500')])
        main, residue = lots
        done = self.hand(trip, main)
        pool = self.pool_of(done['document_id'])
        with self.assertRaises(CorrectionError) as melt_blocked:
            self.correct.reverse('MARKET_TRIP_ENTRY', melt['entry_id'], '048')                # the melt stays protected while the bar is out
        self.assertEqual(melt_blocked.exception.code, 'DEPENDENT_LOT_USE_EXISTS')
        self.service.return_lots(trip['trip_id'], [{'lot_id': str(residue['id'])}], DATE, '', str(uuid4()))
        self.assertEqual(self.service.close(trip['trip_id'])['status'], 'CLOSED')           # its own outstanding is zero although the gold is outside
        self.assertEqual(self.service.trip_detail(trip['trip_id'])['external_holdings'][0]['balance'], D('10'))
        with self.assertRaises(TripError) as closed:
            self.back(pool)
        self.assertEqual(closed.exception.code, 'TRIP_CLOSED')
        with self.assertRaises(CorrectionError) as closed_reversal:
            self.correct.reverse('MARKET_TRIP_ENTRY', done['entry_id'], '048')
        self.assertEqual(closed_reversal.exception.code, 'TRIP_CLOSED')
        self.assertEqual(self.places(main), (D('0'), D('0'), D('10')))
        self.service.reopen(trip['trip_id'], '048 explicit')
        returned = self.back(pool)
        # the restored bar goes to a supplier: the return can no longer be reversed underneath that settlement
        settled = self.service.settle_supplier_lot(trip['trip_id'], self.supplier, str(main['id']), DATE, '', '', str(uuid4()))
        with self.assertRaises(CorrectionError) as used:
            self.correct.reverse('MARKET_TRIP_ENTRY', returned['entry_id'], '048')
        self.assertEqual(used.exception.code, 'DEPENDENT_LOT_USE_EXISTS')
        self.correct.reverse('MARKET_TRIP_ENTRY', settled['entry_id'], '048')
        self.correct.reverse('MARKET_TRIP_ENTRY', returned['entry_id'], '048')
        self.assertEqual(self.places(main), (D('0'), D('0'), D('10')))

    def test_a_held_lot_is_unavailable_everywhere_else(self):
        trip, melt, lots = self.lots()
        main = lots[0]
        self.hand(trip, main)
        other = self.service.issue('', '1', [], DATE, '', str(uuid4()))
        for code, call in (('LOT_NOT_IN_CUSTODY', lambda: self.hand(trip, main)),
                           ('LOT_NOT_IN_CUSTODY', lambda: self.service.return_lots(trip['trip_id'], [{'lot_id': str(main['id'])}], DATE, '', str(uuid4()))),
                           ('LOT_NOT_IN_CUSTODY', lambda: self.service.settle_supplier_lot(trip['trip_id'], self.supplier, str(main['id']), DATE, '', '', str(uuid4()))),
                           ('LOT_NOT_IN_SHOP', lambda: self.service.issue_lots(other['trip_id'], [{'lot_id': str(main['id'])}], DATE, '', str(uuid4())))):
            with self.assertRaises(TripError) as refusal:
                call()
            self.assertEqual(refusal.exception.code, code)
        self.assertEqual(self.places(main), (D('0'), D('0'), D('10')))

    def test_changed_return_review_event_list_refuses_without_writes(self):
        trip, melt, lots = self.lots()
        main = lots[0]
        handed = self.hand(trip, main)
        pool = self.pool_of(handed['document_id'])
        stale_review = self.holding.return_review(str(pool))

        settlement = HoldingSettlementService(self.session, transaction_factory=self.tx, corrections=self.correct)
        allocated = settlement.settle('LOT', str(pool), str(self.supplier), '1', 'GRAM_ENTITLEMENT', False,
                                      DATE, '048 changed event', '', str(uuid4()))
        self.correct.reverse('SUPPLIER_GOLD_SETTLEMENT', allocated['id'], '048 changed event')
        before = self.snapshot()
        with self.assertRaises(TripError) as changed:
            self.holding.return_holding(str(pool), True, stale_review['token'], False, DATE, '', '', str(uuid4()))
        self.assertEqual(changed.exception.code, 'RETURN_REVIEW_CHANGED')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.places(main), (D('0'), D('0'), D('10')))

    def test_return_audit_failure_rolls_back_every_persisted_return_effect(self):
        trip, melt, lots = self.lots()
        main = lots[0]
        handed = self.hand(trip, main)
        pool = self.pool_of(handed['document_id'])
        review = self.holding.return_review(str(pool))
        before = self.snapshot()
        original_audit = IntermediaryHoldingRepository.audit

        def fail_after_audit(instance, *args, **kwargs):
            original_audit(instance, *args, **kwargs)
            raise RuntimeError('048 return audit injected')

        with patch.object(IntermediaryHoldingRepository, 'audit', fail_after_audit):
            with self.assertRaisesRegex(RuntimeError, '048 return audit injected'):
                self.holding.return_holding(str(pool), True, review['token'], False, DATE, '', '', str(uuid4()))

        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.places(main), (D('0'), D('0'), D('10')))

    def test_posting_and_reversal_rollback_after_each_persistent_stage(self):
        trip, melt, lots = self.lots()
        main = lots[0]
        stages = ((IntermediaryHoldingRepository, 'insert_pool'), (MarketTripRepository, 'insert_entry'), (IntermediaryHoldingRepository, 'insert_document'),
                  (MarketTripRepository, 'insert_lot_movement'), (IntermediaryHoldingRepository, 'insert_movement'), (IntermediaryHoldingRepository, 'insert_link'),
                  (MarketTripRepository, 'audit'), (IntermediaryHoldingRepository, 'audit'))
        for cls, name in stages:
            original = getattr(cls, name)

            def fail(instance, *a, _original=original, **kw):
                _original(instance, *a, **kw)
                raise RuntimeError('048 injected')
            before = self.snapshot()
            with self.subTest(stage=f'hand/{name}'), patch.object(cls, name, fail), self.assertRaisesRegex(RuntimeError, '048 injected'):
                self.hand(trip, main)
            self.assertEqual(before, self.snapshot(), name)
        done = self.hand(trip, main)
        pool = self.pool_of(done['document_id'])
        for cls, name in stages[2:]:
            original = getattr(cls, name)

            def fail(instance, *a, _original=original, **kw):
                _original(instance, *a, **kw)
                raise RuntimeError('048 injected')
            before = self.snapshot()
            with self.subTest(stage=f'return/{name}'), patch.object(cls, name, fail), self.assertRaisesRegex(RuntimeError, '048 injected'):
                self.back(pool)
            self.assertEqual(before, self.snapshot(), name)
        for cls, name in ((CorrectionRepository, 'insert_correction'), (IntermediaryHoldingRepository, 'insert_movement')):
            original = getattr(cls, name)

            def fail(instance, *a, _original=original, **kw):
                _original(instance, *a, **kw)
                raise RuntimeError('048 injected')
            before = self.snapshot()
            with self.subTest(stage=f'reverse/{name}'), patch.object(cls, name, fail), self.assertRaisesRegex(RuntimeError, '048 injected'):
                self.correct.reverse('MARKET_TRIP_ENTRY', done['entry_id'], '048')
            self.assertEqual(before, self.snapshot(), name)
        self.assertEqual(self.places(main), (D('0'), D('0'), D('10')))

    # ------------------------------------------------------------------ controlled RR snapshots
    def test_rr_snapshot_before_a_reversed_return_the_current_form_keeps_the_lot_out_of_custody(self):
        """Controlled: the snapshot is taken while the lot is back in custody; another connection then reverses the RETURN (the lot is at the party
        again). The pre-hardening query shape still lists the lot in custody; the current form does not, and the service refuses a second handover."""
        trip, melt, lots = self.lots()
        main = lots[0]
        done = self.hand(trip, main)
        returned = self.back(self.pool_of(done['document_id']))
        with self.tx() as reader:
            q = reader.cursor()
            q.execute('SELECT COUNT(*) FROM transaction_corrections')
            q.fetchone()
            q.close()
            self.correct.reverse('MARKET_TRIP_ENTRY', returned['entry_id'], '048 RR')
            current, legacy = MarketTripRepository(reader), MarketTripRepository(Legacy(reader))
            self.assertEqual(legacy.custody_lots(trip['trip_id'], lot_id=main['id'])[0]['outstanding_grams'], D('10'))      # control: stale
            rows = current.custody_lots(trip['trip_id'], lot_id=main['id'])
            self.assertEqual(sum((r['outstanding_grams'] for r in rows), D('0')), D('0'))                                  # hardened: current
            service = IntermediaryHoldingService(self.session, transaction_factory=lambda: self.on_connection(reader))
            with self.assertRaises(TripError) as refusal:
                service.hand_over_lot(trip['trip_id'], self.party2, str(main['id']), DATE, '', '', str(uuid4()))
            self.assertEqual(refusal.exception.code, 'LOT_NOT_IN_CUSTODY')
        self.assertEqual(self.places(main), (D('0'), D('0'), D('10')))

    def test_rr_snapshot_before_a_reversed_handover_the_current_decisions_see_the_compensation(self):
        trip, melt, lots = self.lots()
        main = lots[0]
        done = self.hand(trip, main)
        pool = self.pool_of(done['document_id'])
        with self.tx() as reader:
            q = reader.cursor()
            q.execute('SELECT COUNT(*) FROM intermediary_holding_movements')
            q.fetchone()
            q.close()
            self.correct.reverse('MARKET_TRIP_ENTRY', done['entry_id'], '048 RR')
            q = reader.cursor()
            q.execute('SELECT COUNT(*) FROM intermediary_holding_movements WHERE pool_id=%s', (pool,))
            self.assertEqual(q.fetchone()[0], 1)                                               # the earlier snapshot still shows only the handover
            q.close()
            self.assertEqual(IntermediaryHoldingRepository(reader).pool_balance(pool), D('0'))          # a CURRENT read sees the compensation
            service = IntermediaryHoldingService(self.session, transaction_factory=lambda: self.on_connection(reader))
            review = service.return_review(str(pool))
            with self.assertRaises(TripError) as refusal:
                service.return_holding(str(pool), True, review['token'], False, DATE, '', '', str(uuid4()))
            self.assertEqual(refusal.exception.code, 'HOLDING_NOT_AVAILABLE')

    # ------------------------------------------------------------------ resource order and contention (no production authorization locks)
    def test_real_acquisition_order_of_handover_return_and_reversal(self):
        trip, melt, lots = self.lots()
        main = lots[0]
        order = []

        def wrap(cls, name, label, only_locking=False):
            original = getattr(cls, name)

            def wrapper(instance, *args, **kwargs):
                if not only_locking or kwargs.get('lock'):
                    order.append(label)
                return original(instance, *args, **kwargs)
            return patch.object(cls, name, wrapper)
        stack = (wrap(MarketTripRepository, 'trip', 'trip', True), wrap(MarketTripRepository, 'lock_lots', 'lots'),
                 wrap(SupplierGoldSettlementRepository, 'get_supplier', 'party', True), wrap(IntermediaryHoldingRepository, 'lock_pool', 'pool'))
        for p in stack:
            p.start()
        try:
            done = self.hand(trip, main)
            self.assertEqual(order, ['trip', 'lots', 'party'])
            order.clear()
            returned = self.back(self.pool_of(done['document_id']))
            self.assertEqual(order, ['trip', 'lots', 'party', 'pool'])
            order.clear()
            self.correct.reverse('MARKET_TRIP_ENTRY', returned['entry_id'], '048')
            self.assertEqual(order[:4], ['trip', 'lots', 'party', 'pool'])
        finally:
            for p in reversed(stack):
                p.stop()

    def test_a_handover_waiting_for_the_lot_holds_the_trip_but_not_the_party_and_blocks_nothing_unrelated(self):
        trip, melt, lots = self.lots()
        main = lots[0]
        results = {}
        with self.tx() as holder:                                                              # raw holder: no authorization locks involved
            q = holder.cursor()
            q.execute('SELECT id FROM market_trip_lots WHERE id=%s FOR UPDATE', (main['id'],))
            q.fetchall()
            q.close()
            worker = self.thread(results, 'H', lambda: self.hand(trip, main))
            try:
                self.wait_waiting('market_trip_lots')
                with self.tx() as observer:
                    trip_lock = self.probe(observer, 'SELECT id FROM market_trips WHERE id=%s FOR UPDATE NOWAIT', (trip['trip_id'],))
                    party_lock = self.probe(observer, 'SELECT id FROM suppliers WHERE id=%s FOR UPDATE NOWAIT', (int(self.party),))
            finally:
                holder.commit()
                worker.join(60)
        self.assertEqual((trip_lock, party_lock), (3572, None))                               # trip -> lot -> party: the party is not yet locked
        self.assertIsInstance(results['H'], dict, results['H'])

    def test_two_handovers_of_one_lot_to_different_parties_one_wins_and_the_bar_is_in_one_place(self):
        """Controlled schedule with UnlockedAuthorization (no shared authorization locks): the trip lock serializes them."""
        trip, melt, lots = self.lots()
        main = lots[0]
        sessions = [self.make_session(f'racer{i}', 'OWNER')[0] for i in range(2)]
        services = [IntermediaryHoldingService(s, transaction_factory=self.tx, authorization=UnlockedAuthorization(s)) for s in sessions]
        barrier, results = threading.Barrier(2), {}

        def race(index, party):
            def call():
                barrier.wait(timeout=10)
                return services[index].hand_over_lot(trip['trip_id'], party, str(main['id']), DATE, '', '', str(uuid4()))
            return call
        workers = [self.thread(results, 'A', race(0, self.party)), self.thread(results, 'B', race(1, self.party2))]
        for worker in workers:
            worker.join(60)
        outcomes = list(results.values())
        self.assertEqual(sum(isinstance(r, dict) for r in outcomes), 1, outcomes)
        self.assertEqual(sum(isinstance(r, TripError) and r.code == 'LOT_NOT_IN_CUSTODY' for r in outcomes), 1, outcomes)
        self.assertEqual(self.places(main), (D('0'), D('0'), D('10')))

    def test_a_return_and_the_reversal_of_its_handover_race_and_exactly_one_order_wins(self):
        trip, melt, lots = self.lots()
        main = lots[0]
        done = self.hand(trip, main)
        pool = self.pool_of(done['document_id'])
        sessions = [self.make_session(f'duel{i}', 'OWNER')[0] for i in range(2)]
        returner = IntermediaryHoldingService(sessions[0], transaction_factory=self.tx, authorization=UnlockedAuthorization(sessions[0]))
        reverser = CorrectionService(sessions[1], transaction_factory=self.tx, authorization=UnlockedAuthorization(sessions[1]))
        barrier, results = threading.Barrier(2), {}

        def give_back():
            barrier.wait(timeout=10)
            review = returner.return_review(str(pool))
            return returner.return_holding(str(pool), True, review['token'], False, DATE, '', '', str(uuid4()))

        def undo():
            barrier.wait(timeout=10)
            return reverser.reverse('MARKET_TRIP_ENTRY', done['entry_id'], '048 duel')
        workers = [self.thread(results, 'R', give_back), self.thread(results, 'V', undo)]
        for worker in workers:
            worker.join(60)
        returned_ok, reversed_ok = isinstance(results['R'], dict), isinstance(results['V'], dict)
        self.assertTrue(returned_ok != reversed_ok or (returned_ok and not reversed_ok), results)
        if returned_ok:
            self.assertIsInstance(results['V'], CorrectionError)
            self.assertEqual(results['V'].code, 'DEPENDENT_HOLDING_RETURN_EXISTS')
        else:
            self.assertIsInstance(results['R'], TripError)
            self.assertIn(results['R'].code, ('HOLDING_NOT_AVAILABLE', 'LOT_NOT_IN_CUSTODY'))
        self.assertEqual(sum(self.places(main)), D('10'))                                      # never in two places

    def test_production_authorization_queues_a_second_holding_writer_behind_the_first(self):
        """Informational: the reason the schedules above remove the authorization locks. A second OWNER writer waits on authorization rows."""
        trip, melt, lots = self.lots()
        main = lots[0]
        other_session, _ = self.make_session('queued', 'OWNER')
        other = MarketTripService(other_session, transaction_factory=self.tx)
        results, tables = {}, set()
        with self.tx() as holder:
            q = holder.cursor()
            q.execute('SELECT id FROM market_trip_lots WHERE id=%s FOR UPDATE', (main['id'],))
            q.fetchall()
            q.close()
            first = self.thread(results, 'H', lambda: self.hand(trip, main))
            second = None
            try:
                self.wait_waiting('market_trip_lots')
                second = self.thread(results, 'D', lambda: other.issue('', '1', [], DATE, '', str(uuid4())))
                deadline = time.time() + 15
                while time.time() < deadline and not (tables & {'users', 'employees', 'user_roles', 'roles', 'role_permissions', 'permissions'}):
                    tables = {r['object_name'] for r in self.query(
                        "SELECT l.object_name FROM performance_schema.data_lock_waits w JOIN performance_schema.data_locks l "
                        "ON l.engine_lock_id = w.requesting_engine_lock_id WHERE l.object_schema = %s", (TARGET,))}
                    time.sleep(0.1)
                early = 'D' in results
            finally:
                holder.commit()
                first.join(60)
                if second is not None:
                    second.join(60)
        self.assertFalse(early)
        self.assertTrue(tables & {'users', 'employees', 'user_roles', 'roles', 'role_permissions', 'permissions'}, tables)
        self.assertIsInstance(results['H'], dict, results['H'])


if __name__ == '__main__':
    unittest.main()
