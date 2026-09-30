"""DEAD_CASH_VIEW guards Dead Cash DETAIL (history, amounts, reasons, DC references, summary); the caution
warning alone stays available where operationally needed. Plus the pinned idempotency / reversal-retry
contracts. No application database (the Customers reads run on the SELECT-only SQLite fixture)."""
from decimal import Decimal as D
from unittest.mock import MagicMock, patch
from uuid import uuid4

from services.customer_service import CustomerService
from tests.auth_fakes import customers_page_service
from tests.test_customer_statement import NoDatabaseTestCase
from tests.test_dead_cash import DeadCashCase, PASSWORD
from services.dead_cash_service import DeadCashError

PAGE_ONLY = frozenset({"APP_LOGIN", "CUSTOMERS_VIEW"})
WITH_VIEW = PAGE_ONLY | {"DEAD_CASH_VIEW"}


class DetailReadTests(NoDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.connection = self.fixtures()
        transaction = MagicMock()
        transaction.connection = self.connection
        transaction.__enter__.return_value = transaction
        transaction.__exit__.return_value = False
        self.enterContext(patch("services.customer_service.DatabaseTransaction", return_value=transaction))

    def dead_cash_queries(self):
        """Detail queries only: the history (reasons / amounts) and the summary (totals / dates). The profile's
        warning booleans (EXISTS / unreversed flag) are the caution itself, not detail."""
        return [sql for sql, *_ in self.connection.executions
                if "id AS dead_cash_id" in sql or "active_total" in sql]

    def test_detail_reads_require_dead_cash_view(self):
        service = customers_page_service(WITH_VIEW)
        profile = service.get_customer_profile("1")
        self.assertFalse(profile["dead_cash_restricted"])
        self.assertEqual(len(profile["dead_cash_page"]["items"]), 3)
        self.assertEqual(int(profile["dead_cash_summary"]["active_count"]), 2)
        self.assertEqual(len(service.get_dead_cash_history("1")["items"]), 3)
        self.assertIn(("READ", "DEAD_CASH_VIEW"), service._authorization.checked)   # read path, never a lock
        self.assertNotIn("WRITE", {kind for kind, _ in service._authorization.checked})

    def test_customers_view_alone_gets_the_warning_but_no_detail(self):
        service = customers_page_service(PAGE_ONLY)
        self.connection.executions.clear()
        profile = service.get_customer_profile("1")
        self.assertEqual(self.dead_cash_queries(), [])                  # never queried (not hidden in QML)
        self.assertTrue(profile["dead_cash_restricted"])
        self.assertEqual((profile["dead_cash_page"]["items"], profile["dead_cash_summary"]), ([], None))
        self.assertTrue(profile["customer"]["has_active_dead_cash_warning"])        # the caution stays
        warning = next(f for f in profile["active_flags"] if f["flag_type"] == "DEAD_CASH_WARNING")
        self.assertEqual(warning["reason"], "سبق شطب رصيد لهذا العميل")            # no stored reason / reference
        self.assertNotIn("related_dead_cash_id", warning)
        with self.assertRaises(PermissionError):
            service.get_dead_cash_history("1")
        statement = service.get_customer_statement("1")
        self.assertEqual((statement["dead_cash_page"]["items"], statement["dead_cash_page"]["restricted"]),
                         ([], True))
        self.assertEqual(self.dead_cash_queries(), [])

    def test_revocation_is_honored_on_the_next_detailed_read(self):
        service = customers_page_service(WITH_VIEW)
        service.get_dead_cash_history("1")
        service._authorization.permissions = PAGE_ONLY                    # revoked in the database only
        self.assertIn("DEAD_CASH_VIEW", service._session.current_actor().permissions)   # stale session
        with self.assertRaises(PermissionError):
            service.get_dead_cash_history("1")
        self.assertTrue(service.get_customer_profile("1")["dead_cash_restricted"])
        service._authorization.permissions = WITH_VIEW                    # restored, no re-login
        self.assertFalse(service.get_customer_profile("1")["dead_cash_restricted"])

    def test_ledger_rows_keep_amount_but_lose_dead_cash_detail(self):
        rows = [{"transaction_type_code": "DEAD_CASH_WRITE_OFF", "amount_delta": D("-4000.00"),
                 "running_balance": D("6000.00"), "notes": "عميل مسافر", "reference_code": None,
                 "reference_id": None, "description": "شطب رصيد"},
                {"transaction_type_code": "DEAD_CASH_REVERSAL", "amount_delta": D("4000.00"),
                 "notes": "بالخطأ", "reference_code": "DCR000001", "reference_id": 1, "description": "عكس شطب رصيد DC000001"},
                {"transaction_type_code": "SALE_BALANCE_DUE", "amount_delta": D("10000"), "notes": "n",
                 "reference_code": "26093001", "reference_id": 7, "description": "فاتورة"}]
        masked = CustomerService._ledger_without_dead_cash_detail(rows)
        self.assertEqual((masked[0]["amount_delta"], masked[0]["running_balance"], masked[0]["notes"]),
                         (D("-4000.00"), D("6000.00"), None))                  # balance still reconciles
        self.assertEqual((masked[1]["reference_code"], masked[1]["description"], masked[1]["notes"]),
                         (None, "عكس شطب رصيد", None))
        self.assertEqual(masked[2], rows[2])                                 # other rows untouched


class CustomerAccountStatementTests(DeadCashCase):
    """The Customer Account statement masks Dead Cash detail without DEAD_CASH_VIEW."""

    def statement(self, permissions):
        from services.customer_account_service import CustomerAccountService
        from tests import test_correction_service as fixture

        class Repo:
            def __init__(self, connection):
                pass

            def statement(self, *args):
                return [{"id": 1, "row_kind": "LEDGER", "source_type": "DEAD_CASH_WRITE_OFF",
                         "source_reference": "DC000001", "dead_cash_id": 1, "dead_cash_reason": "عميل مسافر",
                         "notes": "عميل مسافر", "description": "شطب رصيد", "amount_delta": D("-4000"),
                         "running_balance": D("6000")},
                        {"id": 2, "row_kind": "LEDGER", "source_type": "INVOICE", "source_reference": "26093001",
                         "notes": None, "description": "فاتورة", "amount_delta": D("10000"),
                         "running_balance": D("10000")}]

        class Customers:
            def __init__(self, connection):
                pass

            def get_customer_by_id(self, cid):
                return {"customer_id": cid, "balance": D("6000")}

        class Invoices:
            def __init__(self, connection):
                pass

            def active_bank_accounts(self):
                return []
        session_service = self.make(permissions)
        return CustomerAccountService(session_service._session, transaction_factory=session_service._transaction,
                                      repository=Repo, customers=Customers, invoices=Invoices,
                                      authorization=fixture.Authorization(session_service._session, permissions)
                                      ).statement("12")["rows"]

    def test_statement_detail_follows_dead_cash_view(self):
        viewer = self.statement(frozenset({"APP_LOGIN", "CUSTOMERS_VIEW", "DEAD_CASH_VIEW"}))
        self.assertEqual((viewer[0]["source_reference"], viewer[0]["dead_cash_reason"]), ("DC000001", "عميل مسافر"))
        plain = self.statement(frozenset({"APP_LOGIN", "CUSTOMERS_VIEW"}))
        self.assertEqual((plain[0]["source_reference"], plain[0]["dead_cash_reason"], plain[0]["notes"],
                          plain[0]["dead_cash_id"]), (None, None, None, None))
        self.assertEqual((plain[0]["amount_delta"], plain[0]["running_balance"]), (D("-4000"), D("6000")))
        self.assertEqual(plain[1]["source_reference"], "26093001")           # other sources unaffected


class IdempotencyContractTests(DeadCashCase):
    def test_same_key_same_payload_posts_once(self):
        key = str(uuid4())
        first = self.write_off("4000", reason="A", key=key)
        again = self.service.write_off("12", "4000", "A", PASSWORD, "23000.00", key)     # identical retry
        self.assertEqual((again["id"], again["code"], again["duplicate"]), (first["id"], first["code"], True))
        self.assertEqual((len(self.store.writeoffs), self.store.balance()), (1, D("19000.00")))
        self.assertEqual(len([r for r in self.store.ledger if r[2] == "DEAD_CASH_WRITE_OFF"]), 1)

    def test_same_key_with_any_different_hashed_field_is_refused(self):
        self.store.customers["13"] = {"customer_id": 13, "name": "آخر", "is_active": 1}
        self.store.ledger.append(("13", D("5000.00"), "SALE_BALANCE_DUE", "INVOICE", "600"))
        key = str(uuid4())
        self.service.write_off("12", "4000", "A", PASSWORD, "23000.00", key)
        variants = {"amount": ("12", "5000", "A", "23000.00"), "reason": ("12", "4000", "B", "23000.00"),
                    "customer": ("13", "4000", "A", "5000.00"), "opened balance": ("12", "4000", "A", "19000.00")}
        for field, (customer, amount, reason, expected) in variants.items():
            with self.subTest(field=field):
                self.refused("REQUEST_CONFLICT", lambda c=customer, a=amount, r=reason, e=expected:
                             self.service.write_off(c, a, r, PASSWORD, e, key))   # neither old result nor new post
        self.assertEqual(len(self.store.writeoffs), 1)

    def test_another_user_cannot_replay_the_key(self):
        key = str(uuid4())
        self.service.write_off("12", "4000", "A", PASSWORD, "23000.00", key)
        other = self.make(frozenset({"APP_LOGIN", "DEAD_CASH_MANAGE"}))
        other._session._actor = other._session.current_actor().__class__(
            99, None, "آخر", other._session.current_actor().permissions, other._session.current_actor().generation)
        self.refused("REQUEST_CONFLICT", lambda: other.write_off("12", "4000", "A", PASSWORD, "23000.00", key))


class ReversalRetryTests(DeadCashCase):
    def test_a_repeated_reversal_restores_the_balance_once(self):
        self.write_off("8000")
        self.service.reverse("1", "بالخطأ", PASSWORD)
        self.refused("ALREADY_REVERSED", lambda: self.service.reverse("1", "بالخطأ", PASSWORD))
        self.assertEqual(self.store.balance(), D("23000.00"))
        self.assertEqual(len([r for r in self.store.ledger if r[2] == "DEAD_CASH_REVERSAL"]), 1)
