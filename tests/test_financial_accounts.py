"""Financial Accounts V1 (025): manual deposit / withdrawal / transfer, statement read side and the
FINANCIAL_OPERATION full reversal, against a transactional in-memory store (no application DB)."""
import copy
from datetime import datetime
from decimal import Decimal as D
from unittest.mock import patch
from uuid import uuid4
import unittest

from tests import test_correction_service as fixture
from repositories import financial_account_repository as far
from services.correction_service import CorrectionError, FO, SettlementLeg
from services.financial_account_service import FinancialAccountService, OPERATE

OWNER = frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW", "TRANSACTION_CORRECT", OPERATE, "PAYMENT_RECORD"})
MANAGER = frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW", "TRANSACTION_CORRECT", OPERATE})
CASHIER = frozenset({"APP_LOGIN", "CUSTOMER_ACCOUNT_SETTLE", "INVOICE_RETURN", OPERATE})
SALESPERSON = frozenset({"APP_LOGIN", "INVOICE_RETURN"})
REASON = "إيداع رأس مال"


class FinRepo:
    """The FinancialAccountRepository contract over fixture.Store (money tuples are the ledgers)."""
    fail_on = None
    calls = []

    def __init__(self, connection):
        self.store = connection.store
        store = self.store
        if not hasattr(store, "ops"):
            store.ops, store.op_legs = {}, []
            store.cash_accounts = {1: {"id": 1, "name": "الخزنة الرئيسية", "account_type": "CASH", "is_active": 1}}
            store.bank_accounts[9] = {"id": 9, "name": "حساب موقوف", "account_type": "BANK", "is_active": 0}

    def _fail(self, step):
        if FinRepo.fail_on == step:
            raise RuntimeError(f"forced failure in {step}")

    def _table(self, ledger):
        return self.store.cash_accounts if ledger == "CASH" else self.store.bank_accounts

    def accounts(self, ledgers=("CASH", "BANK")):
        rows = []
        for ledger in ledgers:
            for a in self._table(ledger).values():
                rows.append({"ledger": ledger, "account_id": a["id"], "name": a["name"], "account_type": a["account_type"],
                             "bank_name": None, "is_active": a["is_active"],
                             "balance": self.store.balance(ledger, a["id"]),
                             "movement_count": sum(1 for m in self.store.money if (m[0], m[1]) == (ledger, a["id"])),
                             "last_movement_at": None})
        return rows

    def account(self, ledger, account_id, lock=False):
        row = self._table(ledger).get(int(account_id))
        return None if row is None else dict(row)

    def balance(self, ledger, account_id):
        return self.store.balance(ledger, int(account_id))

    def statement(self, ledger, account_id, start=None, end=None, direction="", source="", search="", offset=0):
        FinRepo.calls.append((ledger, account_id, start, end, direction, source, search, offset))   # reads roll back
        return copy.deepcopy(getattr(self.store, "statement_rows", []))[offset:offset + 51]

    def cash_flow(self, ledger, account_id, start, end, label="", limit=200):
        FinRepo.calls.append(("cash_flow", ledger, account_id, start, end, label, limit))
        return copy.deepcopy(getattr(self.store, "cash_rows", []))

    def operation_accounts(self):
        return [{"id": a["id"], "name": a["name"], "account_type": a["account_type"]}
                for a in self.store.bank_accounts.values() if a["is_active"]]

    def by_request(self, key):
        return next((dict(o) for o in self.store.ops.values() if o["request_key"] == key), None)

    def operation(self, operation_id, lock=False):
        row = self.store.ops.get(str(operation_id))
        if row is None:
            return None
        row = copy.deepcopy(row)
        row["legs"] = [dict(l) for l in self.store.op_legs if l["operation_id"] == str(operation_id)]
        reversal = next((c for c in self.store.corrections if c["source_type"] == FO
                         and c["source_id"] == str(operation_id) and c["correction_type"] == "FULL_REVERSAL"), None)
        row["reversal"] = None if reversal is None else {"correction_id": reversal["correction_id"],
                                                         "reason": reversal["reason"], "occurred_at": self.store.now}
        return row

    def insert_operation(self, *, operation_type, key, digest, amount, source, destination, reason, actor, now,
                         category=None):
        assert amount > 0                                                         # chk_fo_amount
        if operation_type != "ACCOUNT_TRANSFER":
            assert category or (reason and reason.strip())                        # chk_fo_shape (026)
            allowed = ((None, "REVENUE", "REPAIR") if operation_type == "MANUAL_DEPOSIT"
                       else (None, "EXPENSE", "SALARY", "RENT", "REPAIR"))
            assert category in allowed                                            # chk_fo_category
        else:
            assert category is None and source != destination
        operation_id = str(len(self.store.ops) + 1)

        def side(value, ledger):
            return value[1] if value and value[0] == ledger else None
        self.store.ops[operation_id] = {
            "id": operation_id, "operation_type": operation_type, "category": category, "request_key": key,
            "request_hash": digest,
            "amount": amount, "reason": reason, "performed_by_user_id": actor.user_id, "occurred_at": now,
            "from_cash_account_id": side(source, "CASH"), "from_bank_account_id": side(source, "BANK"),
            "to_cash_account_id": side(destination, "CASH"), "to_bank_account_id": side(destination, "BANK")}
        self._fail("header")
        return operation_id

    def post_leg(self, *, operation_id, code, leg_role, ledger, account_id, amount, type_code, description, notes,
                 group, actor, now):
        delta = amount if leg_role == "IN" else -amount
        self.store.money.append((ledger, int(account_id), delta, far.SOURCE, str(operation_id)))
        self._fail(f"movement_{leg_role}")
        assert not any(l["operation_id"] == str(operation_id) and l["leg_role"] == leg_role
                       for l in self.store.op_legs)                              # uq_fol_role
        self.store.op_legs.append({"operation_id": str(operation_id), "leg_role": leg_role, "amount": delta,
                                   "type_code": type_code, "description": description, "group": group})
        self._fail(f"leg_{leg_role}")
        return str(len(self.store.money))

    def audit(self, *, actor, event_type, operation_id, code, description, metadata, now):
        self.store.audit.append((event_type, code))
        self._fail("audit")

    def db_now(self):
        return self.store.now


