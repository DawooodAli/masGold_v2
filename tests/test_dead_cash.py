"""Dead Cash V1 — receivable write-off, warning, history and full reversal, against a transactional
in-memory store (no application database). Real-MySQL proofs live in test_dead_cash_mysql.py."""
import copy
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal as D
from uuid import uuid4
import unittest

from app.session import AppSession
from services.authentication_service import AuthenticationError, AuthenticationResult, GENERIC_ERROR, _VERIFIED
from services.dead_cash_service import DeadCashError, DeadCashService, reversal_code, writeoff_code
from tests.auth_fakes import FakeAuthorization

OWNER = frozenset({"APP_LOGIN", "CUSTOMERS_VIEW", "DEAD_CASH_MANAGE", "DEAD_CASH_REVERSE", "DEAD_CASH_VIEW"})
CASHIER = frozenset({"APP_LOGIN", "CUSTOMER_ACCOUNT_SETTLE", "CUSTOMERS_VIEW"})
PASSWORD = "correct horse"


class Store:
    def __init__(self):
        self.customers = {"12": {"customer_id": 12, "name": "أحمد", "is_active": 1}}
        self.ledger = [("12", D("15000.00"), "SALE_BALANCE_DUE", "INVOICE", "500"),
                       ("12", D("8000.00"), "SALE_BALANCE_DUE", "INVOICE", "501")]     # على العميل 23,000
        self.invoices = {"500": {"net": D("40000")}, "501": {"net": D("30000")}}
        self.money = [("CASH", 1, D("100000.00")), ("BANK", 7, D("5000.00"))]       # must never change
        self.writeoffs, self.flags, self.audit = {}, [], []

    def balance(self, customer="12"):
        return sum((r[1] for r in self.ledger if r[0] == customer), D("0.00"))

    def tables(self):
        return copy.deepcopy(vars(self))


class Conn:
    def __init__(self, store):
        self.store = store


class Balances:
    def __init__(self, connection):
        self.store = connection.store

    def lock_balance(self, customer_id):
        if str(customer_id) not in self.store.customers:
            raise ValueError("العميل غير موجود.")
        return self.store.balance(str(customer_id))


class Customers:
    fail_on = None

    def __init__(self, connection):
        self.store = connection.store

    def get_customer_by_id(self, customer_id):
        return self.store.customers.get(str(customer_id))

    def get_balance(self, customer_id):
        return self.store.balance(str(customer_id))

    def add_transaction(self, *, customer_id, type_code, amount_delta, reference_type=None, reference_id=None,
                        reference_code=None, **kw):
        assert (type_code == "DEAD_CASH_WRITE_OFF" and amount_delta < 0) or \
            (type_code == "DEAD_CASH_REVERSAL" and amount_delta > 0)              # ledger type direction
        self.store.ledger.append((str(customer_id), amount_delta, type_code, reference_type, reference_code))
        if Customers.fail_on == "ledger":
            raise RuntimeError("ledger")
        return str(900 + len(self.store.ledger))


