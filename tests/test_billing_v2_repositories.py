"""Billing V2 repositories without a database: a scripted fake connection records
SQL and returns canned rows; old-gold balance SQL runs on in-memory SQLite."""
from datetime import date
from decimal import Decimal
from pathlib import Path
import re
import sqlite3
import unittest

from repositories.barcode_repository import BarcodeRepository, BarcodeSequenceExhausted
from repositories.customer_repository import CustomerRepository
from repositories.dead_cash_repository import DeadCashRepository, DeadCashRepositoryError
from repositories.invoice_repository import (
    InvoiceNumberExhausted, InvoiceRepository, InvoiceRepositoryError, _id,
)
from repositories.old_gold_conversion_repository import OldGoldConversionRepository


class FakeConnection:
    """Answers each execute() with the first matching (pattern, rows) rule."""

    def __init__(self, rules=(), lastrowid=41, rowcount=1):
        self.rules, self.lastrowid, self.rowcount = list(rules), lastrowid, rowcount
        self.executed = []

    def cursor(self, dictionary=False):
        return FakeCursor(self, dictionary)


class FakeCursor:
    def __init__(self, connection, dictionary):
        self.connection, self.dictionary, self.rows = connection, dictionary, []
        self.lastrowid, self.rowcount = connection.lastrowid, connection.rowcount

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        self.connection.executed.append((sql, tuple(params)))
        self.rows = []
        for pattern, rows in self.connection.rules:
            if re.search(pattern, sql):
                self.rows = rows(sql, params) if callable(rows) else rows
                break

    def fetchone(self):
        row = self.rows[0] if self.rows else None
        return row if self.dictionary or row is None else tuple(row.values())

    def fetchall(self):
        return self.rows if self.dictionary else [tuple(r.values()) for r in self.rows]

    def close(self):
        pass