class FinancialTestCase(fixture.CorrectionTestCase):
    def setUp(self):
        super().setUp()
        FinRepo.fail_on, FinRepo.calls = None, []
        self.addCleanup(setattr, FinRepo, "fail_on", None)
        FinRepo(fixture.Connection(self.store))                                   # seed accounts
        for target in ("repositories.financial_account_repository.FinancialAccountRepository",):
            p = patch(target, FinRepo)
            p.start()
            self.addCleanup(p.stop)
        refs = patch.dict(fixture.REF, {FO: far.SOURCE})
        refs.start()
        self.addCleanup(refs.stop)
        self.fin = self.fin_service(OWNER)

    def fin_service(self, permissions):
        base = self.make(permissions)
        return FinancialAccountService(base._session, transaction_factory=base._transaction, repository=FinRepo,
                                       authorization=base._authorization)

    def cash(self):
        return self.store.balance("CASH", 1)

    def bank(self, account=7):
        return self.store.balance("BANK", account)

    def assertFinRefused(self, code, call):
        before = self.store.tables()
        with self.assertRaises(CorrectionError) as context:
            call()
        self.assertEqual(context.exception.code, code)
        self.assertEqual(self.store.tables(), before)


# ----------------------------------------------------------------------------- deposit
class DepositTests(FinancialTestCase):
    def test_valid_deposit_increases_account_exactly(self):
        before = self.cash()
        result = self.fin.deposit("CASH:1", "10000.00", "  " + REASON + "  ", str(uuid4()))
        self.assertEqual(result["code"], "FO000001")
        self.assertEqual(self.cash(), before + D("10000.00"))
        op = self.store.ops["1"]
        self.assertEqual((op["operation_type"], op["amount"], op["reason"]), ("MANUAL_DEPOSIT", D("10000.00"), REASON))
        self.assertEqual([(l["leg_role"], l["amount"], l["type_code"]) for l in self.store.op_legs],
                         [("IN", D("10000.00"), "MANUAL_DEPOSIT")])
        self.assertEqual(self.store.audit[-1], ("MANUAL_DEPOSIT", "FO000001"))

    def test_reason_required_and_whitespace_refused(self):
        for reason in ("", "   ", None):
            self.assertFinRefused("REASON_REQUIRED", lambda r=reason: self.fin.deposit("CASH:1", "10", r, str(uuid4())))

    def test_zero_negative_and_malformed_amounts_refused(self):
        for amount in ("0", "0.00", "-5", "abc", "1.001", "NaN", ""):
            self.assertFinRefused("AMOUNT_INVALID",
                                  lambda a=amount: self.fin.deposit("CASH:1", a, REASON, str(uuid4())))

    def test_inactive_and_unknown_accounts_refused(self):
        self.assertFinRefused("ACCOUNT_INACTIVE", lambda: self.fin.deposit("BANK:9", "10", REASON, str(uuid4())))
        self.assertFinRefused("ACCOUNT_NOT_FOUND", lambda: self.fin.deposit("BANK:404", "10", REASON, str(uuid4())))
        self.assertFinRefused("ACCOUNT_NOT_FOUND", lambda: self.fin.deposit("GOLD:1", "10", REASON, str(uuid4())))

    def test_duplicate_submit_posts_once(self):
        key = str(uuid4())
        first = self.fin.deposit("CASH:1", "500", REASON, key)
        second = self.fin.deposit("CASH:1", "500", REASON, key)
        self.assertEqual((second["code"], second["duplicate"]), (first["code"], True))
        self.assertEqual(len(self.store.ops), 1)
        self.assertEqual(len([m for m in self.store.money if m[3] == far.SOURCE]), 1)

    def test_request_key_reused_for_a_different_operation_is_refused(self):
        key = str(uuid4())
        self.fin.deposit("CASH:1", "500", REASON, key)
        self.assertFinRefused("REQUEST_CONFLICT", lambda: self.fin.deposit("CASH:1", "600", REASON, key))

    def test_finalized_operation_is_immutable(self):
        self.fin.deposit("CASH:1", "500", REASON, str(uuid4()))
        for name in ("edit", "update", "delete", "set_balance", "adjust_balance"):
            self.assertFalse(any(name in attr for attr in dir(self.fin)), name)
            self.assertFalse(any(name in attr for attr in dir(far.FinancialAccountRepository)), name)


