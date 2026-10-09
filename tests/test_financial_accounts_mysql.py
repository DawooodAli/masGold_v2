"""Explicit opt-in DEV validation of Financial Accounts V1 (025). Persists tagged history; never deletes it.

Run only with MASGOLD_FINANCIAL_ACCOUNTS_DEV=1 against configured/live masgold_v2 at migration 33.
Every manual operation carries the tag in its reason. Two deterministic tagged fixture accounts
(FADEV_BANK_FIXTURE / FADEV_WALLET_FIXTURE) are reused on every run — never one new account per test — so
every assertion is a delta against the balance read before the operation. Main Safe operations are
reversed again.
"""
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal as D
from uuid import uuid4

from app.session import AppSession
from config.database_config import DatabaseConfig
from database.connection import DatabaseConnection
from repositories.authentication_repository import AuthenticationRepository
from repositories.financial_account_repository import FinancialAccountRepository
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.correction_service import CorrectionError, CorrectionService, FO
from services.financial_account_service import FinancialAccountService

TAG = "FINANCIAL ACCOUNTS V1 DEV TEST DATA"
REVERSAL_REASON = TAG + " — reversal"
FIXTURES = (("FADEV_BANK_FIXTURE", "BANK"), ("FADEV_WALLET_FIXTURE", "MOBILE_WALLET"))


