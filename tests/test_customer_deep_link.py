"""Exact-ID customer navigation through the real QML root, with live DB access blocked."""
from datetime import datetime
from decimal import Decimal
import dataclasses
import unittest
from unittest.mock import Mock

from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from app.session import AppSession
from app.viewmodels.customer_viewmodel import CustomerViewModel
from services.authentication_service import AuthenticationResult, _VERIFIED
from tests import test_invoice_page_qml as harness

A, B = '9007199254740993', '9007199254740995'
NAME = 'محمد عبدالرحمن علي'


def profile(customer_id):
    return {'customer': {'customer_id':str(customer_id),'name':NAME,'balance':Decimal('0'),
                         'is_active':True,'balance_status':'SETTLED'},
            'active_flags':[], 'transactions_page':{'items':[],'page':1,'has_more':False},
            'dead_cash_page':{'items':[],'page':1,'has_more':False},
            'statement_summary':{},'statement_loaded_at':datetime(2026,10,5),'ledger_token':None}


class CustomerDeepLinkTests(unittest.TestCase):
    def setUp(self):
        self.session=AppSession()
        self.session._establish(AuthenticationResult(3,None,'موظف',frozenset({'APP_LOGIN','CUSTOMERS_VIEW'}),_VERIFIED),
                                int(self.session.generation))
        self.service=Mock(get_customer_profile=Mock(side_effect=profile))
        self.vm=CustomerViewModel(service=self.service);self.vm.attach_session(self.session)

    def test_exact_bigint_id_ignores_duplicate_name_and_list_page(self):
        self.vm._customers=[profile(B)['customer']];self.vm._page=7;self.vm._search_text='اسم مختلف'
        self.assertTrue(self.vm.openCustomerDetails(A))
        self.assertEqual((self.vm.selectedCustomerId,self.vm.selectedCustomer['name']),(A,NAME))
        self.service.get_customer_profile.assert_called_once_with(A)
        self.service.search_customers.assert_not_called()
        self.assertEqual((self.vm.page,self.vm._search_text),(7,'اسم مختلف'))
        self.assertTrue(self.vm.openCustomerDetails(B));self.assertEqual(self.vm.selectedCustomerId,B)

    def test_cached_permission_denial_does_not_read_profile(self):
        with self.session._lock:
            self.session._actor=dataclasses.replace(self.session._actor,permissions=frozenset({'APP_LOGIN','INVOICE_VIEW'}))
        self.assertFalse(self.vm.openCustomerDetails(A))
        self.service.get_customer_profile.assert_not_called()
        self.assertEqual(self.vm.actionMessage,'لا توجد صلاحية لتنفيذ العملية.')

    def test_live_permission_denial_uses_existing_denial_message(self):
        self.service.get_customer_profile.side_effect=PermissionError('لا توجد صلاحية لتنفيذ العملية.')
        self.assertFalse(self.vm.openCustomerDetails(A))
        self.assertEqual(self.vm.actionMessage,'لا توجد صلاحية لتنفيذ العملية.')

    def test_invalid_and_missing_customer_preserve_previous_selection(self):
        self.assertTrue(self.vm.openCustomerDetails(B));before=dict(self.vm.selectedCustomer)
        self.service.get_customer_profile.reset_mock()
        for invalid in ('','0','-1','abc','18446744073709551616'):
            self.assertFalse(self.vm.openCustomerDetails(invalid))
        self.service.get_customer_profile.assert_not_called()
        self.service.get_customer_profile.side_effect=ValueError('العميل غير موجود.')
        self.assertFalse(self.vm.openCustomerDetails(A))
        self.assertEqual((self.vm.selectedCustomerId,self.vm.selectedCustomer),(B,before))
        self.assertEqual(self.vm.actionMessage,'تعذر فتح بيانات العميل.')

    def test_wrong_profile_is_never_published(self):
        self.service.get_customer_profile.side_effect=lambda cid:profile(B)
        self.assertFalse(self.vm.openCustomerDetails(A));self.assertFalse(self.vm.hasSelection)


