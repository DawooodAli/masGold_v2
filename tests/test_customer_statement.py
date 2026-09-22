"""Read-only statement regressions: SELECT-only fixtures and mocked connections.

SQLite executes the repository's window/CTE query against inline VALUES, not
created tables. Integer-valued fixture amounts avoid SQLite floating precision;
MySQL Decimal behavior is verified separately using exact mocked Decimal values.
"""

from datetime import datetime
from decimal import Decimal
import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from app.viewmodels.customer_viewmodel import CustomerViewModel
from app.viewmodels.customer_presentation import transaction_row, statement_summary
from database.connection import DatabaseConnection
from repositories.customer_repository import CustomerRepository
from services.customer_service import CustomerService


def fixture_tables(movements=None):
    customer_columns = (
        "id", "customer_code", "name", "mobile", "place", "customer_type_id",
        "is_active", "notes", "created_at", "updated_at", "created_by_user_id", "updated_by_user_id",
    )
    transaction_columns = (
        "id", "customer_id", "transaction_type_id", "amount_delta", "reference_type",
        "reference_id", "reference_code", "description", "notes", "occurred_at",
        "created_at", "created_by_user_id", "legacy_source_table", "legacy_source_id",
    )
    if movements is None:
        movements = [
            (1, 1, 100, "2026-09-01 10:00:00"),
            (2, 2, -30, "2026-09-10 12:00:00"),
            (3, 3, 5, "2026-09-10 12:00:00"),
            (4, 2, -25, "2026-09-11 00:00:00"),
            (5, 1, 10, "2026-09-20 23:59:59"),
            (6, 2, -15, "2026-09-21 00:00:00"),
        ]
    transactions = [
        (identifier, 1, type_id, amount, "OTHER", 900 + identifier, f"R{identifier}",
         f"movement {identifier}", "movement notes", occurred, occurred, None, None, None)
        for identifier, type_id, amount, occurred in movements
    ]
    return {
        "customers": (customer_columns, [
            (1, "C000001", "First", "010", "Cairo", 1, 1, "customer notes", "2026-01-01", "2026-01-01", 11, 12),
            (2, None, "Inactive", None, None, None, 0, None, "2026-01-01", "2026-01-01", None, None),
        ]),
        "customer_types": (("id", "code", "name", "is_active", "sort_order"), [(1, "REGULAR", "Regular", 1, 1)]),
        "customer_transaction_types": (("id", "code", "name", "expected_direction", "is_active"), [
            (1, "SALE_BALANCE_DUE", "Sale", "INCREASE", 1),
            (2, "CUSTOMER_PAYMENT", "Payment", "DECREASE", 0),
            (3, "MANUAL_ADJUSTMENT", "Adjustment", "EITHER", 1),
        ]),
        "customer_transactions": (transaction_columns, transactions),
        "customer_flags": (("id", "customer_id", "flag_type", "is_active", "severity", "reason", "related_dead_cash_id", "created_at", "created_by_user_id"), [
            (1, 1, "DEAD_CASH_WARNING", 1, "WARNING", "First warning", 1, "2026-01-01", None),
            (2, 1, "OTHER_WARNING", 1, "INFO", "Second warning", None, "2026-01-01", None),
            (3, 1, "HISTORICAL", 0, "INFO", "Old warning", None, "2026-01-01", None),
        ]),
        "dead_cash_writeoffs": ((
            "id", "customer_id", "amount", "balance_before", "balance_after",
            "writeoff_transaction_id", "reason", "notes", "occurred_at", "created_by_user_id",
            "is_reversed", "reversed_at", "reversed_by_user_id", "reversal_transaction_id", "reversal_reason",
        ), [
            (1, 1, 10, 100, 90, 101, "First write-off", "First notes", "2026-01-01", 11, 0, None, None, None, None),
            (2, 1, 5, 90, 85, 102, "Second write-off", None, "2026-01-02", None, 0, None, None, None, None),
            (3, 1, 20, 85, 65, 103, "Reversed write-off", "Preserve original", "2026-01-03", 11,
             1, "2026-01-04", 12, 104, "Correction"),
        ]),
    }


