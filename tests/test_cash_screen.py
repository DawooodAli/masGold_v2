"""The daily cash screen (prototype cash_flow_page UX on the V2 ledgers): categorised cash movements, bank
deposit / withdrawal, the two-sided cash view and its central Arabic labels. No application database."""
import inspect
import re
from datetime import datetime, timedelta
from decimal import Decimal as D
from uuid import uuid4

from repositories.financial_account_repository import FinancialAccountRepository as RealRepository
from services.correction_service import FO
from services.financial_account_service import CASH_LABELS, CASH_TYPES
from tests.test_financial_accounts import FinRepo, FinancialTestCase, SALESPERSON


def cash_row(i, delta, label_key, side, **extra):
    return {"id": i, "amount_delta": D(delta), "label_key": label_key, "invoice_id": extra.get("invoice_id"),
            "operation_id": extra.get("operation_id"), "source_reference": "RV000058", "movement_code": "X",
            "side_total": D(side[0]), "side_count": side[1]}


class CashMovementTests(FinancialTestCase):
    def move(self, direction, kind, amount="100", note="", bank=""):
        return self.fin.cash_movement("CASH:1", direction, kind, amount, note, str(uuid4()), bank)

    def test_expense_salary_rent_are_cash_out_with_optional_note(self):
        before = self.cash()
        for kind in ("EXPENSE", "SALARY", "RENT"):
            self.move("OUT", kind)
        self.assertEqual(self.cash(), before - D("300"))
        ops = list(self.store.ops.values())
        self.assertEqual([o["category"] for o in ops], ["EXPENSE", "SALARY", "RENT"])
        self.assertEqual({o["operation_type"] for o in ops}, {"MANUAL_WITHDRAWAL"})
        self.assertEqual({o["reason"] for o in ops}, {None})                      # note optional
        self.assertEqual(self.store.op_legs[0]["description"], "مصروفات")

    def test_revenue_in_and_repair_either_way(self):
        before = self.cash()
        self.move("IN", "REVENUE", "2000", "إيراد صيانة")
        self.move("IN", "REPAIR", "600")
        self.move("OUT", "REPAIR", "250")
        self.assertEqual(self.cash(), before + D("2350"))
        self.assertEqual(self.store.op_legs[0]["description"], "إيراد — إيراد صيانة")

    def test_direction_must_match_the_type(self):
        for direction, kind in (("IN", "EXPENSE"), ("OUT", "REVENUE"), ("IN", "BANK_DEPOSIT"),
                                ("OUT", "BANK_WITHDRAWAL"), ("IN", "CUSTOMER_PAYMENT"), ("IN", "ARBOUN")):
            self.assertFinRefused("TYPE_INVALID", lambda d=direction, k=kind: self.move(d, k))

    def test_bank_deposit_and_withdrawal_are_atomic_transfers(self):
        cash, bank = self.cash(), self.bank(7)
        self.move("OUT", "BANK_DEPOSIT", "20000", bank="BANK:7")
        self.assertEqual((self.cash(), self.bank(7)), (cash - D("20000"), bank + D("20000")))
        self.move("IN", "BANK_WITHDRAWAL", "5000", bank="BANK:8")
        self.assertEqual((self.cash(), self.bank(8)), (cash - D("15000"), D("-5000.00")))
        self.assertEqual([o["operation_type"] for o in self.store.ops.values()], ["ACCOUNT_TRANSFER"] * 2)
        self.assertEqual([(l["leg_role"], l["type_code"]) for l in self.store.op_legs][:2],
                         [("OUT", "ACCOUNT_TRANSFER_OUT"), ("IN", "ACCOUNT_TRANSFER_IN")])
        self.assertFinRefused("ACCOUNT_NOT_FOUND", lambda: self.move("OUT", "BANK_DEPOSIT", bank="CASH:1"))
        self.assertFinRefused("ACCOUNT_NOT_FOUND", lambda: self.move("OUT", "BANK_DEPOSIT", bank=""))
        self.assertFinRefused("ACCOUNT_NOT_FOUND",
                              lambda: self.fin.cash_movement("BANK:7", "OUT", "EXPENSE", "1", "", str(uuid4())))

    def test_categorised_movement_is_idempotent_and_reversible(self):
        key = str(uuid4())
        self.fin.cash_movement("CASH:1", "OUT", "SALARY", "12000", "", key)
        self.fin.cash_movement("CASH:1", "OUT", "SALARY", "12000", "", key)
        self.assertEqual(len(self.store.ops), 1)
        before = self.cash()
        self.service.reverse(FO, "1", "تسجيل مرتب بالخطأ")
        self.assertEqual(self.cash(), before + D("12000"))
        self.assertEqual(self.fin.operation("1")["type_text"], "مرتبات")
        self.assertEqual(self.store.audit[0], ("MANUAL_WITHDRAWAL", "FO000001"))      # audit persisted

    def test_no_second_customer_settlement_or_deposit_engine(self):
        self.assertEqual(set(CASH_TYPES), {"EXPENSE", "SALARY", "RENT", "REVENUE", "REPAIR", "BANK_DEPOSIT",
                                           "BANK_WITHDRAWAL"})
        for name in ("settle", "customer", "arboun", "advance"):
            self.assertFalse(any(name in attr.lower() for attr in dir(self.fin)), name)

    def test_permissions(self):
        for permissions in (SALESPERSON, frozenset({"APP_LOGIN", "PAYMENT_RECORD", "CASH_VIEW"})):
            with self.assertRaises(PermissionError):
                self.fin_service(permissions).cash_movement("CASH:1", "OUT", "EXPENSE", "1", "", str(uuid4()))
        self.assertEqual(self.store.ops, {})


