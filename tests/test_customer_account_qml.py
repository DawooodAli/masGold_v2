"""Real clicks in Main.qml reuse its existing Invoice History detail, with DB blocked."""
import copy
from datetime import datetime
from decimal import Decimal as D
from unittest.mock import Mock
from PySide6.QtCore import QObject
from PySide6.QtTest import QTest
from tests import test_invoice_page_qml as harness


class CustomerAccountPageTests(harness.InvoicePageTests):
    def test_customer_invoices_and_event_refresh_preserve_customer_tab_and_filters(self):
        import threading
        vm=self.controller.operational_viewmodels['customerAccountVM']
        customer_vm=self.controller.operational_viewmodels['customerVM']
        state={'balance':D('200'),'statement_calls':0,'invoice_calls':[], 'block':False}
        entered=threading.Event();release=threading.Event()
        self.addCleanup(release.set)
        def customer():
            return {'customer_id':'12','name':'عميل','mobile':'01000000000','balance':state['balance'],
                    'balance_status':'CUSTOMER_OWES_SHOP','is_active':True}
        def profile(cid):
            return {'customer':dict(customer(),customer_id=str(cid)),'active_flags':[],
                'transactions_page':{'items':[],'page':1,'has_more':False},
                'dead_cash_page':{'items':[],'page':1,'has_more':False},
                'statement_summary':{},'statement_loaded_at':datetime(2026,9,30),'ledger_token':None}
        customers=Mock(get_customer_types=Mock(return_value=[]),get_customer_transaction_types=Mock(return_value=[]),
            search_customers=Mock(side_effect=lambda **kw:{'items':[customer()],'page':1,'has_more':False}),
            get_customer_profile=Mock(side_effect=profile))
        customer_vm._service=customers
        class Service:
            def statement(self,*args,**kw):
                state['statement_calls']+=1
                snapshot=customer()
                if state['block']:
                    state['block']=False;entered.set();release.wait(3)
                return {'customer':snapshot,'rows':[],'has_more':False,'page':1,'accounts':[],'can_settle':True}
            def customer_invoices(self,*args,**kw):
                state['invoice_calls'].append(args)
                return {'items':[dict(invoice_id=str(i),invoice_number='2609300'+str(i-20),
                    finalized_at=datetime(2026,9,30,12),transaction_kind='SALE',invoice_total=D('100'),
                    invoice_status='FINALIZED',settlement_status='SETTLED' if i==21 else 'PARTIAL')
                    for i in (21,22)],'page':args[-1],'has_more':args[-1]==1}
        vm.service=Service()
        self.root.setProperty('currentPage',3)
        self.assertGreater(customers.search_customers.call_count,0)
        self.assertTrue(customer_vm.selectCustomer('12'));vm.open('12')
        self.wait_until(lambda:not vm.busy)
        self.click('customerInvoicesTab');self.wait_until(lambda:not vm.busy and len(vm.invoiceRows)==2)
        self.assertEqual(vm.rows,[])  # Invoice list never manufactures ledger rows.
        self.assertEqual(len(self.root.findChildren(QObject,'invoiceHistoryPage')),1)
        vm.filter('2026-09-01','2026-09-30','SELL','2609')
        self.wait_until(lambda:not vm.busy);vm.page(1);self.wait_until(lambda:not vm.busy)
        filters=dict(vm.filters)
        for size in ((1440,900),(1280,800),(1080,680)):
            self.root.setWidth(size[0]);self.root.setHeight(size[1]);QTest.qWait(120)
            for iid in ('21','22'):
                button=self.visual_item('customerInvoice_'+iid)
                self.assert_inside_window(button)
                self.click(button)
                history=self.controller.operational_viewmodels['invoiceHistoryVM']
                self.wait_until(lambda:('detail',iid) in self.history.calls and not history.detailBusy)
                history.closeDetail()
                count=len(state['invoice_calls']);loads=customers.search_customers.call_count
                self.root.setProperty('currentPage',3)
                self.wait_until(lambda:len(state['invoice_calls'])>count and not vm.busy)
                self.assertGreater(customers.search_customers.call_count,loads)
                self.assertEqual(customer_vm.selectedCustomerId,'12')
                self.assertEqual((vm.activeTab,vm.pageNumber,vm.filters),(1,2,filters))
                QTest.qWait(60)
        for key in ('invoiceVM','exchangeVM','returnVM','correctionVM'):
            state['balance']+=D('1')
            count=state['statement_calls']
            self.controller.operational_viewmodels[key].finalized.emit('21')
            self.wait_until(lambda:state['statement_calls']>count and not vm.busy)
            self.assertIn(str(int(state['balance'])),vm.balance)
            self.assertEqual(D(customer_vm.selectedCustomer['balance']),state['balance'])
            self.assertEqual(customer_vm.selectedCustomerId,'12')
        # A finalized event while a read is active must queue a fresh read.
        state['block']=True;vm.refresh();self.wait_until(entered.is_set)
        state['balance']=D('987');vm.refresh();release.set()
        self.wait_until(lambda:not vm.busy and '987' in vm.balance)
        self.assertEqual((vm.activeTab,vm.pageNumber,vm.filters),(1,2,filters))
        count=state['statement_calls']
        self.controller.operational_viewmodels['reviewsVM'].inspectionResolved.emit()
        self.wait_until(lambda:state['statement_calls']>count and not vm.busy)
        self.assertEqual(self.warnings,[])

    # Reuse harness lifecycle without inheriting its unrelated test cases.
    def test_invoice_references_open_existing_detail_at_all_sizes(self):
        vm=self.controller.operational_viewmodels['customerAccountVM']
        self.controller.operational_viewmodels['customerVM']._service=Mock(
            get_customer_types=Mock(return_value=[]),get_customer_transaction_types=Mock(return_value=[]),
            search_customers=Mock(return_value={'items':[],'page':1,'has_more':False}))
        self.assertEqual(len(self.root.findChildren(QObject,'invoiceHistoryPage')),1)
        rows=[dict(id=i,row_kind='LEDGER',occurred_at=datetime(2026,9,29,12),source_type='INVOICE',
            source_reference='26092932',invoice_id='21',invoice_number='26092932',settlement_id=None,
            description='فاتورة',actor_name='مدير',amount_delta=D('100'),running_balance=D(100*i)) for i in (1,2)]
        class Service:
            def statement(self,*args,**kw):
                return {'customer':{'name':'عميل','mobile':'01000000000','balance':D('200')},
                        'rows':copy.deepcopy(rows),'has_more':False,'page':1,'accounts':[],'can_settle':True}
        vm.service=Service()
        clicked=[]
        vm.invoiceRequested.connect(clicked.append)
        self.root.setProperty('currentPage',3)
        vm.open('12')
        self.wait_until(lambda:len(vm.rows)==2)
        self.assertEqual({r['invoiceId'] for r in vm.rows},{'21'})
        for size in ((1440,900),(1280,800),(1080,680)):
            self.root.setWidth(size[0]);self.root.setHeight(size[1]);QTest.qWait(120)
            self.root.setProperty('currentPage',3)
            for key in ('LEDGER1','LEDGER2'):
                button=self.visual_item('accountInvoice_'+key)
                self.assertIsNotNone(button)
                self.assertIn('26092932',button.property('text'))
                self.assert_inside_window(button)
                count=len(clicked)
                self.click(button)
                self.assertEqual(len(clicked),count+1)
                self.assertEqual(clicked[-1:], ['21'])
                self.assertEqual(self.warnings,[])
                history=self.controller.operational_viewmodels['invoiceHistoryVM']
                QTest.qWait(200)
                self.assertTrue(history.detailOpen,str((self.history.calls,self.warnings,self.root.property('currentPage'))))
                self.wait_until(lambda:history.detailOpen and not history.detailBusy)
                self.assertTrue(history.detail)
                self.assertIn(('detail','21'),self.history.calls)
                self.assertEqual(self.root.property('currentPage'),1)
                history.closeDetail()
                self.root.setProperty('currentPage',3)
                QTest.qWait(80)
                self.wait_until(lambda:not vm.busy)
            self.assert_inside_window(self.item('customerAccountSettle'))
            self.click('customerAccountSettle');QTest.qWait(100)
            self.assert_inside_window(self.item('customerAccountFinalize'))
            self.item('customerAccountPaymentDialog').setProperty('visible',False)
            QTest.qWait(300)
        self.assertEqual(self.warnings,[])
        # IDs crossing into QML stay strings even beyond JavaScript's exact integer range.
        self.wait_until(lambda:not vm.busy)
        huge='9007199254740993'
        vm._data['rows'][0]['invoice_id']=huge
        vm.changed.emit();QTest.qWait(80)
        self.click(self.visual_item('accountInvoice_LEDGER1'))
        self.assertEqual(clicked[-1],huge)
        self.wait_until(lambda:('detail',huge) in self.history.calls)
        actor=vm.session.current_actor()
        vm._completed(vm._generation,str(actor.generation),'statement',None,'راجع التاريخ.','','ValueError')
        self.assertEqual(vm.message,'راجع التاريخ.')


    def test_deposit_and_refund_rows_and_real_click_deposit(self):
        vm=self.controller.operational_viewmodels['customerAccountVM']
        self.controller.operational_viewmodels['customerVM']._service=Mock(
            get_customer_types=Mock(return_value=[]),get_customer_transaction_types=Mock(return_value=[]),
            search_customers=Mock(return_value={'items':[],'page':1,'has_more':False}))
        rows=[dict(id=1,row_kind='LEDGER',occurred_at=datetime(2026,9,30,12),source_type='CUSTOMER_DEPOSIT',
                   source_reference='DP000001',invoice_id=None,invoice_number=None,settlement_id=None,deposit_id='1',
                   description='عربون DP000001',actor_name='كاشير',amount_delta=D('-5000'),running_balance=D('-5000')),
              dict(id=2,row_kind='LEDGER',occurred_at=datetime(2026,9,30,13),source_type='CUSTOMER_DEPOSIT_REFUND',
                   source_reference='DR000002',invoice_id=None,invoice_number=None,settlement_id=None,deposit_id='2',
                   description='استرداد عربون DR000002',actor_name='كاشير',amount_delta=D('2000'),running_balance=D('-3000'))]
        class Service:
            def statement(self,*args,**kw):
                return {'customer':{'name':'عميل','mobile':'01000000000','balance':D('-3000')},
                        'rows':copy.deepcopy(rows),'has_more':False,'page':1,'accounts':[],'can_settle':True}
        vm.service=Service()
        import dataclasses
        session=vm.session
        with session._lock:                     # this employee holds the customer-money right (CASHIER)
            session._actor=dataclasses.replace(session._actor,
                permissions=session._actor.permissions|{'CUSTOMER_ACCOUNT_SETTLE'})
        calls=[]
        vm._deposits=Mock(deposit=Mock(side_effect=lambda *a,**k:calls.append(a) or
            {'id':'3','code':'DP000003','direction':'IN','amount':'1500.00','balance_after':'-4500.00','duplicate':False}))
        self.root.setProperty('currentPage',3)
        vm.open('12')
        self.wait_until(lambda:len(vm.rows)==2 and not vm.busy)
        self.assertEqual([(r['source'],r['reference']) for r in vm.rows],[('عربون','DP000001'),('استرداد عربون','DR000002')])
        self.assertEqual([r['balance'] for r in vm.rows],['للعميل: 5,000.00 ج','للعميل: 3,000.00 ج'])
        self.root.setWidth(1080);self.root.setHeight(680);QTest.qWait(120)
        self.click('customerDeposit')
        self.wait_until(lambda:self.item('customerDepositDialog').property('opened'))
        self.type_into('customerDepositAmount','1500')
        self.click('customerDepositNext')
        button=self.item('customerDepositConfirm')
        self.assert_inside_window(button)
        self.click(button)
        self.wait_until(lambda:len(calls)==1 and not vm.busy)
        self.assertEqual(calls[0][:2],('12','1500'))
        self.assertIn('DP000003',vm.message)
        self.assertEqual(self.warnings,[])


# unittest otherwise inherits every Billing harness test, inflating the suite count.
for _name in dir(harness.InvoicePageTests):
    if _name.startswith('test_'):
        setattr(CustomerAccountPageTests,_name,None)