class DeadCashRepo:
    fail_on = None

    def __init__(self, connection):
        self.store = connection.store

    def _fail(self, step):
        if DeadCashRepo.fail_on == step:
            raise RuntimeError(step)

    def by_request(self, key):
        return next((dict(w) for w in self.store.writeoffs.values() if w["request_key"] == key), None)

    def insert_writeoff(self, *, customer_id, amount, balance_before, balance_after, writeoff_transaction_id,
                        reason, created_by_user_id, request_key=None, request_hash=None, **kw):
        # chk_dead_cash_v1_receivable / chk_dead_cash_reason (028)
        assert amount > 0 and balance_before > 0 and balance_after == balance_before - amount >= 0
        assert reason.strip()
        writeoff_id = str(len(self.store.writeoffs) + 1)
        self.store.writeoffs[writeoff_id] = {
            "writeoff_id": writeoff_id, "customer_id": str(customer_id), "amount": amount,
            "balance_before": balance_before, "balance_after": balance_after, "reason": reason,
            "writeoff_transaction_id": writeoff_transaction_id, "created_by_user_id": created_by_user_id,
            "request_key": request_key, "request_hash": request_hash, "is_reversed": False,
            "reversal_transaction_id": None, "reversal_reason": None}
        self._fail("header")
        return writeoff_id

    def get_writeoff(self, writeoff_id):
        w = self.store.writeoffs.get(str(writeoff_id))
        return None if w is None else dict(w)

    lock_writeoff = get_writeoff

    def mark_writeoff_reversed(self, *, writeoff_id, reversal_transaction_id, reversed_by_user_id, reason):
        w = self.store.writeoffs[str(writeoff_id)]
        assert not w["is_reversed"]
        w.update(is_reversed=True, reversal_transaction_id=reversal_transaction_id, reversal_reason=reason)

    def get_active_warning(self, customer_id, for_update=False):
        return next((f for f in self.store.flags if f["customer_id"] == str(customer_id) and f["active"]), None)

    def create_warning(self, *, customer_id, reason, created_by_user_id, related_dead_cash_id=None, **kw):
        assert self.get_active_warning(customer_id) is None                      # uq_customer_active_flag
        self.store.flags.append({"customer_id": str(customer_id), "active": True, "reason": reason,
                                 "related": related_dead_cash_id})
        self._fail("flag")

    def _execute(self, query, params):
        assert query.startswith("INSERT INTO audit_log")
        self.store.audit.append((params[1], params[4], params[6]))
        assert PASSWORD not in params[6]                                         # never the password
        self._fail("audit")
        return 1, 1


class Authentication:
    def __init__(self):
        self.calls = []

    def verify_user_password(self, user_id, password):
        self.calls.append(user_id)
        if password != PASSWORD:
            raise AuthenticationError(GENERIC_ERROR)
        return True


class DeadCashCase(unittest.TestCase):
    def setUp(self):
        DeadCashRepo.fail_on = Customers.fail_on = None
        self.addCleanup(setattr, DeadCashRepo, "fail_on", None)
        self.addCleanup(setattr, Customers, "fail_on", None)
        self.store = Store()
        self.service = self.make(OWNER)

    def make(self, permissions, database_permissions=None):
        session = AppSession()
        session._establish(AuthenticationResult(3, None, "المالك", permissions, _VERIFIED), int(session.generation))

        @contextmanager
        def transaction():
            saved = copy.deepcopy(vars(self.store))
            try:
                yield Conn(self.store)
            except BaseException:
                self.store.__dict__.clear()
                self.store.__dict__.update(saved)
                raise
        authorization = FakeAuthorization(session, database_permissions)
        self.authentication = Authentication()
        return DeadCashService(session, transaction_factory=transaction, repository=DeadCashRepo, balances=Balances,
                               customers=Customers, authorization=authorization, authentication=self.authentication)

    def write_off(self, amount="8000", reason="عميل مسافر ولا يمكن التحصيل", password=PASSWORD, expected=None,
                  key=None, service=None):
        expected = str(self.store.balance()) if expected is None else expected
        return (service or self.service).write_off("12", amount, reason, password, expected, key or str(uuid4()))

    def refused(self, code, call):
        before = self.store.tables()
        with self.assertRaises(DeadCashError) as context:
            call()
        self.assertEqual(context.exception.code, code)
        self.assertEqual(self.store.tables(), before)                             # nothing written