# ----------------------------------------------------------------------------- withdrawal
class WithdrawalTests(FinancialTestCase):
    def test_valid_withdrawal_decreases_and_may_go_negative(self):
        before = self.bank(8)
        self.assertEqual(before, D("0.00"))
        result = self.fin.withdraw("BANK:8", "2500", "سحب شخصي للمالك", str(uuid4()))
        self.assertEqual(result["code"], "FO000001")
        self.assertEqual(self.bank(8), D("-2500.00"))                              # negative is valid
        self.assertEqual(self.cash(), self.store.balance("CASH", 1))               # no fallback
        self.assertEqual(self.store.op_legs[0]["amount"], D("-2500.00"))

    def test_reason_and_amount_required(self):
        self.assertFinRefused("REASON_REQUIRED", lambda: self.fin.withdraw("CASH:1", "10", " ", str(uuid4())))
        self.assertFinRefused("AMOUNT_INVALID", lambda: self.fin.withdraw("CASH:1", "0", REASON, str(uuid4())))

    def test_duplicate_submit_posts_once(self):
        key = str(uuid4())
        self.fin.withdraw("CASH:1", "100", REASON, key)
        self.fin.withdraw("CASH:1", "100", REASON, key)
        self.assertEqual(len(self.store.op_legs), 1)