class IdTests(unittest.TestCase):
    def test_bigint_ids_accept_decimal_strings_and_ints_only(self):
        self.assertEqual(_id("18446744073709551615"), 18446744073709551615)
        self.assertEqual(_id(7), 7)
        for bad in (True, 0, -1, "1.0", "abc", "", " ", None, "١٢", "18446744073709551616", "+5"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _id(bad)


class InvoiceNumberingTests(unittest.TestCase):
    def repository(self, last_seq, used=()):
        rules = [(r"FROM invoice_number_sequences .* FOR UPDATE", [{"last_seq": last_seq}]),
                 (r"SELECT 1 AS used FROM invoices", lambda sql, p: [{"used": 1}] if p[0] in used else [])]
        connection = FakeConnection(rules)
        return InvoiceRepository(connection), connection

    def test_allocates_next_yymmddnn_and_locks_the_day(self):
        repository, connection = self.repository(4)
        self.assertEqual(repository.next_invoice_number(date(2026, 9, 25)), "26092505")
        statements = [sql for sql, _ in connection.executed]
        self.assertTrue(statements[0].startswith("INSERT INTO invoice_number_sequences"))
        self.assertIn("LIKE %s", statements[0])
        self.assertEqual(connection.executed[0][1], (date(2026, 9, 25), "260925__"))
        self.assertTrue(statements[1].endswith("FOR UPDATE"))
        self.assertEqual(connection.executed[-1][1], (5, date(2026, 9, 25)))

    def test_existing_numbers_are_skipped_never_reused(self):
        repository, connection = self.repository(0, used={"26092501", "26092502"})
        self.assertEqual(repository.next_invoice_number(date(2026, 9, 25)), "26092503")
        self.assertEqual(connection.executed[-1][1], (3, date(2026, 9, 25)))

    def test_ninety_nine_per_day(self):
        repository, _ = self.repository(98)
        self.assertEqual(repository.next_invoice_number(date(2026, 9, 25)), "26092599")
        repository, connection = self.repository(99)
        with self.assertRaises(InvoiceNumberExhausted):
            repository.next_invoice_number(date(2026, 9, 25))
        self.assertFalse(any(sql.startswith("UPDATE") for sql, _ in connection.executed))
        repository, _ = self.repository(97, used={"26092598", "26092599"})
        with self.assertRaises(InvoiceNumberExhausted):
            repository.next_invoice_number(date(2026, 9, 25))

    def test_business_date_must_be_a_date(self):
        with self.assertRaises(ValueError):
            InvoiceRepository(FakeConnection()).next_invoice_number("2026-09-25")


class InvoiceRepositoryTests(unittest.TestCase):
    def test_sale_items_are_locked_in_id_order_and_returned_as_requested(self):
        rows = [{"inventory_item_id": 3, "karat_id": 1}, {"inventory_item_id": 10, "karat_id": 1}]
        connection = FakeConnection([(r"FROM inventory_items ii", rows)])
        result = InvoiceRepository(connection).lock_sale_items(["10", 3, "99"])
        self.assertEqual([r["inventory_item_id"] for r in result], ["10", "3"])
        sql, params = connection.executed[0]
        self.assertEqual(params, (3, 10, 99))
        self.assertIn("ORDER BY ii.id FOR UPDATE OF ii", sql)
        with self.assertRaises(ValueError):
            InvoiceRepository(connection).lock_sale_items([3, "3"])

    def test_inserts_return_string_ids_and_write_native_rows(self):
        connection = FakeConnection([(r"FROM inventory_movement_types|FROM old_gold_movement_types",
                                      [{"id": 5, "is_active": 1}])], lastrowid=9007199254740993)
        repository = InvoiceRepository(connection)
        invoice_id = repository.insert_invoice(
            invoice_number="26092501", invoice_type="SELL", customer_id="12", employee_id=3,
            invoice_date="2026-09-25 10:00:00", sell_total=Decimal("100.00"), buy_total=Decimal("0.00"),
            net_amount=Decimal("100.00"), net_direction="CUSTOMER_PAYS_SHOP", settlement_status="UNSETTLED",
            created_by_user_id=17, finalized_at="2026-09-25 10:00:00")
        self.assertEqual(invoice_id, "9007199254740993")                  # beyond float precision
        self.assertIn("'FINALIZED', 'NATIVE'", connection.executed[0][0])
        movement = repository.add_customer_buy_in_movement(
            karat_id=1, weight_grams=Decimal("5.000"), gold_price_per_gram=Decimal("5000"),
            cash_value=Decimal("25000.00"), price_quote_id=None, movement_group_id="g", invoice_id=invoice_id,
            invoice_number="26092501", customer_id="12", created_by_user_id=17, received_by_employee_id=3)
        self.assertIsInstance(movement, str)
        sql = connection.executed[-1][0]
        self.assertIn("'COUNTED'", sql)
        self.assertIn("'INVOICE'", sql)
        self.assertNotRegex(sql, r"\binvoice_id\b")                        # V1 column left NULL

    def test_mark_item_sold_requires_exactly_one_in_stock_row(self):
        repository = InvoiceRepository(FakeConnection(rowcount=0))
        with self.assertRaises(InvoiceRepositoryError):
            repository.mark_item_sold(inventory_item_id="5", updated_by_user_id=17)

    def test_inactive_or_missing_codes_are_refused(self):
        repository = InvoiceRepository(FakeConnection([(r"FROM inventory_movement_types",
                                                        [{"id": 5, "is_active": 0}])]))
        with self.assertRaises(InvoiceRepositoryError):
            repository.inventory_movement_type_id("SOLD")
        with self.assertRaises(InvoiceRepositoryError):
            InvoiceRepository(FakeConnection()).old_gold_movement_type_id("CUSTOMER_BUY_IN")

    def test_reads_convert_bigint_ids_to_strings(self):
        header = {"invoice_id": 7, "customer_id": 12, "employee_id": 3, "invoice_number": "26092501"}
        rules = [(r"FROM invoices i", [header]),
                 (r"FROM invoice_lines", [{"line_id": 1, "inventory_item_id": 5, "old_gold_movement_id": None,
                                           "karat_id": 1}]),
                 (r"FROM invoice_payments", [{"payment_id": 2, "reverses_payment_id": None}]),
                 (r"FROM invoice_price_snapshots", [{"snapshot_id": 3, "quote_id": 44, "karat_id": 1}]),
                 (r"FROM invoice_events", [{"event_id": 4, "related_invoice_id": None}])]
        invoice = InvoiceRepository(FakeConnection(rules)).get_invoice("7")
        self.assertEqual((invoice["invoice_id"], invoice["customer_id"], invoice["employee_id"]), ("7", "12", 3))
        self.assertEqual(invoice["lines"][0]["inventory_item_id"], "5")
        self.assertEqual(invoice["lines"][0]["karat_id"], 1)
        self.assertEqual(invoice["price_snapshots"][0]["quote_id"], "44")
        self.assertIsNone(InvoiceRepository(FakeConnection()).get_invoice(8))

    def test_event_details_are_json_with_exact_decimals(self):
        connection = FakeConnection()
        InvoiceRepository(connection).insert_event(invoice_id=7, event_type="FINALIZED", performed_by_user_id=17,
                                                   details={"net": Decimal("100.10"), "stale_ack": False})
        self.assertEqual(connection.executed[0][1][4], '{"net": "100.10", "stale_ack": false}')

    def test_search_escapes_like_wildcards(self):
        connection = FakeConnection()
        InvoiceRepository(connection).search_invoices(number="26_%")
        params = connection.executed[0][1]
        self.assertEqual(params[:3], (False, "26!_!%%", "26!_!%%"))


class CustomerRepositoryTests(unittest.TestCase):
    def test_default_search_hides_legacy_placeholders(self):
        connection = FakeConnection()
        CustomerRepository(connection).search_customers("x")
        sql, params = connection.executed[0]
        self.assertIn("(%s OR c.record_origin <> 'LEGACY_PLACEHOLDER')", sql)
        self.assertEqual(params[-4:-2], (False, False))

    def test_explicit_paths_for_placeholders(self):
        connection = FakeConnection()
        repository = CustomerRepository(connection)
        repository.search_customers("x", include_legacy_placeholders=True)
        repository.search_legacy_placeholder_accounts("x")
        self.assertEqual(connection.executed[0][1][-4:-2], (True, False))
        self.assertEqual(connection.executed[1][1][-4:-2], (True, True))

    def ledger(self, direction="INCREASE", active=1):
        return FakeConnection([(r"FROM customer_transaction_types",
                                [{"id": 3, "code": "X", "name": "X", "expected_direction": direction,
                                  "is_active": active}])], lastrowid=9007199254740993)

    def test_add_transaction_checks_type_and_direction(self):
        connection = self.ledger()
        repository = CustomerRepository(connection)
        transaction_id = repository.add_transaction(
            customer_id="12", type_code="SALE_BALANCE_DUE", amount_delta=Decimal("250.00"),
            created_by_user_id=17, reference_type="INVOICE", reference_id="7", reference_code="26092501",
            metadata={"note": "x"})
        self.assertEqual(transaction_id, "9007199254740993")
        self.assertEqual(connection.executed[-1][1][:3], (12, 3, Decimal("250.00")))
        for direction, amount in (("INCREASE", Decimal("-1")), ("DECREASE", Decimal("1"))):
            with self.subTest(direction=direction), self.assertRaises(ValueError):
                CustomerRepository(self.ledger(direction)).add_transaction(
                    customer_id=1, type_code="X", amount_delta=amount, created_by_user_id=None)
        CustomerRepository(self.ledger("EITHER")).add_transaction(
            customer_id=1, type_code="X", amount_delta=Decimal("-1"), created_by_user_id=None)
        for amount in (Decimal("0"), 1.5, 2, Decimal("NaN")):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                CustomerRepository(self.ledger()).add_transaction(
                    customer_id=1, type_code="X", amount_delta=amount, created_by_user_id=None)
        with self.assertRaises(ValueError):
            CustomerRepository(self.ledger(active=0)).add_transaction(
                customer_id=1, type_code="X", amount_delta=Decimal("1"), created_by_user_id=None)
        with self.assertRaises(ValueError):
            CustomerRepository(FakeConnection()).add_transaction(
                customer_id=1, type_code="NOPE", amount_delta=Decimal("1"), created_by_user_id=None)

    def test_balance_is_a_decimal_from_the_full_ledger(self):
        connection = FakeConnection([(r"FROM customer_transactions", [{"balance": Decimal("-12.50")}])])
        self.assertEqual(CustomerRepository(connection).get_balance("12"), Decimal("-12.50"))
        self.assertNotIn("LIMIT", connection.executed[0][0])


class DeadCashRepositoryTests(unittest.TestCase):
    def test_writes_are_one_way_and_never_delete(self):
        repository = DeadCashRepository(FakeConnection(rowcount=0))
        with self.assertRaises(DeadCashRepositoryError):
            repository.mark_writeoff_reversed(writeoff_id=1, reversal_transaction_id=2,
                                              reversed_by_user_id=17, reason="x")
        with self.assertRaises(DeadCashRepositoryError):
            repository.deactivate_warning(flag_id=1, deactivated_by_user_id=17, reason="x")
        with self.assertRaises(ValueError):
            repository.insert_writeoff(customer_id=1, amount=1, balance_before=1, balance_after=0,
                                       writeoff_transaction_id=2, reason="x", created_by_user_id=17,
                                       writeoff_direction="BOTH")
        source = Path("repositories/dead_cash_repository.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"\bDELETE\b")

    def test_updates_are_guarded_by_current_state(self):
        connection = FakeConnection()
        repository = DeadCashRepository(connection)
        repository.mark_writeoff_reversed(writeoff_id="5", reversal_transaction_id="6",
                                          reversed_by_user_id=17, reason="خطأ")
        repository.deactivate_warning(flag_id="8", deactivated_by_user_id=17, reason="سداد")
        self.assertIn("WHERE id = %s AND is_reversed = FALSE", connection.executed[0][0])
        self.assertIn("AND is_active = TRUE", connection.executed[1][0])

    def test_reads_return_string_ids_and_decimal_totals(self):
        connection = FakeConnection([
            (r"FROM dead_cash_writeoffs", [{"writeoff_id": 5, "customer_id": 12, "writeoff_transaction_id": 6,
                                            "reversal_transaction_id": None, "amount": Decimal("10")}]),
            (r"FROM dead_cash_recoveries", [{"total": Decimal("7.50")}]),
            (r"FROM customer_flags", [{"flag_id": 8, "customer_id": 12, "related_dead_cash_id": 5}])])
        repository = DeadCashRepository(connection)
        self.assertEqual(repository.lock_writeoff("5")["writeoff_id"], "5")
        self.assertEqual(repository.recovered_total(5), Decimal("7.50"))
        self.assertEqual(repository.get_active_warning(12, for_update=True)["related_dead_cash_id"], "5")
        self.assertTrue(connection.executed[-1][0].endswith("FOR UPDATE"))


class BarcodeAllocationTests(unittest.TestCase):
    def repository(self, last_seq, existing=()):
        rules = [(r"FROM barcode_number_sequences .* FOR UPDATE", [{"last_seq": last_seq}]),
                 (r"FROM inventory_barcodes WHERE barcode_value = %s",
                  lambda sql, p: [{"one": 1}] if p[0] in existing else [])]
        connection = FakeConnection(rules)
        return BarcodeRepository(connection), connection

    def test_two_then_three_digit_sequences(self):
        repository, connection = self.repository(0)
        self.assertEqual(repository.allocate_barcode_value(date(2026, 9, 21)), "01260921")
        seed_sql, seed_params = connection.executed[0]
        self.assertIn("REGEXP '^[0-9]{8,9}$'", seed_sql)
        self.assertNotIn("barcode_status", seed_sql)                       # every status counts
        self.assertEqual(seed_params, (date(2026, 9, 21), "260921"))
        repository, _ = self.repository(141)
        self.assertEqual(repository.allocate_barcode_value(date(2026, 9, 21)), "142260921")

    def test_historical_barcodes_are_never_reissued(self):
        repository, connection = self.repository(1, existing={"02260921", "03260921"})
        self.assertEqual(repository.allocate_barcode_value(date(2026, 9, 21)), "04260921")
        self.assertEqual(connection.executed[-1][1], (4, date(2026, 9, 21)))

    def test_999_per_day(self):
        repository, _ = self.repository(998)
        self.assertEqual(repository.allocate_barcode_value(date(2026, 9, 21)), "999260921")
        repository, _ = self.repository(999)
        with self.assertRaises(BarcodeSequenceExhausted):
            repository.allocate_barcode_value(date(2026, 9, 21))

    def test_old_active_only_maximum_is_gone(self):
        self.assertFalse(hasattr(BarcodeRepository, "get_max_sequence_for_date"))


class SqliteLockConnection:
    """Runs the repository's SQL on SQLite (FOR UPDATE dropped, %s -> ?)."""

    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE karats (id INTEGER PRIMARY KEY, karat_value INTEGER, name TEXT,
                                 is_active INTEGER, sort_order INTEGER);
            CREATE TABLE old_gold_movements (id INTEGER PRIMARY KEY, karat_id INTEGER,
                weight_delta_grams NUMERIC, balance_effect TEXT NOT NULL DEFAULT 'COUNTED',
                legacy_source_table TEXT, legacy_source_id INTEGER);
            INSERT INTO karats VALUES (1, 21, '21K', 1, 1), (2, 18, '18K', 1, 2);
            INSERT INTO old_gold_movements (karat_id, weight_delta_grams) VALUES (1, 100), (1, -10.5), (2, 3);
            INSERT INTO old_gold_movements (karat_id, weight_delta_grams, balance_effect,
                                            legacy_source_table, legacy_source_id)
            VALUES (1, 500, 'REFERENCE_ONLY', 'stockold', 7), (2, -40, 'REFERENCE_ONLY', 'stockold', 8);
        """)

    def cursor(self, dictionary=False):
        owner = self

        class Cursor:
            def execute(self, sql, params=()):
                self.cursor = owner.db.execute(sql.replace("%s", "?").replace("FOR UPDATE", ""), params)

            def fetchall(self):
                return [dict(row) for row in self.cursor.fetchall()]

            def close(self):
                pass
        return Cursor()


class OldGoldCountedBalanceTests(unittest.TestCase):
    """Regression: REFERENCE_ONLY legacy history must not reach live balances."""

    def setUp(self):
        self.connection = SqliteLockConnection()
        self.addCleanup(self.connection.db.close)

    def test_balances_ignore_reference_only_rows(self):
        balances = {r["karat_id"]: Decimal(str(r["balance_grams"]))
                    for r in OldGoldConversionRepository(self.connection).get_old_gold_balances()}
        self.assertEqual(balances, {1: Decimal("89.5"), 2: Decimal("3")})

    def test_locked_rows_exclude_reference_only(self):
        rows = OldGoldConversionRepository(self.connection).lock_old_gold_balance(1)
        self.assertEqual(sorted(Decimal(str(r["weight_delta_grams"])) for r in rows),
                         [Decimal("-10.5"), Decimal("100")])

    def test_both_queries_filter_counted(self):
        source = Path("repositories/old_gold_conversion_repository.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("balance_effect = 'COUNTED'"), 2)


if __name__ == "__main__":
    unittest.main()
