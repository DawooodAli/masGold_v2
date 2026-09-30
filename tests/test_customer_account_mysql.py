"""Explicit opt-in DEV validation. Persists tagged fixtures/history; never deletes history.

Run only with MASGOLD_CUSTOMER_ACCOUNT_DEV=1 against configured/live masgold_v2.
Opening amounts are explicit, audited test fixtures, not invented legacy history.
"""
import os
import threading
import unittest
from uuid import uuid4
from decimal import Decimal as D
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from app.session import AppSession
from config.database_config import DatabaseConfig
from database.connection import DatabaseConnection
from repositories.authentication_repository import AuthenticationRepository
from repositories.customer_repository import CustomerRepository
from repositories.customer_account_repository import CustomerAccountRepository
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.customer_account_service import CustomerAccountService
from services.correction_service import CorrectionService, CorrectionError, SettlementLeg, CS
from services.invoice_service import PaymentInput

TAG='CUSTOMER ACCOUNT V2 DEV TEST DATA'


@unittest.skipUnless(os.getenv('MASGOLD_CUSTOMER_ACCOUNT_DEV')=='1','explicit DEV account validation only')
class CustomerAccountMysqlTests(unittest.TestCase):
    @staticmethod
    @contextmanager
    def transaction():
        config=DatabaseConfig.from_env()
        if config.database!='masgold_v2':raise RuntimeError('DEV target refused')
        c=DatabaseConnection(config).connect()
        try:
            q=c.cursor();q.execute('SELECT DATABASE()');assert q.fetchone()[0]=='masgold_v2'
            q.execute('SELECT MAX(version) FROM schema_migrations');assert q.fetchone()[0]==27
            q.close();c.commit();c.start_transaction()
            yield c;c.commit()
        except BaseException:c.rollback();raise
        finally:c.close()

    def setUp(self):
        self.session=AppSession()
        with self.transaction() as c:
            q=c.cursor(dictionary=True)
            q.execute("SELECT u.id FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id "
                      "WHERE r.code='OWNER' AND u.is_active=1 AND u.must_change_password=0 ORDER BY u.id LIMIT 1")
            uid=q.fetchone()['id'];q.close()
            auth=AuthenticationRepository(c);identity=auth.get_identity(uid)
            self.session._establish(AuthenticationResult(uid,identity['employee_id'],identity['display_name'],
                auth.effective_permissions(uid),_VERIFIED),0)
            repo=CustomerRepository(c)
            self.cid=str(repo.create_customer(TAG+' '+uuid4().hex[:8],None,None,None,TAG,uid))
            # One reusable tagged fixture bank (created on the first run only; never one per test).
            q=c.cursor(dictionary=True)
            q.execute('SELECT id,name,notes FROM bank_accounts WHERE code=%s FOR UPDATE',('CADEV_BANK_FIXTURE',))
            row=q.fetchone()
            if row is None:
                q.execute('INSERT INTO bank_accounts(code,name,account_type,notes) VALUES (%s,%s,%s,%s)',
                    ('CADEV_BANK_FIXTURE',TAG,'BANK',TAG));self.bank=str(q.lastrowid)
            else:
                assert (row['name'],row['notes'])==(TAG,TAG),'fixture code reused by real data'
                self.bank=str(row['id'])
                q.execute('UPDATE bank_accounts SET is_active=TRUE WHERE id=%s AND is_active=FALSE',(row['id'],))
            q.close()
        self.account=CustomerAccountService(self.session,transaction_factory=self.transaction)
        self.correction=CorrectionService(self.session,transaction_factory=self.transaction)

    def seed(self,balance):
        with self.transaction() as c:
            CustomerRepository(c).add_transaction(customer_id=int(self.cid),type_code='MANUAL_ADJUSTMENT',
                amount_delta=D(balance),created_by_user_id=self.session.current_actor().user_id,
                reference_type='CUSTOMER_ACCOUNT_DEV_TEST',reference_id=self.cid,reference_code='DEV-CS-'+self.cid,
                description=TAG,notes='Explicit opening test fixture; not legacy or business history.')
            q=c.cursor();q.execute('INSERT INTO audit_log(user_id,event_type,entity_type,entity_id,entity_reference,description) '
                'VALUES (%s,%s,%s,%s,%s,%s)',(self.session.current_actor().user_id,'DEV_TEST_FIXTURE','CUSTOMER',int(self.cid),'DEV-CS-'+self.cid,TAG));q.close()

    def settle(self,amount,legs=None,key=None):
        return self.account.finalize(self.cid,legs or [PaymentInput('BANK_TRANSFER',amount,bank_account_id=self.bank)],key or str(uuid4()),TAG)

    def balance(self):
        with self.transaction() as c:return CustomerRepository(c).get_balance(int(self.cid))

    def test_a_partial_and_duplicate(self):
        self.seed('20000');key=str(uuid4());a=self.settle('7000',key=key)
        self.assertEqual(self.balance(),D('13000'))
        self.assertEqual(self.settle('7000',key=key),a)
        rows=self.account.statement(self.cid)['rows']
        self.assertEqual(rows[0]['source_reference'],a['code'])
        self.assertEqual(rows[0]['running_balance'],D('13000'))
        self.assertTrue(rows[0]['payment_details'])
        self.assertEqual(rows[0]['settlement_notes'],TAG)
        target=self.correction.settlement_target(a['id'])
        self.assertEqual(target['invoice_id'],'')
        self.assertIn(TAG,target['customer_name'])

    def test_b_out_negative_account_and_reversal(self):
        def bank():
            with self.transaction() as c:
                q=c.cursor();q.execute('SELECT COALESCE(SUM(amount_delta),0) FROM bank_movements WHERE bank_account_id=%s',
                    (self.bank,));value=q.fetchone()[0];q.close();return value
        before=bank()
        self.seed('-10000');cs=self.settle('4000');self.assertEqual(self.balance(),D('-6000'))
        self.assertEqual(bank(),before-D('4000'))       # the reused fixture bank moved exactly; may go negative
        self.correction.reverse(CS,cs['id'],TAG)
        self.assertEqual(self.balance(),D('-10000'))

    def test_c_split_fc_chain_and_rv(self):
        self.seed('20000');cs=self.settle('',[
            PaymentInput('PHYSICAL_CASH','8000'),PaymentInput('BANK_TRANSFER','7000',bank_account_id=self.bank),
            PaymentInput('INSTAPAY','5000',bank_account_id=self.bank)])
        self.assertEqual(self.balance(),0)
        self.correction.correct_settlement(CS,cs['id'],[SettlementLeg('BANK_TRANSFER','20000',self.bank)],None,TAG,expected_sequence=1)
        self.correction.correct_settlement(CS,cs['id'],[SettlementLeg('PHYSICAL_CASH','20000')],None,TAG,expected_sequence=2)
        self.assertEqual(self.balance(),0)
        self.correction.reverse(CS,cs['id'],TAG)
        self.assertEqual(self.balance(),D('20000'))
        rows=self.account.statement(self.cid)['rows']
        self.assertEqual(sum(r['row_kind']=='EVENT' for r in rows),2)
        self.assertEqual(sum(r['source_type']=='FULL_REVERSAL' for r in rows),1)
        for call in (lambda:self.correction.reverse(CS,cs['id'],TAG),lambda:self.correction.correct_settlement(CS,cs['id'],[SettlementLeg('BANK_TRANSFER','20000',self.bank)],None,TAG)):
            with self.assertRaises(CorrectionError) as e:call()
            self.assertEqual(e.exception.code,'ALREADY_REVERSED')

    def test_d_excess_no_partial_state(self):
        self.seed('10000')
        with self.assertRaises(CorrectionError) as e:self.settle('10001')
        self.assertEqual(e.exception.code,'SETTLEMENT_EXCEEDS_BALANCE')
        self.assertEqual(self.balance(),D('10000'))
        with self.transaction() as c:
            q=c.cursor();q.execute('SELECT COUNT(*) FROM customer_account_settlements WHERE customer_id=%s',(self.cid,))
            self.assertEqual(q.fetchone()[0],0);q.close()

    def concurrently(self,call):
        barrier=threading.Barrier(2)
        def work():
            barrier.wait()
            try:return call()
            except CorrectionError as e:return e.code
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(lambda _:work(),range(2)))

    def test_e_concurrent_settlement(self):
        self.seed('10000');results=self.concurrently(lambda:self.settle('7000'))
        self.assertEqual(sum(isinstance(x,dict) for x in results),1)
        self.assertIn('SETTLEMENT_EXCEEDS_BALANCE',results);self.assertEqual(self.balance(),D('3000'))

    def test_concurrent_fc_and_duplicate_rv(self):
        self.seed('10000');cs=self.settle('10000')
        results=self.concurrently(lambda:self.correction.correct_settlement(CS,cs['id'],[SettlementLeg('PHYSICAL_CASH','10000')],None,TAG,expected_sequence=1))
        self.assertEqual(sum(isinstance(x,dict) for x in results),1);self.assertIn('STALE_CORRECTION',results)
        results=self.concurrently(lambda:self.correction.reverse(CS,cs['id'],TAG))
        self.assertEqual(sum(isinstance(x,dict) for x in results),1);self.assertIn('ALREADY_REVERSED',results)
        self.assertEqual(self.balance(),D('10000'))

    def test_f_sources_filters_running_balance(self):
        self.seed('20000');cs=self.settle('5000')
        self.correction.correct_settlement(CS,cs['id'],[SettlementLeg('PHYSICAL_CASH','5000')],None,TAG)
        self.correction.reverse(CS,cs['id'],TAG)
        statement=self.account.statement(self.cid)
        self.assertEqual(statement['customer']['balance'],D('20000'))
        self.assertEqual(sum(r['amount_delta'] for r in statement['rows']),D('20000'))
        filtered=self.account.statement(self.cid,source=CS,search=cs['code'])
        self.assertEqual(len(filtered['rows']),1)
        self.assertEqual(filtered['rows'][0]['running_balance'],D('15000'))
        # Existing DEV invoice/Exchange sources are joined by exact identity, never name.
        with self.transaction() as c:
            q=c.cursor();q.execute("SELECT DISTINCT customer_id FROM customer_transactions WHERE reference_type IN ('INVOICE','INVOICE_LIFECYCLE')")
            ids=[r[0] for r in q.fetchall()];q.close()
            for cid in ids:
                for row in CustomerAccountRepository(c).statement(cid):
                    if row['reference_type'] in ('INVOICE','INVOICE_LIFECYCLE'):
                        self.assertIsNotNone(row['invoice_id']);self.assertTrue(row['invoice_number'])
        # Cross a real 50-row page boundary; filtering must not reset balances.
        for _ in range(55):self.seed('1')
        first=self.account.statement(self.cid)
        second=self.account.statement(self.cid,page=2)
        self.assertEqual(len(first['rows']),50);self.assertTrue(first['has_more'])
        self.assertEqual(len(second['rows']),9);self.assertFalse(second['has_more'])
        combined=first['rows']+second['rows']
        self.assertEqual(len({(r['row_kind'],str(r['id'])) for r in combined}),59)
        balance=D('0')
        for row in reversed(combined):
            balance+=row['amount_delta']
            self.assertEqual(row['running_balance'],balance)
        self.assertEqual(balance,first['customer']['balance'])
        day=first['rows'][0]['occurred_at'].date().isoformat()
        same_day=self.account.statement(self.cid,start=day,end=day)
        self.assertEqual(same_day['rows'],first['rows'])
        self.assertEqual(self.account.statement(self.cid,start='9999-01-01')['rows'],[])

    def test_real_invoice_cs_exchange_return_fc_rv_continuity(self):
        from dataclasses import replace
        from services.inventory_service import InventoryService
        from services.invoice_service import InvoiceService, InvoiceDraft, SaleLineInput
        from services.exchange_service import ExchangeService, ExchangeDraft, ReplacementInput
        from services.return_service import ReturnService, ReturnDraft, ReturnSelection
        from repositories.invoice_repository import InvoiceRepository
        with self.transaction() as c:
            q=c.cursor()
            q.execute('SELECT id FROM employees WHERE is_active=1 ORDER BY id LIMIT 1');employee=q.fetchone()[0]
            q.execute('SELECT id FROM item_types WHERE is_active=1 ORDER BY id LIMIT 1');item_type=q.fetchone()[0]
            q.execute('SELECT id FROM karats WHERE karat_value=21 AND is_active=1');karat=q.fetchone()[0];q.close()
        items=[str(InventoryService().create_inventory_item(employee,item_type,karat,None,None,weight,None,
            'NEW_STOCK',None,TAG,self.session.current_actor().user_id)) for weight in ('1.000','1.000','1.200')]
        billing=InvoiceService(self.session,transaction_factory=self.transaction)
        draft=InvoiceDraft(employee,customer_id=self.cid,sale_lines=tuple(SaleLineInput(i) for i in items[:2]),
            invoice_total='20000',account_amount='20000',account_direction='CUSTOMER_OWES',notes=TAG)
        preview=billing.preview(draft)
        self.assertEqual(preview['blocking_problems'],[])
        invoice=billing.finalize(draft,preview['price_token'],stale_acknowledged=True)
        invoice_id=invoice['invoice_id']
        def continuity():
            result=self.account.statement(self.cid)
            self.assertEqual(result['customer']['balance'],self.balance())
            self.assertEqual(sum(r['amount_delta'] for r in result['rows']),self.balance())
            return result
        self.assertEqual(self.balance(),D('20000'));continuity()
        cs=self.settle('5000',[PaymentInput('PHYSICAL_CASH','5000')]);continuity()
        with self.transaction() as c:lines=InvoiceRepository(c).get_invoice(invoice_id)['lines']
        sold=[l for l in lines if l['line_type']=='SELL_INVENTORY']
        exchange=ExchangeService(self.session,transaction_factory=self.transaction)
        ex=ExchangeDraft(invoice_id,returned_line_ids=(sold[0]['line_id'],),
            replacements=(ReplacementInput(items[2],'100'),),notes=TAG)
        xp=exchange.preview(ex)
        ex=replace(ex,account_amount=str(abs(D(xp['signed_amount']))))
        xp=exchange.preview(ex);self.assertEqual(xp['blocking_problems'],[])
        exchange.finalize(ex,xp['price_token'],stale_acknowledged=True);continuity()
        returns=ReturnService(self.session,transaction_factory=self.transaction)
        rd=ReturnDraft(invoice_id,lines=(ReturnSelection(sold[1]['line_id']),),notes=TAG)
        rp=returns.preview(rd)
        rd=replace(rd,account_amount=rp['total_amount'])
        rp=returns.preview(rd);self.assertEqual(rp['blocking_problems'],[])
        ret=returns.finalize(rd,rp['price_token'],stale_acknowledged=True);continuity()
        self.correction.correct_settlement(CS,cs['id'],[SettlementLeg('BANK_TRANSFER','5000',self.bank)],None,TAG)
        continuity()
        self.correction.reverse('RETURN',ret['return_id'],TAG)
        result=continuity()
        self.assertEqual({r['source_type'] for r in result['rows']},
            {'INVOICE','EXCHANGE','RETURN',CS,'SETTLEMENT_CORRECTION','FULL_REVERSAL'})
        for row in result['rows']:
            if row['source_type'] in ('INVOICE','EXCHANGE','RETURN','FULL_REVERSAL'):
                self.assertEqual(str(row['invoice_id']),invoice_id)
