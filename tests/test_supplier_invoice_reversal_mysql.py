"""Explicit opt-in rehearsal checks for the full reversal of a posted supplier purchase invoice (migration 052). They connect to masgold_rehearsal ONLY
(target, migration level 52 and marker are asserted) and never to the DEV database. Real services, real repositories, real SQL, separate REPEATABLE READ
connections. Fixtures are committed and never cleaned (immutable ledgers); every test builds its own supplier and invoice.

Evidence classes, kept apart on purpose: functional checks (real services over MySQL); resource-order / contention checks that remove the production
authorization locks (raw holders and UnlockedAuthorization: production authorization X-locks the actor and the shared role / permission rows until commit and
serializes every protected writer first). None of this is a stress test and none of it proves general concurrency safety or deadlock freedom."""
from dataclasses import replace
from decimal import Decimal as D
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from config.database_config import DatabaseConfig
from repositories.correction_repository import CorrectionRepository
from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository
from repositories.supplier_payment_repository import SupplierPaymentRepository
from repositories.supplier_purchase_repository import SupplierPurchaseRepository
from services.correction_service import CorrectionError, CorrectionService
from services.supplier_purchase_service import SupplierPurchaseService
from services.supplier_rules import SupplierRuleError
from services.supplier_service import SupplierService
from tests.test_holding_settlement_mysql import Denying, Recording
from tests.test_intermediary_holdings_mysql import HoldingsMySqlTests, TARGET
from tests.test_market_trip_hardening_mysql import UnlockedAuthorization

SOURCE = "SUPPLIER_PURCHASE_INVOICE"
REASON = "052 وزن مدخل بالخطأ"
GROUPS = [{"weight": "30", "making_per_gram": "100"}, {"weight": "20", "making_per_gram": "120"}]      # 50.000 g, making 5,400.00


