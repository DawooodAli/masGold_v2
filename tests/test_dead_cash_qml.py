"""Dead Cash on the real Customers page (Main.qml): warning banner + summary, the write-off dialog (default full
amount, editable, review sentence, password) and the reversal from history — real clicks at 1440×900 /
1280×800 / 1080×680. The database is blocked; services are in-memory doubles."""
from datetime import datetime
from decimal import Decimal as D
from unittest.mock import Mock

from PySide6.QtCore import QObject, QPointF
from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness


def customer(balance):
    return {"customer_id": 12, "customer_code": "C000012", "name": "أحمد", "mobile": "01000000000", "place": "",
            "notes": None, "customer_type_id": None, "customer_type_code": "REGULAR", "is_active": 1,
            "balance": D(balance), "balance_status": "CUSTOMER_OWES_SHOP" if D(balance) > 0 else "SETTLED",
            "last_transaction_at": datetime(2026, 9, 30, 12), "active_warning_count": 1,
            "has_active_dead_cash_warning": 1, "has_dead_cash_history": 1, "unreversed_dead_cash_count": 1}


class DeadCash:
    def __init__(self):
        self.calls = []

    def can_write_off(self):
        return True

    def can_reverse(self):
        return True

    def context(self, customer_id):
        return {"customer_id": "12", "name": "أحمد", "balance": "23000.00", "eligible": True}

    def write_off(self, *args):
        self.calls.append(("write_off",) + args)
        return {"id": "2", "code": "DC000002", "amount": "8000.00", "balance_before": "23000.00",
                "balance_after": "15000.00", "duplicate": False}

    def reverse(self, *args):
        self.calls.append(("reverse",) + args)
        return {"id": args[0], "code": "DC000001", "reversal_code": "DCR000001", "amount": "4000.00"}