class WriteOffTests(DeadCashCase):
    def test_partial_write_off(self):
        result = self.write_off("8000")
        self.assertEqual((result["code"], result["balance_before"], result["balance_after"]),
                         ("DC000001", "23000.00", "15000.00"))
        self.assertEqual(self.store.balance(), D("15000.00"))                    # على العميل 15,000
        w = self.store.writeoffs["1"]
        self.assertEqual((w["amount"], w["balance_before"], w["balance_after"]), (D("8000.00"), D("23000.00"),
                                                                                  D("15000.00")))
        self.assertEqual(self.store.ledger[-1][:3], ("12", D("-8000.00"), "DEAD_CASH_WRITE_OFF"))

    def test_full_write_off_leaves_history_and_warning(self):
        self.write_off("23000")
        self.assertEqual(self.store.balance(), D("0.00"))
        self.assertEqual(len([r for r in self.store.ledger if r[2] == "SALE_BALANCE_DUE"]), 2)   # debt kept
        self.assertTrue(self.store.flags[0]["active"])                           # warning stays at zero balance

    def test_original_invoices_and_ledger_rows_are_untouched(self):
        invoices, first_rows = copy.deepcopy(self.store.invoices), copy.deepcopy(self.store.ledger)
        self.write_off("8000")
        self.assertEqual(self.store.invoices, invoices)
        self.assertEqual(self.store.ledger[:2], first_rows)                      # only one row appended
        self.assertEqual(len(self.store.ledger), 3)

    def test_no_financial_account_movement(self):
        money = copy.deepcopy(self.store.money)
        self.write_off("10000")
        self.assertEqual(self.store.money, money)                                # safe / bank / wallet unchanged
        import inspect
        from services import dead_cash_service
        source = inspect.getsource(dead_cash_service)
        for table in ("cash_movements", "bank_movements", "financial_operations", "invoice_payments"):
            self.assertNotIn(table, source)

    def test_zero_and_negative_balances_are_refused(self):
        self.store.ledger.append(("12", D("-23000.00"), "CUSTOMER_PAYMENT", "CS", None))       # balance 0
        self.refused("NOT_RECEIVABLE", lambda: self.write_off("1", expected="0.00"))
        self.store.ledger.append(("12", D("-500.00"), "CUSTOMER_PAYMENT", "CS", None))         # للعميل 500
        self.refused("NOT_RECEIVABLE", lambda: self.write_off("1", expected="-500.00"))

    def test_amount_rules(self):
        self.refused("EXCEEDS_BALANCE", lambda: self.write_off("23000.01"))
        for amount in ("0", "-5", "abc", "1.001", ""):
            self.refused("AMOUNT_INVALID", lambda a=amount: self.write_off(a))

    def test_reason_is_mandatory(self):
        for reason in ("", "   ", None):
            self.refused("REASON_REQUIRED", lambda r=reason: self.write_off(reason=r))

    def test_wrong_password_writes_nothing(self):
        self.refused("PASSWORD_INVALID", lambda: self.write_off(password="wrong"))
        self.assertEqual(self.authentication.calls, [3])                        # the logged-in actor's own password

    def test_permission_rules(self):
        with self.assertRaises(PermissionError):
            self.write_off(service=self.make(CASHIER))                           # settle ≠ erase receivables
        revoked = self.make(OWNER, database_permissions={"APP_LOGIN", "CUSTOMERS_VIEW"})
        before = self.store.tables()
        with self.assertRaises(PermissionError):
            self.write_off(service=revoked)                                      # DB revocation beats the session
        self.assertEqual(self.store.tables(), before)
        self.write_off("1")
        self.assertIn(("WRITE", "DEAD_CASH_MANAGE"), self.service._authorization.checked)   # strict write path

    def test_inactive_or_logged_out_actor_is_refused(self):
        service = self.make(OWNER)
        service._session.logout()
        with self.assertRaises(PermissionError):
            self.write_off(service=service)
        self.assertEqual(self.store.writeoffs, {})

    def test_warning_history_and_audit(self):
        result = self.write_off("8000")
        flag = self.store.flags[0]
        self.assertEqual((flag["related"], flag["reason"]), ("1", "سبق شطب رصيد لهذا العميل — DC000001"))
        self.write_off("1000")                                                  # a second write-off: one warning
        self.assertEqual(len(self.store.flags), 1)
        event, reference, metadata = self.store.audit[0]
        self.assertEqual((event, reference), ("DEAD_CASH_WRITTEN_OFF", result["code"]))
        for part in ('"amount": "8000.00"', '"before": "23000.00"', '"after": "15000.00"', '"customer_id": "12"'):
            self.assertIn(part, metadata)

    def test_rollback_at_every_step_leaves_everything_unchanged(self):
        for step, target in (("ledger", Customers), ("header", DeadCashRepo), ("flag", DeadCashRepo),
                             ("audit", DeadCashRepo)):
            with self.subTest(step=step):
                target.fail_on = step
                before = self.store.tables()
                with self.assertRaises(RuntimeError):
                    self.write_off("8000")
                self.assertEqual(self.store.tables(), before)
                target.fail_on = None