@unittest.skipUnless(os.getenv("MASGOLD_FINANCIAL_ACCOUNTS_DEV") == "1", "explicit DEV financial-account validation only")
class FinancialAccountsMysqlTests(unittest.TestCase):
    @staticmethod
    @contextmanager
    def transaction():
        config = DatabaseConfig.from_env()
        if config.database != "masgold_v2":
            raise RuntimeError("DEV target refused")
        c = DatabaseConnection(config).connect()
        try:
            q = c.cursor()
            q.execute("SELECT DATABASE()")
            assert q.fetchone()[0] == "masgold_v2"
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert q.fetchone()[0] == 36
            q.execute("SET SESSION innodb_lock_wait_timeout = 10")
            q.close()
            c.commit()
            c.start_transaction()
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def query(self, sql, params=()):
        with self.transaction() as c:
            q = c.cursor(dictionary=True)
            q.execute(sql, params)
            rows = q.fetchall()
            q.close()
            return rows

    def setUp(self):
        self.session = AppSession()
        uid = self.query("SELECT u.id FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id "
                         "WHERE r.code='OWNER' AND u.is_active=1 AND u.must_change_password=0 ORDER BY u.id LIMIT 1")[0]["id"]
        with self.transaction() as c:
            auth = AuthenticationRepository(c)
            identity = auth.get_identity(uid)
            self.session._establish(AuthenticationResult(uid, identity["employee_id"], identity["display_name"],
                                                         auth.effective_permissions(uid), _VERIFIED), 0)
            accounts = [self.fixture_account(c, code, kind) for code, kind in FIXTURES]
        self.bank, self.wallet = accounts
        self.fin = FinancialAccountService(self.session, transaction_factory=self.transaction)
        self.correction = CorrectionService(self.session, transaction_factory=self.transaction)

    @staticmethod
    def fixture_account(connection, code, kind):
        """The one reusable tagged fixture account for this purpose (created on the first run only)."""
        q = connection.cursor(dictionary=True)
        q.execute("SELECT id, name, notes, account_type FROM bank_accounts WHERE code=%s FOR UPDATE", (code,))
        row = q.fetchone()
        if row is None:
            q.execute("INSERT INTO bank_accounts(code,name,account_type,notes) VALUES (%s,%s,%s,%s)", (code, TAG, kind, TAG))
            account_id = q.lastrowid
        else:
            assert (row["name"], row["notes"], row["account_type"]) == (TAG, TAG, kind), "fixture code reused by real data"
            account_id = row["id"]
            q.execute("UPDATE bank_accounts SET is_active=TRUE WHERE id=%s AND is_active=FALSE", (account_id,))
        q.close()
        return f"BANK:{account_id}"

    def balance(self, key):
        ledger, number = key.split(":")
        with self.transaction() as c:
            return D(str(FinancialAccountRepository(c).balance(ledger, int(number))))

    def counts(self):
        return self.query(
            "SELECT (SELECT COUNT(*) FROM financial_operations) ops, (SELECT COUNT(*) FROM financial_operation_legs) legs,"
            " (SELECT COUNT(*) FROM cash_movements WHERE reference_type='FINANCIAL_OPERATION') cash,"
            " (SELECT COUNT(*) FROM bank_movements WHERE reference_type='FINANCIAL_OPERATION') bank,"
            " (SELECT COUNT(*) FROM audit_log WHERE entity_type='FINANCIAL_OPERATION') audit")[0]

    # ------------------------------------------------------------ Scenario A — Manual Deposit (+ E reversal)
    def test_a_deposit_into_cash_statement_and_reversal(self):
        before = self.balance("CASH:1")
        result = self.fin.deposit("CASH:1", "10000", TAG + " — إيداع رأس مال", str(uuid4()))
        self.assertEqual(self.balance("CASH:1"), before + D("10000"))
        row = next(r for r in self.fin.statement("CASH:1", search=result["code"])["rows"])
        self.assertEqual((row["source_type"], row["source_reference"], row["amount_delta"]),
                         ("MANUAL_DEPOSIT", result["code"], D("10000.00")))
        self.assertEqual(row["balance_after"], before + D("10000"))
        self.assertTrue(row["actor_name"])
        self.assertIn(TAG, row["operation_reason"])
        detail = self.fin.operation(result["operation_id"])
        self.assertEqual((detail["code"], detail["reversed_by"], detail["from_name"]), (result["code"], None, None))
        self.assertTrue(detail["to_name"])
        # Scenario E: exact historical reversal; Main Safe returns to its prior balance.
        rv = self.correction.reverse(FO, result["operation_id"], REVERSAL_REASON)
        self.assertEqual(self.balance("CASH:1"), before)
        self.assertEqual(self.fin.operation(result["operation_id"])["reversed_by"], rv["code"])
        original = self.query("SELECT amount, reason FROM financial_operations WHERE id=%s", (result["operation_id"],))[0]
        self.assertEqual(original["amount"], D("10000.00"))
        with self.assertRaises(CorrectionError) as again:
            self.correction.reverse(FO, result["operation_id"], REVERSAL_REASON)
        self.assertEqual(again.exception.code, "ALREADY_REVERSED")
        rv_row = next(r for r in self.fin.statement("CASH:1", source="FULL_REVERSAL", search=rv["code"])["rows"])
        self.assertEqual((rv_row["amount_delta"], rv_row["corrected_source_type"]), (D("-10000.00"), FO))

    # ------------------------------------------------------------ Scenario B — withdrawal to negative
    def test_b_withdrawal_drives_a_controlled_account_negative(self):
        wallet, bank = self.balance(self.wallet), self.balance(self.bank)
        amount = max(wallet, D("0.00")) + D("2500.00")                           # always ends below zero
        result = self.fin.withdraw(self.wallet, str(amount), TAG + " — سحب", str(uuid4()))
        self.assertEqual(self.balance(self.wallet), wallet - amount)
        self.assertLess(self.balance(self.wallet), 0)
        self.assertEqual(self.balance(self.bank), bank)                          # no automatic fallback
        row = self.fin.statement(self.wallet)["rows"][0]
        self.assertEqual((row["source_reference"], row["balance_after"]), (result["code"], wallet - amount))

    # ------------------------------------------------------------ Scenario C — transfer (+ E reversal)
    def test_c_transfer_cash_to_bank_and_exact_reversal(self):
        cash, bank = self.balance("CASH:1"), self.balance(self.bank)
        result = self.fin.transfer("CASH:1", self.bank, "20000", TAG, str(uuid4()))
        self.assertTrue(result["code"].startswith("TR"))
        self.assertEqual((self.balance("CASH:1"), self.balance(self.bank)), (cash - D("20000"), bank + D("20000")))
        legs = self.query("SELECT l.leg_role, l.amount, COALESCE(c.reference_code,b.reference_code) code,"
                          " COALESCE(c.transfer_group_id,b.transfer_group_id) grp FROM financial_operation_legs l"
                          " LEFT JOIN cash_movements c ON c.id=l.cash_movement_id"
                          " LEFT JOIN bank_movements b ON b.id=l.bank_movement_id WHERE l.operation_id=%s ORDER BY l.id",
                          (result["operation_id"],))
        self.assertEqual([(l["leg_role"], l["amount"], l["code"]) for l in legs],
                         [("OUT", D("-20000.00"), result["code"]), ("IN", D("20000.00"), result["code"])])
        self.assertEqual(legs[0]["grp"], legs[1]["grp"])
        self.assertEqual(sum(l["amount"] for l in legs), 0)                        # net shop money unchanged
        self.correction.reverse(FO, result["operation_id"], REVERSAL_REASON)
        self.assertEqual((self.balance("CASH:1"), self.balance(self.bank)), (cash, bank))

    # ------------------------------------------------------------ Scenario D — source traceability
    def test_d_statement_maps_every_existing_source_and_running_balance(self):
        seen = {}
        for source in ("INVOICE", "EXCHANGE", "RETURN", "CUSTOMER_ACCOUNT_SETTLEMENT", "FULL_REVERSAL",
                       "SETTLEMENT_CORRECTION", "OPENING_BALANCE"):
            rows = self.fin.statement("CASH:1", source=source)["rows"]
            if rows:
                seen[source] = rows[0]
        for source in ("INVOICE", "EXCHANGE", "RETURN", "CUSTOMER_ACCOUNT_SETTLEMENT", "FULL_REVERSAL",
                       "SETTLEMENT_CORRECTION"):
            self.assertIn(source, seen, source)
        self.assertRegex(seen["EXCHANGE"]["source_reference"], r"^EX\d{6}")
        self.assertRegex(seen["RETURN"]["source_reference"], r"^RT\d{6}")
        self.assertRegex(seen["CUSTOMER_ACCOUNT_SETTLEMENT"]["source_reference"], r"^CS\d{6}")
        self.assertRegex(seen["FULL_REVERSAL"]["source_reference"], r"^RV\d{6}")
        self.assertRegex(seen["SETTLEMENT_CORRECTION"]["source_reference"], r"^FC\d{6}")
        for source in ("INVOICE", "EXCHANGE", "RETURN"):
            invoice_id = seen[source]["invoice_id"]
            self.assertIsInstance(invoice_id, str)
            self.assertTrue(self.query("SELECT 1 FROM invoices WHERE id=%s", (invoice_id,)))
        self.assertIsNone(seen["CUSTOMER_ACCOUNT_SETTLEMENT"]["invoice_id"])      # no fabricated invoice
        self.assertEqual(self.query("SELECT COUNT(*) n FROM cash_movements m WHERE m.cash_account_id=1 AND NOT ("
                                    "m.reference_type IN ('INVOICE','INVOICE_LIFECYCLE','INVOICE_RETURN',"
                                    "'CUSTOMER_ACCOUNT_SETTLEMENT','CUSTOMER_DEPOSIT','TRANSACTION_CORRECTION','FINANCIAL_OPERATION',"
                                    "'OPENING'))")
                         [0]["n"], 0)
        page = self.fin.statement("CASH:1")
        rows = page["rows"]
        self.assertEqual(rows[0]["balance_after"], D(page["account"]["balance"]))    # newest = current balance
        for newer, older in zip(rows, rows[1:]):
            self.assertEqual(newer["balance_after"] - older["balance_after"], newer["amount_delta"])
        self.assertTrue(all(r["source_type"] != "UNKNOWN" for r in rows))

    # ------------------------------------------------------------ Scenario F — race / double submit
    def test_f_concurrent_duplicate_submit_posts_exactly_once(self):
        key, start = str(uuid4()), threading.Barrier(4)
        before, bank = self.counts(), self.balance(self.bank)

        def submit(_):
            start.wait()
            return self.fin.deposit(self.bank, "300", TAG + " — race", key)
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(submit, range(4)))
        self.assertEqual(len({r["operation_id"] for r in results}), 1)
        self.assertEqual(sum(1 for r in results if not r["duplicate"]), 1)
        after = self.counts()
        self.assertEqual((after["ops"] - before["ops"], after["bank"] - before["bank"]), (1, 1))
        self.assertEqual(self.balance(self.bank), bank + D("300.00"))

    def test_f_concurrent_distinct_operations_serialize(self):
        bank, wallet = self.balance(self.bank), self.balance(self.wallet)
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda i: self.fin.transfer(self.bank, self.wallet, "10", TAG, str(uuid4())), range(8)))
        self.assertEqual((self.balance(self.bank), self.balance(self.wallet)), (bank - D("80"), wallet + D("80")))

    # ------------------------------------------------------------ real atomicity (rollback injection)
    def test_rollback_injection_leaves_no_partial_state(self):
        class Failing(FinancialAccountRepository):
            step = None

            def insert_operation(self, **kw):
                value = super().insert_operation(**kw)
                if Failing.step == "header":
                    raise RuntimeError("header")
                return value

            def post_leg(self, **kw):
                value = super().post_leg(**kw)
                if Failing.step == kw["leg_role"]:
                    raise RuntimeError(kw["leg_role"])
                return value

            def audit(self, **kw):
                super().audit(**kw)
                if Failing.step == "audit":
                    raise RuntimeError("audit")
        service = FinancialAccountService(self.session, transaction_factory=self.transaction, repository=Failing)
        for step in ("header", "OUT", "IN", "audit"):
            with self.subTest(step=step):
                before, balances = self.counts(), (self.balance(self.bank), self.balance(self.wallet))
                Failing.step = step
                with self.assertRaises(RuntimeError):
                    service.transfer(self.bank, self.wallet, "55", TAG + " — rollback " + step, str(uuid4()))
                self.assertEqual(self.counts(), before)
                self.assertEqual((self.balance(self.bank), self.balance(self.wallet)), balances)

    # ------------------------------------------------------------ database constraints and grants
    def test_database_refuses_invalid_shapes(self):
        uid = self.session.current_actor().user_id
        bank_id = int(self.bank.split(":")[1])
        bad = [("ACCOUNT_TRANSFER", None, bank_id, None, bank_id, "x"),        # same account
               ("MANUAL_DEPOSIT", None, None, None, bank_id, "   "),             # blank reason
               ("MANUAL_WITHDRAWAL", None, None, None, bank_id, "x")]            # withdrawal without source
        for operation_type, fc, fb, tc, tb, reason in bad:
            with self.subTest(operation_type=operation_type), self.assertRaises(Exception) as refused:
                with self.transaction() as c:
                    q = c.cursor()
                    q.execute("INSERT INTO financial_operations(operation_type,request_key,request_hash,amount,"
                              "from_cash_account_id,from_bank_account_id,to_cash_account_id,to_bank_account_id,reason,"
                              "performed_by_user_id,occurred_at) VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,NOW())",
                              (operation_type, str(uuid4()), "0" * 64, fc, fb, tc, tb, reason, uid))
            self.assertEqual(getattr(refused.exception, "errno", None), 3819)        # CHECK constraint violated
        with self.assertRaises(Exception) as refused:
            with self.transaction() as c:
                q = c.cursor()
                q.execute("INSERT INTO transaction_corrections(correction_type,correction_status,source_type,source_id,"
                          "source_reference,chain_sequence,reason,performed_by_user_id,occurred_at) VALUES "
                          "('SETTLEMENT_CORRECTION','FINALIZED','FINANCIAL_OPERATION',1,'FO000001',1,%s,%s,NOW())",
                          (TAG, uid))
        self.assertEqual(getattr(refused.exception, "errno", None), 3819)       # FO is full reversal only

    # ------------------------------------------------------------ the daily cash screen (026)
    def test_g_cash_screen_categories_bank_deposit_and_real_labels(self):
        cash, bank = self.balance("CASH:1"), self.balance(self.bank)
        made = [self.fin.cash_movement("CASH:1", "IN", "REVENUE", "2000", TAG, str(uuid4())),
                self.fin.cash_movement("CASH:1", "OUT", "EXPENSE", "1500", "", str(uuid4())),
                self.fin.cash_movement("CASH:1", "OUT", "SALARY", "12000", TAG, str(uuid4())),
                self.fin.cash_movement("CASH:1", "OUT", "BANK_DEPOSIT", "20000", TAG, str(uuid4()), self.bank)]
        self.assertEqual(self.balance("CASH:1"), cash + D("2000") - D("1500") - D("12000") - D("20000"))
        self.assertEqual(self.balance(self.bank), bank + D("20000"))
        stored = self.query("SELECT category, reason FROM financial_operations WHERE id IN (%s,%s,%s) ORDER BY id",
                            tuple(m["operation_id"] for m in made[:3]))
        self.assertEqual([r["category"] for r in stored], ["REVENUE", "EXPENSE", "SALARY"])
        self.assertIsNone(stored[1]["reason"])                                  # categorised: note optional
        view = self.fin.cash_view()
        ids = {m["operation_id"] for m in made}
        mine_in = [r["label"] for r in view["in"]["rows"] if r["operation_id"] in ids]
        mine_out = [r["label"] for r in view["out"]["rows"] if r["operation_id"] in ids]
        self.assertEqual(mine_in, ["إيراد"])
        self.assertEqual(sorted(mine_out), sorted(["مصروفات", "مرتبات", "إيداع بنك"]))
        self.assertEqual(D(view["account"]["balance"]), self.balance("CASH:1"))
        today = self.query("SELECT COALESCE(SUM(CASE WHEN amount_delta>0 THEN amount_delta END),0) i,"
                           " COALESCE(-SUM(CASE WHEN amount_delta<0 THEN amount_delta END),0) o FROM cash_movements"
                           " WHERE cash_account_id=1 AND occurred_at>=CURDATE() AND occurred_at<CURDATE()+INTERVAL 1 DAY")[0]
        self.assertEqual((D(view["in"]["total"]), D(view["out"]["total"])), (today["i"], today["o"]))
        # Every historical Main Safe movement has a business name (none falls back to «حركة أخرى»).
        history = self.fin.cash_view("2020-01-01", view["end"])
        self.assertNotIn("حركة أخرى", {r["label"] for r in history["in"]["rows"] + history["out"]["rows"]})
        for m in made:                                                          # leave Main Safe unchanged
            self.correction.reverse(FO, m["operation_id"], REVERSAL_REASON)
        self.assertEqual((self.balance("CASH:1"), self.balance(self.bank)), (cash, bank))
        labels = {r["label"] for r in self.fin.cash_view()["in"]["rows"]}
        self.assertIn("إلغاء عملية", labels)

    # ------------------------------------------------------------ عربون / استرداد عربون (027)
    def deposit_customer(self):
        """One reusable tagged fixture customer (created on the first run only)."""
        from repositories.customer_repository import CustomerRepository
        name = TAG + " DEPOSIT FIXTURE"
        with self.transaction() as c:
            q = c.cursor(dictionary=True)
            q.execute("SELECT id FROM customers WHERE name=%s AND notes=%s ORDER BY id LIMIT 1 FOR UPDATE", (name, TAG))
            row = q.fetchone()
            q.close()
            if row:
                return str(row["id"])
            return str(CustomerRepository(c).create_customer(name, None, None, None, TAG,
                                                             self.session.current_actor().user_id))

    def customer_balance(self, cid):
        from repositories.customer_repository import CustomerRepository
        with self.transaction() as c:
            return D(str(CustomerRepository(c).get_balance(int(cid))))

    def test_h_deposit_refund_statement_labels_and_limits(self):
        from services.customer_account_service import CustomerAccountService
        from services.customer_deposit_service import CustomerDepositService
        cid, deposits = self.deposit_customer(), CustomerDepositService(self.session, transaction_factory=self.transaction)
        # Leave the fixture exactly as found at the end (credit from earlier runs, if any, is refunded first).
        start_balance, cash = self.customer_balance(cid), self.balance("CASH:1")
        self.assertLessEqual(start_balance, 0)
        leftover = D(deposits.available(cid))
        if leftover:
            deposits.refund(cid, str(leftover), TAG, str(uuid4()))
        base, cash = self.customer_balance(cid), self.balance("CASH:1")
        dp = deposits.deposit(cid, "5000", TAG, str(uuid4()))
        self.assertEqual((self.customer_balance(cid), self.balance("CASH:1")), (base - 5000, cash + 5000))
        dr = deposits.refund(cid, "2000", TAG, str(uuid4()))
        self.assertEqual((self.customer_balance(cid), self.balance("CASH:1")), (base - 3000, cash + 3000))
        with self.assertRaises(CorrectionError) as over:
            deposits.refund(cid, "3000.01", TAG, str(uuid4()))
        self.assertEqual(over.exception.code, "REFUND_EXCEEDS_DEPOSIT")
        # Customer statement (real SQL): both rows, their DP / DR references and the resulting balance.
        rows = CustomerAccountService(self.session, transaction_factory=self.transaction).statement(cid)["rows"]
        by_ref = {r["source_reference"]: r for r in rows}
        self.assertEqual((by_ref[dp["code"]]["source_type"], by_ref[dr["code"]]["source_type"]),
                         ("CUSTOMER_DEPOSIT", "CUSTOMER_DEPOSIT_REFUND"))
        self.assertEqual(by_ref[dr["code"]]["running_balance"], base - 3000)
        self.assertEqual(str(by_ref[dp["code"]]["deposit_id"]), dp["id"])
        # Daily cash screen (real SQL): عربون inside, استرداد عربون outside, no codes.
        view = self.fin.cash_view()
        self.assertIn("عربون", [r["label"] for r in view["in"]["rows"]])
        self.assertIn("استرداد عربون", [r["label"] for r in view["out"]["rows"]])
        # Reversal: the refund first (the deposit is blocked while refunded), then the deposit.
        with self.assertRaises(CorrectionError) as blocked:
            self.correction.reverse("CUSTOMER_DEPOSIT", dp["id"], REVERSAL_REASON)
        self.assertEqual(blocked.exception.code, "DEPOSIT_ALREADY_REFUNDED")
        self.correction.reverse("CUSTOMER_DEPOSIT", dr["id"], REVERSAL_REASON)
        self.correction.reverse("CUSTOMER_DEPOSIT", dp["id"], REVERSAL_REASON)
        self.assertEqual((self.customer_balance(cid), self.balance("CASH:1")), (base, cash))
        self.assertEqual(D(deposits.available(cid)), 0)

    def test_i_concurrent_refunds_cannot_over_refund(self):
        from services.customer_deposit_service import CustomerDepositService
        cid, deposits = self.deposit_customer(), CustomerDepositService(self.session, transaction_factory=self.transaction)
        base, cash = self.customer_balance(cid), self.balance("CASH:1")
        dp = deposits.deposit(cid, "1000", TAG, str(uuid4()))
        start = threading.Barrier(4)

        def refund(_):
            start.wait()
            try:
                return deposits.refund(cid, "500", TAG, str(uuid4()))["code"]
            except CorrectionError as exc:
                return exc.code
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(refund, range(4)))
        self.assertEqual(sorted(r[:2] for r in results), ["DR", "DR", "RE", "RE"])   # 2 refunds, 2 refused
        self.assertEqual(self.customer_balance(cid), base)                          # exactly the deposit, no more
        self.assertEqual(self.balance("CASH:1"), cash)
        self.assertEqual(D(deposits.available(cid)), 0)

    def test_j_deposit_rollback_leaves_cash_and_customer_unchanged(self):
        from repositories.customer_deposit_repository import CustomerDepositRepository
        from services.customer_deposit_service import CustomerDepositService

        class Failing(CustomerDepositRepository):
            step = None

            def post_cash(self, **kw):
                value = super().post_cash(**kw)
                if Failing.step == "cash":
                    raise RuntimeError("cash")
                return value

            def audit(self, **kw):
                super().audit(**kw)
                if Failing.step == "audit":
                    raise RuntimeError("audit")
        cid = self.deposit_customer()
        service = CustomerDepositService(self.session, transaction_factory=self.transaction, repository=Failing)
        for step in ("cash", "audit"):
            with self.subTest(step=step):
                before = (self.customer_balance(cid), self.balance("CASH:1"),
                          self.query("SELECT COUNT(*) n FROM customer_deposits")[0]["n"])
                Failing.step = step
                with self.assertRaises(RuntimeError):
                    service.deposit(cid, "77", TAG, str(uuid4()))
                self.assertEqual((self.customer_balance(cid), self.balance("CASH:1"),
                                  self.query("SELECT COUNT(*) n FROM customer_deposits")[0]["n"]), before)
        # The database itself refuses a refund that would cross zero.
        uid = self.session.current_actor().user_id
        with self.assertRaises(Exception) as refused:
            with self.transaction() as c:
                q = c.cursor()
                q.execute("INSERT INTO customer_deposits(customer_id,direction,amount,request_key,request_hash,"
                          "cash_account_id,balance_before,balance_after,performed_by_user_id,occurred_at) VALUES "
                          "(%s,'OUT',100,%s,%s,1,-50,50,%s,NOW())", (cid, str(uuid4()), "0" * 64, uid))
        self.assertEqual(getattr(refused.exception, "errno", None), 3819)

    def test_fixtures_are_reused_not_multiplied(self):
        rows = self.query("SELECT code, COUNT(*) n FROM bank_accounts WHERE code IN (%s,%s) GROUP BY code",
                          tuple(code for code, _ in FIXTURES))
        self.assertEqual({r["code"]: r["n"] for r in rows}, {code: 1 for code, _ in FIXTURES})
        self.assertEqual(self.query("SELECT COUNT(*) n FROM bank_accounts WHERE code LIKE 'FADEV\\_%%' "
                                    "AND code NOT IN (%s,%s) AND is_active=TRUE",
                                    tuple(code for code, _ in FIXTURES))[0]["n"], 0)

    def test_role_grants(self):
        roles = {r["code"] for r in self.query(
            "SELECT r.code FROM roles r JOIN role_permissions rp ON rp.role_id=r.id JOIN permissions p "
            "ON p.id=rp.permission_id WHERE p.code='FINANCIAL_ACCOUNT_OPERATE'")}
        self.assertEqual(roles, {"OWNER", "MANAGER", "CASHIER"})


if __name__ == "__main__":
    unittest.main()
