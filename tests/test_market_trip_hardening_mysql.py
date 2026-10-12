"""Explicit opt-in rehearsal checks for the prerequisite hardening before 048. They connect to masgold_rehearsal ONLY (the target is asserted
by name, by migration level and by the rehearsal marker) and never to the DEV database.

(A) the resource order of a mixed custody creation: new trip -> existing bars -> cash account -> Scrap;
(B) CURRENT reads for the nested reversal filters of the custody decision queries under REPEATABLE READ.

Every connection here is a separate REPEATABLE READ transaction. The authorization of the production services is left intact; the
resource-order checks use raw lock holders and instrumentation of the real repositories so that the shared authorization locks (shared
grants, compatible with each other) cannot mask or create the contention under test. These are controlled schedules, not a stress test."""
from contextlib import contextmanager, nullcontext
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
from repositories.financial_account_repository import FinancialAccountRepository
from repositories.market_trip_repository import MarketTripRepository
from repositories.old_gold_conversion_repository import OldGoldConversionRepository
from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository
from repositories.supplier_payment_repository import SupplierPaymentRepository
from repositories.supplier_payment_repository import CASH
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.authorization_service import AuthorizationService
from services.correction_service import CorrectionError, CorrectionService
from services.market_trip_service import MarketTripService, TripError
from services.supplier_gold_settlement_service import SupplierGoldSettlementService
from services.supplier_payment_service import SupplierPaymentService
from services.supplier_service import SupplierService

TARGET = 'masgold_rehearsal'
DATE = '2026-10-12'
LEGACY_NESTED = ' FOR SHARE)'        # the nested reversal lookup of a CURRENT form; removing it yields the pre-hardening query shape


class _LegacyCursor:
    def __init__(self, cursor, counter):
        self._cursor, self._counter = cursor, counter

    def execute(self, query, params=()):
        stripped = query.replace(LEGACY_NESTED, ')')
        self._counter.append(query.count(LEGACY_NESTED))
        return self._cursor.execute(stripped, params)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class Legacy:
    """Connection proxy that sends the SAME repository query without the nested lock: the pre-hardening shape."""

    def __init__(self, connection):
        self._connection, self.stripped = connection, []

    def cursor(self, *args, **kwargs):
        return _LegacyCursor(self._connection.cursor(*args, **kwargs), self.stripped)


class UnlockedAuthorization(AuthorizationService):
    """TEST ONLY. Verifies the identity and grants from the database exactly like production but takes no row locks. Production write
    authorization X-locks the actor's user row AND the shared role / permission rows until commit, so protected writers serialize
    there before they reach any resource; the resource-order and stale-snapshot schedules need participants that do not."""

    def require_permission_for_write(self, connection, actor_context, permission_code, *, require_employee=False):
        return self._verify(connection, actor_context, permission_code, lock=False, require_employee=require_employee)

    require_permission = require_permission_for_write


AUTHORIZATION_TABLES = {'users', 'employees', 'user_roles', 'roles', 'role_permissions', 'permissions'}
ORIGINAL_NOT_REVERSED = trip_module._not_reversed


def snapshot_form(alias='e', *, current=False):
    """`_not_reversed` as it was before the hardening: the nested lookup is never a locking read."""
    return ORIGINAL_NOT_REVERSED(alias)


