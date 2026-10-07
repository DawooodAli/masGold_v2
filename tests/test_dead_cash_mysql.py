"""Explicit opt-in DEV validation of Dead Cash V1 (028) on masgold_v2 with ONE reusable tagged fixture customer.

Run only with MASGOLD_DEAD_CASH_DEV=1. The owner's password is never known to this test: the successful path
uses a password-confirmation stub, while the REAL AuthenticationService is proven to refuse a wrong password
against the real stored Argon2 hash (writing nothing). The fixture is restored to a zero balance and an
inactive warning at the end (history rows are kept, never deleted).
"""
from decimal import Decimal as D
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

from app.session import AppSession
from config.database_config import DatabaseConfig
from database.connection import DatabaseConnection
from repositories.authentication_repository import AuthenticationRepository
from repositories.customer_repository import CustomerRepository
from repositories.dead_cash_repository import DeadCashRepository
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.dead_cash_service import DeadCashError, DeadCashService

TAG = "DEAD CASH V1 DEV TEST DATA"
FIXTURE = TAG + " FIXTURE"


class Confirmed:
    """Password-confirmation stub for the successful DEV path (the real primitive is tested separately)."""
    def verify_user_password(self, user_id, password):
        return True


@unittest.skipUnless(os.getenv("MASGOLD_DEAD_CASH_DEV") == "1", "explicit DEV Dead Cash validation only")
class DeadCashMysqlTests(unittest.TestCase):
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
            assert q.fetchone()[0] == 33
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
            q = c.cursor(dictionary=True)
            q.execute("SELECT id FROM customers WHERE name=%s AND notes=%s ORDER BY id LIMIT 1 FOR UPDATE", (FIXTURE, TAG))
            row = q.fetchone()
            q.close()
            self.cid = str(row["id"]) if row else str(CustomerRepository(c).create_customer(FIXTURE, None, None, None,
                                                                                            TAG, uid))
        self.uid = uid
        self.service = DeadCashService(self.session, authentication=Confirmed())
        self.assertEqual(self.balance(), 0, "the reusable fixture must start clean")
        self.adjust(D("10000"), "Explicit Dead Cash V1 fixture receivable (tagged test data).")

    def tearDown(self):
        # Restore the clean state: zero balance, no active warning (the history rows stay).
        balance = self.balance()
        if balance:
            self.adjust(-balance, "Restores the tagged Dead Cash V1 fixture to zero.")
        with self.transaction() as c:
            repo = DeadCashRepository(c)
            flag = repo.get_active_warning(self.cid, for_update=True)
            if flag:
                repo.deactivate_warning(flag_id=flag["flag_id"], deactivated_by_user_id=self.uid,
                                        reason=TAG + " — fixture reset")

    def adjust(self, amount, note):
        with self.transaction() as c:
            CustomerRepository(c).add_transaction(customer_id=int(self.cid), type_code="MANUAL_ADJUSTMENT",
                                                  amount_delta=D(amount), created_by_user_id=self.uid,
                                                  reference_type="DEAD_CASH_DEV_TEST", reference_code=TAG,
                                                  description=TAG, notes=note)

    def balance(self):
        with self.transaction() as c:
            return D(str(CustomerRepository(c).get_balance(int(self.cid))))

    def money(self):
        return self.query("SELECT (SELECT COUNT(*) FROM cash_movements) cash_n, (SELECT COALESCE(SUM(amount_delta),0) "
                          "FROM cash_movements) cash_sum, (SELECT COUNT(*) FROM bank_movements) bank_n, "
                          "(SELECT COALESCE(SUM(amount_delta),0) FROM bank_movements) bank_sum, "
                          "(SELECT COUNT(*) FROM financial_operations) ops")[0]

    def write_off(self, amount, key=None, expected=None, service=None):
        return (service or self.service).write_off(self.cid, amount, TAG + " — شطب اختباري", "stubbed",
                                                   str(self.balance() if expected is None else expected),
                                                   key or str(uuid4()))

    # ------------------------------------------------------------ Scenario 1–11
    def test_a_write_off_statement_history_no_money_and_exact_reversal(self):
        from services.customer_account_service import CustomerAccountService
        from services.customer_service import CustomerService
        from services.financial_account_service import FinancialAccountService
        money = self.money()
        result = self.write_off("4000")
        self.assertEqual(self.balance(), D("6000.00"))                            # active receivable 6,000
        self.assertEqual(self.money(), money)                                    # safe / bank / wallet / FO unchanged
        labels = {r["label"] for r in FinancialAccountService(self.session).cash_view()["in"]["rows"]
                  + FinancialAccountService(self.session).cash_view()["out"]["rows"]}
        self.assertFalse({"شطب رصيد", "عكس شطب رصيد"} & labels)                   # never on الخزنة
        warning = self.query("SELECT related_dead_cash_id FROM customer_flags WHERE customer_id=%s AND "
                             "flag_type='DEAD_CASH_WARNING' AND is_active=TRUE", (self.cid,))
        self.assertEqual(len(warning), 1)
        rows = CustomerAccountService(self.session).statement(self.cid)["rows"]
        dc = next(r for r in rows if r["source_reference"] == result["code"])
        self.assertEqual((dc["source_type"], dc["amount_delta"], dc["running_balance"]),
                         ("DEAD_CASH_WRITE_OFF", D("-4000.00"), D("6000.00")))
        history = CustomerService(self.session).get_dead_cash_history(self.cid)["items"]
        self.assertEqual((str(history[0]["dead_cash_id"]), history[0]["is_reversed"]), (result["id"], 0))
        profile = CustomerService(self.session).get_customer_profile(self.cid)
        self.assertGreaterEqual(int(profile["dead_cash_summary"]["active_count"]), 1)
        audit = self.query("SELECT event_type, metadata_json FROM audit_log WHERE entity_type='DEAD_CASH_WRITEOFF' "
                           "AND entity_id=%s ORDER BY id", (result["id"],))
        self.assertEqual(audit[0]["event_type"], "DEAD_CASH_WRITTEN_OFF")
        # Reversal: exact amount back, the write-off stays (معكوس), still no money.
        reversed_ = DeadCashService(self.session, authentication=Confirmed()).reverse(result["id"], TAG + " — عكس", "stubbed")
        self.assertEqual(self.balance(), D("10000.00"))
        self.assertEqual(self.money(), money)
        record = self.query("SELECT amount, balance_before, balance_after, is_reversed, reversal_reason "
                            "FROM dead_cash_writeoffs WHERE id=%s", (result["id"],))[0]
        self.assertEqual((record["amount"], record["balance_before"], record["balance_after"], record["is_reversed"]),
                         (D("4000.00"), D("10000.00"), D("6000.00"), 1))
        rows = CustomerAccountService(self.session).statement(self.cid)["rows"]
        self.assertIn(reversed_["reversal_code"], {r["source_reference"] for r in rows})
        with self.assertRaises(DeadCashError) as again:
            DeadCashService(self.session, authentication=Confirmed()).reverse(result["id"], TAG, "stubbed")
        self.assertEqual(again.exception.code, "ALREADY_REVERSED")

    def test_b_real_password_check_refuses_a_wrong_password_and_writes_nothing(self):
        real = DeadCashService(self.session)                                      # the REAL AuthenticationService
        before = (self.balance(), self.query("SELECT COUNT(*) n FROM dead_cash_writeoffs")[0]["n"])
        with self.assertRaises(DeadCashError) as refused:
            self.write_off("1000", service=real)
        self.assertEqual(refused.exception.code, "PASSWORD_INVALID")
        self.assertEqual((self.balance(), self.query("SELECT COUNT(*) n FROM dead_cash_writeoffs")[0]["n"]), before)

    # ------------------------------------------------------------ concurrency / idempotency
    def test_c_two_concurrent_write_offs_on_the_same_dialog_balance_post_once(self):
        opened, start = self.balance(), threading.Barrier(2)

        def attempt(_):
            start.wait()
            try:
                return self.write_off("7000", expected=opened)["code"]
            except DeadCashError as exc:
                return exc.code
        with ThreadPoolExecutor(2) as pool:
            results = sorted(pool.map(attempt, range(2)))
        self.assertEqual(results[0], "BALANCE_CHANGED")                           # the second sees the new balance
        self.assertTrue(results[1].startswith("DC"))                             # exactly one write-off posted
        self.assertEqual(self.balance(), D("3000.00"))                            # never over-written-off
        self.cleanup_writeoffs()

    def test_d_double_submit_posts_once_and_a_changed_balance_is_refused(self):
        key, opened = str(uuid4()), self.balance()
        first = self.write_off("1000", key=key, expected=opened)
        second = self.write_off("1000", key=key, expected=opened)
        self.assertEqual((second["code"], second["duplicate"]), (first["code"], True))
        self.assertEqual(self.balance(), D("9000.00"))
        self.adjust(D("-500"), "Simulates a settlement while a write-off dialog is open (tagged test data).")
        with self.assertRaises(DeadCashError) as changed:
            self.write_off("100", expected=D("9000.00"))
        self.assertEqual(changed.exception.code, "BALANCE_CHANGED")
        self.cleanup_writeoffs()

    def test_f_two_concurrent_reversals_restore_the_balance_once(self):
        result = self.write_off("2500")
        self.assertEqual(self.balance(), D("7500.00"))
        start = threading.Barrier(2)

        def attempt(_):
            start.wait()
            try:
                return DeadCashService(self.session, authentication=Confirmed()).reverse(
                    result["id"], TAG + " — concurrent reversal", "stubbed")["reversal_code"]
            except DeadCashError as exc:
                return exc.code
        with ThreadPoolExecutor(2) as pool:
            outcomes = sorted(pool.map(attempt, range(2)))
        self.assertEqual(outcomes, ["ALREADY_REVERSED", "DCR%06d" % int(result["id"])])
        self.assertEqual(self.balance(), D("10000.00"))                           # restored exactly once
        rows = self.query("SELECT COUNT(*) n FROM customer_transactions t JOIN customer_transaction_types y "
                          "ON y.id=t.transaction_type_id WHERE t.customer_id=%s AND y.code='DEAD_CASH_REVERSAL' "
                          "AND t.reference_id=%s", (self.cid, result["id"]))[0]["n"]
        self.assertEqual(rows, 1)                                                 # one reversal ledger row

    def test_e_database_refuses_invalid_write_off_rows(self):
        tx = self.query("SELECT id FROM customer_transactions WHERE customer_id=%s ORDER BY id DESC LIMIT 1", (self.cid,))[0]["id"]
        for before, after, amount, direction in (("-10", "-20", "10", "CUSTOMER_OWES_SHOP"),     # not a receivable
                                                 ("100", "-50", "150", "CUSTOMER_OWES_SHOP"),   # crosses zero
                                                 ("100", "90", "10", "SHOP_OWES_CUSTOMER")):    # prototype direction
            with self.subTest(before=before, direction=direction), self.assertRaises(Exception) as refused:
                with self.transaction() as c:
                    q = c.cursor()
                    q.execute("INSERT INTO dead_cash_writeoffs(customer_id,writeoff_direction,amount,balance_before,"
                              "balance_after,writeoff_transaction_id,reason) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                              (self.cid, direction, amount, before, after, tx, TAG))
            self.assertEqual(getattr(refused.exception, "errno", None), 3819)

    def cleanup_writeoffs(self):
        for row in self.query("SELECT id FROM dead_cash_writeoffs WHERE customer_id=%s AND is_reversed=FALSE", (self.cid,)):
            DeadCashService(self.session, authentication=Confirmed()).reverse(str(row["id"]), TAG + " — cleanup", "stubbed")


if __name__ == "__main__":
    unittest.main()
