"""OWNER emergency password recovery (Stage 1). Never a live database.

The REAL AuthenticationRepository SQL and AuthenticationService run against an in-memory SQLite copy of the
relevant tables, so password + audit atomicity and rollback are exercised for real; only the MySQL dialect
details (placeholders, FOR UPDATE) are adapted by a thin shim."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
import json
import logging
import sqlite3
import sys
import unittest
from unittest.mock import patch

from argon2 import PasswordHasher, Type

from repositories.authentication_repository import AuthenticationRepository
from services.authentication_service import (
    AuthenticationError, AuthenticationService, RECOVERY_SAME_PASSWORD, RECOVERY_TARGET_REFUSED,
    RecoveryFailed, RecoveryRefused,
)
from services.dead_cash_service import DeadCashError, DeadCashService
from services.password_service import PasswordService
from tools import reset_owner_password as tool

OLD, NEW = "old-secret-passphrase", "brand-new-passphrase!"


class _Cursor:
    def __init__(self, connection):
        self._cursor = connection.cursor()

    def execute(self, sql, params=()):
        self._cursor.execute(sql.replace("%s", "?").replace(" FOR UPDATE", ""), params)

    def fetchone(self):
        row = self._cursor.fetchone()
        return None if row is None else dict(zip([c[0] for c in self._cursor.description], row))

    def fetchall(self):
        names = [c[0] for c in self._cursor.description]
        return [dict(zip(names, row)) for row in self._cursor.fetchall()]

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    def close(self):
        self._cursor.close()


class _Connection:
    def __init__(self, connection):
        self._connection = connection

    def cursor(self, dictionary=False):
        return _Cursor(self._connection)


class RecoveryFixture(unittest.TestCase):
    def setUp(self):
        self.no_db = patch("database.connection.DatabaseConnection.connect",
                           side_effect=AssertionError("Live database forbidden"))
        self.no_db.start()
        self.addCleanup(self.no_db.stop)
        self.passwords = PasswordService(PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1, type=Type.ID))
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT, display_name TEXT,
                               is_active INTEGER, must_change_password INTEGER DEFAULT 0, employee_id INTEGER,
                               last_login_at TEXT);
            CREATE TABLE roles(id INTEGER PRIMARY KEY, code TEXT, is_active INTEGER);
            CREATE TABLE user_roles(user_id INTEGER, role_id INTEGER);
            CREATE TABLE permissions(id INTEGER PRIMARY KEY, code TEXT, is_active INTEGER);
            CREATE TABLE role_permissions(role_id INTEGER, permission_id INTEGER);
            CREATE TABLE audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, event_type TEXT,
                entity_type TEXT, entity_id INTEGER, entity_reference TEXT, description TEXT, before_json TEXT,
                after_json TEXT, metadata_json TEXT, workstation_name TEXT,
                occurred_at TEXT DEFAULT CURRENT_TIMESTAMP);
            INSERT INTO roles VALUES (1,'OWNER',1),(2,'CASHIER',1);
            INSERT INTO permissions VALUES (1,'APP_LOGIN',1);
            INSERT INTO role_permissions VALUES (1,1),(2,1);
        """)
        self.db.create_function("NOW", 0, lambda: "2026-10-06 12:00:00")
        self.old_hash = self.passwords.hash(OLD)
        self.add_user(1, "admin", 1, 1)               # the usual OWNER
        self.add_user(2, "second-owner", 1, 1)        # another active OWNER (not "admin")
        self.add_user(3, "retired-owner", 0, 1)       # inactive OWNER
        self.add_user(4, "cashier", 1, 2)             # active, not an OWNER
        self.add_user(5, "other-cashier", 1, 2)
        self.db.commit()
        self.commits = self.rollbacks = 0
        self.service = self.make_service()

    def add_user(self, user_id, username, active, role_id):
        self.db.execute("INSERT INTO users(id,username,password_hash,display_name,is_active) VALUES (?,?,?,?,?)",
                        (user_id, username, self.old_hash, username, active))
        self.db.execute("INSERT INTO user_roles VALUES (?,?)", (user_id, role_id))

    def make_service(self):
        @contextmanager
        def transaction():
            try:
                yield _Connection(self.db)
                self.db.commit()
                self.commits += 1
            except BaseException:
                self.db.rollback()
                self.rollbacks += 1
                raise
        return AuthenticationService(transaction, AuthenticationRepository, self.passwords, lambda: 0)

    def hashes(self):
        return dict(self.db.execute("SELECT id, password_hash FROM users").fetchall())

    def audit_rows(self):
        return self.db.execute("SELECT * FROM audit_log").fetchall()

    def snapshot(self):
        return (self.hashes(), self.audit_rows(), self.db.execute("SELECT * FROM user_roles ORDER BY 1,2").fetchall(),
                self.db.execute("SELECT * FROM roles ORDER BY 1").fetchall())

    def recover(self, username="admin", password=NEW, confirmation=None, reason="نسي المالك كلمة المرور"):
        return self.service.recover_owner_password(username, password,
                                                   password if confirmation is None else confirmation, reason)

    def assert_nothing_written(self, before):
        self.assertEqual(self.snapshot(), before)