# ----------------------------------------------------------------------------- transfer
class TransferTests(FinancialTestCase):
    def test_exact_legs_net_zero_and_one_reference(self):
        cash, bank, total = self.cash(), self.bank(7), sum(m[2] for m in self.store.money)
        result = self.fin.transfer("CASH:1", "BANK:7", "20000", "", str(uuid4()))
        self.assertEqual(result["code"], "TR000001")
        self.assertEqual(self.cash(), cash - D("20000"))
        self.assertEqual(self.bank(7), bank + D("20000"))
        self.assertEqual(sum(m[2] for m in self.store.money), total)               # net shop money unchanged
        legs = self.store.op_legs
        self.assertEqual([(l["leg_role"], l["type_code"]) for l in legs],
                         [("OUT", "ACCOUNT_TRANSFER_OUT"), ("IN", "ACCOUNT_TRANSFER_IN")])
        self.assertEqual(legs[0]["group"], legs[1]["group"])                       # one transfer group
        self.assertIn("تحويل إلى بنك مصر — TR000001", legs[0]["description"])
        self.assertIn("تحويل من الخزنة الرئيسية — TR000001", legs[1]["description"])

    def test_source_may_become_negative(self):
        self.fin.transfer("BANK:8", "CASH:1", "700", "ملاحظة", str(uuid4()))
        self.assertEqual(self.bank(8), D("-700.00"))

    def test_same_and_inactive_accounts_refused(self):
        self.assertFinRefused("SAME_ACCOUNT", lambda: self.fin.transfer("CASH:1", "CASH:1", "10", "", str(uuid4())))
        self.assertFinRefused("ACCOUNT_INACTIVE", lambda: self.fin.transfer("CASH:1", "BANK:9", "10", "", str(uuid4())))
        self.assertFinRefused("ACCOUNT_INACTIVE", lambda: self.fin.transfer("BANK:9", "CASH:1", "10", "", str(uuid4())))

    def test_duplicate_submit_posts_once(self):
        key = str(uuid4())
        self.fin.transfer("CASH:1", "BANK:7", "10", "", key)
        self.fin.transfer("CASH:1", "BANK:7", "10", "", key)
        self.assertEqual((len(self.store.ops), len(self.store.op_legs)), (1, 2))


# ----------------------------------------------------------------------------- rollback injection
class RollbackTests(FinancialTestCase):
    def test_every_failure_point_leaves_nothing(self):
        cases = [("deposit", step) for step in ("header", "movement_IN", "leg_IN", "audit")] + \
                [("transfer", step) for step in ("header", "movement_OUT", "leg_OUT", "movement_IN", "leg_IN", "audit")]
        for kind, step in cases:
            with self.subTest(kind=kind, step=step):
                FinRepo.fail_on = step
                before = self.store.tables()
                with self.assertRaises(RuntimeError):
                    if kind == "deposit":
                        self.fin.deposit("CASH:1", "10", REASON, str(uuid4()))
                    else:
                        self.fin.transfer("CASH:1", "BANK:7", "10", "", str(uuid4()))
                self.assertEqual(self.store.tables(), before)                     # no partial transfer / orphan
        FinRepo.fail_on = None


