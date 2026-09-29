"""Customer invoice scope reuses History independently of account allocation."""
import unittest
from datetime import date, datetime
from tests import test_invoice_history as history
from services.customer_account_service import CustomerAccountService
from repositories.invoice_repository import InvoiceRepository


class CustomerInvoicesTests(unittest.TestCase):
    def test_query_is_customer_scoped_bounded_and_independent_of_ledger(self):
        c=history.HistoryListQueryTests.Connection()
        InvoiceRepository(c).search_invoice_history(customer_id='9007199254740993',text='2609',
            date_from=date(2026,9,1),date_to_exclusive=date(2026,10,1),limit=51,offset=50)
        self.assertEqual(len(c.log),1)
        sql,params=c.log[0]
        self.assertIn('i.customer_id = %s',sql)
        self.assertEqual(params[:2],(9007199254740993,9007199254740993))
        self.assertIn("i.invoice_status <> 'DRAFT'",sql)
        self.assertIn('ORDER BY i.invoice_date DESC, i.id DESC',sql)
        self.assertEqual(params[-2:],(51,50))
        self.assertNotIn('customer_transactions',sql)
        self.assertNotIn('invoice_returns',sql)
        self.assertNotIn('invoice_lifecycle_transactions',sql)

    def test_service_includes_paid_and_account_invoices_with_history_classification(self):
        paid=history.summary_row('26093001',datetime(2026,9,30),invoice_id='21')
        allocated=history.summary_row('26093002',datetime(2026,9,30),invoice_id='22')
        paid.update(settlement_status='SETTLED',money_received=paid['net_amount'])
        service,state,_=history.make(rows=[paid,allocated])
        account=CustomerAccountService(service._session,transaction_factory=service._transaction,
                                       invoices=history.FakeRepo)
        result=account.customer_invoices('12',start='2026-09-01',end='2026-09-30',search='2609')
        self.assertEqual([r['invoice_id'] for r in result['items']],['21','22'])
        self.assertEqual(result['items'][0]['settlement_status'],'SETTLED')
        args=state['calls'][0][1]
        self.assertEqual(args['customer_id'],'12')
        self.assertEqual(args['date_to_exclusive'],date(2026,10,1))
        self.assertEqual((args['limit'],args['offset']),(51,0))
        self.assertEqual([call[0] for call in state['calls']],['search'])

    def test_customer_scope_cannot_fall_back_to_all_history(self):
        service,state,_=history.make()
        account=CustomerAccountService(service._session,transaction_factory=service._transaction,
                                       invoices=history.FakeRepo)
        for cid in ('',None,'name',0):
            with self.subTest(cid=cid),self.assertRaises(ValueError):account.customer_invoices(cid)
        self.assertEqual(state['calls'],[])

    def test_pagination_and_invoice_view_permission(self):
        rows=[history.summary_row(str(i),datetime(2026,9,30),invoice_id=str(i)) for i in range(52)]
        service,state,_=history.make(rows=rows)
        account=CustomerAccountService(service._session,transaction_factory=service._transaction,
                                       invoices=history.FakeRepo)
        first=account.customer_invoices('12');second=account.customer_invoices('12',page=2)
        self.assertEqual((len(first['items']),first['has_more']),(50,True))
        self.assertEqual((len(second['items']),second['has_more']),(2,False))
        self.assertEqual(state['calls'][-1][1]['customer_id'],'12')
        service,_,_=history.make(permissions=frozenset({'APP_LOGIN','CUSTOMERS_VIEW'}))
        account=CustomerAccountService(service._session,transaction_factory=service._transaction,invoices=history.FakeRepo)
        with self.assertRaises(PermissionError):account.customer_invoices('12')
