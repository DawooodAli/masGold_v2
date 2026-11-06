"""The daily cash screen (prototype cash_flow_page UX on the V2 ledgers): categorised cash movements, bank
deposit / withdrawal, the two-sided cash view and its central Arabic labels. No application database."""
import inspect
import re
from datetime import datetime, timedelta
from decimal import Decimal as D
from uuid import uuid4

from repositories.financial_account_repository import FinancialAccountRepository as RealRepository
from services.correction_service import FO, CorrectionError
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

    def test_the_new_expense_categories_are_cash_out_only_and_named_in_arabic(self):
        before = self.cash()
        names = {"UTILITIES": "كهرباء ومياه", "TRANSPORT": "نقل", "MAINTENANCE": "صيانة", "HOSPITALITY": "بوفيه وضيافة"}
        for kind in names:
            self.move("OUT", kind)
        self.assertEqual(self.cash(), before - D("400"))
        self.assertEqual([o["category"] for o in self.store.ops.values()], list(names))
        self.assertEqual([leg["description"] for leg in self.store.op_legs], list(names.values()))
        for kind in names:
            with self.assertRaises(CorrectionError):
                self.move("IN", kind)                                                   # an expense category is never income

    def test_an_advance_category_needs_its_owner_so_the_plain_movement_refuses_it(self):
        for kind in ("STAFF_ADVANCE", "CUSTOMER_CUSTODY", "ADVANCE_REPAY"):
            direction = "IN" if kind == "ADVANCE_REPAY" else "OUT"
            self.assertFinRefused("USE_ADVANCE", lambda kind=kind, direction=direction: self.move(direction, kind))
        self.assertEqual(len(self.store.ops), 0)

    def test_no_second_customer_settlement_or_deposit_engine(self):
        self.assertEqual(set(CASH_TYPES), {"BANK_DEPOSIT", "BANK_WITHDRAWAL"})                     # the income / expense categories live in the cash_categories table (069)
        for name in ("settle", "arboun"):       # customer cash in / out (086) is its own flow over the same operations
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
            # Stored identification only (reference, invoice, corrected transaction, customer) — never a raw code.
            self.assertEqual(set(row), {"id", "label", "amount", "invoice_id", "operation_id", "reference",
                                        "invoice_number", "corrected_reference", "customer", "customer_id", "kind", "detail",
                                        "external", "account", "linked_invoices"})
        self.assertEqual(D(view["account"]["balance"]), self.cash())                             # authoritative
        self.assertEqual(view["account"]["key"], "CASH:1")
        self.assertNotIn("accounts", view)                                                      # no bank cards

    def test_a_sale_paid_in_part_shows_its_whole_value_in_and_the_rest_out_on_the_customer(self):
        self.store.cash_rows = [cash_row(5, "30000", "SALE", ("30000", 1), invoice_id=21)]
        self.store.invoice_debts = {21: D("1600")}
        view = self.fin.cash_view()
        self.assertEqual([(r["label"], r["amount"]) for r in view["in"]["rows"]], [("بيع", "31600.00")])
        self.assertEqual([(r["label"], r["amount"], r["invoice_id"]) for r in view["out"]["rows"]], [("على العميل", "1600.00", "21")])
        self.assertEqual((view["in"]["total"], view["out"]["total"], view["in"]["count"], view["out"]["count"]), ("31600.00", "1600.00", 1, 1))
        self.assertEqual(D(view["account"]["balance"]), self.cash())                             # the real balance is untouched

    def test_an_exchange_shows_the_new_value_in_the_old_value_refunded_out_and_the_rest_on_the_customer(self):
        row = cash_row(6, "20000", "EXCHANGE", ("20000", 1), invoice_id=21)
        row["reference_id"] = 902
        self.store.cash_rows = [row]
        self.store.exchange_debts = {902: (D("100000"), D("5000"))}      # the old pieces were sold for 100,000; 5,000 stayed on the customer
        view = self.fin.cash_view()
        self.assertEqual([r["amount"] for r in view["in"]["rows"]], ["125000.00"])
        self.assertEqual(sorted((r["label"], r["amount"]) for r in view["out"]["rows"]), [("استرداد فاتورة قديمة", "100000.00"), ("على العميل", "5000.00")])
        self.assertEqual((view["in"]["total"], view["out"]["total"], view["out"]["count"]), ("125000.00", "105000.00", 2))   # 125,000 - 105,000 = the 20,000 really paid

    def test_a_later_wallet_payment_of_the_customer_is_shown_in_and_out_so_the_safe_nets_to_zero(self):
        self.store.cash_rows = []
        self.store.bank_rows = [cash_row(9, "1600", "CUSTOMER_PAYMENT", ("1600", 1))]
        for n, account in enumerate(self.store.bank_accounts.values()):
            account["is_active"] = n == 0
        view = self.fin.cash_view()
        # Owner 2026-11-04: a payment received in a wallet is written only in the outgoing table
        self.assertEqual([r["amount"] for r in view["in"]["rows"]], [])
        self.assertEqual([(r["label"], r["amount"]) for r in view["out"]["rows"]], [("انستا", "1600.00")])
        self.assertEqual((view["in"]["total"], view["out"]["total"]), ("0.00", "1600.00"))

    def test_an_exchange_settled_separately_still_shows_its_values_without_touching_the_safe(self):
        self.store.cash_rows = []
        self.store.no_money_exchanges = [{"id": 2, "invoice_id": 21, "invoice_number": "26110301", "occurred_at": datetime(2026, 11, 3, 12),
                                          "account_signed": D("15005"), "signed_amount": D("15005"), "customer_name": "الهام فتوح", "customer_id": 7,
                                          "old_value": D("127000")}]
        view = self.fin.cash_view()
        self.assertEqual([(r["label"], r["amount"]) for r in view["in"]["rows"]], [("استبدال", "142005.00")])
        self.assertEqual(sorted(r["amount"] for r in view["out"]["rows"]), ["127000.00", "15005.00"])
        self.assertEqual((view["in"]["total"], view["out"]["total"], view["in"]["count"], view["out"]["count"]), ("142005.00", "142005.00", 1, 2))

    def test_an_invoice_sold_wholly_on_account_shows_its_full_value_in_and_on_the_customer_out(self):
        self.store.cash_rows = []
        self.store.no_money_invoices = [{"invoice_id": 31, "invoice_number": "26110503", "occurred_at": datetime(2026, 11, 5, 16, 18), "customer_name": "ام محمد عادل",
                                         "customer_id": 9, "debt": D("48000"), "has_buy": 0}]
        view = self.fin.cash_view()
        self.assertEqual([(r["label"], r["amount"], r["invoice_id"]) for r in view["in"]["rows"]], [("بيع", "48000.00", "31")])
        self.assertEqual([(r["label"], r["amount"]) for r in view["out"]["rows"]], [("على العميل", "48000.00")])
        self.assertEqual((view["in"]["total"], view["out"]["total"]), ("48000.00", "48000.00"))

    def test_a_customer_payment_names_the_invoice_it_settles(self):
        row = cash_row(8, "1600", "CUSTOMER_PAYMENT", ("1600", 1))
        row["reference_type"], row["reference_id"] = "CUSTOMER_ACCOUNT_SETTLEMENT", 3
        self.store.cash_rows = [row]
        self.store.payment_links = {("CUSTOMER_ACCOUNT_SETTLEMENT", 3): ["26110313", "26110309"]}
        view = self.fin.cash_view()
        self.assertEqual(view["in"]["rows"][0]["linked_invoices"], "26110313 ، 26110309")

    def test_a_sale_paid_by_wallet_shows_the_invoice_in_and_the_wallet_out(self):
        self.store.cash_rows = []
        self.store.bank_rows = [cash_row(9, "53700", "SALE", ("53700", 1), invoice_id=21)]
        for n, account in enumerate(self.store.bank_accounts.values()):
            account["is_active"] = n == 0
        view = self.fin.cash_view()
        self.assertEqual([r["amount"] for r in view["in"]["rows"]], ["53700.00"])
        self.assertEqual([(r["label"], r["amount"], r["invoice_id"]) for r in view["out"]["rows"]], [("انستا", "53700.00", "21")])
        self.assertEqual((view["in"]["total"], view["out"]["total"]), ("53700.00", "53700.00"))

    def test_cash_plus_wallet_plus_rest_is_one_incoming_row_and_nets_to_the_cash_only(self):
        self.store.cash_rows = [cash_row(5, "20000", "SALE", ("20000", 1), invoice_id=21)]
        self.store.bank_rows = [cash_row(9, "20000", "SALE", ("20000", 1), invoice_id=21)]
        for n, account in enumerate(self.store.bank_accounts.values()):
            account["is_active"] = n == 0
        self.store.invoice_debts = {21: D("13700")}
        view = self.fin.cash_view()
        self.assertEqual([r["amount"] for r in view["in"]["rows"]], ["53700.00"])
        self.assertEqual(sorted((r["label"], r["amount"]) for r in view["out"]["rows"]), [("انستا", "20000.00"), ("على العميل", "13700.00")])
        self.assertEqual((view["in"]["total"], view["out"]["total"]), ("53700.00", "33700.00"))       # the difference, 20,000, is the cash really received

    def test_a_correction_from_cash_to_wallet_shows_the_wallet_out_as_if_never_edited(self):
        original = cash_row(5, "53700", "SALE", ("53700", 2), invoice_id=21)
        fc_out = cash_row(6, "-53700", "CORRECTION", ("-53700", 1), invoice_id=21)
        fc_in = cash_row(9, "53700", "CORRECTION", ("53700", 1), invoice_id=21)
        fc_out["source_reference"] = fc_in["source_reference"] = "FC000023"
        self.store.cash_rows = [original, fc_out]
        self.store.bank_rows = [fc_in]
        for n, account in enumerate(self.store.bank_accounts.values()):
            account["is_active"] = n == 0
        view = self.fin.cash_view()
        self.assertEqual([(r["label"], r["amount"]) for r in view["in"]["rows"]], [("بيع", "53700.00")])
        self.assertEqual([(r["label"], r["amount"]) for r in view["out"]["rows"]], [("انستا", "53700.00")])
        self.assertEqual((view["in"]["total"], view["out"]["total"], view["out"]["count"]), ("53700.00", "53700.00", 1))

    def test_a_correction_from_wallet_to_cash_cancels_the_wallet_out(self):
        fc_in = cash_row(6, "53700", "CORRECTION", ("53700", 1), invoice_id=21)
        fc_out = cash_row(10, "-53700", "CORRECTION", ("-53700", 1), invoice_id=21)
        fc_in["source_reference"] = fc_out["source_reference"] = "FC000030"
        original = cash_row(9, "53700", "SALE", ("53700", 1), invoice_id=21)
        self.store.cash_rows = [fc_in]
        self.store.bank_rows = [fc_out, original]
        for n, account in enumerate(self.store.bank_accounts.values()):
            account["is_active"] = n == 0
        view = self.fin.cash_view()
        self.assertEqual([(r["label"], r["amount"]) for r in view["in"]["rows"]], [("بيع", "53700.00")])
        self.assertEqual(view["out"]["rows"], [])
        self.assertEqual((view["in"]["total"], view["out"]["total"]), ("53700.00", "0.00"))        # the 53,700 really is cash now

    def test_a_correction_that_leaves_part_on_the_customer_shows_it_as_on_the_customer(self):
        original = cash_row(5, "53700", "SALE", ("53700", 1), invoice_id=21)
        fc = cash_row(6, "-13700", "CORRECTION", ("-13700", 1), invoice_id=21)
        fc["reference_type"], fc["reference_id"], fc["source_reference"] = "TRANSACTION_CORRECTION", 30, "FC000030"
        self.store.cash_rows = [original, fc]
        self.store.correction_debts = {30: D("13700")}
        view = self.fin.cash_view()
        self.assertEqual([(r["label"], r["amount"]) for r in view["in"]["rows"]], [("بيع", "53700.00")])
        self.assertEqual([(r["label"], r["amount"]) for r in view["out"]["rows"]], [("على العميل", "13700.00")])
        self.assertEqual((view["in"]["total"], view["out"]["total"]), ("53700.00", "13700.00"))

    def test_a_correction_that_pays_what_stayed_on_the_customer_in_cash_removes_the_on_customer_row(self):
        original = cash_row(5, "40000", "SALE", ("53700", 2), invoice_id=21)
        fc = cash_row(6, "13700", "CORRECTION", ("53700", 2), invoice_id=21)
        fc["reference_type"], fc["reference_id"], fc["source_reference"] = "TRANSACTION_CORRECTION", 31, "FC000031"
        self.store.cash_rows = [original, fc]
        self.store.invoice_debts = {21: D("13700")}
        self.store.correction_credits = {31: D("13700")}
        view = self.fin.cash_view()
        self.assertEqual([(r["label"], r["amount"]) for r in view["in"]["rows"]], [("بيع", "53700.00")])
        self.assertEqual(view["out"]["rows"], [])
        self.assertEqual((view["in"]["total"], view["out"]["total"]), ("53700.00", "0.00"))

    def reversal_row(self, i, delta, ref=40):
        row = cash_row(i, delta, "REVERSAL", (delta, 1), invoice_id=21)
        row["reference_type"], row["reference_id"], row["source_reference"] = "TRANSACTION_CORRECTION", ref, "RV000040"
        row["corrected_source_type"] = "INVOICE"
        return row

    def test_cancelling_a_whole_sale_shows_the_full_refund_out_and_what_was_owed_in(self):
        self.store.cash_rows = [self.reversal_row(7, "-30000")]
        self.store.correction_credits = {40: D("10000")}            # 40,000 invoice: 30,000 paid, 10,000 was still on the customer
        view = self.fin.cash_view()
        self.assertEqual([(r["label"], r["amount"]) for r in view["out"]["rows"]], [("استرداد فاتورة (إلغاء بيع)", "40000.00")])
        self.assertEqual([(r["label"], r["amount"]) for r in view["in"]["rows"]], [("ما كان على العميل (اتلغى)", "10000.00")])
        self.assertEqual((view["in"]["total"], view["out"]["total"], view["in"]["count"], view["out"]["count"]), ("10000.00", "40000.00", 1, 1))

    def test_cancelling_a_sale_refunded_by_wallet_shows_the_transfer_in_and_nets_to_the_cash_only(self):
        self.store.cash_rows = [self.reversal_row(7, "-10000")]
        self.store.bank_rows = [self.reversal_row(9, "-20000")]
        for n, account in enumerate(self.store.bank_accounts.values()):
            account["is_active"] = n == 0
        self.store.correction_credits = {40: D("10000")}
        view = self.fin.cash_view()
        self.assertEqual([r["amount"] for r in view["out"]["rows"]], ["40000.00"])
        self.assertEqual(sorted(r["amount"] for r in view["in"]["rows"]), ["10000.00", "20000.00"])
        self.assertEqual((view["in"]["total"], view["out"]["total"]), ("30000.00", "40000.00"))      # 10,000 really left the safe

    def test_cancelling_a_fully_paid_sale_is_just_the_refund(self):
        self.store.cash_rows = [self.reversal_row(7, "-40000")]
        view = self.fin.cash_view()
        self.assertEqual([r["amount"] for r in view["out"]["rows"]], ["40000.00"])
        self.assertEqual(view["in"]["rows"], [])
        self.assertEqual((view["in"]["total"], view["out"]["total"]), ("0.00", "40000.00"))

    def lower_exchange(self, i, delta):
        row = cash_row(i, delta, "EXCHANGE", (delta, 1), invoice_id=21)
        row["reference_type"], row["reference_id"], row["source_reference"] = "INVOICE_LIFECYCLE", 902, "EX000902"
        return row

    def test_an_exchange_to_a_lower_invoice_paid_back_in_cash_shows_the_whole_old_refund_out(self):
        self.store.cash_rows = [self.lower_exchange(7, "-10000")]
        self.store.exchange_debts = {902: (D("40000"), D("0"))}      # old pieces 40,000, new invoice 30,000, 10,000 paid back in cash
        view = self.fin.cash_view()
        self.assertEqual([(r["label"], r["amount"]) for r in view["out"]["rows"]], [("استرداد فاتورة قديمة", "40000.00")])
        self.assertEqual([(r["label"], r["amount"]) for r in view["in"]["rows"]], [("استبدال (الفاتورة الجديدة)", "30000.00")])
        self.assertEqual((view["in"]["total"], view["out"]["total"]), ("30000.00", "40000.00"))        # 10,000 really left the safe

    def test_an_exchange_to_a_lower_invoice_paid_back_by_wallet_shows_the_transfer_in(self):
        self.store.cash_rows = []
        self.store.bank_rows = [self.lower_exchange(9, "-10000")]
        for n, account in enumerate(self.store.bank_accounts.values()):
            account["is_active"] = n == 0
        self.store.exchange_debts = {902: (D("40000"), D("0"))}
        view = self.fin.cash_view()
        self.assertEqual([r["amount"] for r in view["out"]["rows"]], ["40000.00"])
        self.assertEqual(sorted((r["label"], r["amount"]) for r in view["in"]["rows"]), [("استبدال (الفاتورة الجديدة)", "30000.00"), ("تحويل لعميل فرق فاتورة", "10000.00")])
        self.assertEqual((view["in"]["total"], view["out"]["total"]), ("40000.00", "40000.00"))        # nothing left the safe

    def test_a_split_cash_and_wallet_payment_shows_the_rest_once(self):
        self.store.cash_rows = [cash_row(5, "20000", "SALE", ("20000", 1), invoice_id=21)]
        self.store.bank_rows = [cash_row(9, "10000", "SALE", ("10000", 1), invoice_id=21)]
        for n, account in enumerate(self.store.bank_accounts.values()):
            account["is_active"] = n == 0
        self.store.invoice_debts = {21: D("1600")}
        view = self.fin.cash_view()
        self.assertEqual([r["amount"] for r in view["in"]["rows"]], ["31600.00"])      # ONE incoming row: the whole invoice
        self.assertEqual(sorted(r["amount"] for r in view["out"]["rows"]), ["10000.00", "1600.00"])      # the wallet leg + what stayed on the customer

    def test_bank_movements_are_listed_with_their_account_but_never_added_to_the_safe_totals(self):
        self.store.cash_rows = [cash_row(5, "25000", "SALE", ("25000", 1))]
        self.store.bank_rows = [cash_row(9, "700", "CUSTOMER_CASH_IN", ("700", 1), operation_id=4),
                                cash_row(8, "-300", "REPAIR", ("-300", 1)), cash_row(7, "50", "TRANSFER_IN", ("50", 1))]
        for n, account in enumerate(self.store.bank_accounts.values()):          # one active account (the fake serves the same rows to each)
            account["is_active"] = n == 0
        next(iter(self.store.bank_accounts.values()))["name"] = "بنك مصر"
        self.store.cash_rows[0]["occurred_at"] = datetime(2026, 10, 28, 12)
        self.store.bank_rows[0]["occurred_at"] = datetime(2026, 10, 28, 11)    # the older one
        view = self.fin.cash_view()
        self.assertEqual([(r["external"], r["id"]) for r in view["in"]["rows"]], [(False, "5")])     # the wallet deposit is written in the outgoing table only
        self.assertEqual((view["in"]["total"], view["in"]["bank_total"], view["out"]["total"], view["out"]["bank_total"]),
                         ("25000.00", "0.00", "700.00", "300.00"))                                  # the internal transfer row is not listed

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
        keys = set(re.findall(r"'([A-Z_]+)'", block)) - {"FULL_REVERSAL", "ACCOUNT_TRANSFER", "BUY_OLD_GOLD", "SELL_INVENTORY", "SELL_UNIDENTIFIED", "SELL_OLD_GOLD", "SELL_RAFAYEA"}      # the last four are invoice line types inside the MIXED subquery
        keys |= {"MANUAL_DEPOSIT", "MANUAL_WITHDRAWAL", "EXPENSE", "SALARY", "RENT", "REVENUE", "REPAIR"}
        self.assertLessEqual(keys, set(CASH_LABELS))
        for text in CASH_LABELS.values():
            self.assertIsNone(re.search(r"[A-Z_]{4,}|\d{6}", text))                             # Arabic names only
