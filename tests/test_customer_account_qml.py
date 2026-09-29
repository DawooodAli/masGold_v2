"""Real clicks in Main.qml reuse its existing Invoice History detail, with DB blocked."""
import copy
from datetime import datetime
from decimal import Decimal as D
from unittest.mock import Mock
from PySide6.QtCore import QObject
from PySide6.QtTest import QTest
from tests import test_invoice_page_qml as harness


class CustomerAccountPageTests(harness.InvoicePageTests):
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
            self.assert_inside_window(self.item('customerAccountSettle'))
            self.click('customerAccountSettle');QTest.qWait(100)
            self.assert_inside_window(self.item('customerAccountFinalize'))
            self.item('customerAccountPaymentDialog').setProperty('visible',False)
            QTest.qWait(300)
        self.assertEqual(self.warnings,[])
        # IDs crossing into QML stay strings even beyond JavaScript's exact integer range.
        huge='9007199254740993'
        vm._data['rows'][0]['invoice_id']=huge
        vm.changed.emit();QTest.qWait(80)
        self.click(self.visual_item('accountInvoice_LEDGER1'))
        self.assertEqual(clicked[-1],huge)
        self.wait_until(lambda:('detail',huge) in self.history.calls)
        actor=vm.session.current_actor()
        vm._completed(vm._generation,str(actor.generation),'statement',None,'راجع التاريخ.','','ValueError')
        self.assertEqual(vm.message,'راجع التاريخ.')


# unittest otherwise inherits every Billing harness test, inflating the suite count.
for _name in dir(harness.InvoicePageTests):
    if _name.startswith('test_'):
        setattr(CustomerAccountPageTests,_name,None)