class RecoveryServiceTests(RecoveryFixture):
    def test_active_owner_recovery_succeeds_and_only_the_password_changes(self):
        before = self.snapshot()
        result = self.recover()
        self.assertEqual(result, {"user_id": "1"})
        after = self.hashes()
        self.assertNotEqual(after[1], before[0][1])
        self.assertTrue(after[1].startswith("$argon2id$"))
        self.assertEqual({k: v for k, v in after.items() if k != 1}, {k: v for k, v in before[0].items() if k != 1})
        self.assertEqual(self.snapshot()[2:], before[2:])                 # roles / grants untouched
        columns = self.db.execute("SELECT username, display_name, is_active, must_change_password, employee_id "
                                  "FROM users WHERE id=1").fetchone()
        self.assertEqual(columns, ("admin", "admin", 1, 0, None))

    def test_a_second_active_owner_can_be_targeted_and_admin_is_not_hard_coded(self):
        before = self.hashes()
        self.assertEqual(self.recover("second-owner"), {"user_id": "2"})
        after = self.hashes()
        self.assertNotEqual(after[2], before[2])
        self.assertEqual(after[1], before[1])                              # admin untouched
        self.assertTrue(self.passwords.verify(after[2], NEW))
        self.assertEqual(self.db.execute("SELECT entity_id, entity_reference FROM audit_log").fetchall(),
                         [(2, "second-owner")])

    def test_unknown_inactive_and_non_owner_targets_are_refused_without_writes(self):
        for username in ("nobody", "retired-owner", "cashier", "", None):
            with self.subTest(username=username):
                before = self.snapshot()
                with self.assertRaises(RecoveryRefused) as raised:
                    self.recover(username)
                self.assertEqual(str(raised.exception), RECOVERY_TARGET_REFUSED)
                self.assert_nothing_written(before)

    def test_inactive_owner_role_does_not_make_an_owner(self):
        self.db.execute("UPDATE roles SET is_active=0 WHERE code='OWNER'")
        self.db.commit()
        before = self.snapshot()
        with self.assertRaises(RecoveryRefused):
            self.recover("admin")
        self.assert_nothing_written(before)

    def test_confirmation_mismatch_and_policy_failures_are_refused(self):
        for password, confirmation in ((NEW, NEW + "x"), ("short", "short"), ("", ""), ("x" * 1025, "x" * 1025),
                                       (None, None), (NEW, None)):
            with self.subTest(password=password and len(password)):
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.service.recover_owner_password("admin", password, confirmation, "سبب")
                self.assert_nothing_written(before)

    def test_new_password_equal_to_the_current_one_is_refused_without_any_audit_row(self):
        before = self.snapshot()
        with self.assertRaises(RecoveryRefused) as raised:
            self.recover(password=OLD)
        self.assertEqual(str(raised.exception), RECOVERY_SAME_PASSWORD)
        self.assert_nothing_written(before)
        self.assertEqual(self.audit_rows(), [])

    def test_a_reason_is_mandatory_and_may_not_contain_the_password(self):
        for reason in ("", "   ", None, "x" * 501, f"سبب {NEW} سبب"):
            with self.subTest(reason=reason and reason[:6]):
                before = self.snapshot()
                with self.assertRaises(RecoveryRefused):
                    self.recover(reason=reason)
                self.assert_nothing_written(before)

    def test_failure_after_the_password_update_rolls_back_password_and_audit(self):
        before = self.snapshot()
        original = AuthenticationRepository.record_security_event

        def broken(repository, *args, **kwargs):
            original(repository, *args, **kwargs)                          # the audit row IS inserted…
            raise RuntimeError(f"disk full while writing {NEW}")           # …then the transaction fails
        with patch.object(AuthenticationRepository, "record_security_event", broken):
            with self.assertRaises(RecoveryFailed) as raised:
                self.recover()
        self.assertNotIn(NEW, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)
        self.assert_nothing_written(before)                                # password AND audit rolled back
        self.assertGreaterEqual(self.rollbacks, 1)
        self.assertTrue(self.passwords.verify(self.hashes()[1], OLD))

    def test_failure_during_the_password_update_also_leaves_nothing_behind(self):
        before = self.snapshot()
        with patch.object(AuthenticationRepository, "replace_password_hash", side_effect=RuntimeError("boom")):
            with self.assertRaises(RecoveryFailed):
                self.recover()
        self.assert_nothing_written(before)

    def test_success_writes_exactly_one_safe_audit_event(self):
        with patch("services.authentication_service._local_identity", return_value=("shop-user", "SHOP-PC")):
            self.recover(reason="  نسي كلمة المرور  ")
        rows = self.db.execute("SELECT user_id, event_type, entity_type, entity_id, entity_reference, description, "
                               "metadata_json, workstation_name, occurred_at FROM audit_log").fetchall()
        self.assertEqual(len(rows), 1)
        user_id, event, entity, entity_id, reference, description, metadata, workstation, occurred = rows[0]
        self.assertEqual((user_id, event, entity, entity_id, reference, workstation),
                         (None, "OWNER_PASSWORD_RECOVERED", "USER", 1, "admin", "SHOP-PC"))
        self.assertIsNotNone(occurred)
        self.assertEqual(json.loads(metadata), {"source": "CLI_EMERGENCY", "reason": "نسي كلمة المرور",
                                                "os_user": "shop-user", "host": "SHOP-PC"})
        everything = repr(self.audit_rows())
        for secret in (NEW, OLD, self.hashes()[1], self.old_hash, "$argon2"):
            self.assertNotIn(secret, everything)
        self.assertNotIn(str(len(NEW)), metadata + description)

    def test_recovered_password_works_old_fails_and_dead_cash_reauthentication_accepts_it(self):
        self.recover()
        # Normal login (the verification the Login window uses) and the Dead Cash re-authentication primitive.
        self.assertEqual(self.make_service().authenticate("admin", NEW).user_id, 1)
        with self.assertRaises(AuthenticationError):
            self.make_service().authenticate("admin", OLD)                 # the old password no longer works
        self.assertTrue(self.make_service().verify_user_password(1, NEW))
        with self.assertRaises(AuthenticationError):
            self.make_service().verify_user_password(1, OLD)
        # The EXISTING Dead Cash re-authentication path (DeadCashService._confirm_password).
        actor = type("Actor", (), {"user_id": 1})()
        DeadCashService(object(), authentication=self.make_service())._confirm_password(actor, NEW)   # accepted
        with self.assertRaises(DeadCashError):
            DeadCashService(object(), authentication=self.make_service())._confirm_password(actor, OLD)

    def test_no_secret_in_logs_or_exceptions(self):
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage() + repr(record.args))
        handler = Capture(level=logging.DEBUG)
        root = logging.getLogger()
        previous = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        self.addCleanup(lambda: (root.removeHandler(handler), root.setLevel(previous)))
        outputs = StringIO()
        with redirect_stdout(outputs), redirect_stderr(outputs):
            self.recover()
            for bad in (OLD,):
                with self.assertRaises(RecoveryRefused):
                    self.recover("cashier", password=bad)
        new_hash = self.hashes()[1]
        for text in records + [outputs.getvalue()]:
            for secret in (NEW, OLD, new_hash, self.old_hash):
                self.assertNotIn(secret, text)