class SelectOnlyFixtureConnection:
    def __init__(self, movements=None):
        self.database = sqlite3.connect(":memory:")
        self.database.row_factory = sqlite3.Row
        self.executions = []
        parts, self.fixture_params = [], []
        for name, (columns, rows) in fixture_tables(movements).items():
            placeholders = "(" + ",".join("?" for _ in columns) + ")"
            parts.append(f"{name}({','.join(columns)}) AS (VALUES {','.join(placeholders for _ in rows)})")
            for row in rows:
                self.fixture_params.extend(row)
        parts.append("""vw_customer_balances AS (
            SELECT c.id AS customer_id, COALESCE(SUM(t.amount_delta),0) AS balance,
                CASE WHEN COALESCE(SUM(t.amount_delta),0)>0 THEN 'CUSTOMER_OWES_SHOP'
                     WHEN COALESCE(SUM(t.amount_delta),0)<0 THEN 'SHOP_OWES_CUSTOMER'
                     ELSE 'SETTLED' END AS balance_status,
                MAX(t.occurred_at) AS last_transaction_at
            FROM customers c LEFT JOIN customer_transactions t ON t.customer_id=c.id
            GROUP BY c.id
        )""")
        self.prefix = "WITH " + ",".join(parts)

    def cursor(self, dictionary=False):
        owner = self

        class Cursor:
            def execute(self, query, params=()):
                owner.executions.append((query, params))
                query = query.lstrip()
                if query.startswith("WITH "):
                    sql = owner.prefix + "," + query[5:]
                elif query.startswith("SELECT"):
                    sql = owner.prefix + " " + query
                else:
                    raise AssertionError("Only SELECT/CTE fixture queries are allowed.")
                params = tuple(value.isoformat(" ") if isinstance(value, datetime) else value for value in params)
                self.cursor = owner.database.execute(sql.replace("%s", "?"), tuple(owner.fixture_params) + params)

            def fetchall(self):
                rows = [dict(row) for row in self.cursor.fetchall()]
                money_fields = {
                    "balance", "summary_ledger_balance", "summary_opening_balance", "summary_period_net",
                    "summary_closing_balance", "summary_matching_net", "movement_amount_delta", "movement_running_balance",
                    "amount", "balance_before", "balance_after",
                }
                for row in rows:
                    for key in row.keys() & money_fields:
                        if row[key] is not None:
                            row[key] = Decimal(str(row[key]))
                return rows

            def fetchone(self):
                rows = self.fetchall()
                return rows[0] if rows else None

            def close(self):
                self.cursor.close()

        return Cursor()


class NoDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(
            DatabaseConnection, "connect",
            side_effect=AssertionError("Real database connections are forbidden."),
        ))

    def fixtures(self, movements=None):
        connection = SelectOnlyFixtureConnection(movements)
        self.addCleanup(connection.database.close)
        return connection


