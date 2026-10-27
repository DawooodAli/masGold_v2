"""Read-only authorization never locks; protected writes keep the strict locking re-check. No database."""
from pathlib import Path
import re
from uuid import uuid4
import unittest

from app.session import AppSession
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.authorization_service import AuthorizationService

PERMISSIONS = frozenset({"APP_LOGIN", "CUSTOMERS_VIEW", "CASH_VIEW"})


class Repository:
    """AuthenticationRepository over mutable 'database' state; records every lock request."""
    state = None

    def __init__(self, connection):
        self.connection = connection

    def get_identity(self, user_id, *, lock=False):
        Repository.state["locks"].append(("identity", lock))
        user = Repository.state["user"]
        return None if user is None else dict(user)

    def effective_permissions(self, user_id, *, lock=False):
        Repository.state["locks"].append(("permissions", lock))
        return frozenset(Repository.state["grants"])


class AuthorizationPathTests(unittest.TestCase):
    def setUp(self):
        Repository.state = {"locks": [], "grants": set(PERMISSIONS),
                            "user": {"id": 7, "employee_id": 9, "display_name": "كاشير", "is_active": True,
                                     "must_change_password": False, "employee_active": True}}
        self.session = AppSession()
        self.session._establish(AuthenticationResult(7, 9, "كاشير", PERMISSIONS, _VERIFIED), 0)
        self.actor = self.session.current_actor()
        self.authorization = AuthorizationService(self.session, repository_factory=Repository)

    def locks(self):
        return Repository.state["locks"]

    def test_read_path_takes_no_lock_and_returns_fresh_database_permissions(self):
        Repository.state["grants"].add("BANK_VIEW")                       # granted after login
        verified = self.authorization.require_permission_read(object(), self.actor, "CASH_VIEW")
        self.assertEqual(self.locks(), [("identity", False), ("permissions", False)])
        self.assertIn("BANK_VIEW", verified.permissions)                  # database state, not the session cache
        self.assertEqual((verified.user_id, verified.employee_id), (7, 9))

    def test_write_path_locks_identity_and_grants(self):
        self.authorization.require_permission_for_write(object(), self.actor, "CASH_VIEW")
        self.authorization.require_permission(object(), self.actor, "CASH_VIEW")     # the historical name
        self.assertEqual(self.locks(), [("identity", True), ("permissions", True)] * 2)
        self.assertIs(AuthorizationService.require_permission, AuthorizationService.require_permission_for_write)

    def test_revoked_permission_is_refused_on_the_next_read_and_on_a_write(self):
        self.authorization.require_permission_read(object(), self.actor, "CASH_VIEW")
        Repository.state["grants"].discard("CASH_VIEW")                   # revoked in the database only
        self.assertIn("CASH_VIEW", self.actor.permissions)                # the session cache is stale
        for check in (self.authorization.require_permission_read, self.authorization.require_permission_for_write):
            with self.assertRaises(PermissionError):
                check(object(), self.actor, "CASH_VIEW")

    def test_read_path_keeps_every_identity_rule(self):
        cases = [("user", None), ("is_active", False), ("must_change_password", True), ("employee_active", False)]
        for field, value in cases:
            with self.subTest(field=field):
                saved = dict(Repository.state["user"])
                if field == "user":
                    Repository.state["user"] = None
                else:
                    Repository.state["user"][field] = value
                with self.assertRaises(PermissionError):
                    self.authorization.require_permission_read(object(), self.actor, "CASH_VIEW")
                Repository.state["user"] = saved
        Repository.state["grants"].discard("APP_LOGIN")
        with self.assertRaises(PermissionError):
            self.authorization.require_permission_read(object(), self.actor, "CASH_VIEW")

    def test_stale_session_is_refused_on_both_paths(self):
        self.session.clear() if hasattr(self.session, "clear") else self.session.logout()
        for check in (self.authorization.require_permission_read, self.authorization.require_permission_for_write):
            with self.assertRaises(PermissionError):
                check(object(), self.actor, "CASH_VIEW")
        self.assertEqual(self.locks(), [])                                # refused before any database read

    def test_write_path_can_require_a_linked_employee_read_path_cannot(self):
        Repository.state["user"]["employee_id"] = None
        with self.assertRaises(PermissionError):
            self.authorization.require_permission_for_write(object(), self.actor, "CASH_VIEW", require_employee=True)
        with self.assertRaises(TypeError):
            self.authorization.require_permission_read(object(), self.actor, "CASH_VIEW", require_employee=True)


