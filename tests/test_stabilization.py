"""Final stabilization pack (release-gate findings R1–R6): persistent re-authentication cooldown, backend
authorization + session actor for stock entry and Scrap conversion, Billing V1 unreachable, repository hygiene.

No database is used: services run over in-memory doubles; the repository checks read files / git metadata only."""
import ast
from contextlib import contextmanager
from decimal import Decimal as D
import os
from pathlib import Path
import re
import subprocess
import unittest
from unittest.mock import Mock, patch

from app.session import AppSession
from services.authentication_service import AuthenticationError, AuthenticationResult, AuthenticationService, _VERIFIED
from tests.auth_fakes import FakeAuthorization

ROOT = Path(__file__).resolve().parents[1]


def session_for(permissions, user_id=9):
    session = AppSession()
    session._establish(AuthenticationResult(user_id, None, "موظف", frozenset(permissions), _VERIFIED),
                       int(session.generation))
    return session


# ------------------------------------------------------------------------------------------------ R2
class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Passwords:
    """A password service double: only the password «right» is accepted (never an Argon2 hash here)."""

    def verify(self, encoded, password):
        return password == "right"

    def dummy_verify(self, password):
        pass


class Identity:
    """AuthenticationRepository double for both verify_user_password and verify_credentials."""

    def __init__(self, connection):
        pass

    def get_identity(self, user_id, *, lock=False):
        return {"id": 3, "password_hash": "$argon2id$stored", "is_active": True, "must_change_password": False,
                "employee_id": None, "display_name": "المدير"}

    def find_credentials(self, username):
        return {"id": 3, "password_hash": "$argon2id$stored"} if username == "manager" else None

    def effective_permissions(self, user_id, *, lock=False):
        return frozenset({"APP_LOGIN", "INVOICE_SETTLEMENT_APPROVE"})