class StatementQueryTests(NoDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.connection = self.fixtures()
        self.repository = CustomerRepository(self.connection)

    def test_running_balance_uses_complete_ledger_and_id_tiebreaker(self):
        result = self.repository.get_customer_statement(1)
        self.assertEqual([row["transaction_id"] for row in result["items"]], [6, 5, 4, 3, 2, 1])
        self.assertEqual([row["running_balance"] for row in result["items"]], [45, 60, 50, 75, 70, 100])
        self.assertEqual(result["summary"]["current_balance"], Decimal("45"))
        self.assertEqual(len(self.connection.executions), 1)

    def test_warning_and_dead_cash_counts_do_not_multiply_movements(self):
        result = self.repository.get_customer_statement(1)
        customer = result["customer"]
        self.assertEqual(customer["active_warning_count"], 2)
        self.assertTrue(customer["has_active_dead_cash_warning"])
        self.assertTrue(customer["has_dead_cash_history"])
        self.assertEqual(customer["unreversed_dead_cash_count"], 2)
        self.assertEqual(result["summary"]["ledger_count"], 6)
        self.assertEqual(result["summary"]["ledger_balance"], Decimal("45"))

    def test_customer_and_movement_notes_and_audit_fields_remain_separate(self):
        result = self.repository.get_customer_statement(1)
        self.assertEqual(result["customer"]["notes"], "customer notes")
        self.assertEqual(result["customer"]["created_by_user_id"], 11)
        self.assertEqual(result["items"][0]["notes"], "movement notes")
        self.assertIsNone(result["items"][0]["created_by_user_id"])
        self.assertEqual(result["items"][0]["reference_id"], 906)

    def test_date_range_keeps_real_opening_and_inclusive_end(self):
        result = self.repository.get_customer_statement(
            1, from_at=datetime(2026, 9, 10), to_exclusive=datetime(2026, 9, 21),
        )
        self.assertEqual([row["transaction_id"] for row in result["items"]], [5, 4, 3, 2])
        self.assertEqual(result["summary"]["opening_balance"], 100)
        self.assertEqual(result["summary"]["period_net"], -40)
        self.assertEqual(result["summary"]["closing_balance"], 60)
        self.assertEqual(result["summary"]["current_balance"], 45)

    def test_type_filter_keeps_hidden_movements_in_running_and_range_balances(self):
        result = self.repository.get_customer_statement(
            1, from_at=datetime(2026, 9, 10), to_exclusive=datetime(2026, 9, 21),
            transaction_type_code="CUSTOMER_PAYMENT",
        )
        self.assertEqual([row["transaction_id"] for row in result["items"]], [4, 2])
        self.assertEqual([row["running_balance"] for row in result["items"]], [50, 70])
        self.assertEqual(result["summary"]["opening_balance"], 100)
        self.assertEqual(result["summary"]["closing_balance"], 60)
        self.assertEqual(result["summary"]["period_net"], -40)
        self.assertEqual(result["summary"]["matching_net"], -55)
        self.assertEqual(result["summary"]["matching_count"], 2)

    def test_empty_date_range_still_returns_balances(self):
        result = self.repository.get_customer_statement(
            1, from_at=datetime(2026, 9, 12), to_exclusive=datetime(2026, 9, 20),
        )
        self.assertEqual(result["items"], [])
        self.assertEqual(result["summary"]["opening_balance"], 50)
        self.assertEqual(result["summary"]["closing_balance"], 50)
        self.assertEqual(result["summary"]["period_net"], 0)

    def test_empty_customer_and_missing_customer_are_distinct(self):
        result = self.repository.get_customer_statement(2)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["summary"]["ledger_count"], 0)
        self.assertEqual(result["summary"]["opening_balance"], 0)
        self.assertEqual(result["summary"]["closing_balance"], 0)
        self.assertIsNone(self.repository.get_customer_statement(999))

    def test_more_than_one_page_retains_full_ledger_running_totals(self):
        connection = self.fixtures([(n, 1, 1, "2026-09-10 12:00:00") for n in range(1, 206)])
        repository = CustomerRepository(connection)
        second = repository.get_customer_statement(1, limit=101, offset=100)
        self.assertEqual(len(second["items"]), 101)
        self.assertEqual(second["items"][0]["transaction_id"], 105)
        self.assertEqual(second["items"][0]["running_balance"], 105)
        self.assertEqual(second["items"][-1]["running_balance"], 5)
        self.assertEqual(second["summary"]["current_balance"], 205)
        empty = repository.get_customer_statement(1, limit=101, offset=300)
        self.assertEqual(empty["items"], [])
        self.assertEqual(empty["summary"]["current_balance"], 205)

    def test_transaction_wrapper_reuses_statement_query(self):
        items = self.repository.get_customer_transactions(1, limit=2, offset=2)
        self.assertEqual([row["running_balance"] for row in items], [50, 75])
        self.assertEqual(len(self.connection.executions), 1)
        self.assertIn("SUM(t.amount_delta) OVER", self.connection.executions[0][0])


