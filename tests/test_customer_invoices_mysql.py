"""Opt-in real DEV invoice navigation and automatic CS refresh in the existing Main.qml.

Only masgold_v2; uses existing invoices and a tagged Customer Account V2 test customer.
Persists one tagged 1 EGP cash settlement per run; no financial-history cleanup.
"""
import os
import time
from unittest.mock import patch
import unittest
from decimal import Decimal
from PySide6.QtTest import QTest
from database.connection import DatabaseConnection
from config.database_config import DatabaseConfig
from repositories.authentication_repository import AuthenticationRepository
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.customer_account_service import CustomerAccountService
from services.customer_service import CustomerService
from services.invoice_history_service import InvoiceHistoryService
from tests import test_invoice_page_qml as harness

TAG='CUSTOMER UI REFRESH DEV TEST DATA'


@unittest.skipUnless(os.getenv('MASGOLD_CUSTOMER_UI_DEV')=='1','explicit customer UI DEV validation only')
class CustomerInvoicesDevTests(harness.InvoicePageTests):
    def wait_until(self, predicate, message='timed out'):
        # Give Python's MySQL socket workers time as well as pumping Qt events.
        for _ in range(500):
            if predicate():return
            QTest.qWait(10);time.sleep(0.01)
        self.fail(message)

    def setUp(self):
        self.real_connect=DatabaseConnection.connect
        super().setUp()

    def test_existing_invoices_clicks_and_real_settlement_refresh(self):
        real_connect=self.real_connect
        def guarded_connect(instance):
            if DatabaseConfig.from_env().database!='masgold_v2':raise RuntimeError('DEV target refused')
            connection=real_connect(instance)
            q=connection.cursor();q.execute('SELECT DATABASE()');database=q.fetchone()[0];q.close()
            if database!='masgold_v2':
                connection.close();raise RuntimeError('Live DEV target refused')
            connection.rollback()
            return connection
        self.enterContext(patch.object(DatabaseConnection,'connect',guarded_connect))
        c=DatabaseConnection(DatabaseConfig.from_env()).connect()
        try:
            q=c.cursor(dictionary=True)
            q.execute("SELECT i.customer_id FROM invoices i WHERE i.customer_id IS NOT NULL AND i.invoice_status<>'DRAFT' "
                "GROUP BY i.customer_id HAVING SUM(EXISTS(SELECT 1 FROM customer_transactions t WHERE t.reference_type='INVOICE' AND t.reference_id=i.id))>0 "
                "AND SUM(NOT EXISTS(SELECT 1 FROM customer_transactions t WHERE t.reference_type='INVOICE' AND t.reference_id=i.id))>0 ORDER BY i.customer_id LIMIT 1")
            cid=str(q.fetchone()['customer_id'])
            q.execute("SELECT i.id, i.invoice_number, EXISTS(SELECT 1 FROM customer_transactions t WHERE t.reference_type='INVOICE' AND t.reference_id=i.id) AS has_account "
                "FROM invoices i WHERE i.customer_id=%s AND i.invoice_status<>'DRAFT' ORDER BY i.invoice_date DESC,i.id DESC",(cid,))
            expected=q.fetchall()
            q.execute("SELECT u.id FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id WHERE r.code='OWNER' AND u.is_active=1 AND u.must_change_password=0 ORDER BY u.id LIMIT 1")
            uid=q.fetchone()['id']
            q.execute("SELECT c.id,b.balance FROM customers c JOIN vw_customer_balances b ON b.customer_id=c.id WHERE c.notes='CUSTOMER ACCOUNT V2 DEV TEST DATA' AND b.balance>1 ORDER BY c.id DESC LIMIT 1")
            controlled=q.fetchone();self.assertIsNotNone(controlled)
            q.close()
            auth=AuthenticationRepository(c);identity=auth.get_identity(uid)
            login=AuthenticationResult(uid,identity['employee_id'],identity['display_name'],auth.effective_permissions(uid),_VERIFIED)
        finally:c.rollback();c.close()
        session=self.controller.session
        session._establish(login,int(session.generation))
        models=self.controller.operational_viewmodels
        customers=models['customerVM'];customers._service=CustomerService(session)
        vm=models['customerAccountVM'];vm.service=CustomerAccountService(session)
        history=models['invoiceHistoryVM'];history._service=InvoiceHistoryService(session)
        self.root.setProperty('currentPage',3)
        self.assertTrue(customers.selectCustomer(cid));vm.open(cid)
        self.wait_until(lambda:not vm.busy and bool(vm.header))
        statement_ids={r['invoiceId'] for r in vm.rows if r['invoiceId']}
        paid=next(r for r in expected if not r['has_account'])
        self.assertNotIn(str(paid['id']),statement_ids)
        self.click('customerInvoicesTab');self.wait_until(lambda:not vm.busy)
        all_rows=list(vm.invoiceRows)
        while vm.hasMore:
            vm.page(1);self.wait_until(lambda:not vm.busy);all_rows.extend(vm.invoiceRows)
        self.assertEqual({r['invoiceId'] for r in all_rows},{str(r['id']) for r in expected})
        self.assertEqual(len(all_rows),len(expected))
        choices=[paid,next(r for r in expected if r['has_account'])]
        for chosen in choices:
            vm.filter('','','',chosen['invoice_number']);self.wait_until(lambda:not vm.busy)
            self.assertEqual(len(vm.invoiceRows),1)
            self.click(self.visual_item('customerInvoice_'+str(chosen['id'])))
            self.wait_until(lambda:history.detailOpen and not history.detailBusy)
            self.assertEqual(history.detail['number'],chosen['invoice_number'])
            history.closeDetail();self.root.setProperty('currentPage',3)
            self.wait_until(lambda:not vm.busy)
        # Use a tagged test customer for the one financial operation; keep its history.
        test_cid=str(controlled['id']);before=Decimal(controlled['balance'])
        self.assertTrue(customers.selectCustomer(test_cid))
        self.wait_until(lambda:not vm.busy and vm.customerId==test_cid)
        vm.setTab(0);self.wait_until(lambda:not vm.busy)
        self.click('customerAccountSettle');QTest.qWait(150)
        vm.editLeg(vm.legs[0]['key'],'PHYSICAL_CASH','','1.00')
        from PySide6.QtQuick import QQuickItem
        fields=self.root.findChildren(QQuickItem)
        note=next(f for f in fields if f.property('placeholderText')=='ملاحظة اختيارية')
        note.setProperty('text',TAG)
        self.click('customerAccountFinalize')
        self.wait_until(lambda:not vm.busy and vm._data['customer']['balance']==before-1)
        self.assertEqual(Decimal(customers.selectedCustomer['balance']),before-1)
        self.assertEqual(customers.selectedCustomerId,test_cid)
        self.assertTrue(any(r['notes']==TAG for r in vm.rows))
        print('DEV proof: customer',cid,'all invoices',len(expected),'clicked',
              [r['invoice_number'] for r in choices],'; tagged customer',test_cid,'balance',before,'->',before-1)
        self.assertEqual(self.warnings,[])


for _name in dir(harness.InvoicePageTests):
    if _name.startswith('test_'):
        setattr(CustomerInvoicesDevTests,_name,None)
