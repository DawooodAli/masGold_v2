"""المرتبات (migration 104): monthly salary, bonuses / penalties, the net paid by ONE cash withdrawal, the advances deducted, voiding by reversal. Against the in-memory store of the
financial-account tests (no application DB)."""
import unittest
from datetime import date
from decimal import Decimal as D
from uuid import uuid4

from repositories import financial_account_repository as far
from services.correction_service import CorrectionError, FO
from services.payroll_service import PayrollError, PayrollService
from services.financial_account_service import FinancialAccountService
from tests import test_correction_service as fixture
from tests.test_financial_accounts import FinRepo, FinancialTestCase, MANAGER, OWNER

PAYROLL_OWNER = frozenset(OWNER | {"PAYROLL_MANAGE"})


class PayrollRepo:
    """The PayrollRepository contract over the same store."""

    def __init__(self, connection):
        self.store = connection.store
        self.fin = FinRepo(connection)
        s = self.store
        if not hasattr(s, "rates_list"):
            s.rates_list, s.adjustments_list, s.slips_list, s.payroll_audit = [], [], [], []

    def has_schema(self):
        return True

    def active_employees(self):
        return self.fin.active_employees()

    def lock_employee(self, employee_id):
        return self.fin.employee(employee_id)

    def salary_rate(self, employee_id, month):
        rows = [r for r in self.store.rates_list if r["employee_id"] == int(employee_id) and r["month"] <= month]
        return max(rows, key=lambda r: r["month"], default=None) and {"monthly_amount": max(rows, key=lambda r: r["month"])["amount"], "effective_month": max(rows, key=lambda r: r["month"])["month"]}

    def set_rate(self, employee_id, month, amount, user_id):
        self.store.rates_list = [r for r in self.store.rates_list if not (r["employee_id"] == int(employee_id) and r["month"] == month)]
        self.store.rates_list.append({"employee_id": int(employee_id), "month": month, "amount": amount})

    def _slip_voided(self, slip_id):
        slip = next((s for s in self.store.slips_list if s["id"] == str(slip_id)), None)
        return bool(slip) and self.fin._reversed(slip["operation_id"])

    def adjustments(self, month):
        return [{"id": a["id"], "employee_id": a["employee_id"], "employee_name": "شلاطة", "kind": a["kind"], "amount": a["amount"], "reason": a["reason"], "slip_id": a["slip_id"],
                 "created_at": self.store.now, "voided_at": a["voided"], "void_reason": a.get("void_reason"), "slip_voided": 1 if a["slip_id"] and self._slip_voided(a["slip_id"]) else 0}
                for a in reversed(self.store.adjustments_list) if a["month"] == month]

    def open_adjustments(self, employee_id, month, lock=False):
        return [dict(a) for a in self.store.adjustments_list if a["employee_id"] == int(employee_id) and a["month"] == month and not a["voided"]
                and (a["slip_id"] is None or self._slip_voided(a["slip_id"]))]

    def insert_adjustment(self, *, employee_id, month, kind, amount, reason, user_id):
        assert amount > 0 and reason
        new_id = str(len(self.store.adjustments_list) + 1)
        self.store.adjustments_list.append({"id": new_id, "employee_id": int(employee_id), "month": month, "kind": kind, "amount": amount, "reason": reason, "slip_id": None, "voided": None})
        return new_id

    def adjustment(self, adjustment_id, lock=False):
        a = next((x for x in self.store.adjustments_list if x["id"] == str(adjustment_id)), None)
        return None if a is None else {"id": a["id"], "employee_id": a["employee_id"], "period_month": a["month"], "kind": a["kind"], "amount": a["amount"], "slip_id": a["slip_id"], "voided_at": a["voided"]}

    def void_adjustment(self, adjustment_id, reason, user_id):
        a = next(x for x in self.store.adjustments_list if x["id"] == str(adjustment_id))
        if a["slip_id"] or a["voided"]:
            return False
        a["voided"], a["void_reason"] = True, reason
        return True

    def attach_adjustments(self, ids, slip_id):
        for a in self.store.adjustments_list:
            if int(a["id"]) in [int(i) for i in ids]:
                a["slip_id"] = str(slip_id)

    def slips(self, month):
        return [{"id": s["id"], "employee_id": s["employee_id"], "base_salary": s["base"], "bonuses": s["bonuses"], "penalties": s["penalties"], "advances_deducted": s["advances"],
                 "net_amount": s["net"], "operation_id": s["operation_id"], "note": s["note"], "created_at": self.store.now, "voided": 1 if self._slip_voided(s["id"]) else 0}
                for s in reversed(self.store.slips_list) if s["month"] == month]

    def effective_slip(self, employee_id, month):
        return next(({"id": s["id"], "net_amount": s["net"]} for s in self.store.slips_list if s["employee_id"] == int(employee_id) and s["month"] == month and not self._slip_voided(s["id"])), None)

    def insert_slip(self, *, employee_id, month, base, bonuses, penalties, advances, net, operation_id, note, user_id):
        assert net > 0 and net == base + bonuses - penalties - advances                                       # chk_payroll_slip_amounts
        assert not any(s["operation_id"] == str(operation_id) for s in self.store.slips_list)                  # uq_payroll_slip_operation
        new_id = str(len(self.store.slips_list) + 1)
        self.store.slips_list.append({"id": new_id, "employee_id": int(employee_id), "month": month, "base": base, "bonuses": bonuses, "penalties": penalties, "advances": advances,
                                      "net": net, "operation_id": str(operation_id), "note": note})
        return new_id

    def open_advances(self, employee_id, lock=False):
        rows = [self.fin._advance_row(a) for a in self.fin._advances().values() if a["kind"] == "EMPLOYEE" and a["employee_id"] == int(employee_id) and a["direction"] == "LEND"]
        return [r for r in rows if not r["voided"] and r["amount"] - r["repaid"] > 0]

    def insert_repayment(self, *, advance_id, amount, operation_id, user_id):
        assert amount > 0
        assert not any(r["advance_id"] == str(advance_id) and r["operation_id"] == str(operation_id) for r in self.store.repayments)      # uq_cash_advance_repay_pair
        self.store.repayments.append({"advance_id": str(advance_id), "amount": amount, "operation_id": str(operation_id)})

    def audit(self, **kw):
        self.store.payroll_audit.append(kw["event_type"])