class StatementServiceTests(NoDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.connection = self.fixtures()
        transaction = MagicMock()
        transaction.connection = self.connection
        transaction.__enter__.return_value = transaction
        self.transactions = self.enterContext(patch(
            "services.customer_service.DatabaseTransaction", return_value=transaction,
        ))
        self.service = CustomerService()

    def test_service_dates_type_filter_and_summary_metadata(self):
        result = self.service.get_customer_statement(
            "1", from_date="2026-09-10", to_date="2026-09-20",
            transaction_type_code="CUSTOMER_PAYMENT",
        )
        self.assertEqual(result["summary"]["from_date"], "2026-09-10")
        self.assertEqual(result["summary"]["to_date"], "2026-09-20")
        self.assertTrue(result["summary"]["type_filter_active"])
        self.assertEqual(result["summary"]["closing_balance"], Decimal("60"))
        self.assertEqual(result["ledger_token"], (6, 6, Decimal("45")))
        self.assertFalse(result["ledger_changed"])

    def test_lower_id_committed_later_restarts_page_even_when_max_and_sum_match(self):
        # A zero-net pair arriving late changes COUNT but not MAX(id) or SUM.
        result = self.service.get_customer_statement(
            1, page=2, expected_ledger_token=(4, 6, Decimal("45")),
        )
        self.assertEqual(result["page"], 1)
        self.assertTrue(result["ledger_changed"])
        self.assertTrue(result["message"])
        self.assertEqual(result["items"][0]["transaction_id"], 6)
        self.assertEqual(len(self.connection.executions), 4)
        self.assertIn("FROM customer_flags", self.connection.executions[-2][0])
        self.assertIn("FROM dead_cash_writeoffs", self.connection.executions[-1][0])

    def test_statement_refreshes_warning_and_dead_cash_panels_with_summary(self):
        result = self.service.get_customer_statement(1)
        self.assertEqual(len(result["active_flags"]), result["customer"]["active_warning_count"])
        self.assertTrue(any(row["flag_type"] == "DEAD_CASH_WARNING" for row in result["active_flags"]))
        dead_cash = result["dead_cash_page"]
        self.assertEqual(dead_cash["page"], 1)
        self.assertFalse(dead_cash["has_more"])
        self.assertEqual(len(dead_cash["items"]), 3)
        self.assertEqual(sum(not row["is_reversed"] for row in dead_cash["items"]), result["customer"]["unreversed_dead_cash_count"])
        reversal = dead_cash["items"][0]
        self.assertEqual(reversal["dead_cash_id"], 3)
        self.assertTrue(reversal["is_reversed"])
        self.assertEqual(reversal["reversal_transaction_id"], 104)
        self.assertEqual(reversal["reversed_by_user_id"], 12)
        self.assertEqual(reversal["reversal_reason"], "Correction")
        self.assertEqual(reversal["amount"], Decimal("20"))
        self.assertEqual(reversal["notes"], "Preserve original")
        self.transactions.assert_called_once_with()
        self.assertEqual(len(self.connection.executions), 3)

    def test_matching_token_keeps_requested_page(self):
        result = self.service.get_customer_statement(
            1, page=2, expected_ledger_token=(6, 6, Decimal("45")),
        )
        self.assertEqual(result["page"], 2)
        self.assertFalse(result["ledger_changed"])
        self.assertEqual(result["items"], [])
        self.assertEqual(result["summary"]["current_balance"], 45)

    def test_invalid_dates_and_reversed_range_fail_before_database_access(self):
        cases = (
            dict(from_date="2026-02-30"), dict(to_date="20260920"),
            dict(from_date="2026-09-21", to_date="2026-09-20"),
            dict(to_date="9999-12-31"), dict(from_date="0999-01-01"),
            dict(from_date=123),
        )
        for parameters in cases:
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                self.service.get_customer_statement(1, **parameters)
        self.transactions.assert_not_called()

    def test_inactive_transaction_type_is_available_but_unknown_code_rejected(self):
        choices = self.service.get_customer_transaction_types()
        self.assertTrue(any(row["code"] == "CUSTOMER_PAYMENT" and not row["is_active"] for row in choices))
        self.assertEqual(len(self.service.get_customer_statement(1, transaction_type_code="CUSTOMER_PAYMENT")["items"]), 3)
        with self.assertRaises(ValueError):
            self.service.get_customer_statement(1, transaction_type_code="NOT_A_TYPE")

    def test_customer_selection_rechecks_active_status_and_defaults_to_active(self):
        self.assertEqual(self.service.get_customer_for_selection(1)["customer_id"], 1)
        with self.assertRaises(ValueError):
            self.service.get_customer_for_selection(2)
        self.assertEqual(self.service.get_customer_for_selection(2, allow_inactive=True)["customer_id"], 2)
        with self.assertRaises(ValueError):
            self.service.get_customer_for_selection(999)

    def test_exact_decimal_reconciliation_rejects_mismatch(self):
        exact = Decimal("99999999999999.99")
        raw = {
            "customer": {"balance": exact}, "items": [], "loaded_at": datetime(2026, 9, 20),
            "summary": {"ledger_count": 1, "ledger_max_id": 1, "ledger_balance": exact},
        }
        result = self.service._statement_result(raw, 1, None, None, None)
        self.assertIs(result["summary"]["current_balance"], exact)
        self.assertIs(result["ledger_token"][2], exact)
        raw["summary"]["ledger_balance"] = Decimal("99999999999999.98")
        with self.assertRaises(ValueError):
            self.service._statement_result(raw, 1, None, None, None)

    def test_profile_keeps_v1_keys_and_adds_statement_data_from_one_result(self):
        repository = MagicMock()
        repository.get_customer_statement.return_value = CustomerRepository(self.connection).get_customer_statement(1)
        repository.get_active_customer_flags.return_value = []
        repository.get_dead_cash_history.return_value = []
        with patch("services.customer_service.CustomerRepository", return_value=repository):
            result = self.service.get_customer_profile(1)
        self.assertEqual(result["customer"]["balance"], result["statement_summary"]["current_balance"])
        self.assertEqual(result["transactions_page"]["items"][0]["running_balance"], 45)
        self.assertIn("active_flags", result)
        self.assertIn("dead_cash_page", result)
        self.assertIn("statement_loaded_at", result)
        self.assertEqual(result["ledger_token"], (6, 6, Decimal("45")))
        repository.get_customer_by_id.assert_not_called()
        repository.get_customer_transactions.assert_not_called()


class StatementViewModelTests(NoDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.repository = CustomerRepository(self.fixtures())
        self.service = MagicMock()
        self.service.get_customer_profile.side_effect = self.profile
        self.view_model = CustomerViewModel(service=self.service)
        self.assertTrue(self.view_model.selectCustomer("1"))

    def statement(self, customer_id=1, page=1, **filters):
        result = CustomerService._statement_result(
            self.repository.get_customer_statement(customer_id, **filters), page,
            None, None, filters.get("transaction_type_code"),
        )
        result["active_flags"] = self.repository.get_active_customer_flags(customer_id)
        result["dead_cash_page"] = CustomerService._paged(
            self.repository.get_dead_cash_history(customer_id), 1,
        )
        return result

    def profile(self, customer_id):
        result = self.statement(int(customer_id))
        return {
            "customer": result["customer"], "active_flags": result["active_flags"],
            "transactions_page": {key: result[key] for key in ("items", "page", "page_size", "has_more")},
            "dead_cash_page": result["dead_cash_page"],
            "statement_summary": result["summary"], "ledger_token": result["ledger_token"],
            "statement_loaded_at": result["loaded_at"],
        }

    def test_profile_publishes_ledger_balances_and_recorded_history_wording(self):
        self.assertEqual(self.view_model.transactions[0]["id"], "6")
        self.assertEqual(self.view_model.transactions[0]["running_balance_text"], "+45.00")
        self.assertEqual(self.view_model.statementSummary["opening_balance_text"], "0.00")
        self.assertIn("أول حركة مسجلة", self.view_model.statementSummary["history_basis_text"])
        self.assertEqual(self.view_model.statementSummary["current_balance"], self.view_model.selectedCustomer["balance"])

    def test_statement_refresh_replaces_stale_warnings_and_dead_cash_panels(self):
        self.assertEqual(len(self.view_model.activeFlags), 2)
        self.assertEqual(len(self.view_model.deadCashHistory), 3)
        result = self.statement()
        result["customer"].update(
            active_warning_count=0, has_active_dead_cash_warning=False,
            has_dead_cash_history=False, unreversed_dead_cash_count=0,
        )
        result["active_flags"] = []
        result["dead_cash_page"] = {"items": [], "page": 1, "page_size": 100, "has_more": False}
        self.service.get_customer_statement.return_value = result
        self.view_model.refreshStatement()
        self.assertEqual(self.view_model.selectedCustomer["active_warning_count"], 0)
        self.assertEqual(self.view_model.activeFlags, [])
        self.assertEqual(self.view_model.deadCashHistory, [])
        self.assertEqual(self.view_model.deadCashPage, 1)
        self.assertFalse(self.view_model.hasMoreDeadCash)

    def test_filter_and_page_requests_forward_python_owned_ledger_token(self):
        result = self.statement(transaction_type_code="CUSTOMER_PAYMENT")
        self.service.get_customer_statement.return_value = result
        self.view_model.applyStatementFilters("2026-09-10", "2026-09-20", "CUSTOMER_PAYMENT")
        self.service.get_customer_statement.assert_called_with(
            "1", from_date="2026-09-10", to_date="2026-09-20",
            transaction_type_code="CUSTOMER_PAYMENT", page=1, expected_ledger_token=None,
        )
        self.assertTrue(self.view_model.statementSummary["type_filter_active"])
        self.view_model.loadTransactionsPage(2)
        self.assertEqual(self.service.get_customer_statement.call_args.kwargs["expected_ledger_token"], result["ledger_token"])
        self.assertNotIn("ledger_token", self.view_model.statementSummary)

    def test_ledger_change_publishes_new_first_page_and_notice_together(self):
        result = self.statement()
        result.update(page=1, has_more=True, ledger_changed=True, message="تغيرت حركات العميل.")
        self.service.get_customer_statement.return_value = result
        self.view_model.loadTransactionsPage(3)
        self.assertEqual(self.view_model.transactionsPage, 1)
        self.assertTrue(self.view_model.statementStale)
        self.assertTrue(self.view_model.hasMoreTransactions)
        self.assertTrue(self.view_model.statementSummary)
        self.assertEqual(self.view_model.statementErrorMessage, result["message"])

    def test_failure_clears_old_statement_under_new_filters_and_refresh_recovers(self):
        self.service.get_customer_statement.side_effect = ValueError("تاريخ البداية غير صحيح.")
        self.view_model.applyStatementFilters("not-a-date", "", "")
        self.assertEqual(self.view_model.statementSummary, {})
        self.assertEqual(self.view_model.transactions, [])
        self.assertFalse(self.view_model.hasMoreTransactions)
        self.assertTrue(self.view_model.statementStale)
        self.assertTrue(self.view_model.statementErrorMessage)
        self.service.get_customer_statement.side_effect = None
        self.service.get_customer_statement.return_value = self.statement()
        self.view_model.resetStatementFilters()
        self.assertTrue(self.view_model.statementSummary)
        self.assertFalse(self.view_model.statementStale)
        self.assertEqual(self.view_model.statementErrorMessage, "")

    def test_switching_customer_resets_filters_and_does_not_mix_rows(self):
        self.service.get_customer_statement.return_value = self.statement()
        self.view_model.applyStatementFilters("2026-09-10", "", "CUSTOMER_PAYMENT")
        self.assertTrue(self.view_model.selectCustomer("2"))
        self.assertEqual(self.view_model.selectedCustomerId, "2")
        self.assertEqual(self.view_model.statementFromDate, "")
        self.assertEqual(self.view_model.statementTypeCode, "")
        self.assertEqual(self.view_model.transactions, [])
        self.assertEqual(self.view_model.statementSummary["current_balance_text"], "0.00")

    def test_presentation_uses_actual_amount_sign_and_exact_decimal_strings(self):
        row = transaction_row({
            "transaction_id": 18446744073709551615,
            "amount_delta": Decimal("-0.01"), "running_balance": Decimal("-99999999999999.99"),
            "transaction_type_code": "MANUAL_ADJUSTMENT", "expected_direction": "INCREASE",
        })
        self.assertEqual(row["id"], "18446744073709551615")
        self.assertEqual(row["amount_text"], "-0.01")
        self.assertEqual(row["direction_label"], "خفض الرصيد لصالح العميل")
        self.assertEqual(row["running_balance"], "-99999999999999.99")
        self.assertEqual(row["running_balance_meaning"], "للعميل عند المحل")
        summary = statement_summary({"opening_balance": Decimal("100.00"), "from_date": "2026-09-10"})
        self.assertIn("قبل تاريخ البداية", summary["history_basis_text"])


if __name__ == "__main__":
    unittest.main()
