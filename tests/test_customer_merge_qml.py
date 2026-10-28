"""Customer file in the real Main.qml: دمج / حذف dialog, the warning checkboxes and turning the Dead Cash warning off, with real clicks."""
from unittest.mock import Mock
from PySide6.QtTest import QTest
from tests import test_invoice_page_qml as harness
from tests.test_dead_cash_qml import DeadCashPageTests


class FakeMerge:
    def __init__(self):
        self.calls = []

    def can_manage(self):
        return True

    def usage(self, customer_id):
        return {"customer_id": customer_id, "name": "أحمد", "balance": "23000.00", "merged": False, "merged_in": 0,
                "usage": [{"table": "invoices", "label": "فواتير", "count": 2}], "deletable": False}

    def preview(self, source, target):
        self.calls.append(("preview", source, target))
        return {"source_id": source, "source_name": "أحمد", "target_id": target, "target_name": "أحمد محمد",
                "moved": [{"table": "invoices", "label": "فواتير", "count": 2}], "source_balance": "23000.00",
                "target_balance": "0.00", "combined_balance": "23000.00"}

    def merge(self, source, target, reason):
        self.calls.append(("merge", source, target, reason))
        return {"source_id": source, "target_id": target, "target_name": "أحمد محمد", "duplicate": False, "moved": []}

    def delete(self, customer_id):
        self.calls.append(("delete", customer_id))
        return {"customer_id": customer_id, "name": "أحمد"}


class CustomerMergeQmlTests(DeadCashPageTests):
    def test_merge_button_dialog_and_confirmation_with_real_clicks(self):
        self.open_customer()
        merge = FakeMerge()
        self.cvm._merge_service = merge
        self.cvm.selectionChanged.emit()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(150)
                button = self.scroll_to("customerMergeButton")
                self.assert_inside_window(button)
                self.click(button)
                dialog = self.item("customerMergeDialog")
                self.wait_until(lambda: dialog.property("opened"), "merge dialog")
                self.assertIn("فواتير (2)", self.item("customerMergeUsage").property("value"))
                self.assertFalse(self.item("customerDeleteButton").property("enabled"))     # something points at it: delete is off
                self.assertFalse(self.item("customerMergeConfirm").property("enabled"))     # no target and no reason yet
                self.assert_inside_window(self.item("customerMergeConfirm"))
                dialog.close()
                QTest.qWait(120)
        self.assertEqual(self.warnings, [])

    def test_warning_checkboxes_save_the_ticked_options_and_the_dead_cash_warning_can_be_turned_off(self):
        self.open_customer()
        import dataclasses
        session = self.cvm._session
        with session._lock:                          # this employee holds the customer-management right
            session._actor = dataclasses.replace(session._actor, permissions=session._actor.permissions | {"CUSTOMERS_MANAGE"})
        self.cvm.selectionChanged.emit()
        saved = []
        self.cvm._service.set_warnings = Mock(side_effect=lambda cid, codes: saved.append((cid, list(codes))) or list(codes))
        cleared = []
        self.dead.can_write_off = lambda: True
        self.dead.clear_warning = lambda cid: cleared.append(cid) or {"customer_id": cid}
        self.click(self.scroll_to("customerWarningsTab"))
        QTest.qWait(150)
        self.assertTrue(self.cvm.canManageWarnings, "the test user manages customers")      # the checkboxes are for someone who may manage customers
        check = self.visible_item("warningCheck_NOT_COMMITTED")
        self.assertIsNotNone(check)
        self.click(check)
        self.click(self.visible_item("saveWarningsButton"))
        self.wait_until(lambda: saved, "warnings saved")
        self.assertEqual(saved[-1], ("12", ["NOT_COMMITTED"]))
        # الرصيد المشطوب: the Dead Cash warning is turned off from its own tab
        self.cvm.selectionChanged.emit()
        self.click(self.scroll_to("deadCashHistoryTab"))
        QTest.qWait(150)
        off = self.visible_item("clearDeadCashWarningButton")
        self.assertIsNotNone(off, "the active Dead Cash warning can be turned off")
        self.click(off)
        self.wait_until(lambda: cleared, "warning cleared")
        self.assertEqual(cleared, ["12"])
        self.assertEqual(self.warnings, [])
