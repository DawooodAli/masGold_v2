"""MainWindow balance strip and the manager's financial accounts (read-only).

Unit tests use an in-memory repository; the opt-in MySQL test reads the real
ledgers of the disposable masgold_rehearsal database inside a rolled-back transaction.
"""
from contextlib import contextmanager
from decimal import Decimal
import os
import unittest
import uuid

from PySide6.QtGui import QGuiApplication

from app.session import AppSession
from app.viewmodels.management_review_viewmodel import ManagementReviewViewModel
from app.viewmodels.operational_balance_viewmodel import OperationalBalanceViewModel
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.operational_balance_service import OperationalBalanceService
from tests.test_billing_v2_repositories_mysql import ENABLED, rehearsal_connection


D = Decimal


class Repo:
    def __init__(self, connection):
        self.state = connection

    def cash_balance(self):
        return self.state["cash"]

    def old_gold_counted(self):
        return [{"karat_id": 1, "karat_value": 18, "balance_grams": D("32.500")},
                {"karat_id": 2, "karat_value": 21, "balance_grams": D("114.720")},
                {"karat_id": 3, "karat_value": 24, "balance_grams": D("8.100")}]

    def account_summaries(self):
        return [{"ledger": "CASH", "account_id": 1, "name": "Main Safe", "account_type": "CASH", "bank_name": None,
                 "is_active": 1, "balance": D("125300.00"), "money_in": D("130000.00"), "money_out": D("4700.00"),
                 "movement_count": 3, "last_movement_at": None},
                {"ledger": "BANK", "account_id": 9, "name": "محفظة المحل", "account_type": "MOBILE_WALLET",
                 "bank_name": "فودافون كاش", "is_active": 1, "balance": D("7011.00"), "money_in": D("7011.00"),
                 "money_out": D("0.00"), "movement_count": 1, "last_movement_at": None}]

    def recent_movements(self, limit=5):
        from datetime import datetime
        return [{"ledger": "BANK", "account_id": 9, "amount_delta": D("7011.00"),
                 "occurred_at": datetime(2026, 9, 26, 12, 0), "reference_code": "26092601",
                 "description": "فاتورة 26092601", "movement_type": "Invoice Bank Payment Received"}]


class OperationalBalanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def session(self, permissions=frozenset({"APP_LOGIN"})):
        session = AppSession()
        session._establish(AuthenticationResult(17, 7, "موظف", permissions, _VERIFIED), int(session.generation))
        return session

    def service(self, session, cash=D("125300.00")):
        state = {"cash": cash, "rolled_back": False}

        @contextmanager
        def transaction():
            try:
                yield state
            finally:
                state["rolled_back"] = True
        return OperationalBalanceService(session, transaction_factory=transaction, repository=Repo), state

    def test_strip_is_cash_and_counted_old_gold_21_18_22_24_only(self):
        service, state = self.service(self.session())
        strip = service.strip()
        self.assertEqual(strip, {"cash_balance": "125300.00",
                                 "old_gold": [{"karat": 21, "grams": "114.720"}, {"karat": 18, "grams": "32.500"},
                                              {"karat": 22, "grams": "0.000"}, {"karat": 24, "grams": "8.100"}]})
        self.assertTrue(state["rolled_back"])
        self.assertNotIn("bank", str(strip).lower())

    def test_strip_viewmodel_formats_without_wallet_or_bank(self):
        service, _ = self.service(self.session())
        vm = OperationalBalanceViewModel(self.session(), service=service)
        vm._strip = service.strip()
        self.assertEqual((vm.cashText, [r["text"] for r in vm.oldGold]),
                         ("125,300.00 ج", ["114.720", "32.500", "0.000", "8.100"]))
        vm._strip = {"cash_balance": "-61138.95", "old_gold": []}
        self.assertEqual(vm.cashText, "⁦−61,138.95⁩ ج")
        self.assertEqual({"cashText", "oldGold"} & set(dir(vm)), {"cashText", "oldGold"})
        self.assertFalse([name for name in dir(vm) if "bank" in name.lower() or "wallet" in name.lower()])

    def test_financial_accounts_are_for_managers_only(self):
        service, _ = self.service(self.session())
        with self.assertRaises(PermissionError):
            service.financial_accounts()
        manager, _ = self.service(self.session(frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW"})))
        accounts = manager.financial_accounts()
        self.assertEqual([(a["name"], a["account_type"], len(a["recent"])) for a in accounts],
                         [("Main Safe", "CASH", 0), ("محفظة المحل", "MOBILE_WALLET", 1)])

    def test_manager_review_page_rows_for_accounts(self):
        manager = self.session(frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW"}))
        service, _ = self.service(manager)
        vm = ManagementReviewViewModel(manager, service=object(), balance_service=service)
        vm._on_accounts(service.financial_accounts(), "")
        rows = vm.financialAccounts
        self.assertEqual([(r["title"], r["balance"]) for r in rows],
                         [("Main Safe — نقدية", "125,300.00 ج"), ("محفظة المحل — محفظة", "7,011.00 ج")])
        # The summary row shows only name, type, state and balance (movements are a later drill-down).
        self.assertEqual([(r["name"], r["typeText"], r["stateText"], r["balance"]) for r in rows],
                         [("Main Safe", "نقدية", "نشط", "125,300.00 ج"),
                          ("محفظة المحل", "محفظة", "نشط", "7,011.00 ج")])
        self.assertEqual(rows[1]["details"], "وارد 7,011.00 ج • منصرف 0.00 ج • 1 حركة")
        self.assertEqual(rows[1]["recent"], "\u20662026-09-26 12:00\u2069 \u2066+7,011.00\u2069 — 26092601")


class SetupRepo(Repo):
    """In-memory accounts and ledgers for the manager's setup writes."""

    def __init__(self, state):
        super().__init__(state)
        self.state.setdefault("accounts", {("CASH", 1): {"id": 1, "name": "Main Safe", "is_active": 1}})
        self.state.setdefault("movements", [])
        self.state.setdefault("audit", [])

    def create_bank_account(self, *, name, account_type, bank_name, account_label):
        account_id = 10 + len(self.state["accounts"])
        self.state["accounts"][("BANK", account_id)] = {"id": account_id, "name": name, "account_type": account_type,
                                                         "is_active": 1}
        return account_id

    def set_bank_account_active(self, account_id, active):
        self.state["accounts"][("BANK", int(account_id))]["is_active"] = int(active)

    def lock_account(self, ledger, account_id):
        row = self.state["accounts"].get((ledger, int(account_id)))
        if row is None:
            return None
        count = sum(1 for m in self.state["movements"] if (m["ledger"], m["account_id"]) == (ledger, int(account_id)))
        return {**row, "movement_count": count}

    def add_opening_balance(self, *, ledger, account_id, amount, created_by_user_id, occurred_at):
        self.state["movements"].append({"ledger": ledger, "account_id": int(account_id), "amount": amount,
                                        "code": "OPENING_BALANCE", "by": created_by_user_id})
        return len(self.state["movements"])

    def audit(self, **values):
        self.state["audit"].append(values)

    def now(self):
        from datetime import datetime
        return datetime(2026, 9, 26, 12, 0)


class Authorization:
    def __init__(self, permissions):
        self.permissions, self.checked = permissions, []

    def require_permission(self, connection, actor, code):
        self.checked.append(code)
        if code not in self.permissions:
            raise PermissionError("لا توجد صلاحية لتنفيذ العملية.")
        return actor


class AccountSetupTests(unittest.TestCase):
    OWNER = frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW", "BANK_MANAGE", "CASH_ADJUST"})

    def service(self, permissions=OWNER, db_permissions=None):
        session = AppSession()
        session._establish(AuthenticationResult(1, None, "المالك", permissions, _VERIFIED), int(session.generation))
        state = {"cash": D("0")}

        @contextmanager
        def transaction():
            yield state
        authorization = Authorization(db_permissions if db_permissions is not None else permissions)
        return OperationalBalanceService(session, transaction_factory=transaction, repository=SetupRepo,
                                         authorization=authorization), state, authorization

    def test_manager_creates_real_accounts_with_an_audited_opening_balance(self):
        service, state, authorization = self.service()
        wallet = service.create_account("محفظة المحل", "MOBILE_WALLET", "2500.50", "فودافون كاش")
        bank = service.create_account("حساب CIB", "BANK")
        self.assertEqual([m["amount"] for m in state["movements"]], [D("2500.50")])      # none for a zero opening
        self.assertEqual(state["movements"][0]["code"], "OPENING_BALANCE")
        self.assertEqual([a["event_type"] for a in state["audit"]], ["FINANCIAL_ACCOUNT_CREATED"] * 2)
        self.assertEqual((state["audit"][0]["user_id"], state["audit"][0]["metadata"]["opening_balance"]),
                         (1, D("2500.50")))
        self.assertEqual(authorization.checked, ["BANK_MANAGE", "BANK_MANAGE"])
        self.assertEqual({wallet["account_id"], bank["account_id"]}, {"11", "12"})
        for name, kind, amount, code in (("", "BANK", "0", "NAME_REQUIRED"), ("x", "CASH", "0", "TYPE_INVALID"),
                                         ("x", "BANK", "-1", "AMOUNT_INVALID"), ("x", "BANK", "1.001", "AMOUNT_INVALID")):
            with self.subTest(code=code), self.assertRaises(ValueError) as refused:
                service.create_account(name, kind, amount)
            self.assertEqual(refused.exception.code, code)

    def test_opening_balance_is_recorded_once_and_never_repeated(self):
        service, state, authorization = self.service()
        service.initialize_opening_balance("CASH", "1", "125300")
        self.assertEqual((state["movements"][0]["ledger"], state["movements"][0]["amount"]), ("CASH", D("125300.00")))
        self.assertEqual(authorization.checked, ["CASH_ADJUST"])
        with self.assertRaises(ValueError) as again:
            service.initialize_opening_balance("CASH", "1", "1")
        self.assertEqual(again.exception.code, "ALREADY_INITIALIZED")
        self.assertEqual(len(state["movements"]), 1)
        with self.assertRaises(ValueError) as zero:
            service.initialize_opening_balance("CASH", "1", "0")
        self.assertEqual(zero.exception.code, "AMOUNT_INVALID")
        self.assertEqual(state["audit"][0]["event_type"], "FINANCIAL_OPENING_BALANCE")

    def test_only_managers_with_the_cash_bank_permission_can_set_up(self):
        clerk, _, _ = self.service(frozenset({"APP_LOGIN", "INVOICE_CREATE"}))
        with self.assertRaises(PermissionError):
            clerk.create_account("x", "BANK")
        # Reviewer without BANK_MANAGE in the database: refused inside the transaction.
        manager, state, _ = self.service(frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW"}),
                                         db_permissions=frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW"}))
        with self.assertRaises(PermissionError):
            manager.create_account("x", "BANK")
        with self.assertRaises(PermissionError):
            manager.initialize_opening_balance("CASH", "1", "10")
        self.assertEqual((state.get("movements", []), state.get("audit", [])), ([], []))

    def test_account_can_be_deactivated_and_reactivated(self):
        service, state, _ = self.service()
        account = service.create_account("حساب", "BANK")["account_id"]
        service.set_account_active(account, False)
        self.assertEqual(state["accounts"][("BANK", int(account))]["is_active"], 0)
        with self.assertRaises(ValueError):
            service.initialize_opening_balance("BANK", account, "10")          # inactive: no opening
        service.set_account_active(account, True)
        service.initialize_opening_balance("BANK", account, "10")
        self.assertEqual([a["event_type"] for a in state["audit"]][-3:],
                         ["FINANCIAL_ACCOUNT_DEACTIVATED", "FINANCIAL_ACCOUNT_ACTIVATED", "FINANCIAL_OPENING_BALANCE"])


@unittest.skipUnless(ENABLED, "set MASGOLD_REHEARSAL_DB_TESTS=1 to run against masgold_rehearsal")
class FinancialBalanceRepositoryMySqlTests(unittest.TestCase):
    def setUp(self):
        self.connection = rehearsal_connection()
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        self.connection.rollback()
        self.connection.start_transaction()

    def sql(self, query, params=(), fetch=False):
        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(query, params)
            return cursor.fetchall() if fetch else cursor.lastrowid
        finally:
            cursor.close()

    def test_balances_come_from_the_ledgers(self):
        from repositories.financial_balance_repository import FinancialBalanceRepository
        repo = FinancialBalanceRepository(self.connection)
        cash_before = D(str(repo.cash_balance()))
        grams_before = {r["karat_value"]: D(str(r["balance_grams"])) for r in repo.old_gold_counted()}
        cash_account = self.sql("SELECT id FROM cash_accounts WHERE code = 'MAIN_SAFE'", fetch=True)[0]["id"]
        self.sql("INSERT INTO cash_movements (cash_account_id, movement_type_id, amount_delta) "
                 "SELECT %s, id, 1000.00 FROM cash_movement_types WHERE code = 'CAPITAL_IN'", (cash_account,))
        self.sql("INSERT INTO cash_movements (cash_account_id, movement_type_id, amount_delta) "
                 "SELECT %s, id, -250.00 FROM cash_movement_types WHERE code = 'EXPENSE_OUT'", (cash_account,))
        karat = self.sql("SELECT id FROM karats WHERE karat_value = 21", fetch=True)[0]["id"]
        self.sql("INSERT INTO old_gold_movements (movement_type_id, karat_id, weight_delta_grams, balance_effect) "
                 "SELECT id, %s, 2.500, 'COUNTED' FROM old_gold_movement_types WHERE code = 'ADJUSTMENT_IN'", (karat,))
        wallet = self.sql("INSERT INTO bank_accounts (code, name, account_type) VALUES (%s, 'محفظة', 'MOBILE_WALLET')",
                          (f"W{uuid.uuid4().hex[:8]}",))
        self.sql("INSERT INTO bank_movements (bank_account_id, movement_type_id, amount_delta) "
                 "SELECT %s, id, 500.00 FROM bank_movement_types WHERE code = 'CAPITAL_IN'", (wallet,))
        self.assertEqual(D(str(repo.cash_balance())), cash_before + D("750.00"))
        grams = {r["karat_value"]: D(str(r["balance_grams"])) for r in repo.old_gold_counted()}
        self.assertEqual(grams[21], grams_before[21] + D("2.500"))          # COUNTED rows only (query filter)
        summary = {(r["ledger"], r["account_id"]): r for r in repo.account_summaries()}
        wallet_row = summary[("BANK", wallet)]
        self.assertEqual((wallet_row["account_type"], D(str(wallet_row["balance"])), D(str(wallet_row["money_in"]))),
                         ("MOBILE_WALLET", D("500.00"), D("500.00")))
        cash_row = summary[("CASH", cash_account)]
        self.assertGreaterEqual(D(str(cash_row["money_out"])), D("250.00"))
        recent = [r for r in repo.recent_movements() if r["ledger"] == "BANK" and r["account_id"] == wallet]
        self.assertEqual([D(str(r["amount_delta"])) for r in recent], [D("500.00")])

    def test_opening_balance_goes_through_the_ledger_once(self):
        from repositories.financial_balance_repository import FinancialBalanceRepository
        repo = FinancialBalanceRepository(self.connection)
        wallet = repo.create_bank_account(name="محفظة اختبار", account_type="MOBILE_WALLET", bank_name=None,
                                          account_label=None)
        self.assertEqual(int(repo.lock_account("BANK", wallet)["movement_count"]), 0)
        repo.add_opening_balance(ledger="BANK", account_id=wallet, amount=D("2500.50"), created_by_user_id=None,
                                 occurred_at=repo.now())
        self.assertEqual(int(repo.lock_account("BANK", wallet)["movement_count"]), 1)
        row = self.sql("SELECT balance FROM vw_bank_balances WHERE bank_account_id = %s", (wallet,), fetch=True)[0]
        self.assertEqual(D(str(row["balance"])), D("2500.50"))
        code = self.sql("SELECT t.code FROM bank_movements m JOIN bank_movement_types t ON t.id = m.movement_type_id "
                        "WHERE m.bank_account_id = %s", (wallet,), fetch=True)
        self.assertEqual(code, [{"code": "OPENING_BALANCE"}])


if __name__ == "__main__":
    unittest.main()
