"""In-memory SQLite mirror of the Billing V2 schema after 014 + 015.

Columns and keys mirror the live MySQL definitions (read-only SHOW CREATE
TABLE) plus the objects added by 014/015. Every CHECK constraint is taken
verbatim from the schema SQL files (004, 005, 006, 014, 015), so the tests
exercise the real constraint text; only the MySQL-specific DDL wrapper is
replaced. No MySQL server or real data is touched.

SQLite evaluates DECIMAL arithmetic in binary floating point, unlike MySQL's
exact DECIMAL, so tests use values whose rounding is unambiguous.
"""
from decimal import Decimal
import re
import sqlite3

from tests import migration_sql


SOURCE_FILES = ("004_old_gold.sql", "005_customers.sql", "006_billing.sql",
                "014_billing_v2.sql", "015_legacy_provenance.sql")


TABLES = {
    "karats": """
        id INTEGER PRIMARY KEY,
        karat_value INTEGER NOT NULL,
        name TEXT NOT NULL
    """,
    "users": """
        id INTEGER PRIMARY KEY
    """,
    "customers": """
        id INTEGER PRIMARY KEY,
        customer_code TEXT UNIQUE,
        name TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        record_origin TEXT NOT NULL DEFAULT 'NATIVE',
        data_completeness TEXT NOT NULL DEFAULT 'COMPLETE'
    """,
    "customer_transaction_types": """
        id INTEGER PRIMARY KEY,
        code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        expected_direction TEXT NOT NULL,
        description TEXT
    """,
    "customer_transactions": """
        id INTEGER PRIMARY KEY,
        customer_id INTEGER NOT NULL REFERENCES customers(id),
        transaction_type_id INTEGER NOT NULL REFERENCES customer_transaction_types(id),
        amount_delta NUMERIC NOT NULL,
        legacy_source_table TEXT,
        legacy_source_id INTEGER
    """,
    "old_gold_movement_types": """
        id INTEGER PRIMARY KEY,
        code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        description TEXT,
        expected_direction TEXT NOT NULL
    """,
    "old_gold_movements": """
        id INTEGER PRIMARY KEY,
        movement_type_id INTEGER NOT NULL REFERENCES old_gold_movement_types(id),
        karat_id INTEGER REFERENCES karats(id),
        weight_delta_grams NUMERIC NOT NULL,
        balance_effect TEXT NOT NULL DEFAULT 'COUNTED',
        gold_price_per_gram NUMERIC,
        cash_value NUMERIC,
        legacy_source_table TEXT,
        legacy_source_id INTEGER
    """,
    "invoices": """
        id INTEGER PRIMARY KEY,
        invoice_number TEXT NOT NULL UNIQUE,
        invoice_type TEXT NOT NULL,
        invoice_status TEXT NOT NULL DEFAULT 'DRAFT',
        customer_id INTEGER REFERENCES customers(id),
        sell_total NUMERIC DEFAULT NULL,
        buy_total NUMERIC DEFAULT NULL,
        net_amount NUMERIC NOT NULL DEFAULT 0,
        net_direction TEXT NOT NULL DEFAULT 'SETTLED',
        settlement_status TEXT NOT NULL DEFAULT 'UNSETTLED',
        legacy_source_system TEXT,
        legacy_bill_number TEXT,
        legacy_completeness TEXT,
        record_origin TEXT NOT NULL DEFAULT 'NATIVE',
        provenance_json TEXT,
        finalized_at TEXT,
        voided_at TEXT,
        void_reason TEXT,
        UNIQUE (id, record_origin)
    """,
    "invoice_lines": """
        id INTEGER PRIMARY KEY,
        invoice_id INTEGER NOT NULL REFERENCES invoices(id),
        record_origin TEXT NOT NULL DEFAULT 'NATIVE',
        line_number INTEGER NOT NULL,
        line_type TEXT NOT NULL,
        inventory_item_id INTEGER,
        old_gold_movement_id INTEGER UNIQUE REFERENCES old_gold_movements(id),
        karat_id INTEGER NOT NULL REFERENCES karats(id),
        weight_grams NUMERIC NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 1,
        gold_price_per_gram NUMERIC,
        making_per_gram NUMERIC,
        making_total NUMERIC,
        gross_total NUMERIC,
        discount_type TEXT,
        discount_value NUMERIC,
        discount_amount NUMERIC,
        line_total NUMERIC,
        notes TEXT,
        provenance_json TEXT,
        UNIQUE (invoice_id, line_number),
        FOREIGN KEY (invoice_id, record_origin) REFERENCES invoices(id, record_origin)
    """,
    "invoice_customer_snapshots": """
        invoice_id INTEGER PRIMARY KEY REFERENCES invoices(id),
        customer_id INTEGER REFERENCES customers(id),
        balance_before NUMERIC,
        balance_after NUMERIC,
        dead_cash_warning_active INTEGER NOT NULL DEFAULT 0
    """,
    "dead_cash_writeoffs": """
        id INTEGER PRIMARY KEY,
        customer_id INTEGER NOT NULL REFERENCES customers(id),
        writeoff_direction TEXT NOT NULL DEFAULT 'CUSTOMER_OWES_SHOP',
        amount NUMERIC NOT NULL,
        writeoff_transaction_id INTEGER NOT NULL UNIQUE REFERENCES customer_transactions(id),
        reason TEXT NOT NULL,
        UNIQUE (id, customer_id)
    """,
    "dead_cash_recoveries": """
        id INTEGER PRIMARY KEY,
        dead_cash_writeoff_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL REFERENCES customers(id),
        amount NUMERIC NOT NULL,
        recovery_transaction_id INTEGER NOT NULL UNIQUE REFERENCES customer_transactions(id),
        payment_transaction_id INTEGER NOT NULL UNIQUE REFERENCES customer_transactions(id),
        notes TEXT,
        occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_by_user_id INTEGER REFERENCES users(id),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (dead_cash_writeoff_id, customer_id)
            REFERENCES dead_cash_writeoffs(id, customer_id)
    """,
    "invoice_number_sequences": """
        sequence_date TEXT PRIMARY KEY,
        last_seq INTEGER NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    """,
    "barcode_number_sequences": """
        sequence_date TEXT PRIMARY KEY,
        last_seq INTEGER NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    """,
    "legacy_import_runs": """
        id INTEGER PRIMARY KEY,
        source_system TEXT NOT NULL,
        source_file TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,
        source_dump_at TEXT,
        etl_version TEXT NOT NULL,
        run_mode TEXT NOT NULL,
        target_database TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'RUNNING',
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at TEXT,
        reconciliation_json TEXT,
        approved_by_user_id INTEGER REFERENCES users(id),
        approved_at TEXT,
        notes TEXT
    """,
    "legacy_record_map": """
        id INTEGER PRIMARY KEY,
        run_id INTEGER NOT NULL REFERENCES legacy_import_runs(id),
        source_system TEXT NOT NULL,
        source_table TEXT NOT NULL,
        source_pk TEXT NOT NULL,
        target_table TEXT NOT NULL,
        target_id INTEGER NOT NULL,
        mapping_role TEXT NOT NULL,
        completeness TEXT NOT NULL,
        metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (source_system, source_table, source_pk, target_table, mapping_role)
    """,
    "legacy_exceptions": """
        id INTEGER PRIMARY KEY,
        run_id INTEGER NOT NULL REFERENCES legacy_import_runs(id),
        source_system TEXT NOT NULL,
        source_table TEXT,
        source_pk TEXT,
        exception_code TEXT NOT NULL,
        severity TEXT NOT NULL,
        detail_json TEXT,
        resolution_status TEXT NOT NULL DEFAULT 'OPEN',
        resolved_by_user_id INTEGER REFERENCES users(id),
        resolved_at TEXT,
        resolution_notes TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (run_id, source_system, source_table, source_pk, exception_code)
    """,
}