class SharedCooldownTests(unittest.TestCase):
    """A fresh AuthenticationService per attempt resets the failed-attempt cooldown every time: both owners of
    a re-authentication must keep ONE instance for their whole life."""

    def setUp(self):
        self.clock, self.created = Clock(), []

        @contextmanager
        def transaction():
            yield object()

        def factory():
            service = AuthenticationService(transaction, Identity, passwords=Passwords(), clock=self.clock)
            self.created.append(service)
            return service
        patcher = patch("services.authentication_service.AuthenticationService", factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---- Dead Cash password confirmation
    def dead_cash(self):
        from services.dead_cash_service import DeadCashError, DeadCashService
        session = session_for({"APP_LOGIN", "DEAD_CASH_MANAGE"}, user_id=3)
        return DeadCashService(session), session.current_actor(), DeadCashError

    def test_dead_cash_failed_attempts_share_one_cooldown(self):
        service, actor, error = self.dead_cash()
        with self.assertRaises(error):
            service._confirm_password(actor, "wrong-1")
        with self.assertRaises(error):
            service._confirm_password(actor, "right")             # immediately after a failure: still cooling down
        self.assertEqual(len(self.created), 1)                      # ONE AuthenticationService, not one per attempt
        self.assertEqual(self.created[0]._failures, 1)              # the first failure is remembered …
        self.assertGreater(self.created[0]._retry_at, self.clock.now)   # … and the second was throttled by it
        self.clock.now += 60
        service._confirm_password(actor, "right")                   # after the cooldown the right password works
        self.assertEqual((len(self.created), self.created[0]._failures), (1, 0))

    def test_dead_cash_right_password_still_works_first_time(self):
        service, actor, _ = self.dead_cash()
        service._confirm_password(actor, "right")
        self.assertEqual(len(self.created), 1)

    # ---- Billing settlement approver (a manager types THEIR credentials at the counter)
    def billing(self):
        from services.invoice_service import InvoiceService
        session = session_for({"APP_LOGIN", "INVOICE_CREATE"}, user_id=7)
        return InvoiceService(session), session.current_actor()

    def test_billing_approver_guesses_share_one_cooldown(self):
        service, actor = self.billing()
        with self.assertRaises(AuthenticationError):
            service.verify_settlement_approver("manager", "wrong-1", _expected_actor=actor)
        with self.assertRaises(AuthenticationError):                # guessing again at once is throttled …
            service.verify_settlement_approver("manager", "right", _expected_actor=actor)   # … even if correct
        self.assertEqual((len(self.created), self.created[0]._failures), (1, 1))
        self.assertGreater(self.created[0]._retry_at, self.clock.now)
        self.clock.now += 60
        grant = service.verify_settlement_approver("manager", "right", _expected_actor=actor)
        self.assertEqual((grant.user_id, len(self.created)), (3, 1))

    def test_billing_approver_right_password_still_works_first_time(self):
        service, actor = self.billing()
        self.assertEqual(service.verify_settlement_approver("manager", "right", _expected_actor=actor).user_id, 3)
        self.assertEqual(len(self.created), 1)

    def test_no_service_builds_an_authentication_service_per_call_any_more(self):
        for name in ("dead_cash_service.py", "invoice_service.py", "user_admin_service.py"):
            source = (ROOT / "services" / name).read_text(encoding="utf-8")
            self.assertNotRegex(source, r"=\s*self\._authentication or AuthenticationService\(\)", name)
            self.assertIn("self._authentication = AuthenticationService()", source, name)


# ------------------------------------------------------------------------------------------------ R3
class InventoryAuthorizationTests(unittest.TestCase):
    def service(self, permissions=frozenset({"APP_LOGIN", "INVENTORY_MANAGE"}), user_id=9, granted=None):
        from tests.test_inventory_stock import Store, service_for
        store = Store()
        session = session_for(permissions, user_id)
        authorization = FakeAuthorization(session, granted)
        return service_for(store, session, authorization), store, authorization

    VALUES = dict(item_type_id=1, karat_id=1, weight_grams="3.600", stock_classification="NEW_STOCK")

    def test_authorized_user_creates_and_the_session_user_is_the_actor(self):
        service, store, authorization = self.service(user_id=41)
        self.assertEqual(service.create_inventory_item(**self.VALUES), 41)
        self.assertEqual(store.created[0]["created_by_user_id"], 41)
        self.assertEqual(store.movements[0]["performed_by_user_id"], 41)
        self.assertIn(("WRITE", "INVENTORY_MANAGE"), authorization.checked)       # the strict (locking) path

    def test_user_without_the_permission_cannot_create(self):
        service, store, _ = self.service(permissions={"APP_LOGIN", "INVOICE_CREATE"})
        with self.assertRaises(PermissionError):
            service.create_inventory_item(**self.VALUES)
        self.assertEqual((store.created, store.movements, store.reads), ([], [], 0))   # no transaction was opened

    def test_missing_session_or_logged_out_actor_cannot_create_silently(self):
        from tests.test_inventory_stock import Repo, Store
        from services.inventory_service import InventoryService
        store = Store()

        @contextmanager
        def transaction():
            store.reads += 1
            yield type("T", (), {"connection": type("C", (), {"store": store})()})()
        for service in (InventoryService(transaction_factory=transaction, repository=Repo),          # no session
                        InventoryService(transaction_factory=transaction, repository=Repo, session=AppSession())):
            with self.assertRaises(PermissionError):
                service.create_inventory_item(**self.VALUES)
        self.assertEqual((store.created, store.reads), ([], 0))

    def test_permission_revoked_in_the_database_refuses_even_with_a_stale_session(self):
        service, store, authorization = self.service()
        authorization.permissions = {"APP_LOGIN"}                  # revoked behind the (cached) session
        with self.assertRaises(PermissionError):
            service.create_inventory_item(**self.VALUES)
        self.assertEqual(store.created, [])

    def test_a_caller_cannot_spoof_the_actor(self):
        service, store, _ = self.service(user_id=9)
        with self.assertRaises(PermissionError):
            service.create_inventory_item(user_id=777, **self.VALUES)
        self.assertEqual(store.created, [])
        service.create_inventory_item(user_id=9, **self.VALUES)      # the same identity is harmless
        self.assertEqual(store.created[0]["created_by_user_id"], 9)

    def test_the_screen_slot_carries_no_identity(self):
        from app.viewmodels.inventory_viewmodel import InventoryViewModel
        source = (ROOT / "app" / "viewmodels" / "inventory_viewmodel.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "createInventoryItem")
        names = {a.arg for a in method.args.args}
        self.assertFalse(names & {"user_id", "employee_id", "actor", "actor_id", "created_by"}, names)
        call = next(n for n in ast.walk(method) if isinstance(n, ast.Call)
                    and getattr(n.func, "attr", "") == "create_inventory_item")
        self.assertNotIn("user_id", {k.arg for k in call.keywords})
        self.assertTrue(hasattr(InventoryViewModel, "attach_session"))


class ConversionAuthorizationTests(unittest.TestCase):
    """Scrap → NEW_STOCK consumes ledger grams: OLD_GOLD_MANAGE through the strict path, actor = the session."""

    def run_conversion(self, permissions=frozenset({"APP_LOGIN", "OLD_GOLD_MANAGE"}), user_id=3, session="auto",
                       held="0.000", physical="100.000", **extra):
        from services.old_gold_conversion_service import OldGoldConversionService
        writes, calls = {}, []
        repository = Mock()
        repository.get_active_item_type.return_value = {"is_active": 1, "default_category_id": None}
        repository.get_active_karat.return_value = {"is_active": 1, "karat_value": 21}
        repository.lock_old_gold_balance.return_value = [{"weight_delta_grams": D(physical)},
                                                         {"weight_delta_grams": -D(held)}]
        repository.held_old_gold.return_value = D(held)
        repository.get_movement_type_id.return_value = 1
        for name in ("create_new_inventory_item", "create_inventory_movement", "create_old_gold_out_movement",
                     "create_conversion_batch"):
            getattr(repository, name).side_effect = lambda _n=name, **kw: writes.setdefault(_n, kw) and 1

        @contextmanager
        def transaction():
            calls.append("transaction")
            yield Mock(connection=Mock())
        session = session_for(permissions, user_id) if session == "auto" else session
        authorization = FakeAuthorization(session) if session is not None else None
        service = OldGoldConversionService(session=session, authorization=authorization)
        with patch("services.old_gold_conversion_service.DatabaseTransaction", transaction), \
                patch("services.old_gold_conversion_service.OldGoldConversionRepository", return_value=repository):
            try:
                service.convert_to_new_stock(item_type_id=1, karat_id=1, category_id=0, factory_id=0,
                                             result_weight_grams="3.500", consumed_old_gold_grams="3.600", size="",
                                             barcode_value="", notes="", **extra)
            finally:
                self.writes, self.calls, self.repository = writes, calls, repository
        return writes

    def test_authorized_conversion_succeeds_and_the_session_user_is_the_actor(self):
        writes = self.run_conversion(user_id=44)
        self.assertEqual(sorted(writes), ["create_conversion_batch", "create_inventory_movement",
                                          "create_new_inventory_item", "create_old_gold_out_movement"])
        for name, values in writes.items():
            self.assertEqual((values.get("employee_id"), values.get("user_id")), (None, 44), name)

    def test_unauthorized_conversion_is_refused_before_any_work(self):
        with self.assertRaises(PermissionError):
            self.run_conversion(permissions={"APP_LOGIN", "INVOICE_CREATE"})
        self.assertEqual((self.writes, self.calls), ({}, []))        # no transaction, no ledger write

    def test_missing_session_or_actor_is_refused(self):
        with self.assertRaises(PermissionError):
            self.run_conversion(session=None)
        with self.assertRaises(PermissionError):
            self.run_conversion(session=AppSession())                # nobody is logged in
        self.assertEqual((self.writes, self.calls), ({}, []))

    def test_revoked_permission_with_a_stale_session_is_refused_in_the_transaction(self):
        from services.old_gold_conversion_service import OldGoldConversionService
        session = session_for({"APP_LOGIN", "OLD_GOLD_MANAGE"})
        authorization = FakeAuthorization(session)
        authorization.permissions = {"APP_LOGIN"}
        service = OldGoldConversionService(session=session, authorization=authorization)

        @contextmanager
        def transaction():
            yield Mock(connection=Mock())
        repository = Mock()
        repository.get_active_item_type.return_value = {"is_active": 1, "default_category_id": None}
        with patch("services.old_gold_conversion_service.DatabaseTransaction", transaction), \
                patch("services.old_gold_conversion_service.OldGoldConversionRepository", return_value=repository):
            with self.assertRaises(PermissionError):
                service.convert_to_new_stock(item_type_id=1, karat_id=1, category_id=0, factory_id=0,
                                             result_weight_grams="3.5", consumed_old_gold_grams="3.6", size="",
                                             barcode_value="", notes="")
        repository.create_old_gold_out_movement.assert_not_called()

    def test_a_caller_cannot_spoof_the_actor(self):
        from services.old_gold_conversion_service import OldGoldConversionService
        session = session_for({"APP_LOGIN", "OLD_GOLD_MANAGE"}, 3)
        service = OldGoldConversionService(session=session, authorization=FakeAuthorization(session))
        with patch("services.old_gold_conversion_service.DatabaseTransaction") as transaction:
            with self.assertRaises(PermissionError):
                service.convert_to_new_stock(user_id=999, item_type_id=1, karat_id=1, category_id=0, factory_id=0,
                                             result_weight_grams="3.5", consumed_old_gold_grams="3.6", size="",
                                             barcode_value="", notes="")
        transaction.assert_not_called()                                   # refused before any transaction

    def test_held_scrap_stays_non_consumable(self):
        # physical 5.000, all held: 4.990 g is refused with the held explanation (the rule is unchanged).
        with self.assertRaises(ValueError) as refused:
            self.run_conversion(physical="5.000", held="5.000")
        self.assertIn("محجوز لمراجعة المدير", str(refused.exception))
        self.assertEqual(self.writes, {})
        # physical 10.000, 5.000 held, 3.600 requested: the available 5.000 is enough.
        self.assertEqual(len(self.run_conversion(physical="10.000", held="5.000")), 4)


# ------------------------------------------------------------------------------------------------ R4
class LegacyBillingV1Tests(unittest.TestCase):
    def test_the_frozen_billing_view_model_is_not_registered(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "create_operational_viewmodels")
        keys = {k.value for n in ast.walk(function) if isinstance(n, ast.Dict) for k in n.keys
                if isinstance(k, ast.Constant)}
        self.assertNotIn("billingVM", keys)
        self.assertIn("customerVM", keys)                                  # the dictionary is still the live one
        imports = {a.name for n in ast.walk(function) if isinstance(n, ast.ImportFrom) for a in n.names}
        self.assertNotIn("BillingViewModel", imports)

    def test_no_live_python_path_imports_the_frozen_billing_service(self):
        offenders = []
        for folder in ("app", "services", "repositories", "main.py"):
            paths = [ROOT / folder] if folder.endswith(".py") else (ROOT / folder).rglob("*.py")
            for path in paths:
                if path.name in ("billing_viewmodel.py", "billing_service.py", "billing_repository.py"):
                    continue                                              # the frozen files themselves
                text = path.read_text(encoding="utf-8")
                if re.search(r"(from|import)\s+(app\.viewmodels\.billing_viewmodel|services\.billing_service|"
                             r"repositories\.billing_repository)", text):
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_the_frozen_source_is_kept_as_history(self):
        for relative in ("app/viewmodels/billing_viewmodel.py", "services/billing_service.py",
                         "repositories/billing_repository.py"):
            self.assertTrue((ROOT / relative).is_file(), relative)


# ------------------------------------------------------------------------------------------------ R5 / R6
def tracked_files():
    try:
        output = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return output.stdout.splitlines() if output.returncode == 0 else None


class RepositoryHygieneTests(unittest.TestCase):
    def test_no_accidental_shell_output_files_at_the_root(self):
        for name in ("it status", "tatus", "tatusgit status"):
            self.assertFalse((ROOT / name).exists(), name)
        tracked = tracked_files()
        if tracked is not None:
            # A removal that is not committed yet still shows in the index; only files that exist count.
            self.assertFalse([f for f in tracked if f in ("it status", "tatus", "tatusgit status")
                              and (ROOT / f).exists()])

    def test_a_conventional_dependency_manifest_exists_and_covers_the_imports(self):
        self.assertTrue((ROOT / "requirements.txt").is_file())
        self.assertFalse((ROOT / "requirements,txt").exists())
        manifest = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for package in ("pyside6", "mysql-connector-python", "python-dotenv", "argon2-cffi", "keyring"):
            self.assertIn(package, manifest, package)
        imports = set()
        for folder in ("app", "services", "repositories", "config", "database", "utils"):
            for path in (ROOT / folder).rglob("*.py"):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    if isinstance(node, ast.Import):
                        imports |= {a.name.split(".")[0] for a in node.names}
                    elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                        imports.add(node.module.split(".")[0])
        for module, package in (("PySide6", "pyside6"), ("mysql", "mysql-connector-python"), ("dotenv", "python-dotenv"),
                                ("argon2", "argon2-cffi"), ("keyring", "keyring")):
            if module in imports:
                self.assertIn(package, manifest, f"{module} is imported but {package} is not in requirements.txt")

    def test_review_screenshots_are_not_tracked(self):
        tracked = tracked_files()
        if tracked is None:
            self.skipTest("git metadata is not available")
        present = [f for f in tracked if f.startswith("reports/") and f.lower().endswith((".png", ".json"))
                   and (ROOT / f).exists()]
        self.assertEqual(present, [])

    def test_the_password_note_is_ignored_by_the_repository_itself(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^/?Note for password\.txt\s*$")
        try:
            result = subprocess.run(["git", "check-ignore", "-q", "--no-index", "Note for password.txt"], cwd=ROOT,
                                    timeout=30)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git is not available")
        self.assertEqual(result.returncode, 0)                           # ignored by .gitignore, not only a local exclude
        broad = [line for line in text.splitlines() if line.strip() in ("*.txt", "*.md", "docs/", "*password*")]
        self.assertEqual(broad, [])                                       # no over-broad rule hiding real documents

    def test_loose_sql_is_not_shipped_as_a_runnable_migration_or_fixture(self):
        self.assertFalse((ROOT / "database" / "test" / "ADD_DUMMY_100G_21K.sql").exists())
        # The four 20260921 prototypes stay as read-only history (migration 013 / test_schema_chain depend on
        # them) and are fenced by a README; nothing else may sit there as a runnable script.
        folder = ROOT / "database" / "migrations"
        loose = sorted(p.name for p in folder.glob("*.sql")) if folder.is_dir() else []
        self.assertEqual([n for n in loose if not n.startswith("20260921_")], [])
        self.assertEqual(len(loose), 4)
        readme = (folder / "README.md").read_text(encoding="utf-8")
        self.assertIn("NOT a migration chain", readme)
        self.assertIn("Do **not** apply", readme)
        self.assertEqual(len(list((ROOT / "database" / "schema").glob("0[0-9][0-9]_*.sql"))), 77)   # the one chain

    def test_documentation_states_the_current_schema_and_rules(self):
        for name in ("AGENTS.md", "CLAUDE.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("001–077", text, name)
            self.assertIn("next migration is `078`", text, name)
            self.assertNotIn("001–024", text, name)
            for rule in ("1.25%–5.00%", "حفظ فقط", "MASGOLD_DEV_AUTO_LOGIN", "USERS_MANAGE"):
                self.assertIn(rule, text, f"{name}: {rule}")


class SchemaVersionExpectationTests(unittest.TestCase):
    """The opt-in MySQL tests are never run here; they must at least expect the repository's real schema chain."""

    def test_opt_in_tests_expect_migration_33(self):
        for name in ("test_customer_account_mysql.py", "test_inventory_stock_mysql.py", "test_master_data_mysql.py",
                     "test_dead_cash_mysql.py", "test_financial_accounts_mysql.py"):
            text = (ROOT / "tests" / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"==\s*30\b|\"masgold_v2\",\s*30\)|migration 25", name)

    def test_the_repository_chain_ends_at_033(self):
        chain = sorted(p.name for p in (ROOT / "database" / "schema").glob("0[0-9][0-9]_*.sql"))
        self.assertEqual([int(n[:3]) for n in chain], list(range(1, 78)))


if __name__ == "__main__":
    unittest.main()