class RecoveryToolTests(RecoveryFixture):
    def run_tool(self, inputs, passwords, argv=()):
        inputs, passwords = iter(inputs), iter(passwords)
        out, err = StringIO(), StringIO()
        with patch("builtins.input", side_effect=lambda prompt="": next(inputs)), \
             patch("getpass.getpass", side_effect=lambda prompt="": next(passwords)), \
             patch.object(tool, "_database_name", return_value="masgold_test"), \
             redirect_stdout(out), redirect_stderr(err):
            code = tool.main(list(argv), service_factory=lambda: self.service)
        return code, out.getvalue(), err.getvalue()

    def assert_clean(self, *streams):
        secrets = (NEW, OLD, self.old_hash, "$argon2") + tuple(self.hashes().values())
        for text in streams:
            for secret in secrets:
                self.assertNotIn(secret, text)

    def test_full_interactive_recovery_succeeds_without_printing_any_secret(self):
        code, out, err = self.run_tool(["second-owner", "نسي كلمة المرور", "RECOVER second-owner"], [NEW, NEW])
        self.assertEqual(code, 0)
        self.assertIn("تمت استعادة", out)
        self.assertIn("masgold_test", out)
        self.assertTrue(self.passwords.verify(self.hashes()[2], NEW))
        self.assertEqual(len(self.audit_rows()), 1)
        self.assert_clean(out, err)

    def test_admin_is_only_the_prompt_default(self):
        code, out, err = self.run_tool(["", "السبب", "RECOVER admin"], [NEW, NEW])
        self.assertEqual(code, 0)
        self.assertTrue(self.passwords.verify(self.hashes()[1], NEW))
        self.assert_clean(out, err)

    def test_cancelled_final_confirmation_writes_nothing(self):
        before = self.snapshot()
        for answer in ("", "recover admin", "RECOVER", "RECOVER cashier", "yes"):
            with self.subTest(answer=answer):
                code, out, err = self.run_tool(["admin", "السبب", answer], [NEW, NEW])
                self.assertEqual(code, 1)
                self.assertIn("الإلغاء", out)
                self.assert_nothing_written(before)
                self.assert_clean(out, err)

    def test_interrupted_input_writes_nothing(self):
        before = self.snapshot()
        for failing in (EOFError, KeyboardInterrupt):
            with patch("builtins.input", side_effect=failing), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(tool.main([], service_factory=lambda: self.service), 1)
        self.assert_nothing_written(before)

    def test_password_mismatch_policy_empty_reason_and_unsafe_targets_are_refused(self):
        before = self.snapshot()
        cases = [
            (["admin", "السبب"], [NEW, NEW + "x"]),                       # mismatch
            (["admin", "السبب"], ["short", "short"]),                     # policy
            (["admin", ""], [NEW, NEW]),                                  # empty reason
            (["admin", "السبب", "RECOVER admin"], [OLD, OLD]),            # same as current
            (["nobody", "السبب", "RECOVER nobody"], [NEW, NEW]),          # unknown
            (["retired-owner", "السبب", "RECOVER retired-owner"], [NEW, NEW]),   # inactive OWNER
            (["cashier", "السبب", "RECOVER cashier"], [NEW, NEW]),        # not an OWNER
        ]
        for inputs, passwords in cases:
            with self.subTest(inputs=inputs):
                code, out, err = self.run_tool(inputs, passwords)
                self.assertEqual(code, 1)
                self.assert_nothing_written(before)
                self.assert_clean(out, err)

    def test_a_failed_recovery_prints_only_a_generic_message(self):
        class Failing:
            def recover_owner_password(self, *args):
                raise RuntimeError(f"connection string password={NEW} host=secret-host")
        out, err = StringIO(), StringIO()
        with patch("builtins.input", side_effect=["admin", "السبب", "RECOVER admin"]), \
             patch("getpass.getpass", side_effect=[NEW, NEW]), patch.object(tool, "_database_name", return_value="x"), \
             redirect_stdout(out), redirect_stderr(err):
            code = tool.main([], service_factory=Failing)
        self.assertEqual(code, 1)
        self.assertIn(tool.FAILED, out.getvalue())
        self.assert_clean(out.getvalue(), err.getvalue())
        self.assertNotIn("secret-host", out.getvalue() + err.getvalue())

    def test_the_tool_never_reads_a_password_from_argv(self):
        called = []
        out, err = StringIO(), StringIO()
        with patch("builtins.input", side_effect=lambda *a: called.append("input")), \
             patch("getpass.getpass", side_effect=lambda *a: called.append("getpass")), \
             redirect_stdout(out), redirect_stderr(err):
            code = tool.main([NEW], service_factory=lambda: called.append("service"))
            with patch.object(sys, "argv", ["reset_owner_password.py", NEW, "--password", NEW]):
                self.assertEqual(tool.main(), 2)
        self.assertEqual(code, 2)
        self.assertEqual(called, [])                                       # nothing prompted, nothing written
        self.assertNotIn(NEW, out.getvalue() + err.getvalue())
        self.assertEqual(self.audit_rows(), [])
        import inspect
        source = inspect.getsource(tool)
        self.assertNotIn("argparse", source)
        self.assertEqual(source.count("sys.argv"), 1)                     # only "no arguments allowed" handling

    def test_a_non_utf8_output_stream_does_not_crash_the_tool(self):
        import io
        stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        errors = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        with patch.object(sys, "stdout", stream), patch.object(sys, "stderr", errors):
            self.assertEqual(tool.main([NEW], service_factory=lambda: self.service), 2)   # Arabic text printed
        self.assertEqual(self.audit_rows(), [])

    def test_hidden_input_fallback_is_refused(self):
        import getpass
        out = StringIO()
        with patch("builtins.input", side_effect=["admin", "السبب"]), \
             patch("getpass.getpass", side_effect=getpass.GetPassWarning("echo")), \
             patch.object(tool, "_database_name", return_value="x"), redirect_stdout(out), redirect_stderr(StringIO()):
            self.assertEqual(tool.main([], service_factory=lambda: self.service), 1)
        self.assertEqual(self.audit_rows(), [])

    def test_tool_output_has_no_logging_and_the_service_has_no_hardcoded_admin_rule(self):
        import inspect
        body = inspect.getsource(AuthenticationService.recover_owner_password)
        self.assertNotIn("admin", body)
        self.assertNotIn("logger", body)
        self.assertNotIn("logging", inspect.getsource(tool))


if __name__ == "__main__":
    unittest.main()
