"""المرتبات: the manager-reviews button, the dialog and its forms, with REAL clicks at every target size (the service is an in-memory fake; the rules are tested in test_payroll)."""
import unittest
from decimal import Decimal as D

from PySide6.QtTest import QTest

from app.viewmodels.payroll_viewmodel import PayrollViewModel
from tests import test_invoice_page_qml as harness


class FakeService:
    def __init__(self):
        self.calls, self.base, self.bonus, self.paid = [], None, D("0.00"), False

    def can_manage(self):
        return True

    def overview(self, month="", *, _expected_actor=None):
        base = self.base
        row = {"id": "1", "name": "شلاطة", "base": None if base is None else f"{base:.2f}", "bonuses": f"{self.bonus:.2f}", "penalties": "0.00", "owed": "300.00",
               "default_deduction": "300.00", "net_preview": None if base is None else f"{base + self.bonus - D('300'):.2f}",
               "status": "PAID" if self.paid else ("NO_SALARY" if base is None else "READY"),
               "paid": {"net": "4700.00", "base": "5000.00", "bonuses": "0.00", "penalties": "0.00", "advances": "300.00", "date": "2026-11-08"} if self.paid else None}
        adjustments = [] if not self.bonus else [{"id": "1", "employee": "شلاطة", "kind": "BONUS", "kind_text": "حافز", "amount": f"{self.bonus:.2f}", "reason": "مبيعات", "state": "OPEN",
                                                    "void_reason": "", "date": "2026-11-08"}]
        return {"month": "2026-11", "employees": [row], "adjustments": adjustments, "accounts": [{"key": "CASH:1", "name": "الخزنة: الرئيسية", "balance": "10000.00"}],
                "paid_total": "4700.00" if self.paid else "0.00", "due_total": "0.00"}

    def set_salary(self, employee_id, amount, month, *, _expected_actor=None):
        self.calls.append(("salary", employee_id, amount))
        self.base = D(amount)

    def add_adjustment(self, employee_id, month, kind, amount, reason, *, _expected_actor=None):
        self.calls.append(("adjust", employee_id, kind, amount, reason))
        self.bonus = D(amount)

    def void_adjustment(self, adjustment_id, reason, *, _expected_actor=None):
        self.calls.append(("void", adjustment_id, reason))
        self.bonus = D("0.00")

    def pay(self, employee_id, month, account, deduction, note, key, *, _expected_actor=None):
        self.calls.append(("pay", employee_id, account, deduction))
        self.paid = True
        return {"operation_id": "1", "amount": "4700.00", "duplicate": False}


class ScreenTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        self.grant("PAYROLL_MANAGE")
        self.fake = FakeService()
        self.payroll_vm = self.controller.operational_viewmodels["payrollVM"]
        self.payroll_vm._service = self.fake
        self.payroll_vm.changed.emit()

    def test_the_owner_sets_a_salary_adds_a_bonus_and_pays_with_real_clicks_at_all_sizes(self):
        self.root.setProperty("currentPage", 10)
        self.wait_until(lambda: self.visual_item("reviewsOpenPayroll") is not None and self.item("reviewsOpenPayroll").property("visible"), "the payroll button is shown")
        self.assert_inside_window(self.item("reviewsOpenPayroll"))
        self.open_settings_dialog(10, "reviewsOpenPayroll", "payrollDialog")
        self.wait_until(lambda: len(self.payroll_vm.employees) == 1, "the employees are listed")
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            self.scroll_dialog_to("payrollDialog", "payrollSetSalary")
            self.assert_inside_window(self.item("payrollSetSalary"))
        self.click("payrollSetSalary")
        self.type_into("payrollAmount", "5000")
        self.click("payrollSave")
        self.wait_until(lambda: self.fake.calls == [("salary", "1", "5000")], "the salary is saved")
        self.wait_until(lambda: self.payroll_vm.employees[0]["status"] == "READY", "the employee is ready to pay")
        self.click("payrollAddAdjustment")
        self.type_into("payrollAmount", "200")
        self.type_into("payrollReason", "مبيعات")
        self.click("payrollSave")
        self.wait_until(lambda: self.fake.calls[-1][0] == "adjust" and self.fake.calls[-1][2] == "BONUS", "the bonus is saved")
        self.wait_until(lambda: len(self.payroll_vm.adjustments) == 1, "the bonus is listed")
        self.click("payrollPay")
        self.assertFalse(self.item("payrollSave").property("enabled"))                          # no account chosen yet
        self.item("payrollAccount").setProperty("currentIndex", 0)
        self.wait_until(lambda: self.item("payrollSave").property("enabled"), "an account is chosen")
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            self.scroll_dialog_to("payrollDialog", "payrollSave")
            self.assert_inside_window(self.item("payrollSave"))
        self.assertIn("4,900.00", str(self.item("payrollNetPreview").property("text")))
        self.click("payrollSave")
        self.wait_until(lambda: self.fake.calls[-1][0] == "pay" and self.fake.calls[-1][2] == "CASH:1", "the salary is paid")
        self.wait_until(lambda: self.payroll_vm.employees[0]["status"] == "PAID", "the slip shows as paid")
        self.assertEqual(self.warnings, [])


for _cls in (ScreenTests,):
    for _name in dir(harness.InvoicePageTests):
        if _name.startswith("test_") and _name not in _cls.__dict__:
            setattr(_cls, _name, None)


if __name__ == "__main__":
    unittest.main()