class CustomerDeepLinkQmlTests(harness.InvoicePageTests):
    def prepare(self, permitted=True):
        self.customers=self.controller.operational_viewmodels['customerVM']
        self.history_vm=self.controller.operational_viewmodels['invoiceHistoryVM']
        original_detail=self.history.detail
        self.link_customer=A
        def detail(invoice_id,**kwargs):
            result=original_detail(invoice_id,**kwargs)
            result['invoice_number']='26100501'
            result['customer'].update(customer_id=self.link_customer,name=NAME)
            return result
        self.history.detail=detail
        self.customers._service=Mock(get_customer_profile=Mock(side_effect=profile),
            get_customer_types=Mock(return_value=[]),get_customer_transaction_types=Mock(return_value=[]),
            search_customers=Mock(return_value={'items':[profile(B)['customer']],'page':7,'has_more':False}))
        with self.customers._session._lock:
            actor=self.customers._session._actor
            permissions=actor.permissions|{'CUSTOMERS_VIEW'} if permitted else actor.permissions-{'CUSTOMERS_VIEW'}
            self.customers._session._actor=dataclasses.replace(actor,permissions=permissions)
        self.root.setProperty('currentPage',1);self.click('billingTabHistory')
        self.history_vm.openInvoice('21');self.wait_until(lambda:self.history_vm.detail and not self.history_vm.detailBusy)
        self.history_vm._detail['invoice_number']='26100501'
        self.history_vm._detail['customer'].update(customer_id=A,name=NAME)
        self.history_vm.changed.emit();QTest.qWait(120)

    def show_invoice(self, customer_id=A):
        self.link_customer=customer_id
        self.root.setProperty('currentPage',1)
        self.wait_until(lambda:not self.history_vm.detailBusy)
        self.history_vm._detail['customer']['customer_id']=customer_id
        self.history_vm.changed.emit();QTest.qWait(120)

    def test_invoice_click_opens_exact_details_at_all_sizes_without_search(self):
        self.prepare()
        account=self.controller.operational_viewmodels['customerAccountVM']
        account.service=Mock(statement=Mock(side_effect=lambda cid,*a,**k:{
            'customer':profile(cid)['customer'],'rows':[],'accounts':[],'has_more':False,'can_settle':False}))
        account._customer_id=B;account._open=True
        for width,height in ((1440,900),(1280,800),(1080,680)):
            self.root.setWidth(width);self.root.setHeight(height);QTest.qWait(120)
            self.show_invoice(A)
            link=self.item('historyCustomerName')
            self.assertEqual(link.property('customerId'),A)
            self.assertEqual(link.property('text'),NAME)
            self.assert_inside_window(link)
            self.assertLessEqual(link.property('contentWidth'),link.width()+1)
            QTest.mouseMove(self.root,link.mapToScene(QPointF(link.width()/2,link.height()/2)).toPoint())
            QTest.qWait(40);self.assertTrue(link.property('font').underline())
            self.click(link)
            self.wait_until(lambda:self.root.property('currentPage')==3)
            QTest.qWait(120)
            self.assertEqual(self.customers.selectedCustomerId,A)
            self.assertTrue(self.item('customersPage').property('visible'))
            self.assertFalse(self.item('customerAccountPage').property('visible'))
            self.assert_inside_window(self.item('customerProfileName'))
            self.assertTrue(all(call.kwargs.get('search_text','')==''
                                for call in self.customers._service.search_customers.call_args_list))
            # The Customers page is already loaded when the next invoice requests B.
            self.show_invoice(B);self.click('historyCustomerName')
            self.assertEqual(self.customers.selectedCustomerId,B)
            self.assertEqual(self.customers.selectedCustomer['name'],NAME)
            from PySide6.QtQml import QQmlEngine, QQmlExpression
            navigation=QQmlExpression(QQmlEngine.contextForObject(self.root),self.root,
                                      "openCustomerDetails('"+A+"')")
            self.assertTrue(navigation.evaluate()[0])
            self.assertEqual(self.root.property('currentPage'),3)
            self.assertEqual(self.customers.selectedCustomerId,A)
            self.assertEqual(self.warnings,[])

    def test_permission_and_missing_customer_keep_source_screen_intact(self):
        self.prepare(permitted=False)
        self.click('historyCustomerName')
        self.assertEqual(self.root.property('currentPage'),1)
        self.customers._service.get_customer_profile.assert_not_called()
        self.assertIn('لا توجد صلاحية',self.item('customerNavigationMessage').property('text'))
        self.item('customerNavigationDialog').setProperty('visible',False);QTest.qWait(350)
        with self.customers._session._lock:
            self.customers._session._actor=dataclasses.replace(self.customers._session._actor,
                permissions=self.customers._session._actor.permissions|{'CUSTOMERS_VIEW'})
        self.customers._service.get_customer_profile.side_effect=ValueError('العميل غير موجود.')
        self.click('historyCustomerName')
        self.assertEqual(self.root.property('currentPage'),1)
        self.assertIn('تعذر فتح بيانات العميل.',self.item('customerNavigationMessage').property('text'))
        self.assertEqual(self.history_vm.detail['number'],'26100501')
        self.assertTrue(self.history_vm.detailOpen);self.assertEqual(self.warnings,[])

    def test_missing_customer_id_is_inert(self):
        self.prepare();self.show_invoice('')
        link=self.item('historyCustomerName')
        self.assertEqual(link.property('customerId'),'')
        QTest.mouseClick(self.root,Qt.LeftButton,Qt.NoModifier,
                         link.mapToScene(QPointF(link.width()/2,link.height()/2)).toPoint())
        self.customers._service.get_customer_profile.assert_not_called()
        self.assertEqual(self.root.property('currentPage'),1);self.assertEqual(self.warnings,[])

    def test_lifecycle_links_reuse_exact_id_navigation_at_all_sizes(self):
        self.prepare()
        models=self.controller.operational_viewmodels
        context={'invoice_id':'21','invoice_number':'26100501','finalized_at':'2026-10-05 12:00',
            'customer_id':A,'customer_name':NAME,'has_customer':True,'lines':[],'bank_accounts':[],
            'window_text':'داخل مدة الاسترداد','making_window_open':True,'requires_approval':False,
            'expired':False,'last_allowed_at':'2026-10-20 00:00','age_days':0,
            'refund_window_hours':72,'refund_window_open':True}
        for width,height in ((1440,900),(1280,800),(1080,680)):
            self.root.setWidth(width);self.root.setHeight(height);QTest.qWait(120)
            for model_name,link_name in [('exchangeVM','exchangeCustomerName'),('returnVM','returnCustomerName'),
                                        ('correctionVM','correctionCustomerName'),
                                        ('correctionVM','correctionPreviewCustomerName')]:
                vm=models[model_name]
                if model_name=='correctionVM':
                    vm._targets={'invoice_id':'21','invoice_number':'26100501','customer_id':A,'customer_name':NAME,
                        'targets':[{'source_type':'INVOICE','source_id':'21','reference':'26100501',
                        'source_text':'الفاتورة','reversed_by':None,'settlement_corrections':0}],'bank_accounts':[]}
                    vm._open=True;vm.selectTarget(0)
                    if link_name=='correctionPreviewCustomerName':
                        vm._mode='REVERSAL'
                        vm._reversal={'customer_id':A,'customer_name':NAME,'reference':'26100501','source_text':'الفاتورة',
                            'original_date':'2026-10-05 12:00','original_total':'2000','money':[],'account':[],
                            'inventory':[],'old_gold':[],'reviews':0,'cases':0,'blockers':[],'can_reverse':True}
                else:
                    vm._context=dict(context);vm._open=True
                    if model_name=='exchangeVM':vm._mode='edit'
                    if hasattr(vm,'linesChanged'):vm.linesChanged.emit()
                vm.changed.emit();self.root.setProperty('currentPage',1);QTest.qWait(120)
                link=self.item(link_name)
                self.assertEqual(link.property('customerId'),A)
                self.assert_inside_window(link)
                self.assertLessEqual(link.property('contentWidth'),link.width()+1)
                self.click(link)
                self.assertEqual(self.root.property('currentPage'),3)
                self.assertEqual(self.customers.selectedCustomerId,A)
                vm.close();QTest.qWait(80)
                self.assertEqual(self.warnings,[])


for _name in dir(harness.InvoicePageTests):
    if _name.startswith('test_'):
        setattr(CustomerDeepLinkQmlTests,_name,None)
