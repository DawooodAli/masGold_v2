"""Disk-backed isolated SQL fixture for the real price repository/service path.

Mirrors the inspected MySQL columns and foreign keys used here. The adapter
only translates placeholders, row locks and the MySQL active-pointer upsert;
production schema and production data are never changed by these tests.
"""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from repositories.gold_price_repository import GoldPriceRepository
from services.gold_price_service import GoldPriceService


class SqlCursor:
    def __init__(self, connection):
        self.owner = connection
        self.cursor = connection.raw.cursor()

    def execute(self, sql, params=()):
        sql = sql.replace("%s", "?").replace(" FOR UPDATE", "")
        sql = sql.replace("ON DUPLICATE KEY UPDATE", "ON CONFLICT(karat_id) DO UPDATE SET")
        if "INSERT INTO active_gold_prices" in sql:
            self.owner.pointer_writes += 1
            if self.owner.fixture.fail_second_pointer and self.owner.pointer_writes == 2:
                raise RuntimeError("Injected failure on second active-price pointer")
        params = tuple(str(value) if isinstance(value, Decimal) else
                       value.isoformat() if isinstance(value, datetime) else value for value in params)
        self.cursor.execute(sql, params)

    @property
    def lastrowid(self):
        return self.cursor.lastrowid

    def _row(self, row):
        if row is None:
            return None
        row = dict(row)
        for key, value in row.items():
            if value is not None and (key.endswith("price_per_gram") or key in (
                    "max_deviation_percent", "deviation_percent")):
                row[key] = Decimal(value)
            elif value is not None and (key.endswith("_at") or key == "provider_timestamp"):
                row[key] = datetime.fromisoformat(value)
        return row

    def fetchone(self):
        return self._row(self.cursor.fetchone())

    def fetchall(self):
        return [self._row(row) for row in self.cursor.fetchall()]

    def close(self):
        self.cursor.close()


class SqlConnection:
    def __init__(self, raw, fixture):
        self.raw, self.fixture = raw, fixture
        self.pointer_writes = 0

    def cursor(self, **kwargs):
        return SqlCursor(self)

    @property
    def in_transaction(self):
        return self.raw.in_transaction


class PriceDatabase:
    def __init__(self):
        self.temp = TemporaryDirectory(prefix="masgold-price-test-")
        self.path = Path(self.temp.name) / "prices.sqlite"
        self.fail_second_pointer = False
        self.fail_commit = False
        self.transactions = 0
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE employees (id INTEGER PRIMARY KEY, is_active INTEGER);
                CREATE TABLE users (id INTEGER PRIMARY KEY, employee_id INTEGER REFERENCES employees(id),
                    display_name TEXT, is_active INTEGER, must_change_password INTEGER, password_hash TEXT);
                CREATE TABLE roles (id INTEGER PRIMARY KEY, is_active INTEGER);
                CREATE TABLE permissions (id INTEGER PRIMARY KEY, code TEXT, is_active INTEGER);
                CREATE TABLE user_roles (user_id INTEGER REFERENCES users(id), role_id INTEGER REFERENCES roles(id));
                CREATE TABLE role_permissions (role_id INTEGER REFERENCES roles(id), permission_id INTEGER REFERENCES permissions(id));
                CREATE TABLE karats (id INTEGER PRIMARY KEY, karat_value INTEGER UNIQUE, is_active INTEGER);
                CREATE TABLE gold_price_sources (id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT,
                    source_type TEXT, priority INTEGER, is_enabled INTEGER);
                CREATE TABLE gold_price_engine_settings (id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT,
                    primary_source_id INTEGER REFERENCES gold_price_sources(id),
                    fallback_source_id INTEGER REFERENCES gold_price_sources(id),
                    reference_source_id INTEGER REFERENCES gold_price_sources(id),
                    refresh_seconds INTEGER, stale_after_seconds INTEGER, max_deviation_percent TEXT,
                    updated_by_user_id INTEGER REFERENCES users(id), updated_at TEXT);
                CREATE TABLE gold_price_quotes (id INTEGER PRIMARY KEY AUTOINCREMENT, quote_group_id TEXT,
                    source_id INTEGER REFERENCES gold_price_sources(id), karat_id INTEGER REFERENCES karats(id),
                    shop_buy_price_per_gram TEXT, shop_sell_price_per_gram TEXT, reference_price_per_gram TEXT,
                    quote_method TEXT, provider_timestamp TEXT, fetched_at TEXT, validation_status TEXT,
                    deviation_percent TEXT, validation_message TEXT, entered_by_user_id INTEGER REFERENCES users(id), notes TEXT);
                CREATE TABLE gold_price_activations (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    karat_id INTEGER REFERENCES karats(id), quote_id INTEGER REFERENCES gold_price_quotes(id),
                    activation_type TEXT, activated_at TEXT, activated_by_user_id INTEGER REFERENCES users(id), reason TEXT,
                    UNIQUE(id, quote_id, karat_id));
                CREATE TABLE active_gold_prices (karat_id INTEGER PRIMARY KEY REFERENCES karats(id),
                    quote_id INTEGER REFERENCES gold_price_quotes(id), activation_id INTEGER UNIQUE REFERENCES gold_price_activations(id),
                    updated_at TEXT, FOREIGN KEY(activation_id, quote_id, karat_id) REFERENCES gold_price_activations(id, quote_id, karat_id));
                CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id),
                    event_type TEXT, entity_type TEXT, entity_id INTEGER, entity_reference TEXT, description TEXT,
                    before_json TEXT, after_json TEXT, metadata_json TEXT, occurred_at TEXT);
                INSERT INTO employees VALUES (29,1);
                INSERT INTO users VALUES (17,29,'Test owner',1,0,'unused');
                INSERT INTO roles VALUES (5,1);
                INSERT INTO permissions VALUES (7,'APP_LOGIN',1),(8,'SETTINGS_MANAGE',1);
                INSERT INTO user_roles VALUES (17,5);
                INSERT INTO role_permissions VALUES (5,7),(5,8);
                INSERT INTO karats VALUES (4,18,1),(8,21,1),(9,24,1);
                INSERT INTO gold_price_sources VALUES (6,'MANUAL','Manual','MANUAL',1,1);
                INSERT INTO gold_price_engine_settings VALUES (1,'AUTO',NULL,NULL,NULL,60,300,'5',NULL,'2026-09-23T00:00:00');
            """)

    def _connect(self):
        raw = sqlite3.connect(self.path)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys=ON")
        return raw

    @contextmanager
    def connect(self):
        raw = self._connect()
        try:
            with raw:
                yield raw
        finally:
            raw.close()

    @contextmanager
    def transaction(self):
        raw = self._connect()
        self.transactions += 1
        try:
            raw.execute("BEGIN")
            yield SqlConnection(raw, self)
            if self.fail_commit:
                raise RuntimeError("Injected commit failure")
            raw.commit()
        except BaseException:
            raw.rollback()
            raise
        finally:
            raw.close()

    def service(self, session):
        return GoldPriceService(session, transaction_factory=self.transaction,
                                repository_factory=GoldPriceRepository, providers={})

    def close(self):
        self.temp.cleanup()