class PayrollCase(FinancialTestCase):
    def setUp(self):
        super().setUp()
        self.fin_service(OWNER)
        FinRepo(fixture.Connection(self.store))._advances()                           # seed the lazily created tables before any snapshot is taken
        PayrollRepo(fixture.Connection(self.store))
        FinRepo.connection = property(lambda repo: fixture.Connection(repo.store))     # the real repository exposes its connection (the payroll repository is built on it)
        self.addCleanup(lambda: delattr(FinRepo, "connection"))
        self.payroll = self.payroll_service(PAYROLL_OWNER)

    def payroll_service(self, permissions):
        base = self.make(permissions)
        finance = FinancialAccountService(base._session, transaction_factory=base._transaction, repository=FinRepo, authorization=base._authorization)
        return PayrollService(base._session, transaction_factory=base._transaction, repository=PayrollRepo, authorization=base._authorization, finance=finance, today=lambda: date(2026, 11, 8))

    def pay(self, deduction="0", account="CASH:1", key=None, month="2026-11"):
        return self.payroll.pay("1", month, account, deduction, "", key or str(uuid4()))

    def assertPayrollRefused(self, code, call):
        before = self.store.tables()
        with self.assertRaises((PayrollError, CorrectionError)) as context:
            call()
        self.assertEqual(context.exception.code, code)
        self.assertEqual(self.store.tables(), before)