def schema_checks():
    """{table: {constraint: expression}} from the SQL files, mirrored tables only."""
    by_table = {}
    for name in SOURCE_FILES:
        for constraint, (table, expression) in migration_sql.checks(migration_sql.read(name)).items():
            if table in TABLES:
                by_table.setdefault(table, {})[constraint] = expression
    return by_table


def _regexp(pattern, value):
    return value is not None and re.search(pattern, str(value)) is not None


def _param(value):
    return str(value) if isinstance(value, Decimal) else value


class BillingSchemaDatabase:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.create_function("regexp", 2, _regexp, deterministic=True)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.checks = schema_checks()
        for table, columns in TABLES.items():
            constraints = [f"CONSTRAINT {name} CHECK ({expression})"
                           for name, expression in self.checks.get(table, {}).items()]
            body = ",\n".join([columns.strip().rstrip(",")] + constraints)
            self.connection.execute(f"CREATE TABLE {table} (\n{body}\n)")
        view = migration_sql.view(migration_sql.read("015_legacy_provenance.sql"), "vw_old_gold_balances")
        self.connection.execute(f"CREATE VIEW vw_old_gold_balances AS {view}")
        self._seed()

    def _seed(self):
        for karat_id, value in ((1, 21), (2, 18), (3, 24)):
            self.insert("karats", id=karat_id, karat_value=value, name=f"{value}K")
        self.insert("users", id=17)
        for code, direction in (("SALE_BALANCE_DUE", "INCREASE"), ("CUSTOMER_PAYMENT", "DECREASE"),
                                ("DEAD_CASH_WRITE_OFF", "DECREASE"), ("DEAD_CASH_RECOVERY", "INCREASE")):
            self.insert("customer_transaction_types", code=code, name=code, expected_direction=direction)
        for code, direction in (("CUSTOMER_BUY_IN", "IN"), ("LEGACY_IN", "IN"),
                                ("OPENING_BALANCE", "IN")):
            self.insert("old_gold_movement_types", code=code, name=code, expected_direction=direction)

    def close(self):
        self.connection.close()

    def insert(self, table, **values):
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        cursor = self.connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})",
                                         tuple(_param(v) for v in values.values()))
        return cursor.lastrowid

    def id_of(self, table, code):
        return self.connection.execute(f"SELECT id FROM {table} WHERE code = ?", (code,)).fetchone()[0]

    def columns(self, table):
        return {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}

    def query(self, sql, params=()):
        return self.connection.execute(sql, params).fetchall()