class ConcurrencyTests(DeadCashCase):
    def test_balance_changed_since_the_dialog_opened_is_refused(self):
        opened = str(self.store.balance())                                       # 23,000 shown in the dialog
        self.store.ledger.append(("12", D("-5000.00"), "CUSTOMER_ACCOUNT_SETTLEMENT", "CS", "CS000001"))   # CS meanwhile
        error = None
        try:
            self.write_off("8000", expected=opened)
        except DeadCashError as exc:
            error = exc
        self.assertEqual(error.code, "BALANCE_CHANGED")
        self.assertEqual(str(error), "رصيد العميل تغير منذ فتح نافذة الشطب. أعد فتح العملية للمراجعة.")
        self.assertEqual(self.store.writeoffs, {})                               # never silently adjusted

    def test_a_second_dialog_on_the_old_balance_cannot_over_write_off(self):
        opened = str(self.store.balance())
        self.write_off("20000", expected=opened)                                 # first write-off
        self.refused("BALANCE_CHANGED", lambda: self.write_off("20000", expected=opened))
        self.assertEqual(self.store.balance(), D("3000.00"))

    def test_double_click_posts_once(self):
        key = str(uuid4())
        first = self.write_off("8000", key=key)
        second = self.write_off("8000", key=key, expected="23000.00")            # the same request again
        self.assertEqual((second["code"], second["duplicate"]), (first["code"], True))
        self.assertEqual((len(self.store.writeoffs), self.store.balance()), (1, D("15000.00")))
        self.refused("REQUEST_CONFLICT", lambda: self.write_off("9000", key=key, expected="23000.00"))


class ReversalTests(DeadCashCase):
    def reverse(self, writeoff_id="1", reason="أُدخل بالخطأ", password=PASSWORD, service=None):
        return (service or self.service).reverse(writeoff_id, reason, password)

    def test_full_reversal_restores_exact_amount_and_keeps_the_record(self):
        self.write_off("8000")
        original = copy.deepcopy(self.store.writeoffs["1"])
        result = self.reverse()
        self.assertEqual((result["code"], result["reversal_code"]), ("DC000001", "DCR000001"))
        self.assertEqual(self.store.balance(), D("23000.00"))
        after = self.store.writeoffs["1"]
        for field in ("amount", "balance_before", "balance_after", "reason", "writeoff_transaction_id"):
            self.assertEqual(after[field], original[field])                     # the write-off itself is immutable
        self.assertTrue(after["is_reversed"])
        self.assertEqual(self.store.ledger[-1][:3], ("12", D("8000.00"), "DEAD_CASH_REVERSAL"))
        self.assertEqual(self.store.ledger[-1][4], "DCR000001")
        self.assertTrue(self.store.flags[0]["active"])                          # warning history kept
        self.assertEqual(self.store.audit[-1][0], "DEAD_CASH_REVERSED")

    def test_second_reversal_reason_password_and_permission(self):
        self.write_off("8000")
        self.refused("REASON_REQUIRED", lambda: self.reverse(reason="  "))
        self.refused("PASSWORD_INVALID", lambda: self.reverse(password="wrong"))
        with self.assertRaises(PermissionError):
            self.reverse(service=self.make(frozenset({"APP_LOGIN", "DEAD_CASH_MANAGE"})))   # needs DEAD_CASH_REVERSE
        self.reverse()
        self.refused("ALREADY_REVERSED", self.reverse)
        self.refused("NOT_FOUND", lambda: self.reverse("99"))

    def test_reversal_creates_no_money_movement(self):
        money = copy.deepcopy(self.store.money)
        self.write_off("10000")
        self.reverse()
        self.assertEqual(self.store.money, money)


class ContextTests(DeadCashCase):
    def test_dialog_context_is_the_authoritative_balance_by_read_path(self):
        context = self.service.context("12")
        self.assertEqual(context, {"customer_id": "12", "name": "أحمد", "balance": "23000.00", "eligible": True})
        self.assertEqual(self.service._authorization.checked, [("READ", "DEAD_CASH_MANAGE")])   # no lock
        self.store.ledger.append(("12", D("-23000.00"), "CUSTOMER_PAYMENT", "CS", None))
        self.assertFalse(self.service.context("12")["eligible"])
        with self.assertRaises(PermissionError):
            self.make(CASHIER).context("12")


