"""Explicit opt-in DEV lock proof for the two authorization paths. Nothing is committed: every probe runs in
a transaction that is rolled back (a revocation is made and observed inside one uncommitted transaction).

Run only with MASGOLD_AUTHORIZATION_DEV=1 against configured/live masgold_v2.
"""
import os
import threading
import time
import unittest
from contextlib import contextmanager

from app.session import AppSession
from config.database_config import DatabaseConfig
from database.connection import DatabaseConnection
from repositories.authentication_repository import AuthenticationRepository
from repositories.financial_account_repository import FinancialAccountRepository
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.authorization_service import AuthorizationService

HOLD = 6                                        # seconds one slow reader keeps its transaction open


@unittest.skipUnless(os.getenv("MASGOLD_AUTHORIZATION_DEV") == "1", "explicit DEV authorization lock proof only")
class AuthorizationLockTests(unittest.TestCase):
    @staticmethod
    def connect(lock_wait=3):
        config = DatabaseConfig.from_env()
        if config.database != "masgold_v2":
            raise RuntimeError("DEV target refused")
        c = DatabaseConnection(config).connect()
        q = c.cursor()
        q.execute("SELECT DATABASE()")
        assert q.fetchone()[0] == "masgold_v2"
        q.execute(f"SET SESSION innodb_lock_wait_timeout = {int(lock_wait)}")
        q.close()
        c.commit()
        c.start_transaction()
        return c

    @classmethod
    @contextmanager
    def transaction(cls):
        """Read-only probes: always rolled back."""
        c = cls.connect()
        try:
            yield c
        finally:
            c.rollback()
            c.close()

    def setUp(self):
        self.session = AppSession()
        with self.transaction() as c:
            q = c.cursor(dictionary=True)
            q.execute("SELECT u.id FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id "
                      "WHERE r.code='OWNER' AND u.is_active=1 AND u.must_change_password=0 ORDER BY u.id LIMIT 1")
            self.uid = q.fetchone()["id"]
            q.close()
            auth = AuthenticationRepository(c)
            identity = auth.get_identity(self.uid)
            self.session._establish(AuthenticationResult(self.uid, identity["employee_id"], identity["display_name"],
                                                         auth.effective_permissions(self.uid), _VERIFIED), 0)
        self.actor = self.session.current_actor()
        self.authorization = AuthorizationService(self.session)

    def elapsed(self, call):
        started = time.perf_counter()
        try:
            call()
            return time.perf_counter() - started, None
        except Exception as exc:                                        # noqa: BLE001 — the lock errno is the result
            return time.perf_counter() - started, getattr(exc, "errno", type(exc).__name__)

    # ------------------------------------------------------------ raw lock behaviour of the two paths
    def test_readers_never_block_each_other_or_a_writer(self):
        holder = self.connect()
        try:
            self.authorization.require_permission_read(holder, self.actor, "APP_LOGIN")     # reader A, left open
            with self.transaction() as other:
                took, error = self.elapsed(lambda: self.authorization.require_permission_read(other, self.actor, "APP_LOGIN"))
                self.assertIsNone(error)
                self.assertLess(took, 1.0)                                                  # reader B not queued
            with self.transaction() as writer:
                took, error = self.elapsed(
                    lambda: self.authorization.require_permission_for_write(writer, self.actor, "APP_LOGIN"))
                self.assertIsNone(error)                                                    # a reader never blocks a write
                self.assertLess(took, 1.0)
        finally:
            holder.rollback()
            holder.close()

    def test_write_path_still_locks_exclusively_but_does_not_block_readers(self):
        holder = self.connect()
        try:
            self.authorization.require_permission_for_write(holder, self.actor, "APP_LOGIN")   # strict lock held
            with self.transaction() as reader:
                took, error = self.elapsed(lambda: self.authorization.require_permission_read(reader, self.actor, "APP_LOGIN"))
                self.assertIsNone(error)
                self.assertLess(took, 1.0)                                                  # read not queued behind a write
            second = self.connect(lock_wait=2)
            try:
                took, error = self.elapsed(
                    lambda: self.authorization.require_permission_for_write(second, self.actor, "APP_LOGIN"))
                self.assertEqual(error, 1205)                                               # writes still serialize
                self.assertGreaterEqual(took, 1.5)
            finally:
                second.rollback()
                second.close()
        finally:
            holder.rollback()
            holder.close()

    def test_revocation_is_observed_by_the_next_read_and_write(self):
        """Made and observed inside ONE uncommitted transaction; rolled back, so no grant really changes."""
        with self.transaction() as c:
            self.authorization.require_permission_read(c, self.actor, "CUSTOMERS_VIEW")
            q = c.cursor()
            q.execute("DELETE rp FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id "
                      "JOIN user_roles ur ON ur.role_id=rp.role_id WHERE ur.user_id=%s AND p.code='CUSTOMERS_VIEW'",
                      (self.uid,))
            self.assertGreater(q.rowcount, 0)
            q.close()
            self.assertIn("CUSTOMERS_VIEW", self.actor.permissions)                          # session cache is stale
            for check in (self.authorization.require_permission_read, self.authorization.require_permission_for_write):
                with self.assertRaises(PermissionError):
                    check(c, self.actor, "CUSTOMERS_VIEW")
        with self.transaction() as c:                                                        # rolled back: still granted
            self.authorization.require_permission_read(c, self.actor, "CUSTOMERS_VIEW")

    # ------------------------------------------------------------ customer mutations (CUSTOMERS_MANAGE)
    def test_customer_mutation_uses_the_strict_write_path_on_real_mysql(self):
        """Deactivate then reactivate the tagged DEV fixture customer (its state is restored)."""
        from services.customer_service import CustomerService
        with self.transaction() as c:
            q = c.cursor(dictionary=True)
            q.execute("SELECT id, is_active FROM customers WHERE name=%s ORDER BY id LIMIT 1",
                      ("FINANCIAL ACCOUNTS V1 DEV TEST DATA DEPOSIT FIXTURE",))
            row = q.fetchone()
            q.close()
        if row is None:
            self.skipTest("the tagged DEV fixture customer does not exist yet")
        service, cid = CustomerService(self.session), str(row["id"])

        def state():
            with self.transaction() as c:
                q = c.cursor(dictionary=True)
                q.execute("SELECT is_active, updated_by_user_id FROM customers WHERE id=%s", (cid,))
                found = q.fetchone()
                q.close()
                return bool(found["is_active"]), found["updated_by_user_id"]
        self.assertTrue(state()[0])
        service.deactivate_customer(cid)
        self.assertEqual(state(), (False, self.uid))                      # attributed to the verified actor
        # While another connection holds the strict authorization lock, the mutation waits (writes serialize).
        holder = self.connect()
        try:
            self.authorization.require_permission_for_write(holder, self.actor, "CUSTOMERS_MANAGE")
            worker = threading.Thread(target=lambda: service.reactivate_customer(cid))
            worker.start()
            time.sleep(1.0)
            self.assertEqual(state()[0], False)                           # still waiting on the write lock
        finally:
            holder.rollback()
            holder.close()
        worker.join(20)
        self.assertEqual(state(), (True, self.uid))                       # restored

    # ------------------------------------------------------------ real screens overlapping for one user
    def test_a_slow_cash_read_does_not_queue_other_screens(self):
        from repositories.customer_repository import CustomerRepository
        from services.customer_account_service import CustomerAccountService
        from services.dashboard_service import DashboardService
        from services.financial_account_service import FinancialAccountService
        from services.invoice_history_service import InvoiceHistoryService
        from services.management_review_service import ManagementReviewService
        from services.customer_service import CustomerService
        from services.gold_price_settings_service import GoldPriceSettingsService
        inside, release = threading.Event(), threading.Event()

        class SlowRepository(FinancialAccountRepository):
            def cash_flow(self, *args, **kwargs):
                rows = super().cash_flow(*args, **kwargs)
                inside.set()
                release.wait(HOLD)                       # the read transaction stays open (authorized, mid-query)
                return rows
        slow = FinancialAccountService(self.session, repository=SlowRepository)
        result = {}
        worker = threading.Thread(target=lambda: result.update(view=slow.cash_view()))
        worker.start()
        try:
            self.assertTrue(inside.wait(20))
            with self.transaction() as c:
                q = c.cursor(dictionary=True)
                q.execute("SELECT id FROM customers ORDER BY id LIMIT 1")
                customer_id = str(q.fetchone()["id"])
                q.close()
                self.assertTrue(CustomerRepository(c) is not None)
            timings = {
                "Cash": self.elapsed(lambda: FinancialAccountService(self.session).cash_view()),
                "Customers statement": self.elapsed(lambda: CustomerAccountService(self.session).statement(customer_id)),
                "History": self.elapsed(lambda: InvoiceHistoryService(self.session).search()),
                "Dashboard": self.elapsed(lambda: DashboardService(self.session).today()),
                "Reviews": self.elapsed(lambda: ManagementReviewService(self.session).open_reviews()),
                "Customers list": self.elapsed(lambda: CustomerService(self.session).search_customers()),
                "Gold Price settings load": self.elapsed(
                    lambda: GoldPriceSettingsService(self.session).load(self.actor)["refresh"]),
            }
            self.assertTrue(worker.is_alive(), "the slow read must still be open while the others ran")
            with self.transaction() as c:
                q = c.cursor()
                q.execute("SELECT COUNT(*) FROM performance_schema.data_locks WHERE OBJECT_SCHEMA='masgold_v2' "
                          "AND OBJECT_NAME IN ('users','role_permissions','user_roles','employees') AND LOCK_MODE LIKE 'X%'")
                exclusive = q.fetchone()[0]
                q.close()
            for name, (took, error) in timings.items():
                self.assertIsNone(error, name)
                self.assertLess(took, 3.0, f"{name} queued for {took:.2f}s")
            self.assertEqual(exclusive, 0)                                # the slow reader holds no identity lock
            print("\n  while a cash read was held open for", HOLD, "s: " +
                  ", ".join(f"{name} {took:.2f}s" for name, (took, _) in timings.items()) +
                  f"; exclusive identity/grant locks: {exclusive}")
        finally:
            release.set()
            worker.join(30)
        self.assertIn("in", result["view"])


if __name__ == "__main__":
    unittest.main()