class CallSiteAuditTests(unittest.TestCase):
    """The non-locking path is used only by the confirmed read-only flows; every mutation stays strict."""
    READ_SITES = {("services/stock_min_level_service.py", "overview"),                 # SETTINGS_MANAGE (the minimum stock rules of the Settings card, 072)
                  ("services/category_cost_service.py", "costs"),                       # SETTINGS_MANAGE (the category cost list of the Settings card, 070)
                  ("services/cash_category_service.py", "categories"),                # SETTINGS_MANAGE (the category list of the Settings card, 069)
                  ("services/report_service.py", "__call__"),                          # the report's rights, re-read per request by `_Gate` (REPORTS_VIEW + the report's own view right, packet26/27)
                  ("services/market_trip_book_service.py", "book_report"),            # MARKET_TRIP_MANAGE (059 read-only gain / reconciliation report)
                  ("services/market_trip_book_service.py", "closure_preview"),       # MARKET_TRIP_MANAGE (059 read-only closure readiness)
                  ("services/market_trip_service.py", "preview_settle_melt"),         # MARKET_TRIP_MANAGE (060 read-only figures of a melt before it is recorded)
                  ("services/marketer_custody_service.py", "account"),                  # INTERMEDIARY_HOLDING_VIEW (marketer account, read-only)
                  ("services/marketer_custody_service.py", "marketers"),                # INTERMEDIARY_HOLDING_VIEW (marketer list, read-only)
                  ("services/gold_position_service.py", "position"),                # MANAGEMENT_REVIEW + MARKET_TRIP_MANAGE + INTERMEDIARY_HOLDING_VIEW (read-only)
                  ("services/financial_account_service.py", "_read"),              # Cash: any view permission
                  ("services/supplier_payment_service.py", "payment_context"),      # SUPPLIER_MAKING_PAY (payment dialog)
                  ("services/supplier_payment_service.py", "payment_detail"),       # SUPPLIER_VIEW (payment document)
                 ("services/supplier_gold_settlement_service.py", "settlement_context"),   # SUPPLIER_GOLD_SETTLE (settlement dialog)
                 ("services/supplier_gold_settlement_service.py", "settlement_detail"),    # SUPPLIER_GOLD_SETTLE (settlement document)
                 ("services/market_trip_service.py", "supplier_settlement_sources"),     # MARKET_TRIP_MANAGE + SUPPLIER_GOLD_SETTLE (custody source catalog)
                 ("services/holding_settlement_service.py", "settlement_sources"),       # INTERMEDIARY_HOLDING_MANAGE + SUPPLIER_GOLD_SETTLE (holding source catalog)
                  ("services/holding_receipt_service.py", "context"),                # INTERMEDIARY_HOLDING_VIEW + MANAGE (receipt dialog context)
                  ("services/intermediary_holding_service.py", "return_review"),      # MANAGE + MARKET_TRIP_MANAGE (whole-bar return review, read-only)
                  ("services/holding_settlement_service.py", "context"),            # SUPPLIER_GOLD_SETTLE + INTERMEDIARY_HOLDING_MANAGE (settlement dialog context)
                  ("services/intermediary_holding_service.py", "party_holdings"),        # INTERMEDIARY_HOLDING_VIEW (party panel)
                 ("services/supplier_service.py", "_holdings_view"),                    # INTERMEDIARY_HOLDING_VIEW (holdings inside the supplier detail)
                  ("services/market_trip_service.py", "context"),                  # MARKET_TRIP_MANAGE (entry dialog data)
                  ("services/market_trip_service.py", "trips"),                    # MARKET_TRIP_MANAGE (trip list)
                  ("services/market_trip_service.py", "trip_page"),                # MARKET_TRIP_MANAGE (paginated active / archived trip list, read-only)
                  ("services/market_trip_book_service.py", "book_preview"),        # MARKET_TRIP_MANAGE (BOOK dialog preview, read-only)
                  ("services/market_trip_book_service.py", "reconciliation_approval_preview"), # approval dialog, read-only
                  ("services/market_trip_book_service.py", "reconciliation_preview"),   # MARKET_TRIP_MANAGE (R1 read-only reconciliation preview of a BOOK trip: snapshot reads, no write)
                  ("services/market_trip_service.py", "trip_detail"),              # MARKET_TRIP_MANAGE (custody and history)
                  ("services/market_trip_service.py", "shop_lots"),                # MARKET_TRIP_MANAGE (shop-side lot stock)
                  ("services/market_trip_service.py", "sale_price_check"),         # MARKET_TRIP_MANAGE (read-only sale price preview)
                  ("services/customer_account_service.py", "statement"),           # CUSTOMERS_VIEW or settle
                  ("services/customer_deposit_service.py", "refund_status"),       # CUSTOMER_ACCOUNT_SETTLE; available delegates here
                  ("services/gold_price_settings_service.py", "load"),             # SETTINGS_MANAGE
                  ("services/dashboard_service.py", "today"),                      # APP_LOGIN (+ sales grants)
                  ("services/invoice_history_service.py", "_read_verified"),       # INVOICE_VIEW / INVOICE_REPRINT
                  ("services/management_review_service.py", "open_reviews"),       # either review permission
                  ("services/management_review_service.py", "counts"),             # APP_LOGIN; count only for reviewers
                  ("services/customer_service.py", "_authorize_page_read"),        # CUSTOMERS_VIEW (Customers page)
                  ("services/invoice_service.py", "search_customers"),             # invoice permissions (Billing picker)
                  ("services/dead_cash_service.py", "context"),                    # DEAD_CASH_MANAGE (dialog balance)
                  ("services/customer_service.py", "_dead_cash_visible"),          # DEAD_CASH_VIEW (detail gate)
                  ("services/customer_service.py", "get_dead_cash_history"),       # DEAD_CASH_VIEW (history)
                  ("services/user_admin_service.py", "overview"),                  # USERS_MANAGE (read model, no hashes)
                  ("services/supplier_service.py", "_read_verified"),              # SUPPLIER_VIEW (suppliers, ledgers)
                  ("services/supplier_purchase_service.py", "_read_verified"),     # SUPPLIER_VIEW (invoices, intake)
                  ("services/system_settings_service.py", "overview"),             # SYSTEM_SETTINGS_VIEW (the settings list, 081)
                  ("services/system_settings_service.py", "public_settings")}      # SYSTEM_SETTINGS_VIEW (the public settings of the interface, 081)

    def sites(self, method):
        found = set()
        for path in sorted(Path("services").glob("*.py")):
            if path.name == "authorization_service.py":
                continue
            function = None
            for line in path.read_text(encoding="utf-8").splitlines():
                match = re.match(r"\s{4}def (\w+)\(", line)
                if match:
                    function = match.group(1)
                if re.search(rf"\.{method}\(", line):
                    found.add((path.as_posix(), function))
        return found

    def test_read_path_call_sites_are_exactly_the_audited_reads(self):
        self.assertEqual(self.sites("require_permission_read") | self.sites("require_any_permission_read"),
                         self.READ_SITES)

    def test_explicit_read_only_service_sites_recheck_the_documented_permissions(self):
        import ast
        writers = ("insert", "update_", "delete", "post", "lock", "write", "record", "reverse", "close", "reopen", "commit")
        cases = (
            ("services/market_trip_service.py", "trip_page", {"PERMISSION"}),
            ("services/market_trip_book_service.py", "book_preview", {"PERMISSION"}),
            ("services/market_trip_book_service.py", "reconciliation_preview", {"PERMISSION"}),
            ("services/market_trip_service.py", "supplier_settlement_sources", {"PERMISSION", "GOLD_SETTLE_PERMISSION"}),
            ("services/holding_settlement_service.py", "settlement_sources", {"SETTLE_PERMISSION", "MANAGE_PERMISSION"}),
        )
        for path, name, expected_permissions in cases:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"))
            function = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == name)
            calls = [n for n in ast.walk(function) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
            permission = [c for c in calls if c.func.attr == "require_permission_read"]
            self.assertEqual({ast.unparse(c.args[2]) for c in permission}, expected_permissions, name)
            self.assertFalse([c.func.attr for c in calls if c.func.attr.startswith(writers)], name)
            self.assertFalse([c for c in calls if c.func.attr in ("require_permission", "require_permission_for_write")], name)

    def test_every_mutation_keeps_the_locking_path(self):
        writes = self.sites("require_permission") | self.sites("require_permission_for_write")
        self.assertFalse(writes & self.READ_SITES)
        for expected in (("services/invoice_service.py", "finalize"), ("services/exchange_service.py", "finalize"),
                         ("services/return_service.py", "finalize"), ("services/correction_service.py", "reverse"),
                         ("services/correction_service.py", "correct_settlement"),
                         ("services/customer_account_service.py", "finalize"),
                         ("services/customer_deposit_service.py", "_finalize"),
                         ("services/financial_account_service.py", "_finalize"),
                         ("services/management_review_service.py", "_resolve"),
                         ("services/operational_balance_service.py", "_write"),
                         ("services/customer_service.py", "_authorize_write"),        # CUSTOMERS_MANAGE
                         ("services/dead_cash_service.py", "write_off"),              # DEAD_CASH_MANAGE
                         ("services/dead_cash_service.py", "reverse"),                # DEAD_CASH_REVERSE
                         ("services/master_data_service.py", "_authorize_write")):    # MASTER_DATA_MANAGE
            self.assertIn(expected, writes, expected)


class ServicePathTests(unittest.TestCase):
    """Through the real services: reads ask for the READ path, mutations for the WRITE path."""

    def setUp(self):
        from tests import test_financial_accounts as financial
        self.case = financial.FinancialTestCase("run")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.fin = self.case.fin
        self.checked = lambda: [kind for kind, _ in getattr(self.fin._authorization, "checked", [])]

    def test_cash_reads_use_read_path_and_writes_use_write_path(self):
        self.fin.cash_view()
        self.fin.accounts()
        self.fin.statement("CASH:1")
        self.assertEqual(set(self.checked()), {"READ"})
        self.fin._authorization.checked = []
        self.fin.cash_movement("CASH:1", "OUT", "EXPENSE", "10", "", str(uuid4()))
        self.fin.transfer("CASH:1", "BANK:7", "10", "", str(uuid4()))
        self.assertEqual(self.checked(), ["WRITE", "WRITE"])

    def test_revocation_refuses_the_next_read_and_the_next_write(self):
        self.fin.cash_view()
        self.fin._authorization.permissions = frozenset({"APP_LOGIN"})     # revoked in the database
        with self.assertRaises(PermissionError):
            self.fin.cash_view()                                           # no stale session cache
        with self.assertRaises(PermissionError):
            self.fin.cash_movement("CASH:1", "OUT", "EXPENSE", "10", "", str(uuid4()))
        self.assertEqual(self.case.store.ops, {})                          # nothing became visible / posted


class ProtectedReadRevocationTests(unittest.TestCase):
    """Each protected read follows the DATABASE grants: refused after a revocation while the user stays
    logged in (the session cache still lists the permission), and working again once it is restored."""

    def revoke_restore(self, authorization, permission, read, *, session_permissions):
        full = frozenset(session_permissions)
        authorization.permissions = full
        read()                                                           # granted
        authorization.permissions = full - {permission}                  # revoked in the database only
        with self.assertRaises(PermissionError):
            read()
        authorization.permissions = full                                 # restored: no re-login needed
        read()
        self.assertEqual({kind for kind, _ in authorization.checked}, {"READ"})    # never the locking path

    def test_invoice_history_search_detail_lookup_and_reprint(self):
        from tests import test_invoice_history as history
        service, _, _ = history.make(permissions=history.EMPLOYEE)
        authorization = service._authorization
        for read in (lambda: service.search(), lambda: service.detail("21"), lambda: service.find_by_number("26092001")):
            authorization.checked.clear()
            self.revoke_restore(authorization, "INVOICE_VIEW", read, session_permissions=history.EMPLOYEE)
        authorization.checked.clear()
        self.revoke_restore(authorization, "INVOICE_REPRINT", lambda: service.reprint("21"),
                            session_permissions=history.EMPLOYEE)

    def test_invoice_history_manager_details_follow_database_grants(self):
        from tests import test_invoice_history as history
        service, _, _ = history.make(permissions=history.MANAGER)
        as_manager = service.detail("21")
        service._authorization.permissions = history.EMPLOYEE             # MANAGEMENT_REVIEW revoked in the database
        as_employee = service.detail("21")
        self.assertNotEqual(as_manager, as_employee)                     # manager-only data no longer returned

    def test_dashboard_sales_section_follows_database_grants(self):
        from tests import test_dashboard as dashboard
        service, _ = dashboard.make_service(dashboard.busy_state())
        authorization = service._authorization
        self.assertTrue(service.today()["can_see_sales"])
        authorization.permissions = frozenset({"APP_LOGIN"})              # both sales permissions revoked
        snapshot = service.today()
        self.assertFalse(snapshot["can_see_sales"])
        self.assertNotIn("invoice_count", snapshot)                      # no sales data becomes visible
        authorization.permissions = dashboard.BILLING
        self.assertTrue(service.today()["can_see_sales"])
        authorization.permissions = frozenset()                          # APP_LOGIN itself revoked
        with self.assertRaises(PermissionError):
            service.today()
        self.assertEqual({kind for kind, _ in authorization.checked}, {"READ"})

    def test_reviews_list_needs_one_of_its_permissions_in_the_database(self):
        from tests import test_returned_item_inspection as inspection
        case = inspection.ReturnedItemInspectionTests("run") if hasattr(inspection, "ReturnedItemInspectionTests") \
            else next(getattr(inspection, n)("run") for n in dir(inspection)
                      if isinstance(getattr(inspection, n), type) and hasattr(getattr(inspection, n), "review_service"))
        case.setUp()
        self.addCleanup(case.doCleanups)
        service = case.review_service(frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW", "INVENTORY_RECONCILE"}))
        service.open_reviews()
        service._authorization.permissions = frozenset({"APP_LOGIN", "INVENTORY_RECONCILE"})   # one remains
        service.open_reviews()
        service._authorization.permissions = frozenset({"APP_LOGIN"})                          # both revoked
        with self.assertRaises(PermissionError):
            service.open_reviews()
        service._authorization.permissions = frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW"})     # restored
        service.open_reviews()
        self.assertTrue(service.counts())                                # reviewers see the pending counts
        service._authorization.permissions = frozenset({"APP_LOGIN"})
        self.assertEqual(service.counts(), {})                           # no protected count for non-reviewers
        service._authorization.permissions = frozenset({"APP_LOGIN", "INVENTORY_RECONCILE"})
        self.assertTrue(service.counts())

    def test_gold_price_settings_load_is_a_non_locking_read_and_save_stays_strict(self):
        from contextlib import contextmanager
        from datetime import datetime
        from unittest.mock import Mock
        from services.gold_price_settings_service import GoldPriceSettingsService
        Repository.state = {"locks": [], "grants": {"APP_LOGIN", "SETTINGS_MANAGE"},
                            "user": {"id": 7, "employee_id": None, "display_name": "مالك", "is_active": True,
                                     "must_change_password": False}}
        session = AppSession()
        session._establish(AuthenticationResult(7, None, "مالك", frozenset({"APP_LOGIN", "SETTINGS_MANAGE"}),
                                                _VERIFIED), 0)
        actor = session.current_actor()
        saved = []

        class Settings:
            def __init__(self, connection):
                pass

            def get_settings(self, lock=False):
                return {"refresh_seconds": 60, "stale_after_seconds": 300}

            def get_provider_options(self):
                return {}

            def save_provider_settings(self, *args):
                saved.append(args)

        @contextmanager
        def transaction():
            yield object()
        secrets = Mock()
        secrets.get_secret.return_value = None
        service = GoldPriceSettingsService(session, secrets=secrets, transaction=transaction, repository=Settings)
        service.authorization = AuthorizationService(session, repository_factory=Repository)
        self.assertEqual(service.load(actor)["refresh"], "60")
        self.assertEqual(Repository.state["locks"], [("identity", False), ("permissions", False)])   # no lock
        Repository.state["grants"].discard("SETTINGS_MANAGE")            # revoked in the database
        with self.assertRaises(PermissionError):
            service.load(actor)
        Repository.state["grants"].add("SETTINGS_MANAGE")                # restored
        service.load(actor)
        Repository.state["locks"].clear()
        service.save(actor, "https://goldtimo.com/api/v1", "10", "60", "300", "")
        self.assertEqual(Repository.state["locks"], [("identity", True), ("permissions", True)])     # strict write
        self.assertEqual(len(saved), 1)
        Repository.state["grants"].discard("SETTINGS_MANAGE")
        with self.assertRaises(PermissionError):
            service.save(actor, "https://goldtimo.com/api/v1", "10", "60", "300", "")
        self.assertEqual(len(saved), 1)                                  # a revoked write posts nothing


if __name__ == "__main__":
    unittest.main()
