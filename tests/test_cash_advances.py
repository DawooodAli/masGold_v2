"""Temporary cash advances / custody (migration 074): a staff advance or a customer's temporary custody leaves the safe on a NAME and stays owed until repaid."""
from decimal import Decimal as D
from uuid import uuid4

from services.correction_service import FO
from tests.test_financial_accounts import FinancialTestCase, OWNER, SALESPERSON


class CashAdvanceTests(FinancialTestCase):
    def open_employee(self, amount="500", party="1", key=None):
        return self.fin.open_advance("CASH:1", "EMPLOYEE", party, amount, "", key or str(uuid4()))

    def advance_row(self, index=0):
        return self.fin.advances()["advances"][index]

    def test_an_employee_advance_leaves_the_safe_on_the_employees_name(self):
        before = self.cash()
        result = self.open_employee()
        self.assertEqual((self.cash(), result["party_name"]), (before - D("500"), "شلاطة"))
        row = self.advance_row()
        self.assertEqual((row["kind"], row["party"], row["amount"], row["owed"], row["code"]), ("EMPLOYEE", "شلاطة", "500.00", "500.00", "SA000001"))
        self.assertEqual(self.store.ops["1"]["category"], "STAFF_ADVANCE")

    def test_a_customer_custody_is_a_separate_advance_and_never_touches_the_customer_ledger(self):
        self.fin.open_advance("CASH:1", "CUSTOMER", "7", "1200", "عهدة مؤقتة", str(uuid4()))
        row = self.advance_row()
        self.assertEqual((row["kind"], row["party"], row["owed"]), ("CUSTOMER", "عميل أ", "1200.00"))
        self.assertEqual(self.store.ops["1"]["category"], "CUSTOMER_CUSTODY")
        self.assertFalse(getattr(self.store, "customer_transactions", []))

    def test_the_party_is_mandatory_active_and_a_failed_link_leaves_no_money_behind(self):
        before = self.cash()
        for kind, party, code in (("EMPLOYEE", "", "PARTY_REQUIRED"), ("EMPLOYEE", "0", "PARTY_REQUIRED"), ("EMPLOYEE", "2", "PARTY_NOT_FOUND"),
                                  ("EMPLOYEE", "99", "PARTY_NOT_FOUND"), ("CUSTOMER", "99", "PARTY_NOT_FOUND"), ("OTHER", "1", "TYPE_INVALID")):
            self.assertFinRefused(code, lambda kind=kind, party=party: self.fin.open_advance("CASH:1", kind, party, "10", "", str(uuid4())))
        self.assertEqual(self.cash(), before)

    def test_a_retry_with_the_same_key_posts_once(self):
        key = str(uuid4())
        self.open_employee(key=key)
        again = self.open_employee(key=key)
        self.assertTrue(again["duplicate"])
        self.assertEqual((len(self.store.ops), len(self.store.advances)), (1, 1))

    def test_partial_repayment_returns_the_money_and_never_exceeds_what_is_owed(self):
        self.open_employee("500")
        before = self.cash()
        self.fin.repay_advance("1", "CASH:1", "200", "", str(uuid4()))
        self.assertEqual((self.cash(), self.advance_row()["owed"]), (before + D("200"), "300.00"))
        self.assertFinRefused("REPAY_EXCEEDS", lambda: self.fin.repay_advance("1", "CASH:1", "300.01", "", str(uuid4())))
        self.fin.repay_advance("1", "CASH:1", "300", "", str(uuid4()))
        self.assertEqual(self.advance_row()["owed"], "0.00")
        self.assertEqual(self.store.ops["2"]["category"], "ADVANCE_REPAY")

    def test_the_advance_cannot_be_reversed_while_a_repayment_stands_and_a_reversed_repayment_is_owed_again(self):
        self.open_employee("500")
        self.fin.repay_advance("1", "CASH:1", "500", "", str(uuid4()))
        self.assertRefused("ADVANCE_HAS_REPAYMENTS", lambda: self.service.reverse(FO, "1", "خطأ"))
        self.service.reverse(FO, "2", "سداد بالخطأ")
        self.assertEqual(self.advance_row()["owed"], "500.00")
        self.service.reverse(FO, "1", "سلفة بالخطأ")
        row = self.advance_row()
        self.assertEqual((row["voided"], row["owed"]), (True, "0.00"))
        self.assertFinRefused("ADVANCE_REVERSED", lambda: self.fin.repay_advance("1", "CASH:1", "10", "", str(uuid4())))

    def test_the_plain_cash_movement_never_posts_an_advance_without_its_owner(self):
        for kind, direction in (("STAFF_ADVANCE", "OUT"), ("CUSTOMER_CUSTODY", "OUT"), ("ADVANCE_REPAY", "IN")):
            self.assertFinRefused("USE_ADVANCE", lambda kind=kind, direction=direction: self.fin.cash_movement("CASH:1", direction, kind, "5", "", str(uuid4())))
        view_types = [t["code"] for t in self.fin.cash_view("", "", "").get("types", [])] if hasattr(self.fin, "cash_view") else []
        self.assertFalse({"STAFF_ADVANCE", "CUSTOMER_CUSTODY", "ADVANCE_REPAY"} & set(view_types))

    def test_a_salesperson_cannot_open_or_repay(self):
        service = self.fin_service(SALESPERSON)
        with self.assertRaises(PermissionError):
            service.open_advance("CASH:1", "EMPLOYEE", "1", "10", "", str(uuid4()))
        self.assertEqual(self.store.ops, {})

    def test_the_advance_money_is_neither_expense_nor_income_in_the_report_sources(self):
        from services.financial_account_service import ADVANCE_CATEGORIES
        self.assertEqual(set(ADVANCE_CATEGORIES), {"STAFF_ADVANCE", "CUSTOMER_CUSTODY", "ADVANCE_REPAY"})


if __name__ == "__main__":
    import unittest
    unittest.main()
