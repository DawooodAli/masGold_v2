"""Price repository transaction and audit guarantees without touching live MySQL."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from repositories.gold_price_repository import GoldPriceRepository, gold_price_transaction


UTC_NOW = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
ACTOR = SimpleNamespace(user_id=17, employee_id=29)
KARATS = {18: 4, 21: 8, 24: 9}


def quotes():
    return tuple(SimpleNamespace(
        source_code="MANUAL", karat=karat,
        shop_buy_price_per_gram=Decimal("6125.0000") * karat / 21,
        shop_sell_price_per_gram=Decimal("6225.0000") * karat / 21,
        provider_timestamp=UTC_NOW, fetched_at=UTC_NOW,
        currency="EGP", unit="GRAM",
    ) for karat in (18, 21, 24))


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.lastrowid = 0

    def execute(self, sql, params=()):
        if sql.startswith("INSERT INTO active_gold_prices"):
            self.connection.pointer_writes += 1
            if self.connection.fail_second_pointer and self.connection.pointer_writes == 2:
                raise RuntimeError("simulated second active-pointer failure")
        self.connection.pending.append((sql, params))
        if sql.startswith("INSERT INTO"):
            self.connection.next_id += 1
            self.lastrowid = self.connection.next_id

    def fetchone(self):
        return {"id": 1, "mode": "AUTO"}

    def fetchall(self):
        return []

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.pending = []
        self.committed = []
        self.next_id = 100
        self.pointer_writes = 0
        self.fail_second_pointer = False
        self.rollback_count = 0
        self.commit_count = 0
        self.closed = False
        self.started = None

    def cursor(self, **kwargs):
        return FakeCursor(self)

    def start_transaction(self, **kwargs):
        self.started = kwargs

    def commit(self):
        self.committed.extend(self.pending)
        self.pending.clear()
        self.commit_count += 1

    def rollback(self):
        self.pending.clear()
        self.rollback_count += 1

    def close(self):
        self.closed = True


class GoldPriceRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection()
        self.repository = GoldPriceRepository(self.connection)
        config = SimpleNamespace(host="localhost", port=3306, database="unused",
                                 user="unused", password="unused")
        self.config_patch = patch(
            "repositories.gold_price_repository.DatabaseConfig.from_env", return_value=config)
        self.connection_patch = patch(
            "repositories.gold_price_repository.MySQLConnection", return_value=self.connection)
        self.config_patch.start()
        self.factory = self.connection_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.addCleanup(self.connection_patch.stop)

    def publish(self, values=None):
        return self.repository.append_publication(
            quotes() if values is None else values, karat_ids=KARATS, source_id=7,
            activation_type="MANUAL", actor=ACTOR, reason="سعر المحل المؤكد",
            activated_at=UTC_NOW, derivation="21K * K / 21; HALF_UP 0.0001")

    def test_complete_set_is_one_transaction_and_preserves_decimal_and_actor_audit(self):
        with gold_price_transaction():
            group = self.publish()
            self.assertEqual(self.connection.committed, [])
        inserts = [(sql, params) for sql, params in self.connection.committed
                   if sql.startswith("INSERT INTO")]
        quote_rows = [params for sql, params in inserts if "INSERT INTO gold_price_quotes " in sql]
        activation_rows = [params for sql, params in inserts
                           if "INSERT INTO gold_price_activations " in sql]
        pointers = [params for sql, params in inserts if "INSERT INTO active_gold_prices " in sql]
        self.assertEqual(len(quote_rows), 3)
        self.assertEqual({row[0] for row in quote_rows}, {group})
        self.assertEqual({row[2] for row in quote_rows}, set(KARATS.values()))
        self.assertTrue(all(isinstance(row[3], Decimal) for row in quote_rows))
        self.assertEqual([row[5] for row in quote_rows], ["DERIVED", "MANUAL", "DERIVED"])
        self.assertTrue(all(row[8] == ACTOR.user_id for row in quote_rows))
        self.assertEqual({row[0] for row in pointers}, set(KARATS.values()))
        self.assertEqual({(row[0], row[1]) for row in activation_rows},
                         {(row[0], row[1]) for row in pointers})
        audit = [params for sql, params in inserts if "INSERT INTO audit_log " in sql][0]
        metadata = json.loads(audit[8])
        self.assertEqual(metadata["employee_id"], ACTOR.employee_id)
        self.assertEqual(metadata["user_id"], ACTOR.user_id)
        self.assertEqual(metadata["quote_group_id"], group)
        self.assertEqual(len(metadata["quotes"]), 3)
        self.assertEqual(self.connection.commit_count, 1)
        self.assertTrue(self.connection.closed)
        self.assertEqual(self.connection.started, {"isolation_level": "READ COMMITTED"})
        for option in ("connection_timeout", "read_timeout", "write_timeout"):
            self.assertEqual(self.factory.call_args.kwargs[option], 5)

    def test_failure_on_second_active_pointer_rolls_back_entire_publication(self):
        self.connection.fail_second_pointer = True
        with self.assertRaisesRegex(RuntimeError, "second active-pointer"):
            with gold_price_transaction():
                self.publish()
        self.assertEqual(self.connection.committed, [])
        self.assertEqual(self.connection.pending, [])
        self.assertEqual(self.connection.rollback_count, 1)
        self.assertEqual(self.connection.commit_count, 0)
        self.assertTrue(self.connection.closed)

    def test_incomplete_or_duplicate_karat_set_cannot_start_writing(self):
        values = quotes()
        for invalid in (values[:2], (values[0], values[0], values[2])):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.publish(invalid)
                self.assertEqual(self.connection.pending, [])

    def test_manual_publication_requires_actor_and_reason(self):
        for actor, reason in ((None, "reason"), (ACTOR, " ")):
            with self.subTest(actor=actor, reason=reason):
                with self.assertRaises(ValueError):
                    self.repository.append_publication(
                        quotes(), karat_ids=KARATS, source_id=7, activation_type="MANUAL",
                        actor=actor, reason=reason, activated_at=UTC_NOW)
                self.assertEqual(self.connection.pending, [])

    def test_timestamps_are_explicit_utc_and_caller_connection_is_not_reconfigured(self):
        values = quotes()
        cairo = timezone(timedelta(hours=3))
        for value in values:
            value.provider_timestamp = UTC_NOW.astimezone(cairo)
            value.fetched_at = UTC_NOW.astimezone(cairo)
        self.publish(values)
        inserted = next(params for sql, params in self.connection.pending
                        if sql.startswith("INSERT INTO gold_price_quotes"))
        self.assertEqual(inserted[6], UTC_NOW.replace(tzinfo=None))
        self.assertEqual(inserted[7], UTC_NOW.replace(tzinfo=None))
        self.assertFalse(any(sql.startswith("SET") for sql, _ in self.connection.pending))
        self.assertEqual(self.connection.commit_count, 0)

    def test_mode_change_preserves_old_and_new_values_with_verified_actor(self):
        self.repository.set_mode("MANUAL", ACTOR, "سعر يدوي", UTC_NOW)
        audit = next(params for sql, params in self.connection.pending
                     if sql.startswith("INSERT INTO audit_log"))
        self.assertEqual(json.loads(audit[6]), {"mode": "AUTO"})
        self.assertEqual(json.loads(audit[7]), {"mode": "MANUAL"})
        self.assertEqual(json.loads(audit[8])["employee_id"], 29)
        self.assertEqual(audit[0], 17)

    def test_rejection_history_redacts_common_secret_carriers(self):
        self.repository.append_rejection(
            "GOLDTIMO", 'https://example.test?api_key=URLSECRET api_key=KEYSECRET '
            'Authorization: Bearer BEARERSECRET {"password":"JSONSECRET"}', UTC_NOW)
        audit = self.connection.pending[0][1]
        encoded = str(audit)
        for secret in ("URLSECRET", "KEYSECRET", "BEARERSECRET", "JSONSECRET"):
            self.assertNotIn(secret, encoded)
        self.assertEqual(json.loads(audit[8])["source_code"], "GOLDTIMO")
        self.assertTrue(all("gold_price_quotes" not in sql for sql, _ in self.connection.pending))


if __name__ == "__main__":
    unittest.main()