class PayrollPayTests(PayrollCase):
    def test_the_net_leaves_the_account_once_and_the_slip_is_tied_to_that_operation(self):
        self.payroll.set_salary("1", "5000", "2026-11")
        before = self.cash()
        result = self.pay()
        self.assertEqual((self.cash(), result["amount"]), (before - D("5000"), "5000.00"))
        self.assertEqual(self.store.ops["1"]["category"], "SALARY")
        slip = self.store.slips_list[0]
        self.assertEqual((slip["operation_id"], slip["net"], slip["month"]), ("1", D("5000.00"), date(2026, 11, 1)))

    def test_bonuses_and_penalties_of_the_month_are_settled_by_the_slip(self):
        self.payroll.set_salary("1", "5000", "2026-11")
        self.payroll.add_adjustment("1", "2026-11", "BONUS", "700", "حافز مبيعات")
        self.payroll.add_adjustment("1", "2026-11", "PENALTY", "200", "تأخير")
        row = self.payroll.overview("2026-11")["employees"][0]
        self.assertEqual((row["base"], row["bonuses"], row["penalties"], row["net_preview"]), ("5000.00", "700.00", "200.00", "5500.00"))
        before = self.cash()
        self.pay()
        self.assertEqual(self.cash(), before - D("5500"))
        states = {a["kind"]: a["state"] for a in self.payroll.overview("2026-11")["adjustments"]}
        self.assertEqual(states, {"BONUS": "PAID", "PENALTY": "PAID"})
        self.assertPayrollRefused("ALREADY_PAID", lambda: self.payroll.add_adjustment("1", "2026-11", "BONUS", "10", "متأخر"))

    def test_the_advance_is_deducted_oldest_first_and_comes_back_when_the_salary_is_reversed(self):
        self.payroll.set_salary("1", "5000", "2026-11")
        self.fin.open_advance("CASH:1", "EMPLOYEE", "1", "1000", "", str(uuid4()))
        self.fin.open_advance("CASH:1", "EMPLOYEE", "1", "500", "", str(uuid4()))
        self.assertEqual(self.payroll.overview("2026-11")["employees"][0]["owed"], "1500.00")
        before = self.cash()
        self.pay("1200")
        self.assertEqual(self.cash(), before - D("3800"))                                                         # 5000 - 1200 in cash
        owed = {a["id"]: a["owed"] for a in self.fin.advances()["advances"]}
        self.assertEqual(owed, {"1": "0.00", "2": "300.00"})
        self.service.reverse(FO, "3", "صرف بالخطأ")                                                            # the salary operation
        owed = {a["id"]: a["owed"] for a in self.fin.advances()["advances"]}
        self.assertEqual(owed, {"1": "1000.00", "2": "500.00"})
        self.assertEqual(self.payroll.overview("2026-11")["employees"][0]["status"], "READY")                     # the slip is void: payable again
        self.pay()
        self.assertEqual(len(self.store.slips_list), 2)

    def test_one_slip_per_month_and_a_retry_with_the_same_key_posts_once(self):
        self.payroll.set_salary("1", "3000", "2026-11")
        key = str(uuid4())
        self.pay(key=key)
        again = self.pay(key=key)
        self.assertTrue(again["duplicate"])
        self.assertEqual((len(self.store.ops), len(self.store.slips_list)), (1, 1))
        self.assertPayrollRefused("ALREADY_PAID", lambda: self.pay())

    def test_refusals_leave_nothing_behind(self):
        self.assertPayrollRefused("NO_SALARY", lambda: self.pay())
        self.payroll.set_salary("1", "1000", "2026-11")
        self.payroll.add_adjustment("1", "2026-11", "PENALTY", "1000", "جزاء كبير")
        self.assertPayrollRefused("NET_NOT_POSITIVE", lambda: self.pay())
        self.fin.open_advance("CASH:1", "EMPLOYEE", "1", "100", "", str(uuid4()))
        self.assertPayrollRefused("DEDUCTION_TOO_HIGH", lambda: self.pay("100.01"))
        self.assertPayrollRefused("MONTH_FUTURE", lambda: self.pay(month="2027-01"))
        self.assertPayrollRefused("ACCOUNT_REQUIRED", lambda: self.pay(account="GOLD:1"))

    def test_only_the_owner_with_the_payroll_right_may_use_it(self):
        for permissions in (OWNER, MANAGER):
            service = self.payroll_service(frozenset(permissions))
            with self.assertRaises(PermissionError):
                service.set_salary("1", "100", "2026-11")
            with self.assertRaises(PermissionError):
                service.overview("2026-11")
        self.assertEqual(self.store.rates_list, [])

    def test_a_bonus_not_yet_paid_can_be_voided_with_a_reason_and_is_then_ignored(self):
        self.payroll.set_salary("1", "2000", "2026-11")
        self.payroll.add_adjustment("1", "2026-11", "BONUS", "300", "حافز")
        adjustment = self.payroll.overview("2026-11")["adjustments"][0]["id"]
        self.assertPayrollRefused("REASON_REQUIRED", lambda: self.payroll.void_adjustment(adjustment, " "))
        self.payroll.void_adjustment(adjustment, "اتسجل بالغلط")
        self.assertEqual(self.payroll.overview("2026-11")["employees"][0]["bonuses"], "0.00")
        before = self.cash()
        self.pay()
        self.assertEqual(self.cash(), before - D("2000"))
        self.assertPayrollRefused("ALREADY_VOIDED", lambda: self.payroll.void_adjustment(adjustment, "تاني"))

    def test_a_salary_rate_holds_for_the_later_months_until_changed(self):
        self.payroll.set_salary("1", "4000", "2026-09")
        self.assertEqual(self.payroll.overview("2026-11")["employees"][0]["base"], "4000.00")
        self.payroll.set_salary("1", "4500", "2026-11")
        self.assertEqual((self.payroll.overview("2026-10")["employees"][0]["base"], self.payroll.overview("2026-11")["employees"][0]["base"]), ("4000.00", "4500.00"))


if __name__ == "__main__":
    unittest.main()