# ----------------------------------------------------------------------------- permissions
class PermissionTests(FinancialTestCase):
    def test_owner_manager_cashier_operate(self):
        for permissions in (OWNER, MANAGER, CASHIER):
            self.fin_service(permissions).deposit("CASH:1", "1", REASON, str(uuid4()))
        self.assertEqual(len(self.store.ops), 3)

    def test_salesperson_payment_record_and_transaction_correct_alone_refused(self):
        for permissions in (SALESPERSON, frozenset({"APP_LOGIN", "PAYMENT_RECORD"}),
                            frozenset({"APP_LOGIN", "TRANSACTION_CORRECT", "MANAGEMENT_REVIEW"})):
            service = self.fin_service(permissions)
            with self.assertRaises(PermissionError):
                service.deposit("CASH:1", "1", REASON, str(uuid4()))
            with self.assertRaises(PermissionError):
                service.transfer("CASH:1", "BANK:7", "1", "", str(uuid4()))
        self.assertEqual(self.store.ops, {})

    def test_permission_is_rechecked_inside_the_transaction(self):
        service = self.fin_service(OWNER)
        service._authorization.permissions = frozenset({"APP_LOGIN"})              # revoked in the database
        with self.assertRaises(PermissionError):
            service.deposit("CASH:1", "1", REASON, str(uuid4()))
        self.assertEqual(self.store.ops, {})

    def keys(self, permissions):
        return {a["key"].split(":")[0] for a in self.fin_service(permissions).accounts()["accounts"]}

    def test_managers_and_operators_see_every_ledger(self):
        for permissions in (OWNER, MANAGER, CASHIER):
            self.assertEqual(self.keys(permissions), {"CASH", "BANK"})

    def test_cash_view_only_never_sees_bank_or_wallet(self):
        viewer = self.fin_service(frozenset({"APP_LOGIN", "CASH_VIEW"}))
        self.assertEqual(self.keys(frozenset({"APP_LOGIN", "CASH_VIEW"})), {"CASH"})
        self.assertFalse(viewer.accounts()["can_operate"])
        viewer.statement("CASH:1")
        for key in ("BANK:7", "BANK:8"):
            with self.assertRaises(PermissionError):
                viewer.statement(key)
        FinRepo.calls.clear()
        with self.assertRaises(PermissionError):
            viewer.statement("BANK:7")
        self.assertEqual(FinRepo.calls, [])                                      # refused before any read

    def test_bank_view_only_never_sees_the_cash_safe(self):
        viewer = self.fin_service(frozenset({"APP_LOGIN", "BANK_VIEW"}))
        self.assertEqual(self.keys(frozenset({"APP_LOGIN", "BANK_VIEW"})), {"BANK"})
        viewer.statement("BANK:8")
        with self.assertRaises(PermissionError):
            viewer.statement("CASH:1")

    def test_operation_detail_needs_every_side_visible(self):
        self.fin.transfer("CASH:1", "BANK:7", "10", "", str(uuid4()))
        self.fin.deposit("BANK:7", "10", REASON, str(uuid4()))
        cash_only = self.fin_service(frozenset({"APP_LOGIN", "CASH_VIEW"}))
        bank_only = self.fin_service(frozenset({"APP_LOGIN", "BANK_VIEW"}))
        for viewer in (cash_only, bank_only):
            with self.assertRaises(PermissionError):
                viewer.operation("1")                                             # cash → bank transfer
        self.assertEqual(bank_only.operation("2")["code"], "FO000002")
        with self.assertRaises(PermissionError):
            cash_only.operation("2")

    def test_visibility_follows_database_verified_permissions(self):
        service = self.fin_service(frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW", "CASH_VIEW"}))
        service._authorization.permissions = frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW", "CASH_VIEW"})
        self.assertEqual({a["key"][:4] for a in service.accounts()["accounts"]}, {"CASH", "BANK"})
        # MANAGEMENT_REVIEW revoked in the database while the session still holds it: cash only.
        service._authorization = type(service._authorization)(service._session, frozenset({"APP_LOGIN", "CASH_VIEW"}))
        service._authorization.require_permission = lambda c, actor, code, **k: fixture.dataclasses.replace(
            actor, permissions=frozenset({"APP_LOGIN", "CASH_VIEW"}))
        self.assertEqual({a["key"][:4] for a in service.accounts()["accounts"]}, {"CASH"})

    def test_view_permissions(self):
        self.assertTrue(self.fin_service(CASHIER).can_view())
        self.assertFalse(self.fin_service(SALESPERSON).can_view())
        with self.assertRaises(PermissionError):
            self.fin_service(SALESPERSON).accounts()


# ----------------------------------------------------------------------------- reversal
class ReversalTests(FinancialTestCase):
    def reverse(self, operation_id, reason="تم تسجيل العملية بالخطأ"):
        return self.service.reverse(FO, operation_id, reason)

    def test_deposit_in_reversed_out(self):
        self.fin.deposit("CASH:1", "10000", REASON, str(uuid4()))
        original = copy.deepcopy(self.store.ops["1"])
        before = self.cash()
        result = self.reverse("1")
        self.assertTrue(result["code"].startswith("RV"))
        self.assertEqual(result["source_reference"], "FO000001")
        self.assertEqual(self.cash(), before - D("10000"))
        self.assertEqual(self.store.ops["1"], original)                           # original unchanged
        correction = self.store.corrections[-1]
        self.assertEqual((correction["source_type"], correction["source_id"], correction["invoice_id"]), (FO, "1", None))

    def test_withdrawal_out_reversed_in(self):
        self.fin.withdraw("BANK:7", "8000", "مصروف صيانة", str(uuid4()))
        before = self.bank(7)
        self.reverse("1")
        self.assertEqual(self.bank(7), before + D("8000"))

    def test_transfer_two_legs_exactly_reversed(self):
        cash, bank = self.cash(), self.bank(7)
        self.fin.transfer("CASH:1", "BANK:7", "20000", "", str(uuid4()))
        self.reverse("1")
        self.assertEqual((self.cash(), self.bank(7)), (cash, bank))
        posted = [m for m in self.store.money if m[3] == "TRANSACTION_CORRECTION"]
        self.assertEqual(sorted((m[0], m[1], m[2]) for m in posted),
                         [("BANK", 7, D("-20000.00")), ("CASH", 1, D("20000.00"))])

    def test_second_reversal_refused_and_reason_mandatory(self):
        self.fin.deposit("CASH:1", "10", REASON, str(uuid4()))
        self.assertRefused("REASON_REQUIRED", lambda: self.reverse("1", "  "))
        self.reverse("1")
        self.assertRefused("ALREADY_REVERSED", lambda: self.reverse("1"))
        self.assertEqual(self.fin.operation("1")["reversed_by"][:2], "RV")

    def test_settlement_correction_is_not_offered(self):
        self.fin.deposit("CASH:1", "10", REASON, str(uuid4()))
        self.assertRefused("SETTLEMENT_NOT_SUPPORTED", lambda: self.service.current_settlement(FO, "1"))
        self.assertRefused("SETTLEMENT_NOT_SUPPORTED", lambda: self.service.correct_settlement(
            FO, "1", [SettlementLeg("PHYSICAL_CASH", "10")], None, "سبب"))
        target = self.service.financial_operation_target("1")
        self.assertEqual(target["targets"][0]["settlement_allowed"], False)
        self.assertEqual(target["invoice_id"], "")

    def test_reversal_requires_transaction_correct(self):
        self.fin.deposit("CASH:1", "10", REASON, str(uuid4()))
        cashier = self.make(CASHIER)
        with self.assertRaises(PermissionError):
            cashier.reverse(FO, "1", "سبب")


# ----------------------------------------------------------------------------- statement read side
def _row(i, delta, balance, source="INVOICE", **extra):
    return {"id": i, "amount_delta": D(delta), "balance_after": D(balance), "occurred_at": datetime(2026, 9, 30, 10, i % 60),
            "reference_type": "INVOICE", "reference_id": 99, "reference_code": "x", "description": "فاتورة",
            "notes": None, "created_by_user_id": 3, "movement_code": "INVOICE_PAYMENT_IN", "actor_name": "المدير",
            "source_type": source, "invoice_id": extra.pop("invoice_id", None), "invoice_number": None,
            "source_reference": "26092001", "corrected_source_type": None, "corrected_source_id": None,
            "corrected_reference": None, "operation_id": None, "operation_reason": None, "customer_name": None, **extra}


class StatementTests(FinancialTestCase):
    def test_page_of_fifty_plus_one_and_string_ids(self):
        self.store.statement_rows = [_row(i, "10", str(10 * (60 - i)), invoice_id=12345678901234 + i)
                                     for i in range(60)]
        result = self.fin.statement("CASH:1", page=1)
        self.assertEqual((len(result["rows"]), result["has_more"]), (50, True))
        self.assertIsInstance(result["rows"][0]["invoice_id"], str)
        self.assertEqual(result["rows"][0]["invoice_id"], "12345678901234")
        self.assertEqual(FinRepo.calls[-1][-1], 0)
        second = self.fin.statement("CASH:1", page=2)
        self.assertEqual((len(second["rows"]), second["has_more"]), (10, False))
        self.assertEqual(FinRepo.calls[-1][-1], 50)

    def test_filters_are_validated_and_bounded(self):
        self.fin.statement("BANK:7", "2026-09-01", "2026-09-30", "OUT", "RETURN", " RT ")
        ledger, account, start, end, direction, source, search, _ = FinRepo.calls[-1]
        self.assertEqual((ledger, account, direction, source, search), ("BANK", 7, "OUT", "RETURN", "RT"))
        self.assertEqual((start, end), (datetime(2026, 9, 1), datetime(2026, 10, 1)))   # inclusive end date
        for bad in ({"direction": "SIDEWAYS"}, {"source": "DEAD_CASH"}, {"start": "2026-10-01", "end": "2026-09-01"}):
            with self.assertRaises(ValueError):
                self.fin.statement("CASH:1", **bad)
        with self.assertRaises(CorrectionError):
            self.fin.statement("CASH:x")

    def test_balance_is_the_ledger_sum(self):
        accounts = {a["key"]: a for a in self.fin.accounts()["accounts"]}
        self.assertEqual(D(accounts["CASH:1"]["balance"]), self.cash())
        self.assertEqual(D(self.fin.statement("CASH:1")["account"]["balance"]), self.cash())


class StatementQueryTests(unittest.TestCase):
    """The read model is one bounded query per page (no per-row source query)."""

    class Recorder:
        def __init__(self):
            self.queries = []

        def cursor(self, dictionary=False):
            recorder = self

            class Cursor:
                def execute(self, sql, params=()):
                    recorder.queries.append((sql, params))

                def fetchall(self):
                    return []

                def fetchone(self):
                    return None

                def close(self):
                    pass
            return Cursor()

    def test_single_query_window_before_filters_and_limit_51(self):
        connection = self.Recorder()
        far.FinancialAccountRepository(connection).statement("BANK", 7, None, None, "IN", "", "", 100)
        self.assertEqual(len(connection.queries), 1)
        sql, params = connection.queries[0]
        self.assertIn("FROM bank_movements m", sql)
        self.assertIn("ROWS UNBOUNDED PRECEDING", sql)
        self.assertLess(sql.index("ROWS UNBOUNDED PRECEDING"), sql.index("SELECT * FROM resolved"))  # before filters
        self.assertIn("LIMIT 51 OFFSET %s", sql)
        self.assertEqual(params[-1], 100)
        for reference in ("'INVOICE'", "'INVOICE_LIFECYCLE'", "'INVOICE_RETURN'", "'CUSTOMER_ACCOUNT_SETTLEMENT'",
                          "'TRANSACTION_CORRECTION'", "'FINANCIAL_OPERATION'", "'OPENING_BALANCE'"):
            self.assertIn(reference, sql)

    def test_account_keys_and_codes(self):
        self.assertEqual(far.parse_account_key("CASH:1"), ("CASH", 1))
        for bad in ("", "CASH", "CASH:0", "GOLD:1", "BANK:-1", "BANK:1.5", "BANK:70000", None):
            self.assertIsNone(far.parse_account_key(bad))
        self.assertEqual(far.operation_code("ACCOUNT_TRANSFER", 7), "TR000007")
        self.assertEqual(far.operation_code("MANUAL_DEPOSIT", 7), "FO000007")


if __name__ == "__main__":
    unittest.main()
