"""رصيد قديم on the real Main.qml with real clicks: the dialog, the confirmation step and the call that reaches the service, at the three target sizes. The service is a recorder (no database)."""
import copy
import dataclasses
from datetime import datetime
from decimal import Decimal as D
from unittest.mock import Mock

from PySide6.QtCore import QPointF
from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness


class LegacyBalanceDialogTests(harness.InvoicePageTests):
    def prepare(self, owner):
        vm = self.controller.operational_viewmodels['customerAccountVM']
        self.controller.operational_viewmodels['customerVM']._service = Mock(
            get_customer_types=Mock(return_value=[]), get_customer_transaction_types=Mock(return_value=[]),
            search_customers=Mock(return_value={'items': [], 'page': 1, 'has_more': False}))
        calls = []
        rows = [dict(id=1, row_kind='LEDGER', occurred_at=datetime(2026, 9, 30, 12), source_type='UNKNOWN', source_reference=None, invoice_id=None, invoice_number=None,
                     settlement_id=None, deposit_id=None, description='x', actor_name='م', amount_delta=D('500'), running_balance=D('500'))]

        class Service:
            def statement(self, *args, **kw):
                return {'customer': {'name': 'عميل', 'mobile': '0100', 'balance': D('500')}, 'rows': copy.deepcopy(rows), 'has_more': False, 'page': 1, 'accounts': [], 'can_settle': False}

            def add_legacy_balance(self, *args, **kw):
                calls.append(args)
                return {'id': '9', 'balance_before': '500.00', 'balance_after': '2000.00', 'duplicate': False}
        vm.service = Service()
        if owner:
            with vm.session._lock:
                vm.session._actor = dataclasses.replace(vm.session._actor, permissions=vm.session._actor.permissions | {'CUSTOMER_BALANCE_ADJUST'})
        self.root.setProperty('currentPage', 3)
        vm.open('12')
        self.wait_until(lambda: len(vm.rows) == 1 and not vm.busy)
        return vm, calls

    def scroll_to_name(self):
        """The customer's file scrolls (a narrow window stacks the list above it): bring the area under the customer's name into view, as the employee would."""
        flick = self.item("customerBodyScroll").property("contentItem")
        top = self.item("customerProfileName").mapToItem(flick.property("contentItem"), QPointF(0, 0)).y()
        flick.setProperty("contentY", max(0, min(max(0, flick.property("contentHeight") - flick.height()), top - 40)))
        QTest.qWait(150)

    def test_the_owner_adds_an_old_balance_with_real_clicks_at_every_size(self):
        vm, calls = self.prepare(True)
        self.assertTrue(vm.canLegacy)
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(120)
            self.scroll_to_name()
            self.assert_inside_window(self.visual_item('customerProfileLegacy'))              # right under the customer's name
        self.click('customerProfileLegacy')
        self.wait_until(lambda: self.item('customerLegacyDialog').property('opened'))
        self.type_into('customerLegacyAmount', '1500')
        self.type_into('customerLegacyDate', '2023-05-10')
        self.type_into('customerLegacyNote', 'دفتر ورقي 2023')
        self.click('customerLegacyNext')
        button = self.item('customerLegacyConfirm')
        self.wait_until(lambda: button.property('visible') and button.property('enabled'), 'confirm shown')
        QTest.qWait(120)
        self.assert_inside_window(button)
        self.click(button)
        self.wait_until(lambda: len(calls) == 1 and not vm.busy)
        customer, direction, amount, note, day, key = calls[0]
        self.assertEqual((customer, direction, amount, note, day), ('12', 'OWES', '1500', 'دفتر ورقي 2023', '2023-05-10'))
        self.assertEqual(len(key), 36)
        self.assertIn('تمت إضافة الرصيد القديم', vm.message)
        self.assertEqual(self.warnings, [])

    def test_a_user_without_the_right_is_not_offered_the_button(self):
        vm, calls = self.prepare(False)
        self.assertFalse(vm.canLegacy)
        self.assertFalse(self.item('customerProfileLegacy').property('visible'))
        self.assertFalse(self.item('customerLegacyBalance').property('visible'))
        vm.legacyAction('OWES', '1', '', 'x')                      # even a forged call does nothing
        self.assertEqual(calls, [])