class DeadCashPageTests(harness.InvoicePageTests):
    def open_customer(self):
        self.cvm = self.controller.operational_viewmodels["customerVM"]
        profile = {"customer": customer("23000"), "active_flags": [
                       {"flag_id": 1, "flag_type": "DEAD_CASH_WARNING", "severity": "WARNING",
                        "reason": "سبق شطب رصيد لهذا العميل — DC000001", "created_at": datetime(2026, 9, 29)}],
                   "transactions_page": {"items": [], "page": 1, "page_size": 100, "has_more": False},
                   "statement_summary": {}, "ledger_token": None, "statement_loaded_at": datetime(2026, 9, 30),
                   "dead_cash_page": {"items": [{"dead_cash_id": 1, "amount": D("4000.00"),
                                                 "balance_before": D("27000.00"), "balance_after": D("23000.00"),
                                                 "reason": "عميل مسافر", "occurred_at": datetime(2026, 9, 29, 11),
                                                 "is_reversed": 0}], "page": 1, "page_size": 100, "has_more": False},
                   "dead_cash_summary": {"active_count": 1, "active_total": D("4000.00"),
                                         "last_writeoff_at": datetime(2026, 9, 29, 11), "reversed_count": 0}}
        self.cvm._service = Mock(
            get_customer_types=Mock(return_value=[]), get_customer_transaction_types=Mock(return_value=[]),
            search_customers=Mock(return_value={"items": [customer("23000")], "page": 1, "has_more": False}),
            get_customer_profile=Mock(return_value=profile),
            get_customer_statement=Mock(return_value={"customer": customer("23000"), "items": [], "page": 1,
                                                      "page_size": 100, "has_more": False, "summary": {},
                                                      "loaded_at": datetime(2026, 9, 30), "ledger_token": None}))
        self.dead = DeadCash()
        self.cvm._dead_cash_service = self.dead
        self.root.setProperty("currentPage", 3)
        self.assertTrue(self.cvm.selectCustomer("12"))
        self.wait_until(lambda: self.cvm.selectedCustomerId == "12" and self.cvm.canWriteOff, "profile")

    def scroll_to(self, name):
        """The Customers page scrolls; bring an item into view as the owner would."""
        flick = self.item("customerBodyScroll").property("contentItem")
        target = self.visible_item(name)
        top = target.mapToItem(flick.property("contentItem"), QPointF(0, 0)).y()
        limit = max(0, flick.property("contentHeight") - flick.height())
        flick.setProperty("contentY", max(0, min(limit, top - 120)))
        QTest.qWait(150)
        return target

    def type_field(self, form_name, text):
        form = self.item(form_name)
        field = next(child for child in form.childItems() if child.inherits("QQuickTextField"))
        self.click(field)
        QTest.keyClick(self.root, harness.Qt.Key_A, harness.Qt.ControlModifier)
        self.type_text(text)

    def test_warning_write_off_dialog_and_reversal_at_all_sizes(self):
        self.open_customer()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                self.root.setProperty("currentPage", 3)
                QTest.qWait(150)
                banner = self.visible_item("deadCashWarningBanner")
                self.assertIsNotNone(banner)
                self.assertEqual(self.item("deadCashWarningText").property("text").strip("‏"),
                                 "⚠️ سبق شطب رصيد لهذا العميل")
                summary = self.item("deadCashSummaryText").property("text")
                for part in ("عدد مرات الشطب: 1", "إجمالي الرصيد المشطوب: 4,000.00 ج", "آخر تاريخ شطب:"):
                    self.assertIn(part, summary)
                before = len(self.dead.calls)
                button = self.scroll_to("writeOffButton")
                self.assert_inside_window(button)
                self.click(button)
                dialog = self.item("writeOffDialog")
                self.wait_until(lambda: dialog.property("opened"), "write-off dialog")
                amount_field = next(c for c in self.item("writeOffAmount").childItems() if c.inherits("QQuickTextField"))
                self.assertEqual(amount_field.property("text"), "23000.00")               # default = full balance
                self.assertIn("23,000.00 ج", self.item("writeOffBalance").property("value"))
                self.type_field("writeOffAmount", "8000")                                 # partial, editable
                self.assertIn("15,000.00 ج", self.item("writeOffAfter").property("value"))
                self.assertFalse(self.item("writeOffNext").property("enabled"))           # reason still missing
                self.type_field("writeOffReason", "عميل مسافر ولا يمكن التحصيل")
                self.click("writeOffNext")
                self.assertEqual(self.item("writeOffConfirmText").property("text").strip("‏"),
                                 "سيتم شطب 8,000.00 ج من رصيد أحمد. سيصبح الرصيد المتبقي على العميل 15,000.00 ج.")
                confirm = self.item("writeOffConfirm")
                self.assertFalse(confirm.property("enabled"))                             # password first
                password = self.item("writeOffPassword")
                self.click(password)
                self.type_text("owner-password")
                self.assertEqual(password.property("text"), "owner-password")
                self.assertNotIn("owner-password", password.property("displayText"))      # masked on screen
                for name in ("writeOffConfirm", "writeOffPassword", "writeOffAmount", "writeOffReason"):
                    self.assert_inside_window(self.item(name))
                self.click(confirm)
                self.wait_until(lambda: len(self.dead.calls) == before + 1, "write-off call")
                call = self.dead.calls[-1]
                self.assertEqual(call[:6][:5], ("write_off", "12", "8000", "عميل مسافر ولا يمكن التحصيل",
                                                "owner-password"))
                self.assertEqual(call[5], "23000.00")                                     # the dialog's balance
                self.wait_until(lambda: not dialog.property("visible"), "dialog closes")
                # History: business reference + status, reversal with reason + password.
                self.item("customersPage").setProperty("historyTab", 2)
                QTest.qWait(120)
                reverse = self.scroll_to("reverseDeadCash_DC000001")
                self.assert_inside_window(reverse)
                self.click(reverse)
                reverse_dialog = self.item("reverseDeadCashDialog")
                self.wait_until(lambda: reverse_dialog.property("opened"), "reverse dialog")
                self.type_field("reverseDeadCashReason", "أُدخل بالخطأ")
                self.click("reverseDeadCashPassword")
                self.type_text("owner-password")
                self.click("reverseDeadCashConfirm")
                self.wait_until(lambda: self.dead.calls[-1][0] == "reverse", "reverse call")
                self.assertEqual(self.dead.calls[-1][1:], ("1", "أُدخل بالخطأ", "owner-password"))
                self.wait_until(lambda: not reverse_dialog.property("visible"), "reverse dialog closes")
                self.item("customersPage").setProperty("historyTab", 0)
        self.assertEqual(self.warnings, [])


    def test_without_dead_cash_view_only_the_warning_is_shown(self):
        self.open_customer()
        restricted = dict(self.cvm._service.get_customer_profile.return_value)
        restricted.update(dead_cash_restricted=True, dead_cash_summary=None,
                          dead_cash_page={"items": [], "page": 1, "page_size": 100, "has_more": False,
                                          "restricted": True})
        self.cvm._service.get_customer_profile.return_value = restricted
        self.cvm.refresh()
        self.wait_until(lambda: not self.cvm.canViewDeadCash, "restricted profile")
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(120)
            self.assertIsNotNone(self.visible_item("deadCashWarningBanner"))           # the caution stays
            self.assertIsNone(self.visible_item("deadCashSummaryText"))                # no count / total / date
            self.assertIsNone(self.visible_item("deadCashHistoryTab"))                 # no history tab
        self.assertEqual(self.warnings, [])


# unittest otherwise inherits every Billing harness test, inflating the suite count.
for _name in dir(harness.InvoicePageTests):
    if _name.startswith("test_"):
        setattr(DeadCashPageTests, _name, None)