@unittest.skipUnless(os.environ.get('MASGOLD_HARDENING_REHEARSAL') == '1', 'explicit rehearsal-only opt-in')
class HardeningMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = replace(DatabaseConfig.from_env(), database=TARGET)
        with cls.tx() as c:
            q = c.cursor(dictionary=True)
            q.execute('SELECT DATABASE() AS db, MAX(version) AS v FROM schema_migrations')
            row = q.fetchone()
            assert row['db'] == TARGET and row['v'] in (47, 48)
            q.execute("SELECT target_database FROM masgold_rehearsal_marker WHERE marker_key='MASGOLD_SCHEMA_REHEARSAL'")
            assert q.fetchone()['target_database'] == TARGET
            q.execute('SELECT id,karat_value FROM karats')
            cls.karats = {r['karat_value']: r['id'] for r in q.fetchall()}
            q.close()
        cls.session, cls.user = cls.make_session('main')
        with cls.tx() as c:
            q = c.cursor()
            q.execute("INSERT INTO old_gold_movements(movement_type_id,karat_id,weight_delta_grams,created_by_user_id) "
                      "SELECT id,%s,10000,%s FROM old_gold_movement_types WHERE code='OPENING_BALANCE'", (cls.karats[21], cls.user))
            assert q.rowcount == 1
            q.execute("SELECT id FROM cash_accounts WHERE code='MAIN_SAFE'")
            assert q.fetchone() is not None
            q.close()
        suppliers = SupplierService(cls.session, transaction_factory=cls.tx)
        cls.sup18 = suppliers.create_supplier('hardening 18K supplier ' + uuid4().hex[:12], karat_id=cls.karats[18])
        cls.sup21 = suppliers.create_supplier('hardening 21K supplier ' + uuid4().hex[:12], karat_id=cls.karats[21])

    @classmethod
    def make_session(cls, label):
        """A separate OWNER user with its own verified session. The write authorization locks the actor's user row, so two writers acting as
        the SAME user serialize there before they ever reach a resource; contention between resources needs distinct actors."""
        with cls.tx() as c:
            q = c.cursor()
            q.execute('INSERT INTO employees(name) VALUES(%s)', ('hardening rehearsal ' + label,))
            emp = q.lastrowid
            q.execute("INSERT INTO users(employee_id,username,password_hash,display_name) VALUES(%s,%s,'unusable-rehearsal-hash',%s)",
                      (emp, f'rehearsal-hardening-{label}-' + uuid4().hex[:12], 'hardening ' + label))
            user = q.lastrowid
            q.execute("INSERT INTO user_roles(user_id,role_id) SELECT %s,id FROM roles WHERE code='OWNER'", (user,))
            q.close()
            auth = AuthenticationRepository(c)
            identity, permissions = auth.get_identity(user), auth.effective_permissions(user)
        session = AppSession()
        session._establish(AuthenticationResult(user, emp, identity['display_name'], permissions, _VERIFIED), int(session.generation))
        return session, user

    def services(self, label):
        """(trip service, correction service, session) of a fresh actor."""
        session, _ = self.make_session(label)
        correct = CorrectionService(session, transaction_factory=self.tx)
        trips = MarketTripService(session, transaction_factory=self.tx)
        trips._corrections = correct
        return trips, correct, session

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
        """Reuse the reader's existing RR transaction, including its earlier snapshot."""
        yield connection

    def setUp(self):
        self.service = MarketTripService(self.session, transaction_factory=self.tx)
        self.correct = CorrectionService(self.session, transaction_factory=self.tx)
        self.service._corrections = self.correct

    # ------------------------------------------------------------------ helpers
    def query(self, sql, params=()):
        with self.tx() as c:
            q = c.cursor(dictionary=True)
            q.execute(sql, params)
            rows = q.fetchall()
            q.close()
            return rows

    def entries(self, trip):
        rows = self.query('SELECT id, entry_kind FROM market_trip_entries WHERE trip_id=%s ORDER BY entry_no', (trip,))
        return [(int(r['id']), r['entry_kind']) for r in rows]

    def reverse(self, entry_id):
        return self.correct.reverse('MARKET_TRIP_ENTRY', str(entry_id), 'hardening rehearsal')

    def logo_trip(self, grams='10'):
        trip = self.service.issue('', '', [{'karat_id': self.karats[21], 'weight': grams, 'designation': 'LOGO'}], DATE, 'hardening', str(uuid4()))
        return trip['trip_id']

    def heat(self, trip, before='2', after='1.9'):
        return self.service.heat(trip, [{'karat_id': self.karats[21], 'input_weight': before, 'output_weight': after}], DATE, 'ws', '', '', '', str(uuid4()))

    def shop_lot(self):
        """One MAIN bar (10 g at 875) that is back in the shop; its origin custody stays open and empty of it."""
        trip = self.service.issue('', '', [{'karat_id': self.karats[21], 'weight': '20', 'designation': 'MELT'}], DATE, 'hardening', str(uuid4()))
        melt = self.service.melt(trip['trip_id'], [{'karat_id': self.karats[21], 'weight': '20'}], DATE, 'ws',
                                 {'weight': '10', 'fineness': '875'}, [], 'assay', '', '', str(uuid4()))
        lot = self.query('SELECT id FROM market_trip_lots WHERE entry_id=%s ORDER BY id', (melt['entry_id'],))[0]['id']
        self.service.return_lots(trip['trip_id'], [{'lot_id': str(lot)}], DATE, '', str(uuid4()))
        return int(lot)

    def lock_waits(self):
        """(table, mode) of every lock request currently waiting in the sandbox schema."""
        rows = self.query(
            "SELECT l.object_name, l.lock_mode FROM performance_schema.data_lock_waits w "
            "JOIN performance_schema.data_locks l ON l.engine_lock_id = w.requesting_engine_lock_id WHERE l.object_schema = %s", (TARGET,))
        return [(r['object_name'], r['lock_mode']) for r in rows]

    def wait_for_lock_wait(self, table, timeout=20):
        deadline = time.time() + timeout
        seen = []
        while time.time() < deadline:
            seen = self.lock_waits()
            if any(name == table for name, _ in seen):
                return seen
            time.sleep(0.1)
        self.fail(f'no request ever waited on {table}; waiting requests: {seen}')

    @staticmethod
    def probe(connection, sql, params=()):
        """A NOWAIT locking probe: errno 3572 means another transaction holds the lock."""
        q = connection.cursor()
        try:
            q.execute(sql, params)
            q.fetchall()
            return None
        except Exception as error:                                   # noqa: BLE001 - the errno is the observation
            return getattr(error, 'errno', None)
        finally:
            q.close()

    def resource_probes(self):
        """(cash account, Scrap rows of 21K) probed with NOWAIT on a fresh connection: None = free, 3572 = held by someone."""
        with self.tx() as c:
            cash = self.probe(c, "SELECT id FROM cash_accounts WHERE code='MAIN_SAFE' FOR UPDATE NOWAIT")
            scrap = self.probe(c, "SELECT id FROM old_gold_movements WHERE karat_id=%s AND weight_delta_grams IS NOT NULL "
                                  "AND balance_effect='COUNTED' FOR UPDATE NOWAIT", (self.karats[21],))
            return cash, scrap

    @staticmethod
    def thread(results, name, call):
        def run():
            try:
                results[name] = call()
            except BaseException as error:                           # noqa: BLE001 - the outcome is the observation
                results[name] = error
        worker = threading.Thread(target=run, name=name)
        worker.start()
        return worker

    # ------------------------------------------------------------------ B: the stale-snapshot hazard of the nested filters
    def test_stale_snapshot_hides_committed_reversals_from_the_pre_hardening_form_only(self):
        """One RR snapshot, then other connections commit reversals. The same repository query WITHOUT the nested lock (control) still
        counts every reversed entry; the hardened CURRENT form does not. The default (display) form keeps the snapshot behaviour."""
        cash_trip = self.service.issue('', '100', [], DATE, 'hardening', str(uuid4()))['trip_id']
        self.service.pay_supplier(cash_trip, str(self.sup18), '30', DATE, '', '', str(uuid4()))
        gold_trip = self.service.issue('', '', [{'karat_id': self.karats[21], 'weight': '10', 'designation': 'LOGO'},
                                                {'karat_id': self.karats[21], 'weight': '5', 'designation': 'MELT'}], DATE, 'hardening', str(uuid4()))['trip_id']
        self.service.melt(gold_trip, [{'karat_id': self.karats[21], 'weight': '5'}], DATE, 'ws', {'weight': '4.9', 'fineness': '900'}, [], 'assay', '', '', str(uuid4()))
        self.heat(gold_trip)
        self.service.return_custody(gold_trip, '', [{'karat_id': self.karats[21], 'weight': '1', 'designation': 'LOGO'}], DATE, '', str(uuid4()))
        self.service.settle_supplier_gold(gold_trip, str(self.sup18), self.karats[21], 'LOGO', '1', DATE, '', '', str(uuid4()))
        cash_entries, gold_entries = self.entries(cash_trip), self.entries(gold_trip)
        self.assertEqual([k for _, k in cash_entries], ['ISSUE', 'SUPPLIER_PAY'])
        self.assertEqual([k for _, k in gold_entries], ['ISSUE', 'MELT', 'HEAT', 'RETURN', 'GOLD_SETTLE'])
        with self.tx() as reader:
            q = reader.cursor()
            q.execute('SELECT COUNT(*) FROM transaction_corrections')
            before = q.fetchone()[0]                                  # the snapshot is established here
            for entry_id, kind in reversed(gold_entries[1:]):          # newest first: settle, return, heat, melt
                self.reverse(entry_id)
            self.reverse(cash_entries[1][0])                            # the custody payment, then the cash issue
            self.reverse(cash_entries[0][0])
            q.execute('SELECT COUNT(*) FROM transaction_corrections')
            self.assertEqual(q.fetchone()[0], before)                   # the earlier snapshot is still in force
            q.close()
            current, legacy_connection = MarketTripRepository(reader), Legacy(reader)
            legacy = MarketTripRepository(legacy_connection)
            # --- cash: issued / returned / paid to suppliers
            stale = legacy.custody_cash(cash_trip, current=True)
            self.assertEqual(tuple(D(str(v)) for v in stale), (D('100'), D('0'), D('30')))        # control: the reversed entries still count
            self.assertEqual(tuple(D(str(v)) for v in current.custody_cash(cash_trip)), (D('100'), D('0'), D('30')))   # display form: unchanged
            self.assertEqual(tuple(D(str(v)) for v in current.custody_cash(cash_trip, current=True)), (D('0'), D('0'), D('0')))
            # --- gold: legs, melts, heats and supplier settlements
            def table(rows):
                return {(int(r['karat_id']), r['designation']): {k: D(str(r[k])) for k in ('issued', 'returned', 'melted', 'heat_delta', 'supplier_settled')}
                        for r in rows}
            k21 = self.karats[21]
            stale_gold = table(legacy.custody_gold(gold_trip, current=True))
            self.assertEqual(stale_gold[(k21, 'LOGO')], {'issued': D('10'), 'returned': D('1'), 'melted': D('0'), 'heat_delta': D('-0.1'), 'supplier_settled': D('1')})
            self.assertEqual(stale_gold[(k21, 'MELT')]['melted'], D('5'))
            fresh_gold = table(current.custody_gold(gold_trip, current=True))
            self.assertEqual(fresh_gold[(k21, 'LOGO')], {'issued': D('10'), 'returned': D('0'), 'melted': D('0'), 'heat_delta': D('0'), 'supplier_settled': D('0')})
            self.assertEqual(fresh_gold[(k21, 'MELT')], {'issued': D('5'), 'returned': D('0'), 'melted': D('0'), 'heat_delta': D('0'), 'supplier_settled': D('0')})
            self.assertEqual(table(current.custody_gold(gold_trip)), stale_gold)                  # display form: unchanged
            # --- the reversal-guard lookups
            ids = {kind: entry_id for entry_id, kind in gold_entries}
            for name, kind in (('later_heat_entries', 'HEAT'), ('later_logo_returns', 'RETURN'), ('later_gold_settlements', 'GOLD_SETTLE')):
                with self.subTest(query=name):
                    self.assertEqual([int(r['id']) for r in getattr(legacy, name)(gold_trip, 0, [k21])], [ids[kind]])    # control: stale
                    self.assertEqual(getattr(current, name)(gold_trip, 0, [k21]), [])                                      # hardened: current
            self.assertTrue(all(count == 1 for count in legacy_connection.stripped if count), 'the control must really remove the nested lock')
            self.assertGreaterEqual(sum(1 for count in legacy_connection.stripped if count), 8)

    def test_a_posting_decision_on_a_stale_snapshot_over_permits_until_the_current_form_is_used(self):
        """Controlled: a return larger than the REAL custody (its issue was reversed after the snapshot) is accepted when the nested
        filters read the snapshot, and refused with the hardened filters. The accepted post is rolled back; nothing persists."""
        for hardened in (False, True):
            with self.subTest(hardened=hardened):
                trip = self.service.issue('', '100', [], DATE, 'hardening', str(uuid4()))['trip_id']
                issue = self.entries(trip)[0][0]
                with self.tx() as reader:
                    q = reader.cursor()
                    q.execute('SELECT COUNT(*) FROM transaction_corrections')
                    q.fetchone()                                                       # snapshot
                    q.close()
                    self.reverse(issue)                                                # custody is really empty now
                    service = MarketTripService(self.session, transaction_factory=lambda: self.on_connection(reader))
                    if hardened:
                        with self.assertRaises(TripError) as refused:
                            service.return_custody(trip, '100', [], DATE, '', str(uuid4()))
                        self.assertEqual(refused.exception.code, 'RETURN_EXCEEDS_CUSTODY')
                    else:
                        with patch.object(trip_module, '_not_reversed', snapshot_form):
                            accepted = service.return_custody(trip, '100', [], DATE, '', str(uuid4()))
                        self.assertEqual(accepted['kind'], 'RETURN')                   # cash returned that was never in custody
                        reader.rollback()
                self.assertEqual([k for _, k in self.entries(trip)], ['ISSUE'])          # the over-permitted post did not persist

    def run_reversal_schedule(self, trip, first_entry, dependent_entry, legacy):
        """W reverses the dependent entry and holds its transaction open until R is blocked on the trip lock; R (which already read the
        entry, hence has its snapshot) then reverses the earlier entry once W has committed. Returns (W outcome, R outcome, waits)."""
        w_has_lock, r_blocked, results = threading.Event(), threading.Event(), {}
        original = CorrectionRepository.insert_correction
        holder = {}
        unlocked = CorrectionService(self.session, transaction_factory=self.tx, authorization=UnlockedAuthorization(self.session))

        def reverse(entry):
            return unlocked.reverse('MARKET_TRIP_ENTRY', str(entry), 'hardening rehearsal')

        def hooked(repository, *args, **kwargs):
            outcome = original(repository, *args, **kwargs)
            if threading.current_thread().name == 'W':
                w_has_lock.set()
                r_blocked.wait(30)
            return outcome

        context = patch.object(trip_module, '_not_reversed', snapshot_form) if legacy else nullcontext()
        with context, patch.object(CorrectionRepository, 'insert_correction', hooked):
            holder['w'] = self.thread(results, 'W', lambda: reverse(dependent_entry))
            try:
                self.assertTrue(w_has_lock.wait(30), 'W never reached its reversal')
                holder['r'] = self.thread(results, 'R', lambda: reverse(first_entry))
                waits = self.wait_for_lock_wait('market_trips')
            finally:
                r_blocked.set()
            holder['w'].join(60)
            holder['r'].join(60)
        self.assertFalse(holder['w'].is_alive() or holder['r'].is_alive())
        return results['W'], results['R'], waits

    def test_reversal_of_an_issue_blocked_on_the_trip_lock_decides_on_current_filters(self):
        """CorrectionService reads the entry (an RR snapshot) BEFORE it takes the trip lock, so a reversal that waits for another reversal's
        trip lock decides with a snapshot that predates the committed reversal. Both participants use UnlockedAuthorization: with the
        production authorization the second writer would wait on the shared grant rows first and take its snapshot only afterwards."""
        for legacy in (True, False):
            with self.subTest(pre_hardening=legacy):
                trip = self.logo_trip()
                heat = self.heat(trip)
                issue = self.entries(trip)[0][0]
                waiter, reverser, waits = self.run_reversal_schedule(trip, issue, int(heat['entry_id']), legacy)
                self.assertNotIsInstance(waiter, BaseException, waiter)                  # the heat reversal always succeeds
                self.assertIn('market_trips', [name for name, _ in waits])                # R really waited on the TRIP lock
                if legacy:
                    self.assertIsInstance(reverser, CorrectionError)                      # false refusal on the stale snapshot
                    self.assertIn(reverser.code, ('DEPENDENT_HEAT_EXISTS', 'DEPENDENT_RETURN_EXISTS'))
                else:
                    self.assertNotIsInstance(reverser, BaseException, reverser)          # decided on the committed state
                    reversed_ids = {int(r['source_id']) for r in self.query(
                        "SELECT source_id FROM transaction_corrections WHERE source_type='MARKET_TRIP_ENTRY' AND source_id IN (%s,%s)",
                        (issue, int(heat['entry_id'])))}
                    self.assertEqual(reversed_ids, {issue, int(heat['entry_id'])})

    # ------------------------------------------------------------------ A: the resource order of a mixed creation
    def recorded_creation(self, cash, grams, lots):
        order = []

        def wrap(cls, name, label, only_locking=False):
            original = getattr(cls, name)

            def wrapper(instance, *args, **kwargs):
                if not only_locking or kwargs.get('lock') or (len(args) > 2 and args[2]):
                    order.append(label)
                return original(instance, *args, **kwargs)
            return patch.object(cls, name, wrapper)

        stack = (wrap(MarketTripRepository, 'insert_trip', 'trip'), wrap(MarketTripRepository, 'lock_lots', 'lots'),
                 wrap(FinancialAccountRepository, 'account', 'account', True), wrap(OldGoldConversionRepository, 'lock_old_gold_balance', 'scrap'))
        for p in stack:
            p.start()
        try:
            outcome = None
            try:
                outcome = self.service.create_trip(cash, grams, lots, DATE, 'hardening', str(uuid4()))
            except TripError as error:
                outcome = error
        finally:
            for p in reversed(stack):
                p.stop()
        return outcome, order

    def test_real_acquisition_order_of_a_mixed_creation_and_of_its_refusals(self):
        lot = self.shop_lot()
        melt21 = [{'karat_id': self.karats[21], 'weight': '1', 'designation': 'LOGO'}]
        done, order = self.recorded_creation('10', melt21, [{'lot_id': str(lot)}])
        self.assertIsInstance(done, dict, done)
        self.assertEqual(order, ['trip', 'lots', 'account', 'scrap'])
        self.assertEqual([k for _, k in self.entries(int(done['trip_id']))], ['ISSUE', 'LOT_ISSUE'])
        refused, order = self.recorded_creation('10', melt21, [{'lot_id': str(lot)}])        # the bar now sits in the new custody
        self.assertIsInstance(refused, TripError)
        self.assertEqual(refused.code, 'LOT_NOT_IN_SHOP')
        self.assertEqual(order, ['trip', 'lots'], 'cash / Scrap were locked before the unavailable bar was refused')
        for cash, grams, lots, expected in (('10', [], [], ['trip', 'account']), ('', melt21, [], ['trip', 'scrap']),
                                            ('10', melt21, [], ['trip', 'account', 'scrap'])):
            ok, order = self.recorded_creation(cash, grams, lots)
            self.assertIsInstance(ok, dict, ok)
            self.assertEqual(order, expected)                                                 # unchanged paths, empty bar range takes no lot lock
        before = self.query('SELECT COUNT(*) AS n FROM market_trips')[0]['n']
        nothing, order = self.recorded_creation('10', melt21, [{'lot_id': '999999999'}])
        self.assertEqual((nothing.code, order), ('LOT_NOT_FOUND', ['trip', 'lots']))
        self.assertEqual(self.query('SELECT COUNT(*) AS n FROM market_trips')[0]['n'], before)   # the refused creation rolled back its trip

    def test_a_creation_waiting_for_a_bar_holds_no_cash_account_or_scrap_lock(self):
        lot = self.shop_lot()
        results = {}
        with self.tx() as holder:                                                    # raw holder: no authorization locks involved
            q = holder.cursor()
            q.execute('SELECT id FROM market_trip_lots WHERE id=%s FOR UPDATE', (lot,))
            q.fetchall()
            q.close()
            worker = self.thread(results, 'C', lambda: self.service.create_trip(
                '10', [{'karat_id': self.karats[21], 'weight': '1', 'designation': 'LOGO'}], [{'lot_id': str(lot)}], DATE, 'hardening', str(uuid4())))
            try:
                waits = self.wait_for_lock_wait('market_trip_lots')
                cash, scrap = self.resource_probes()
            finally:
                holder.commit()
                worker.join(60)
        self.assertFalse(worker.is_alive())
        self.assertIsInstance(results['C'], dict, results['C'])
        self.assertIn('market_trip_lots', [name for name, _ in waits])
        self.assertEqual((cash, scrap), (None, None), 'the creation held the cash account or the Scrap rows while it waited for the bar')

    def cycle(self, via_cash):
        """The static cycle supplier -> (Scrap | Main Safe) -> bar -> supplier. H plays a bar settlement in flight (holds the bar and will need
        the supplier), C is the real mixed creation service (production authorization), D replays the resource order of a direct settlement
        / payment through the real repositories. D takes no authorization locks, which would otherwise queue it behind C."""
        lot = self.shop_lot()
        results = {}
        outcome = {}
        with self.tx() as holder:
            q = holder.cursor()
            q.execute('SELECT id FROM market_trip_lots WHERE id=%s FOR UPDATE', (lot,))
            q.fetchall()
            q.close()
            creation = (lambda: self.service.create_trip('10', [], [{'lot_id': str(lot)}], DATE, 'hardening', str(uuid4()))) if via_cash else \
                (lambda: self.service.create_trip('', [{'karat_id': self.karats[21], 'weight': '1', 'designation': 'LOGO'}], [{'lot_id': str(lot)}],
                                                  DATE, 'hardening', str(uuid4())))
            def direct():
                """The resource acquisitions of a direct settlement / payment, through the real repositories: supplier first, then the shared
                resource (Scrap rows of the karat, or the Main Safe row), then commit. No authorization locks."""
                with self.tx() as c:
                    if via_cash:
                        suppliers = SupplierPaymentRepository(c)
                        suppliers.get_supplier(self.sup18, lock=True)
                        FinancialAccountRepository(c).account('CASH', suppliers.main_safe()['id'], lock=True)
                    else:
                        SupplierGoldSettlementRepository(c).get_supplier(self.sup18, lock=True)
                        OldGoldConversionRepository(c).lock_old_gold_balance(self.karats[21])
                return 'done'

            workers = [self.thread(results, 'C', creation)]
            try:
                self.wait_for_lock_wait('market_trip_lots')
                workers.append(self.thread(results, 'D', direct))
                workers[1].join(15)
                outcome['D_finished_while_C_waited'] = not workers[1].is_alive()
                request = self.probe(holder, 'SELECT id FROM suppliers WHERE id=%s FOR UPDATE', (int(self.sup18),))   # H now needs the supplier
                outcome['H_supplier_request'] = request                                  # 1213 = this request closed a deadlock cycle
            finally:
                try:
                    holder.commit()
                except Exception:                                                        # noqa: BLE001 - a deadlock victim may already be rolled back
                    pass
                for worker in workers:
                    worker.join(60)
        self.assertFalse(any(w.is_alive() for w in workers))
        return results, outcome

    def test_the_scrap_cycle_does_not_form_when_the_creation_waits_for_the_bar(self):
        results, outcome = self.cycle(via_cash=False)
        self.assertTrue(outcome['D_finished_while_C_waited'], f'direct settlement blocked behind the creation: {outcome}')
        self.assertIsNone(outcome['H_supplier_request'], outcome)
        self.assertIsInstance(results['C'], dict, results['C'])
        self.assertNotIsInstance(results['D'], BaseException, results['D'])

    def test_the_main_safe_cycle_does_not_form_when_the_creation_waits_for_the_bar(self):
        results, outcome = self.cycle(via_cash=True)
        self.assertTrue(outcome['D_finished_while_C_waited'], f'direct payment blocked behind the creation: {outcome}')
        self.assertIsNone(outcome['H_supplier_request'], outcome)
        self.assertIsInstance(results['C'], dict, results['C'])
        self.assertNotIsInstance(results['D'], BaseException, results['D'])

    def test_production_authorization_queues_a_second_protected_writer_behind_the_first(self):
        """Informational, and the reason the schedules above avoid it: a second protected writer, even acting as a DIFFERENT OWNER user, waits
        on the authorization rows (not on the bar, Scrap or cash it needs) while the first one waits for a bar."""
        lot = self.shop_lot()
        other, _, _ = self.services('second')
        results, tables = {}, set()
        with self.tx() as holder:
            q = holder.cursor()
            q.execute('SELECT id FROM market_trip_lots WHERE id=%s FOR UPDATE', (lot,))
            q.fetchall()
            q.close()
            first = self.thread(results, 'C', lambda: self.service.create_trip(
                '', [{'karat_id': self.karats[21], 'weight': '1', 'designation': 'LOGO'}], [{'lot_id': str(lot)}], DATE, 'hardening', str(uuid4())))
            second = None
            try:
                self.wait_for_lock_wait('market_trip_lots')
                second = self.thread(results, 'D', lambda: other.issue('', '1', [], DATE, 'hardening', str(uuid4())))
                deadline = time.time() + 15
                while time.time() < deadline and not (tables & AUTHORIZATION_TABLES):
                    tables = {name for name, _ in self.lock_waits()}
                    time.sleep(0.1)
                finished_early = 'D' in results
            finally:
                holder.commit()
                first.join(60)
                if second is not None:
                    second.join(60)
        self.assertFalse(finished_early, 'the second writer finished although the first still waited for the bar')
        self.assertTrue(tables & AUTHORIZATION_TABLES, f'the second writer waited on {tables}, not on the authorization rows')
        self.assertIsInstance(results['C'], dict, results['C'])
        self.assertIsInstance(results['D'], dict, results['D'])


if __name__ == '__main__':
    unittest.main()
