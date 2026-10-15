"""Explicit opt-in rehearsal checks for the 050 supplier gold settlement from intermediary holdings. They connect to masgold_rehearsal ONLY (target, migration level and
marker are asserted) and never to the DEV database. Real services, real repositories, real SQL, separate REPEATABLE READ connections.

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
from services.authorization_service import AuthorizationService
from services.correction_service import CorrectionError, CorrectionService
from services.holding_settlement_service import HoldingSettlementService
from services.intermediary_holding_service import IntermediaryHoldingService
from services.market_trip_service import TripError
from services.supplier_gold_settlement_service import SupplierGoldSettlementService
from services.supplier_rules import SupplierRuleError
from tests.test_intermediary_holdings_mysql import DATE
from tests.test_logo_holdings_mysql import LogoMySqlTests
from tests.test_market_trip_hardening_mysql import UnlockedAuthorization

WHOLE, ENTITLEMENT, DIVIDED = 'WHOLE_ORIGINAL_BAR', 'GRAM_ENTITLEMENT', 'PHYSICALLY_DIVIDED'


class Recording(AuthorizationService):
    """Records every right checked as a WRITE (and delegates to the real database check)."""

    def __init__(self, session):
        super().__init__(session)
        self.codes = []

    def require_permission_for_write(self, connection, actor_context, code, **kwargs):
        self.codes.append(code)
        return super().require_permission_for_write(connection, actor_context, code, **kwargs)

    require_permission = require_permission_for_write


class Denying(AuthorizationService):
    """Verifies everything against the database except ONE right, which the database 'refuses' (so each right is shown to be checked in the transaction)."""

    def __init__(self, session, denied):
        super().__init__(session)
        self.denied = denied

    def require_permission_for_write(self, connection, actor_context, code, **kwargs):
        if code == self.denied:
            raise PermissionError('denied in the transaction')
        return super().require_permission_for_write(connection, actor_context, code, **kwargs)

    require_permission = require_permission_for_write


class SettleMySqlTests(LogoMySqlTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        suppliers = cls.suppliers
        cls.s21 = suppliers.create_supplier('050 مورد 21 ' + uuid4().hex[:10], karat_id=cls.karats[21])
        cls.s24 = suppliers.create_supplier('050 مورد 24 ' + uuid4().hex[:10], karat_id=cls.karats[24])
        cls.s_off = suppliers.create_supplier('050 مورد موقوف ' + uuid4().hex[:10], karat_id=cls.karats[18])
        suppliers.set_active(cls.s_off, False)

    def setUp(self):
        super().setUp()
        self.settler = HoldingSettlementService(self.session, transaction_factory=self.tx, corrections=self.correct)

    # ------------------------------------------------------------------ helpers
    def settle(self, kind, pool, supplier, grams, declaration=None, confirmed=False, key=None, service=None):
        return (service or self.settler).settle(kind, str(pool), str(supplier), grams, declaration, confirmed, DATE, '050 ref', '050 notes', key or str(uuid4()))

    def logo_pool(self, grams='10'):
        trip = self.trip('20')
        return trip, self.pool_of(self.put(trip, grams)['document_id'])

    def lot_pool(self, weight='10', fineness='875', residues=(), index=0):
        trip, melt, rows = self.lots(weight, fineness, residues)
        done = self.hand(trip, rows[index])
        return trip, rows, int(self.query('SELECT pool_id FROM intermediary_holding_documents WHERE id=%s', (done['document_id'],))[0]['pool_id']), done

    def lot_balance(self, pool):
        return self.query('SELECT COALESCE(SUM(weight_delta),0) AS g FROM intermediary_holding_movements WHERE pool_id=%s', (pool,))[0]['g']

    def payable(self, supplier):
        return self.query('SELECT COALESCE(SUM(weight_delta_grams),0) AS g FROM supplier_gold_movements WHERE supplier_id=%s', (int(supplier),))[0]['g']

    def header(self, settlement_id):
        return self.query('SELECT * FROM supplier_gold_settlements WHERE id=%s', (settlement_id,))[0]

    def others(self):
        tables = ('old_gold_movements', 'cash_movements', 'bank_movements', 'supplier_making_movements', 'market_trip_lot_shop_movements', 'inventory_items',
                  'market_trip_entries', 'market_trip_lot_movements')
        return {t: self.query(f'SELECT COUNT(*) AS n FROM {t}')[0]['n'] for t in tables}

    def state(self):
        tables = ('supplier_gold_settlements', 'supplier_gold_settlement_legs', 'supplier_gold_movements', 'intermediary_holding_movements', 'logo_holding_movements',
                  'transaction_corrections', 'audit_log')
        return {**{t: self.query(f'SELECT COUNT(*) AS n FROM {t}')[0]['n'] for t in tables}, **self.others()}

    def reverse(self, settlement, reason='050', service=None):
        return (service or self.settler).reverse(str(settlement), reason)

    def entry_of(self, document_id):
        return self.query('SELECT entry_id FROM market_trip_holding_links WHERE document_id=%s', (document_id,))[0]['entry_id']

    # ------------------------------------------------------------------ functional
    def test_exact_effects_conversion_and_a_single_gold_leg_for_both_sources(self):
        trip, pool = self.logo_pool('10')
        custody = self.logo(trip)['outstanding']
        before = self.others()
        done = self.settle('LOGO', pool, self.supplier, '1')                                  # 21K -> an 18K supplier
        self.assertEqual((done['source_kind'], done['delivered_weight'], done['credited_weight'], done['analytical'], done['rounding_difference'],
                          done['remaining'], done['duplicate']), ('HOLDING_LOGO', '1.000', '1.167', '1.166667', '0.000333', '9.000', False))
        header = self.header(done['id'])
        self.assertEqual((header['delivered_karat_value'], header['target_karat_value'], header['logo_pool_id'], header['lot_pool_id'], header['physical_declaration'],
                          header['delivered_lot_id'], header['holding_weight_grams']), (21, 18, pool, None, None, None, D('1.000')))
        self.assertEqual(self.logo(trip)['outstanding'], custody)
        self.assertEqual(self.others(), before)                                               # no custody, Scrap, cash, bank, making, shop-lot or inventory movement
        legs = self.query('SELECT leg_kind, source_kind, old_gold_movement_id FROM supplier_gold_settlement_legs WHERE settlement_id=%s', (done['id'],))
        self.assertEqual([(l['leg_kind'], l['source_kind'], l['old_gold_movement_id']) for l in legs], [('GOLD', 'HOLDING_LOGO', None)])
        self.assertEqual(self.payable(self.supplier) <= D('-1.167'), True)
        # a known-assay lot: the ORIGINAL weight stays beside the delivered grams
        lot_trip, rows, lot_pool, _ = self.lot_pool('10', '875')
        before = self.others()
        lot = self.settle('LOT', lot_pool, self.supplier, '4', ENTITLEMENT)
        self.assertEqual((lot['credited_weight'], lot['analytical'], lot['rounding_difference'], lot['remaining'], lot['declaration']),
                         ('4.667', '4.666667', '0.000333', '6.000', ENTITLEMENT))
        header = self.header(lot['id'])
        self.assertEqual((header['delivered_lot_weight_grams'], header['delivered_weight_grams'], header['delivered_fineness_permille'], header['lot_pool_id'],
                          header['delivered_karat_id']), (D('10.000'), D('4.000'), D('875.0'), lot_pool, None))
        self.assertEqual(self.others(), before)
        self.assertEqual(self.query('SELECT weight_grams, fineness_permille FROM market_trip_lots WHERE id=%s', (rows[0]['id'],))[0],
                         {'weight_grams': D('10.000'), 'fineness_permille': D('875.0')})

    def test_two_suppliers_consume_the_same_source_inactive_recipient_and_credit_crossing_are_allowed(self):
        _, _, pool, _ = self.lot_pool('10', '875')
        first = self.settle('LOT', pool, self.s21, '4', ENTITLEMENT)
        second = self.settle('LOT', pool, self.s_off, '6', ENTITLEMENT)                      # an INACTIVE recipient
        self.assertEqual((first['credited_weight'], second['credited_weight'], self.lot_balance(pool)), ('4.000', '7.000', D('0')))
        self.assertLessEqual(self.payable(self.s21), D('-4.000'))                              # the payable crossed zero into supplier credit
        with self.assertRaises(TripError) as drained:
            self.settle('LOT', pool, self.s21, '0.001', ENTITLEMENT)
        self.assertEqual(drained.exception.code, 'HOLDING_NOT_AVAILABLE')

    def test_double_rounding_boundary_and_zero_credit(self):
        _, _, pool, _ = self.lot_pool('20', '499.6')
        before = self.state()
        with self.assertRaises(SupplierRuleError) as zero:                                    # 0.001 x 499.6/1000 x 24/24 = 0.0004996: directly 0.000 (a six-decimal detour would say 0.001)
            self.settle('LOT', pool, self.s24, '0.001', ENTITLEMENT)
        self.assertEqual(zero.exception.code, 'CREDIT_ROUNDS_TO_ZERO')
        self.assertEqual(self.state(), before)
        _, _, half, _ = self.lot_pool('20', '500')
        done = self.settle('LOT', half, self.s24, '0.001', ENTITLEMENT)                      # exactly 0.0005 -> HALF_UP 0.001
        self.assertEqual((done['credited_weight'], done['analytical'], done['rounding_difference']), ('0.001', '0.000500', '0.000500'))

    def test_refusals_leave_no_trace(self):
        trip, pool = self.logo_pool('2')
        before = self.state()
        for grams, error, code in (('1.0001', SupplierRuleError, 'WEIGHT_INVALID'), ('0', SupplierRuleError, 'WEIGHT_INVALID'), ('-1', SupplierRuleError, 'WEIGHT_INVALID'),
                                   ('2.001', TripError, 'HOLDING_EXCEEDED')):
            with self.assertRaises(error) as refused:
                self.settle('LOGO', pool, self.supplier, grams)
            self.assertEqual(refused.exception.code, code, grams)
        with self.assertRaises(SupplierRuleError) as role:
            self.settle('LOGO', pool, self.party2, '1')                                       # an intermediary-only recipient has no gold account
        self.assertEqual(role.exception.code, 'SUPPLIER_ROLE_REQUIRED')
        with self.assertRaises(TripError) as missing:
            self.settle('LOGO', '999999999', self.supplier, '1')
        self.assertEqual(missing.exception.code, 'HOLDING_NOT_FOUND')
        self.assertEqual(self.state(), before)

    # ------------------------------------------------------------------ physical declarations
    def test_full_quantity_entitlement_versus_a_real_whole_bar_delivery(self):
        _, _, pool, handed = self.lot_pool('10', '875')
        entitlement = self.settle('LOT', pool, self.s21, '10', ENTITLEMENT)                   # the full quantity, but only an entitlement
        self.reverse(entitlement['id'])
        self.assertEqual(self.lot_balance(pool), D('10'))
        with self.assertRaises(TripError) as whole:
            self.settle('LOT', pool, self.s21, '10', WHOLE)
        self.assertEqual(whole.exception.code, 'WHOLE_NOT_ELIGIBLE')
        self.assertEqual(self.back(pool)['kind'], 'HOLD_RETURN')                              # a cancelled entitlement needs no false error declaration
        with self.assertRaises(CorrectionError) as handover:
            self.correct.reverse('MARKET_TRIP_ENTRY', self.entry_of(handed['document_id']), '050')
        self.assertEqual(handover.exception.code, 'DEPENDENT_HOLDING_SETTLEMENT_EXISTS')
        self.assertIn(f"SG{int(entitlement['id']):06d}", str(handover.exception))

    def test_whole_delivery_reversal_confirmed_normal_return_and_repeat_delivery(self):
        _, _, pool, handed = self.lot_pool('10', '875')
        whole = self.settle('LOT', pool, self.s21, '10', WHOLE)
        self.assertEqual((self.header(whole['id'])['physical_declaration'], self.lot_balance(pool)), (WHOLE, D('0')))
        self.reverse(whole['id'])
        with self.assertRaises(TripError) as unconfirmed:
            self.back(pool, confirmed=False)
        self.assertEqual(unconfirmed.exception.code, 'INTACT_CONFIRMATION_REQUIRED')          # a full balance alone is not enough
        with self.assertRaises(CorrectionError) as handover:
            self.correct.reverse('MARKET_TRIP_ENTRY', self.entry_of(handed['document_id']), '050')
        self.assertEqual(handover.exception.code, 'DEPENDENT_HOLDING_SETTLEMENT_EXISTS')     # the handover reversal must not bypass the physical verification
        again = self.settle('LOT', pool, self.s_off, '10', WHOLE)                            # repeat whole delivery to another supplier after the reversal
        self.reverse(again['id'])
        self.assertEqual(self.back(pool)['kind'], 'HOLD_RETURN')                              # a confirmed normal return is not blocked by whole-delivery history

    def test_partial_allocation_blocks_whole_return_in_every_order_and_the_remaining_balance_is_not_a_whole_bar(self):
        _, _, pool, handed = self.lot_pool('10', '875')
        first = self.settle('LOT', pool, self.s21, '4', ENTITLEMENT)
        with self.assertRaises(TripError) as no_whole:
            self.settle('LOT', pool, self.s21, '6', WHOLE)
        self.assertEqual(no_whole.exception.code, 'WHOLE_NOT_ELIGIBLE')
        second = self.settle('LOT', pool, self.s_off, '6', DIVIDED)
        self.assertEqual(self.lot_balance(pool), D('0'))
        self.reverse(first['id'])
        self.reverse(second['id'])
        self.assertEqual(self.lot_balance(pool), D('10'))
        with self.assertRaises(TripError) as blocked:
            self.back(pool)
        self.assertEqual(blocked.exception.code, 'PHYSICAL_EVENT_DECLARATION_REQUIRED')
        self.assertEqual(self.back(pool, physical_confirmed=True)['kind'], 'HOLD_RETURN')
        moves = self.query('SELECT movement_type, weight_delta, correction_id, document_id FROM intermediary_holding_movements WHERE pool_id=%s AND settlement_id IS NOT NULL ORDER BY id', (pool,))
        self.assertEqual(sorted(m['movement_type'] for m in moves), ['SETTLEMENT', 'SETTLEMENT', 'SETTLEMENT_REVERSAL', 'SETTLEMENT_REVERSAL'])
        self.assertTrue(all(m['document_id'] is None for m in moves))
        self.assertEqual(sum((m['weight_delta'] for m in moves), D('0')), D('0'))              # compensations counted beside the originals

    # ------------------------------------------------------------------ reversal
    def test_one_canonical_correction_restores_the_stored_credit_and_the_grams(self):
        _, _, pool, _ = self.lot_pool('10', '875')
        done = self.settle('LOT', pool, self.supplier, '4', ENTITLEMENT)
        credit = self.query('SELECT weight_delta_grams, equivalent_21_delta_grams FROM supplier_gold_movements WHERE source_type=%s AND source_id=%s',
                            ('SUPPLIER_GOLD_SETTLEMENT', done['id']))
        result = self.reverse(done['id'])
        restore = self.query('SELECT weight_delta_grams, equivalent_21_delta_grams, source_type, source_id FROM supplier_gold_movements WHERE source_type=%s AND source_id=%s',
                             ('TRANSACTION_CORRECTION', result['correction_id']))
        self.assertEqual([(r['weight_delta_grams'], r['equivalent_21_delta_grams']) for r in restore], [(-credit[0]['weight_delta_grams'], -credit[0]['equivalent_21_delta_grams'])])
        compensation = self.query("SELECT correction_id, weight_delta FROM intermediary_holding_movements WHERE pool_id=%s AND movement_type='SETTLEMENT_REVERSAL'", (pool,))
        self.assertEqual([(int(c['correction_id']), c['weight_delta']) for c in compensation], [(int(result['correction_id']), D('4.000'))])
        self.assertEqual(self.lot_balance(pool), D('10'))
        with self.assertRaises(CorrectionError) as again:
            self.reverse(done['id'])
        self.assertEqual(again.exception.code, 'ALREADY_REVERSED')
        # the supplier page reaches the very same correction
        _, pool2 = self.logo_pool('5')
        logo = self.settle('LOGO', pool2, self.supplier, '2')
        SupplierGoldSettlementService(self.session, transaction_factory=self.tx, corrections=self.correct).reverse(logo['id'], 'من صفحة المورد')
        with self.assertRaises(CorrectionError):
            self.reverse(logo['id'])
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM transaction_corrections WHERE source_type='SUPPLIER_GOLD_SETTLEMENT' AND source_id=%s", (logo['id'],))[0]['n'], 1)

    def test_logo_settlement_return_and_reversal_arithmetic_with_a_closed_origin_custody(self):
        trip, pool = self.logo_pool('10')
        s1 = self.settle('LOGO', pool, self.supplier, '4')
        returned = self.take(pool, '3')
        s2 = self.settle('LOGO', pool, self.s21, '3')
        self.assertEqual((self.balance(pool), self.logo(trip)['outstanding']), (D('0'), D('13.000')))
        with self.assertRaises(CorrectionError) as blocked:
            self.correct.reverse('MARKET_TRIP_ENTRY', self.query('SELECT entry_id FROM market_trip_logo_hold_links WHERE pool_id=%s AND entry_kind=%s', (pool, 'LOGO_HOLD'))[0]['entry_id'], '050')
        self.assertEqual(blocked.exception.code, 'DEPENDENT_HOLDING_RETURN_EXISTS')
        self.service.return_custody(trip['trip_id'], '', [{'karat_id': self.karats[21], 'weight': '13', 'designation': 'LOGO'}], DATE, '', str(uuid4()))
        self.service.close(trip['trip_id'])                                                    # the origin custody is CLOSED while the settlements stay reversible
        self.reverse(s2['id'])
        self.reverse(s1['id'])
        self.assertEqual(self.balance(pool), D('7'))
        self.assertEqual(self.query('SELECT status FROM market_trips WHERE id=%s', (trip['trip_id'],))[0]['status'], 'CLOSED')
        self.assertIsNotNone(returned)

    def test_the_resulting_balance_is_checked_explicitly(self):
        _, pool = self.logo_pool('10')
        done = self.settle('LOGO', pool, self.supplier, '4')
        stray = self.query("SELECT id FROM logo_holding_documents WHERE pool_id=%s AND doc_kind='HANDOVER'", (pool,))[0]['id']
        with self.tx() as c:                                                                   # a stray +5 (balance 11 > the initial 10): never reachable through the services
            q = c.cursor()
            q.execute("INSERT INTO logo_holding_documents (doc_kind,pool_id,party_id,karat_id,trip_id,weight_grams,document_date,request_key,request_hash,performed_by_user_id,occurred_at) "
                      "SELECT 'RETURN',pool_id,party_id,karat_id,trip_id,5,%s,%s,REPEAT('f',64),performed_by_user_id,NOW() FROM logo_holding_documents WHERE id=%s",
                      (DATE, str(uuid4()), stray))
            document = q.lastrowid
            q.execute("INSERT INTO transaction_corrections (correction_type, source_type, source_id, source_reference, invoice_id, reason, performed_by_user_id, occurred_at) "
                      "VALUES ('FULL_REVERSAL','FINANCIAL_OPERATION',%s,'050-stray',NULL,'stray',%s,NOW())", (uuid4().int % 10 ** 9, self.user))
            correction = q.lastrowid
            q.execute("INSERT INTO logo_holding_movements (pool_id,document_id,doc_kind,movement_type,weight_grams,weight_delta,correction_id) "
                      "VALUES (%s,%s,'RETURN','RETURN_REVERSAL',5,5,%s)", (pool, document, correction))
            q.close()
        with self.assertRaises(CorrectionError) as invalid:
            self.reverse(done['id'])
        self.assertEqual(invalid.exception.code, 'HOLDING_BALANCE_INVALID')

    # ------------------------------------------------------------------ rights
    def test_each_right_is_checked_in_the_transaction_and_existing_source_rights_are_unchanged(self):
        trip, pool = self.logo_pool('10')
        before = self.state()
        for denied in ('INTERMEDIARY_HOLDING_MANAGE', 'SUPPLIER_GOLD_SETTLE'):
            service = HoldingSettlementService(self.session, transaction_factory=self.tx, authorization=Denying(self.session, denied), corrections=self.correct)
            with self.assertRaises(PermissionError):
                self.settle('LOGO', pool, self.supplier, '1', service=service)
            self.assertEqual(self.state(), before, denied)
        manager, _ = self.make_session('050-manager', 'MANAGER')                                # a MANAGER has the supplier right but not the holding right
        with self.assertRaises(PermissionError):
            self.settle('LOGO', pool, self.supplier, '1', service=HoldingSettlementService(manager, transaction_factory=self.tx))
        done = self.settle('LOGO', pool, self.supplier, '2')
        for denied in ('TRANSACTION_CORRECT', 'SUPPLIER_GOLD_SETTLE_REVERSE', 'INTERMEDIARY_HOLDING_REVERSE'):
            correcting = CorrectionService(self.session, authorization=Denying(self.session, denied), transaction_factory=self.tx)
            service = HoldingSettlementService(self.session, transaction_factory=self.tx, corrections=correcting)
            before = self.state()
            with self.assertRaises(PermissionError):
                self.reverse(done['id'], service=service)
            self.assertEqual(self.state(), before, denied)
        with self.assertRaises(PermissionError):
            self.reverse(done['id'], service=HoldingSettlementService(manager, transaction_factory=self.tx))
        # the rights each correction source needs, recorded from the real authorization
        recording = Recording(self.session)
        recorded = CorrectionService(self.session, authorization=recording, transaction_factory=self.tx)

        def needed(call):
            recording.codes.clear()
            call()
            return set(recording.codes)
        holding = HoldingSettlementService(self.session, transaction_factory=self.tx, corrections=recorded)
        self.assertEqual(needed(lambda: self.reverse(done['id'], service=holding)), {'TRANSACTION_CORRECT', 'SUPPLIER_GOLD_SETTLE_REVERSE', 'INTERMEDIARY_HOLDING_REVERSE'})
        handed = self.put(trip, '1')
        self.assertEqual(needed(lambda: recorded.reverse('MARKET_TRIP_ENTRY', handed['entry_id'], '050')),
                         {'TRANSACTION_CORRECT', 'MARKET_TRIP_MANAGE', 'INTERMEDIARY_HOLDING_REVERSE'})
        issue = self.service.issue(trip['trip_id'], '', [{'karat_id': self.karats[21], 'weight': '1', 'designation': 'LOGO'}], DATE, '', str(uuid4()))
        self.assertEqual(needed(lambda: recorded.reverse('MARKET_TRIP_ENTRY', issue['entry_id'], '050')), {'TRANSACTION_CORRECT', 'MARKET_TRIP_MANAGE'})
        direct = SupplierGoldSettlementService(self.session, transaction_factory=self.tx, corrections=recorded).settle(self.supplier, self.karats[21], '1', DATE, '', '', str(uuid4()))
        self.assertEqual(needed(lambda: recorded.reverse('SUPPLIER_GOLD_SETTLEMENT', direct['id'], '050')), {'TRANSACTION_CORRECT', 'SUPPLIER_GOLD_SETTLE_REVERSE'})
        custody = self.service.settle_supplier_gold(trip['trip_id'], self.supplier, self.karats[21], 'LOGO', '1', DATE, '', '', str(uuid4()))
        self.assertEqual(needed(lambda: recorded.reverse('MARKET_TRIP_ENTRY', custody['entry_id'], '050')),
                         {'TRANSACTION_CORRECT', 'MARKET_TRIP_MANAGE', 'SUPPLIER_GOLD_SETTLE_REVERSE'})

    # ------------------------------------------------------------------ parties, replay, cross-flow conflicts
    def test_inactive_holder_self_settlement_and_account_separation(self):
        trip = self.trip('20')
        pool = self.pool_of(self.put(trip, '10', party=self.dual)['document_id'])           # a dual-role party (a 21K supplier too)
        with self.assertRaises(TripError) as unconfirmed:
            self.settle('LOGO', pool, self.dual, '4')
        self.assertEqual(unconfirmed.exception.code, 'SELF_SETTLEMENT_CONFIRMATION_REQUIRED')
        key = str(uuid4())
        payable_before = self.payable(self.dual)
        done = self.settle('LOGO', pool, self.dual, '4', confirmed=True, key=key)
        self.assertEqual((self.header(done['id'])['self_settlement_confirmed'], self.header(done['id'])['holding_party_id'], self.balance(pool)),
                         (1, int(self.dual), D('6')))
        self.assertEqual(self.payable(self.dual), payable_before - D('4.000'))                  # holdings and the supplier payable moved separately
        with self.assertRaises(TripError) as flag:
            self.settle('LOGO', pool, self.dual, '4', confirmed=False, key=key)                  # the flag is part of the digest
        self.assertEqual(flag.exception.code, 'REQUEST_CONFLICT')
        self.suppliers.set_active(self.dual, False)
        try:
            self.assertEqual(self.settle('LOGO', pool, self.s21, '1')['remaining'], '5.000')   # an inactive holder can still be settled
            with self.assertRaises(TripError) as inactive:
                self.put(trip, '1', party=self.dual)
            self.assertEqual(inactive.exception.code, 'PARTY_INACTIVE')
        finally:
            self.suppliers.set_active(self.dual, True)
        with self.assertRaises(SupplierRuleError) as history:
            self.suppliers.update_supplier(self.dual, 'x ' + uuid4().hex[:8], is_supplier=True, is_intermediary=False)
        self.assertIn(history.exception.code, ('ROLE_HISTORY_EXISTS', 'ROLE_REQUIRED'))     # the holding history keeps the intermediary role

    def test_replay_after_reversal_and_cross_flow_key_conflicts_in_both_directions(self):
        trip, pool = self.logo_pool('10')
        key = str(uuid4())
        first = self.settle('LOGO', pool, self.supplier, '10', key=key)
        again = self.settle('LOGO', pool, self.supplier, '10', key=key)                       # nothing is left: no availability is derived
        self.assertEqual((again['duplicate'], again['id']), (True, first['id']))
        self.reverse(first['id'])
        after = self.settle('LOGO', pool, self.supplier, '10', key=key)
        self.assertEqual((after['duplicate'], after['id']), (True, first['id']))
        self.assertEqual(self.query('SELECT COUNT(*) AS n FROM supplier_gold_settlements WHERE request_key=%s', (key,))[0]['n'], 1)
        for call, error in ((lambda: self.settle('LOGO', pool, self.supplier, '9', key=key), TripError),
                            (lambda: self.settle('LOGO', pool, self.s21, '10', key=key), TripError),
                            (lambda: self.put(trip, '1', key=key), TripError),
                            (lambda: self.take(pool, '1', key=key), TripError),
                            (lambda: self.service.settle_supplier_gold(trip['trip_id'], self.supplier, self.karats[21], 'LOGO', '1', DATE, '', '', key), TripError),
                            (lambda: SupplierGoldSettlementService(self.session, transaction_factory=self.tx).settle(self.supplier, self.karats[21], '1', DATE, '', '', key),
                             SupplierRuleError)):
            with self.assertRaises(error) as conflict:
                call()
            self.assertEqual(conflict.exception.code, 'REQUEST_CONFLICT')
        other = str(uuid4())
        self.put(trip, '1', key=other)                                                          # an IG / entry key reused by the settlement
        with self.assertRaises(TripError) as reuse:
            self.settle('LOGO', pool, self.supplier, '1', key=other)
        self.assertEqual(reuse.exception.code, 'REQUEST_CONFLICT')

    def test_rollback_after_each_persistent_stage(self):
        _, pool = self.logo_pool('10')
        _, _, lot_pool, _ = self.lot_pool('10', '875')

        def failing(cls, name):
            original = getattr(cls, name)

            def fail(instance, *a, _original=original, **kw):
                _original(instance, *a, **kw)
                raise RuntimeError('050 injected')
            return patch.object(cls, name, fail)
        stages = ((SupplierGoldSettlementRepository, 'insert_holding_header'), (SupplierGoldSettlementRepository, 'insert_gold_movement'),
                  (SupplierGoldSettlementRepository, 'insert_leg'), (IntermediaryHoldingRepository, 'insert_settlement_movement'),
                  (SupplierGoldSettlementRepository, 'audit'), (IntermediaryHoldingRepository, 'audit'))
        for kind, source, declaration in (('LOGO', pool, None), ('LOT', lot_pool, ENTITLEMENT)):
            for cls, name in stages:
                before = self.state()
                with self.subTest(stage=f'{kind}/{name}'), failing(cls, name), self.assertRaisesRegex(RuntimeError, '050 injected'):
                    self.settle(kind, source, self.supplier, '1', declaration)
                self.assertEqual(self.state(), before, f'{kind}/{name}')
        done = self.settle('LOGO', pool, self.supplier, '1')
        for cls, name in ((CorrectionRepository, 'insert_correction'), (SupplierGoldSettlementRepository, 'insert_gold_movement'),
                          (IntermediaryHoldingRepository, 'insert_settlement_movement')):
            before = self.state()
            with self.subTest(stage=f'reverse/{name}'), failing(cls, name), self.assertRaisesRegex(RuntimeError, '050 injected'):
                self.reverse(done['id'])
            self.assertEqual(self.state(), before, name)
        self.assertEqual(self.balance(pool), D('9'))

    # ------------------------------------------------------------------ controlled RR snapshots
    def test_rr_snapshot_the_balance_and_the_allocation_history_are_current_reads(self):
        _, _, pool, _ = self.lot_pool('10', '875')
        with self.tx() as reader:
            q = reader.cursor()
            q.execute('SELECT COUNT(*) FROM supplier_gold_settlements')                       # the snapshot: no allocation exists yet
            q.fetchone()
            q.close()
            allocation = self.settle('LOT', pool, self.s21, '10', ENTITLEMENT)                 # another connection commits an allocation ...
            self.reverse(allocation['id'])                                                      # ... and its reversal: the balance is full again
            service = HoldingSettlementService(self.session, transaction_factory=lambda: self.on_connection(reader))
            with self.assertRaises(TripError) as current:
                service.settle('LOT', str(pool), str(self.s21), '10', WHOLE, False, DATE, '', '', str(uuid4()))
            self.assertEqual(current.exception.code, 'WHOLE_NOT_ELIGIBLE')                      # decided on the committed history, not the stale snapshot
            with patch.object(IntermediaryHoldingRepository, 'pool_settlement_history',
                              lambda instance, pool_id: instance._fetch_all('SELECT id, physical_declaration FROM supplier_gold_settlements WHERE lot_pool_id = %s', (int(pool_id),))):
                control = service.settle('LOT', str(pool), str(self.s21), '10', WHOLE, False, DATE, '', '', str(uuid4()))
            self.assertEqual(control['declaration'], WHOLE)                                     # control: the non-locking shape wrongly accepts it
            reader.rollback()
        self.assertEqual(self.query('SELECT COUNT(*) AS n FROM supplier_gold_settlements WHERE lot_pool_id=%s', (pool,))[0]['n'], 1)

    def test_rr_snapshot_a_settlement_sees_the_committed_balance(self):
        _, pool = self.logo_pool('10')
        with self.tx() as reader:
            q = reader.cursor()
            q.execute('SELECT COUNT(*) FROM logo_holding_movements')
            q.fetchone()
            q.close()
            self.settle('LOGO', pool, self.supplier, '6')                                        # committed elsewhere: 4 g left
            service = HoldingSettlementService(self.session, transaction_factory=lambda: self.on_connection(reader))
            with self.assertRaises(TripError) as exceeded:
                service.settle('LOGO', str(pool), str(self.supplier), '8', None, False, DATE, '', '', str(uuid4()))
            self.assertEqual(exceeded.exception.code, 'HOLDING_EXCEEDED')
            self.assertIn('4.000', str(exceeded.exception))
            reader.rollback()
        self.assertEqual(self.balance(pool), D('4'))

    def test_rr_snapshot_a_reversal_decides_on_the_committed_settlement_state(self):
        _, pool = self.logo_pool('10')
        done = self.settle('LOGO', pool, self.supplier, '4')
        with self.tx() as reader:
            q = reader.cursor()
            q.execute('SELECT COUNT(*) FROM transaction_corrections')
            q.fetchone()
            q.close()
            self.reverse(done['id'])
            reversing = HoldingSettlementService(self.session, transaction_factory=lambda: self.on_connection(reader),
                                                 corrections=CorrectionService(self.session, transaction_factory=lambda: self.on_connection(reader)))
            with self.assertRaises(CorrectionError) as twice:
                reversing.reverse(str(done['id']), 'stale')
            self.assertEqual(twice.exception.code, 'ALREADY_REVERSED')
            reader.rollback()
        self.assertEqual(self.balance(pool), D('10'))

    # ------------------------------------------------------------------ resource order and contention (no production authorization locks)
    def test_real_acquisition_order_is_lot_then_parties_ascending_then_pool_and_never_the_trip(self):
        _, _, lot_pool, _ = self.lot_pool('10', '875')
        _, logo = self.logo_pool('10')
        order = []

        def wrap(cls, name, label, locking_only=False):
            original = getattr(cls, name)

            def wrapper(instance, *args, **kwargs):
                if not locking_only or kwargs.get('lock'):
                    order.append(label(args, kwargs) if callable(label) else label)
                return original(instance, *args, **kwargs)
            return patch.object(cls, name, wrapper)
        stack = (wrap(MarketTripRepository, 'trip', 'trip', True), wrap(SupplierGoldSettlementRepository, 'get_supplier', lambda a, k: f'party:{int(a[0])}', True),
                 wrap(IntermediaryHoldingRepository, 'lock_pool', 'pool'), wrap(IntermediaryHoldingRepository, 'lock_logo_pool', 'logo_pool'),
                 wrap(MarketTripRepository, 'lock_lots', 'lots'))
        for p in stack:
            p.start()
        try:
            holder = int(self.party)
            for recipient in (self.supplier, self.s21, self.s24):
                order.clear()
                self.settle('LOT', lot_pool, recipient, '1', ENTITLEMENT)
                expected = sorted({holder, int(recipient)})
                self.assertEqual(order, ['lots'] + [f'party:{p}' for p in expected] + ['pool'], recipient)
            order.clear()
            self.settle('LOGO', logo, self.supplier, '1')
            self.assertEqual(order, [f'party:{p}' for p in sorted({holder, int(self.supplier)})] + ['logo_pool'])
            done = self.settle('LOGO', logo, self.s24, '1')
            order.clear()
            self.reverse(done['id'])
            self.assertEqual(order[:3], [f'party:{p}' for p in sorted({holder, int(self.s24)})] + ['logo_pool'])
            self.assertNotIn('trip', order)
        finally:
            for p in reversed(stack):
                p.stop()

    def test_a_settlement_waiting_for_the_pool_holds_the_lot_and_both_parties_but_not_the_trip(self):
        trip, rows, pool, _ = self.lot_pool('10', '875')
        results = {}
        with self.tx() as holder:                                                              # raw holder: no authorization locks involved
            q = holder.cursor()
            q.execute('SELECT id FROM intermediary_holding_pools WHERE id=%s FOR UPDATE', (pool,))
            q.fetchall()
            q.close()
            worker = self.thread(results, 'S', lambda: self.settle('LOT', pool, self.supplier, '2', ENTITLEMENT, service=HoldingSettlementService(
                self.session, transaction_factory=self.tx, authorization=UnlockedAuthorization(self.session))))
            try:
                self.wait_waiting('intermediary_holding_pools')
                with self.tx() as observer:
                    lot_lock = self.probe(observer, 'SELECT id FROM market_trip_lots WHERE id=%s FOR UPDATE NOWAIT', (rows[0]['id'],))
                    holder_lock = self.probe(observer, 'SELECT id FROM suppliers WHERE id=%s FOR UPDATE NOWAIT', (int(self.party),))
                    recipient_lock = self.probe(observer, 'SELECT id FROM suppliers WHERE id=%s FOR UPDATE NOWAIT', (int(self.supplier),))
                    trip_lock = self.probe(observer, 'SELECT id FROM market_trips WHERE id=%s FOR UPDATE NOWAIT', (trip['trip_id'],))
            finally:
                holder.commit()
                worker.join(60)
        self.assertEqual((lot_lock, holder_lock, recipient_lock), (3572, 3572, 3572))          # lot -> parties -> pool ...
        self.assertIsNone(trip_lock)                                                           # ... and the originating trip is never locked
        self.assertIsInstance(results['S'], dict, results['S'])

    def test_two_settlements_racing_on_one_source_never_exceed_its_balance(self):
        _, pool = self.logo_pool('6')
        sessions = [self.make_session(f'settle-racer{i}', 'OWNER')[0] for i in range(2)]
        services = [HoldingSettlementService(s, transaction_factory=self.tx, authorization=UnlockedAuthorization(s)) for s in sessions]
        barrier, results = threading.Barrier(2), {}

        def race(index):
            def call():
                barrier.wait(timeout=10)
                return self.settle('LOGO', pool, self.supplier, '4', service=services[index])
            return call
        workers = [self.thread(results, 'A', race(0)), self.thread(results, 'B', race(1))]
        for worker in workers:
            worker.join(60)
        outcomes = list(results.values())
        self.assertEqual(sum(isinstance(r, dict) for r in outcomes), 1, outcomes)
        self.assertEqual(sum(isinstance(r, TripError) and r.code == 'HOLDING_EXCEEDED' for r in outcomes), 1, outcomes)
        self.assertEqual(self.balance(pool), D('2'))

    def test_a_settlement_and_a_return_racing_on_one_source_never_overdraw_it(self):
        _, pool = self.logo_pool('6')
        sessions = [self.make_session(f'settle-return{i}', 'OWNER')[0] for i in range(2)]
        settle = HoldingSettlementService(sessions[0], transaction_factory=self.tx, authorization=UnlockedAuthorization(sessions[0]))
        give_back = IntermediaryHoldingService(sessions[1], transaction_factory=self.tx, authorization=UnlockedAuthorization(sessions[1]))
        barrier, results = threading.Barrier(2), {}

        def sell():
            barrier.wait(timeout=10)
            return self.settle('LOGO', pool, self.supplier, '4', service=settle)

        def back():
            barrier.wait(timeout=10)
            return give_back.return_logo(str(pool), '4', DATE, '', '', str(uuid4()))
        workers = [self.thread(results, 'S', sell), self.thread(results, 'R', back)]
        for worker in workers:
            worker.join(60)
        done = [k for k, v in results.items() if isinstance(v, dict)]
        self.assertEqual(len(done), 1, results)
        self.assertEqual(self.balance(pool), D('2'))
        self.assertGreaterEqual(self.balance(pool), 0)

    def test_a_settlement_and_a_handover_reversal_racing_never_reverse_a_pool_that_was_settled(self):
        trip, pool = self.logo_pool('6')
        handover = self.query('SELECT entry_id FROM market_trip_logo_hold_links WHERE pool_id=%s AND entry_kind=%s', (pool, 'LOGO_HOLD'))[0]['entry_id']
        sessions = [self.make_session(f'settle-reverse{i}', 'OWNER')[0] for i in range(2)]
        settle = HoldingSettlementService(sessions[0], transaction_factory=self.tx, authorization=UnlockedAuthorization(sessions[0]))
        reverser = CorrectionService(sessions[1], transaction_factory=self.tx, authorization=UnlockedAuthorization(sessions[1]))
        barrier, results = threading.Barrier(2), {}

        def sell():
            barrier.wait(timeout=10)
            return self.settle('LOGO', pool, self.supplier, '2', service=settle)

        def undo():
            barrier.wait(timeout=10)
            return reverser.reverse('MARKET_TRIP_ENTRY', handover, '050 race')
        workers = [self.thread(results, 'S', sell), self.thread(results, 'V', undo)]
        for worker in workers:
            worker.join(60)
        settled, reversed_ = isinstance(results['S'], dict), isinstance(results['V'], dict)
        self.assertNotEqual(settled, reversed_, results)                                        # exactly one wins: the loser is refused by arithmetic
        self.assertEqual(self.balance(pool), D('4') if settled else D('0'))
        self.assertEqual(self.logo(trip)['outstanding'], D('14') if settled else D('20'))
        if settled:
            self.assertEqual(results['V'].code, 'DEPENDENT_HOLDING_RETURN_EXISTS')
        else:
            self.assertEqual(results['S'].code, 'HOLDING_NOT_AVAILABLE')


def load_tests(loader, tests, pattern):
    """Only the tests defined here: the class inherits the 048 / 049 helpers and tests, which their own modules run."""
    suite = unittest.TestSuite()
    suite.addTests(SettleMySqlTests(name) for name in loader.getTestCaseNames(SettleMySqlTests) if name in SettleMySqlTests.__dict__)
    return suite


if __name__ == '__main__':
    unittest.main()