class CustomerPageTests(unittest.TestCase):
    """The Customers page: warning text, summary, history presentation and the dialog maths."""

    def test_profile_warning_summary_and_history_rows(self):
        from app.viewmodels.customer_presentation import customer_row
        from app.viewmodels.customer_viewmodel import CustomerViewModel
        row = customer_row({"customer_id": 12, "has_active_dead_cash_warning": 1, "has_dead_cash_history": 1,
                            "unreversed_dead_cash_count": 1, "balance": D("15000")})
        self.assertEqual(row["dead_cash_message"], "⚠️ سبق شطب رصيد لهذا العميل")
        vm = CustomerViewModel(service=object())
        vm._dead_cash_summary = {"active_count": 2, "active_total": D("9000.00"),
                                 "last_writeoff_at": datetime(2026, 9, 30, 12, 0), "reversed_count": 1}
        summary = vm.deadCashSummary
        self.assertEqual((summary["countText"], summary["totalText"]),
                         ("عدد مرات الشطب: 2", "إجمالي الرصيد المشطوب: 9,000.00 ج"))
        self.assertIn("2026-09-30", summary["lastText"])
        history = vm._dead_cash_row({"dead_cash_id": 7, "amount": D("8000"), "is_reversed": 1,
                                     "occurred_at": datetime(2026, 9, 30, 12)})
        self.assertEqual((history["reference"], history["status_label"]), ("DC000007", "معكوس"))
        self.assertEqual(vm._dead_cash_row({"dead_cash_id": 8, "amount": D("1"), "is_reversed": 0})["status_label"],
                         "نشط")

    def test_dialog_defaults_to_the_full_balance_and_previews_the_remainder(self):
        from app.viewmodels.customer_viewmodel import CustomerViewModel
        vm = CustomerViewModel(service=object())
        vm._writeoff = {"customer_id": "12", "name": "أحمد", "balance": "23000.00", "eligible": True}
        self.assertEqual(vm.writeOff["defaultAmount"], "23000.00")                 # default = full amount
        preview = vm.writeOffPreview("8000")
        self.assertEqual(preview["afterText"], "15,000.00 ج")
        self.assertEqual(preview["confirmText"],
                         "سيتم شطب 8,000.00 ج من رصيد أحمد. سيصبح الرصيد المتبقي على العميل 15,000.00 ج.")
        for bad in ("0", "-1", "23000.01", "x", "1.005"):
            self.assertFalse(vm.writeOffPreview(bad)["valid"], bad)


class BillingWarningTests(unittest.TestCase):
    def test_picker_exposes_only_the_flag_and_a_flagged_customer_can_buy(self):
        from tests.test_invoice_service import InvoiceServiceTestCase
        from tests.test_invoice_workflow_service import NewCustomerTests

        class Case(InvoiceServiceTestCase):
            def runTest(self):
                pass
        case = Case()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.store.warnings[12] = {"flag_id": "1", "flag_type": "DEAD_CASH_WARNING"}      # an active warning
        rows = case.service.search_customers("عميل")
        self.assertEqual(set(rows[0]), {"customer_id", "customer_code", "name", "mobile", "place", "balance",
                                        "dead_cash_warning"})                    # the flag only, no history
        from services.invoice_service import InvoiceDraft
        from tests.test_invoice_service import cash
        from tests.test_invoice_workflow_service import scanned
        paid = InvoiceDraft(5, "12", (scanned(),), (), (cash("67011"),), account_amount="7011",
                            account_direction="CUSTOMER_OWES")
        preview = case.service.preview(paid)
        self.assertTrue(preview["dead_cash_warning_active"])                    # the warning is shown
        case.finalize(paid, dead_cash_confirmed=True)                             # existing rule: an acknowledgement
        self.assertEqual(case.store.invoices[0]["customer_id"], "12")            # a caution, never a sales block


if __name__ == "__main__":
    unittest.main()