class CashViewTests(FinancialTestCase):
    def test_two_sides_business_labels_and_exact_totals(self):
        self.store.cash_rows = [
            cash_row(5, "25000", "SALE", ("32000", 3), invoice_id=21),
            cash_row(4, "-15000", "OLD_GOLD_PURCHASE", ("-36600", 3)),
            cash_row(3, "5000", "CUSTOMER_PAYMENT", ("32000", 3)),
            cash_row(2, "-1500", "EXPENSE", ("-36600", 3), operation_id=9),
            cash_row(1, "2000", "TRANSFER_IN", ("32000", 3)),
            cash_row(0, "-20100", "CORRECTION", ("-36600", 3))]
        view = self.fin.cash_view()
        self.assertEqual([r["label"] for r in view["in"]["rows"]], ["بيع", "سداد عميل", "سحب بنك"])
        self.assertEqual([r["label"] for r in view["out"]["rows"]], ["شراء كسر", "مصروفات", "تصحيح تسوية"])
        self.assertEqual([r["amount"] for r in view["out"]["rows"]], ["15000.00", "1500.00", "20100.00"])
        self.assertEqual((view["in"]["total"], view["out"]["total"]), ("32000.00", "36600.00"))
        self.assertEqual(view["in"]["rows"][0]["invoice_id"], "21")
        for row in view["in"]["rows"] + view["out"]["rows"]:
            self.assertEqual(set(row), {"id", "label", "amount", "invoice_id", "operation_id"})   # no raw codes
        self.assertEqual(D(view["account"]["balance"]), self.cash())                             # authoritative
        self.assertEqual(view["account"]["key"], "CASH:1")
        self.assertNotIn("accounts", view)                                                      # no bank cards

    def test_default_period_is_today_and_filter_is_validated(self):
        view = self.fin.cash_view()
        today = self.store.now.date()
        self.assertEqual((view["start"], view["end"]), (today.isoformat(), today.isoformat()))
        call = FinRepo.calls[-1]
        midnight = datetime.combine(today, datetime.min.time())
        self.assertEqual((call[3], call[4]), (midnight, midnight + timedelta(days=1)))
        self.fin.cash_view("2026-09-01", "2026-09-30", "EXPENSE")
        self.assertEqual(FinRepo.calls[-1][5], "EXPENSE")
        for bad in (("", "", "RETURN_PAYMENT_OUT"), ("2026-10-01", "2026-09-01", "")):
            with self.assertRaises(ValueError):
                self.fin.cash_view(*bad)

    def test_bank_only_viewer_cannot_open_the_cash_screen(self):
        bank_only = self.fin_service(frozenset({"APP_LOGIN", "BANK_VIEW"}))
        with self.assertRaises(PermissionError):
            bank_only.cash_view()
        self.assertFalse(bank_only.can_view())
        viewer = self.fin_service(frozenset({"APP_LOGIN", "CASH_VIEW"})).cash_view()
        self.assertEqual((viewer["bank_accounts"], viewer["can_operate"]), ([], False))      # no bank names

    def test_every_read_model_label_key_has_an_arabic_name(self):
        sql = inspect.getsource(RealRepository._resolved)
        block = sql[sql.index("END AS source_type"):sql.index("END AS label_key")]
        keys = set(re.findall(r"'([A-Z_]+)'", block)) - {"FULL_REVERSAL", "ACCOUNT_TRANSFER"}
        keys |= {"MANUAL_DEPOSIT", "MANUAL_WITHDRAWAL", "EXPENSE", "SALARY", "RENT", "REVENUE", "REPAIR"}
        self.assertLessEqual(keys, set(CASH_LABELS))
        for text in CASH_LABELS.values():
            self.assertIsNone(re.search(r"[A-Z_]{4,}|\d{6}", text))                             # Arabic names only