class SupplierInvoiceReversalMySqlTests(HoldingsMySqlTests):
    @classmethod
    def setUpClass(cls):
        cls.config = replace(DatabaseConfig.from_env(), database=TARGET)
        with cls.tx() as c:
            q = c.cursor(dictionary=True)
            q.execute('SELECT DATABASE() AS db, MAX(version) AS v FROM schema_migrations')
            row = q.fetchone()
            assert row['db'] == TARGET and int(row['v']) == 52, row
            q.execute("SELECT target_database FROM masgold_rehearsal_marker WHERE marker_key='MASGOLD_SCHEMA_REHEARSAL'")
            assert q.fetchone()['target_database'] == TARGET
            q.execute('SELECT id,karat_value FROM karats')
            cls.karats = {r['karat_value']: r['id'] for r in q.fetchall()}
            q.close()
        cls.session, cls.user = cls.make_session('owner-052', 'OWNER')
        cls.suppliers = SupplierService(cls.session, transaction_factory=cls.tx)

    def setUp(self):
        self.correct = CorrectionService(self.session, transaction_factory=self.tx)
        self.purchases = SupplierPurchaseService(self.session, transaction_factory=self.tx, corrections=self.correct)

    # ------------------------------------------------------------------ helpers
    def posted(self, karat=21, groups=None):
        supplier = self.suppliers.create_supplier('052 مورد ' + uuid4().hex[:10], karat_id=self.karats[karat])
        draft = self.purchases.create_draft(supplier, self.karats[karat], 'REF-' + uuid4().hex[:6], '2026-10-09', groups or GROUPS, '', str(uuid4()))
        done = self.purchases.post_invoice(draft['id'], str(uuid4()))
        return supplier, int(done['id'])

    def stored(self, invoice):
        gold = self.query("SELECT m.* FROM supplier_gold_movements m JOIN supplier_gold_movement_types t ON t.id=m.movement_type_id "
                          "WHERE m.source_type=%s AND m.source_id=%s AND t.code='PURCHASE_INVOICE'", (SOURCE, invoice))
        making = self.query("SELECT m.* FROM supplier_making_movements m JOIN supplier_making_movement_types t ON t.id=m.movement_type_id "
                            "WHERE m.source_type=%s AND m.source_id=%s AND t.code='PURCHASE_INVOICE'", (SOURCE, invoice))
        return gold, making

    def invoice_row(self, invoice):
        return self.query("SELECT id,supplier_id,karat_id,status,supplier_weight_grams,gross_making_total,post_request_key,posted_at,posted_by_user_id "
                          "FROM supplier_purchase_invoices WHERE id=%s", (invoice,))

    def balances(self, supplier):
        gold = self.query("SELECT COALESCE(SUM(weight_delta_grams),0) AS g FROM supplier_gold_movements WHERE supplier_id=%s", (int(supplier),))[0]['g']
        making = self.query("SELECT COALESCE(SUM(amount_delta),0) AS g FROM supplier_making_movements WHERE supplier_id=%s", (int(supplier),))[0]['g']
        return gold, making

    def reversal_counts(self):
        tables = ('transaction_corrections', 'supplier_gold_movements', 'supplier_making_movements', 'audit_log', 'supplier_invoice_pieces')
        return {t: self.query(f'SELECT COUNT(*) AS n FROM {t}')[0]['n'] for t in tables}

    def foreign_counts(self):
        """What a reversal may NOT touch."""
        tables = ('inventory_items', 'old_gold_movements', 'cash_movements', 'bank_movements', 'supplier_making_payments', 'supplier_gold_settlements',
                  'supplier_purchase_invoices', 'supplier_invoice_groups', 'market_trip_entries')
        return {t: self.query(f'SELECT COUNT(*) AS n FROM {t}')[0]['n'] for t in tables}

    def service_for(self, session, authorization=None):
        correction = CorrectionService(session, transaction_factory=self.tx, authorization=authorization)
        return SupplierPurchaseService(session, transaction_factory=self.tx, corrections=correction)

    def intake(self, invoice, service=None, weight='5'):
        """One real intaken piece; the test is SKIPPED (never silently green) when the rehearsal master data cannot take an intake."""
        item_type = self.query("SELECT id FROM item_types WHERE is_active=1 ORDER BY id LIMIT 1")
        if not item_type:
            self.skipTest('the rehearsal database has no active item type for a supplier intake')
        group = self.query("SELECT id FROM supplier_invoice_groups WHERE invoice_id=%s ORDER BY group_no LIMIT 1", (invoice,))[0]['id']
        try:
            return (service or self.purchases).add_piece(invoice, str(group), int(item_type[0]['id']), weight, str(uuid4()), barcode_mode='NONE')
        except SupplierRuleError as error:
            self.skipTest(f'the rehearsal master data cannot take a supplier intake: {error.code}')

    # ------------------------------------------------------------------ functional
    def test_exact_opposite_entries_derived_state_labels_and_untouched_history(self):
        supplier, invoice = self.posted()
        gold, making = self.stored(invoice)
        header = self.invoice_row(invoice)
        before_foreign = self.foreign_counts()
        self.assertEqual((len(gold), len(making), gold[0]['weight_delta_grams'], making[0]['amount_delta']), (1, 1, D('50.000'), D('5400.00')))
        done = self.purchases.reverse_invoice(invoice, REASON)
        correction = self.query("SELECT * FROM transaction_corrections WHERE id=%s", (done['correction_id'],))[0]
        self.assertEqual((correction['source_type'], int(correction['source_id']), correction['correction_type'], correction['invoice_id'], correction['reason']),
                         (SOURCE, invoice, 'FULL_REVERSAL', None, REASON))
        undo_gold = self.query("SELECT m.*, t.code FROM supplier_gold_movements m JOIN supplier_gold_movement_types t ON t.id=m.movement_type_id "
                               "WHERE m.source_type='TRANSACTION_CORRECTION' AND m.source_id=%s", (done['correction_id'],))
        undo_making = self.query("SELECT m.*, t.code FROM supplier_making_movements m JOIN supplier_making_movement_types t ON t.id=m.movement_type_id "
                                 "WHERE m.source_type='TRANSACTION_CORRECTION' AND m.source_id=%s", (done['correction_id'],))
        self.assertEqual([(m['code'], m['weight_delta_grams'], m['equivalent_21_delta_grams'], m['karat_id'], m['supplier_id']) for m in undo_gold],
                         [('PURCHASE_INVOICE_REVERSAL', -gold[0]['weight_delta_grams'], -gold[0]['equivalent_21_delta_grams'], gold[0]['karat_id'],
                           gold[0]['supplier_id'])])
        self.assertEqual([(m['code'], m['amount_delta'], m['supplier_id']) for m in undo_making],
                         [('PURCHASE_INVOICE_REVERSAL', -making[0]['amount_delta'], making[0]['supplier_id'])])
        self.assertEqual(self.stored(invoice), (gold, making))                                    # the original movements are byte-for-byte unchanged
        self.assertEqual(self.invoice_row(invoice), header)                                       # the invoice stays POSTED and immutable
        self.assertEqual(self.balances(supplier), (D('0.000'), D('0.00')))
        self.assertEqual(self.foreign_counts(), before_foreign)                                   # no inventory, Scrap, cash, bank, payment or other effect
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM audit_log WHERE event_type='TRANSACTION_REVERSED' AND entity_id=%s", (done['correction_id'],))[0]['n'], 1)
        detail = self.purchases.invoice_detail(invoice)
        self.assertEqual((detail['status'], detail['reversed'], detail['reversal_code']), ('POSTED', True, done['code']))
        (row,) = [r for r in self.purchases.list_invoices(supplier) if int(r['id']) == invoice]
        self.assertEqual((row['reversed'], row['reversal_code']), (True, done['code']))
        ledger = self.suppliers.supplier_detail(supplier)
        self.assertEqual({r['movement'] for r in ledger['gold_ledger']}, {'فاتورة شراء', 'عكس فاتورة شراء'})
        self.assertEqual({r['invoice_reversed_by'] for r in ledger['gold_ledger']} - {None}, {done['code']})
        self.assertEqual({r['reverses_invoice'] for r in ledger['making_ledger']} - {None}, {f'SP{invoice:06d}'})

    def test_the_new_objects_exist_and_the_right_belongs_to_the_owner_alone(self):
        owners = {r['code'] for r in self.query("SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id JOIN roles r ON r.id=rp.role_id "
                                                "WHERE r.code='OWNER'")}
        managers = {r['code'] for r in self.query("SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id JOIN roles r ON r.id=rp.role_id "
                                                  "WHERE r.code='MANAGER'")}
        self.assertIn('SUPPLIER_INVOICE_REVERSE', owners)
        self.assertNotIn('SUPPLIER_INVOICE_REVERSE', managers)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM supplier_gold_movement_types WHERE code='PURCHASE_INVOICE_REVERSAL'")[0]['n'], 1)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM supplier_making_movement_types WHERE code='PURCHASE_INVOICE_REVERSAL'")[0]['n'], 1)
        clause = self.query("SELECT cc.CHECK_CLAUSE AS c FROM information_schema.CHECK_CONSTRAINTS cc JOIN information_schema.TABLE_CONSTRAINTS tc "
                            "ON tc.CONSTRAINT_SCHEMA=cc.CONSTRAINT_SCHEMA AND tc.CONSTRAINT_NAME=cc.CONSTRAINT_NAME "
                            "WHERE tc.TABLE_SCHEMA=DATABASE() AND tc.TABLE_NAME='transaction_corrections' AND tc.CONSTRAINT_NAME='chk_correction_source'")
        self.assertIn(SOURCE, clause[0]['c'])

    def test_a_second_reversal_and_a_reason_less_or_foreign_request_write_nothing(self):
        supplier, invoice = self.posted()
        with self.assertRaises(CorrectionError) as blank:
            self.purchases.reverse_invoice(invoice, '  ')
        self.assertEqual(blank.exception.code, 'REASON_REQUIRED')
        self.purchases.reverse_invoice(invoice, REASON)
        before = self.reversal_counts()
        with self.assertRaises(CorrectionError) as again:
            self.purchases.reverse_invoice(invoice, REASON)
        self.assertEqual(again.exception.code, 'ALREADY_REVERSED')
        self.assertEqual(self.reversal_counts(), before)
        draft = self.purchases.create_draft(supplier, self.karats[21], 'DRAFT-' + uuid4().hex[:6], '2026-10-09', GROUPS, '', str(uuid4()))
        with self.assertRaises(CorrectionError) as not_posted:
            self.purchases.reverse_invoice(draft['id'], REASON)
        self.assertEqual(not_posted.exception.code, 'INVOICE_NOT_POSTED')
        self.assertEqual(self.reversal_counts(), before)

    def test_each_right_is_checked_in_the_transaction_and_the_actor_is_the_session_user(self):
        supplier, invoice = self.posted()
        before = self.reversal_counts()
        manager, _ = self.make_session('manager-052', 'MANAGER')
        with self.assertRaises(PermissionError):
            self.service_for(manager).reverse_invoice(invoice, REASON)
        for denied in ('TRANSACTION_CORRECT', 'SUPPLIER_INVOICE_REVERSE'):
            with self.subTest(denied=denied), self.assertRaises(PermissionError):
                self.service_for(self.session, Denying(self.session, denied)).reverse_invoice(invoice, REASON)
        self.assertEqual(self.reversal_counts(), before)
        recording = Recording(self.session)
        done = self.service_for(self.session, recording).reverse_invoice(invoice, REASON)
        self.assertEqual(set(recording.codes), {'TRANSACTION_CORRECT', 'SUPPLIER_INVOICE_REVERSE'})
        self.assertEqual(int(self.query("SELECT performed_by_user_id AS u FROM transaction_corrections WHERE id=%s", (done['correction_id'],))[0]['u']),
                         int(self.user))

    def test_a_piece_taken_into_inventory_refuses_the_reversal_and_a_reversed_invoice_refuses_intake(self):
        supplier, invoice = self.posted()
        self.intake(invoice)
        before, foreign = self.reversal_counts(), self.foreign_counts()
        with self.assertRaises(CorrectionError) as intaken:
            self.purchases.reverse_invoice(invoice, REASON)
        self.assertEqual(intaken.exception.code, 'INVOICE_HAS_INTAKE')
        self.assertEqual((self.reversal_counts(), self.foreign_counts()), (before, foreign))
        other, second = self.posted()
        self.purchases.reverse_invoice(second, REASON)
        group = self.query("SELECT id FROM supplier_invoice_groups WHERE invoice_id=%s ORDER BY group_no LIMIT 1", (second,))[0]['id']
        with self.assertRaises(SupplierRuleError) as reversed_invoice:
            self.purchases.add_piece(second, str(group), 1, '5', str(uuid4()), barcode_mode='NONE')
        self.assertEqual(reversed_invoice.exception.code, 'INVOICE_REVERSED')

    # ------------------------------------------------------------------ rollback at every persistent stage
    def test_rollback_after_each_persistent_stage_of_the_reversal(self):
        supplier, invoice = self.posted()

        def inject_after(cls, name):
            original = getattr(cls, name)

            def fail(instance, *args, **kwargs):
                original(instance, *args, **kwargs)
                raise RuntimeError('052 reversal injected')
            return patch.object(cls, name, fail)

        stages = ((CorrectionRepository, 'insert_correction'), (SupplierGoldSettlementRepository, 'insert_gold_movement'),
                  (SupplierPaymentRepository, 'insert_making_movement'))
        for cls, name in stages:
            before = self.reversal_counts()
            with self.subTest(stage=name), inject_after(cls, name), self.assertRaisesRegex(RuntimeError, '052 reversal injected'):
                self.purchases.reverse_invoice(invoice, REASON)
            self.assertEqual(self.reversal_counts(), before, name)
        original_audit = CorrectionService._audit

        def fail_after_audit(*args, **kwargs):
            original_audit(*args, **kwargs)
            raise RuntimeError('052 reversal audit injected')
        before = self.reversal_counts()
        with patch.object(CorrectionService, '_audit', staticmethod(fail_after_audit)):
            with self.assertRaisesRegex(RuntimeError, '052 reversal audit injected'):
                self.purchases.reverse_invoice(invoice, REASON)
        self.assertEqual(self.reversal_counts(), before, 'audit')
        self.assertEqual(self.balances(supplier), (D('50.000'), D('5400.00')))
        self.purchases.reverse_invoice(invoice, REASON)                                           # the untouched state is still reversible
        self.assertEqual(self.balances(supplier), (D('0.000'), D('0.00')))

    # ------------------------------------------------------------------ lock order and contention (no production authorization locks)
    def test_real_acquisition_order_is_the_invoice_then_the_supplier_then_the_pieces(self):
        supplier, invoice = self.posted()
        order = []

        def wrap(cls, name, label, locking_only=True):
            original = getattr(cls, name)

            def wrapper(instance, *args, **kwargs):
                if not locking_only or kwargs.get('lock'):
                    order.append(label)
                return original(instance, *args, **kwargs)
            return patch.object(cls, name, wrapper)

        patches = (wrap(SupplierPurchaseRepository, 'get_invoice', 'invoice'), wrap(SupplierPurchaseRepository, 'get_supplier', 'supplier'),
                   wrap(SupplierPurchaseRepository, 'intaken_pieces', 'pieces'))
        for item in patches:
            item.start()
        try:
            self.purchases.reverse_invoice(invoice, REASON)
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(order, ['invoice', 'supplier', 'pieces'])

    def test_a_reversal_waiting_for_the_supplier_already_holds_the_invoice(self):
        supplier, invoice = self.posted()
        results = {}
        service = self.service_for(self.session, UnlockedAuthorization(self.session))
        with self.tx() as holder:                                                                  # raw holder of the supplier row
            cursor = holder.cursor()
            cursor.execute('SELECT id FROM suppliers WHERE id=%s FOR UPDATE', (int(supplier),))
            cursor.fetchall()
            cursor.close()
            worker = self.thread(results, 'reversal', lambda: service.reverse_invoice(invoice, REASON))
            try:
                self.wait_waiting('suppliers')
                with self.tx() as observer:
                    invoice_lock = self.probe(observer, 'SELECT id FROM supplier_purchase_invoices WHERE id=%s FOR UPDATE NOWAIT', (invoice,))
            finally:
                holder.commit()
                worker.join(60)
        self.assertEqual(invoice_lock, 3572)                                                        # the invoice was already locked: invoice -> supplier
        self.assertIsInstance(results['reversal'], dict, results['reversal'])

    def test_two_reversals_racing_on_one_invoice_reverse_it_once(self):
        supplier, invoice = self.posted()
        sessions = [self.make_session(f'invoice-undo-{i}', 'OWNER')[0] for i in range(2)]
        services = [self.service_for(s, UnlockedAuthorization(s)) for s in sessions]
        barrier, results = threading.Barrier(2), {}

        def race(index):
            def call():
                barrier.wait(timeout=10)
                return services[index].reverse_invoice(invoice, REASON)
            return call

        workers = [self.thread(results, 'A', race(0)), self.thread(results, 'B', race(1))]
        for worker in workers:
            worker.join(60)
        outcomes = list(results.values())
        self.assertEqual(sum(isinstance(r, dict) for r in outcomes), 1, outcomes)
        self.assertEqual(sum(isinstance(r, CorrectionError) and r.code == 'ALREADY_REVERSED' for r in outcomes), 1, outcomes)
        self.assertEqual(self.balances(supplier), (D('0.000'), D('0.00')))
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM transaction_corrections WHERE source_type=%s AND source_id=%s", (SOURCE, invoice))[0]['n'], 1)

    def test_an_intake_and_a_reversal_racing_never_both_succeed(self):
        supplier, invoice = self.posted()
        self.intake(invoice)                                                                       # skips when the rehearsal data cannot take an intake
        other, second = self.posted()
        sessions = [self.make_session(f'invoice-race-{i}', 'OWNER')[0] for i in range(2)]
        taker, undoer = self.service_for(sessions[0], UnlockedAuthorization(sessions[0])), self.service_for(sessions[1], UnlockedAuthorization(sessions[1]))
        barrier, results = threading.Barrier(2), {}

        def take():
            barrier.wait(timeout=10)
            return self.intake(second, service=taker)

        def undo():
            barrier.wait(timeout=10)
            return undoer.reverse_invoice(second, REASON)

        workers = [self.thread(results, 'intake', take), self.thread(results, 'undo', undo)]
        for worker in workers:
            worker.join(60)
        pieces = self.query("SELECT COUNT(*) AS n FROM supplier_invoice_pieces WHERE invoice_id=%s", (second,))[0]['n']
        reversed_ = self.query("SELECT COUNT(*) AS n FROM transaction_corrections WHERE source_type=%s AND source_id=%s", (SOURCE, second))[0]['n']
        self.assertFalse(pieces > 0 and reversed_ > 0, (results, pieces, reversed_))               # never an intaken piece on a reversed invoice
        self.assertEqual(sum(isinstance(r, dict) for r in results.values()), 1, results)


def load_tests(loader, tests, pattern):
    """Only the tests defined here: the class inherits the 048 helpers and tests, which their own module runs."""
    suite = unittest.TestSuite()
    suite.addTests(SupplierInvoiceReversalMySqlTests(name) for name in loader.getTestCaseNames(SupplierInvoiceReversalMySqlTests)
                   if name in SupplierInvoiceReversalMySqlTests.__dict__)
    return suite


if __name__ == '__main__':
    unittest.main()
