"""Migration 111 (a settlement difference the employee waives is stored on an Exchange / a Return): the static contract of the forward file, its rollback and the chain, and the
meaning of the two replaced CHECK constraints, evaluated from the SQL text of the migration itself (sqlite3 evaluates the expression; no database server and no data)."""
import re
import sqlite3
import unittest
from decimal import Decimal

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD_NAME = "111_waived_settlement_difference.sql"


def check_body(text, constraint):
    """The expression between `ADD CONSTRAINT <constraint> CHECK (` and its closing `)", 'DO 0'`."""
    match = re.search(r"ADD CONSTRAINT " + constraint + r" CHECK \((.*?)\)\", 'DO 0'", text, re.S)
    assert match, constraint
    return match.group(1)


def evaluate(body, row):
    """Evaluate a CHECK expression for one row with sqlite3 (MySQL's SIGN / MOD are registered as functions)."""
    connection = sqlite3.connect(":memory:")
    connection.create_function("SIGN", 1, lambda value: (value > 0) - (value < 0))
    connection.create_function("MOD", 2, lambda a, b: float(Decimal(str(a)) % Decimal(str(b))))
    names = ", ".join(row)
    values = ", ".join("?" for _ in row)
    query = f"WITH t({names}) AS (VALUES ({values})) SELECT CASE WHEN ({body}) THEN 1 ELSE 0 END FROM t"
    return bool(connection.execute(query, [float(v) if isinstance(v, Decimal) else v for v in row.values()]).fetchone()[0])


def exchange(**overrides):
    row = {"gold_amount": Decimal("345"), "making_charge": Decimal("0"), "making_refund": Decimal("0"), "money_received": Decimal("0"), "money_paid": Decimal("0"),
           "making_refund_eligible": 1, "rounding_adjustment": Decimal("0"), "signed_amount": Decimal("-345"), "valued_delta_grams": Decimal("-1"),
           "account_signed": Decimal("0"), "waived_amount": Decimal("0")}
    row.update(overrides)
    return row


def returned(**overrides):
    row = {"gold_amount": Decimal("345"), "making_refund": Decimal("0"), "money_paid": Decimal("0"), "account_amount": Decimal("0"), "waived_amount": Decimal("0"),
           "refund_basis": Decimal("345"), "valuation_mode": "CURRENT_BUY", "rounding_adjustment": Decimal("0"), "total_amount": Decimal("345"), "return_status": "FINALIZED"}
    row.update(overrides)
    return row


class MigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD_NAME)
        cls.rollback = sql.read("111_waived_settlement_difference_rollback.sql", sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_111_requires_110(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("MIGRATION_111_REFUSED_missing_110", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(111,")
        self.assertLessEqual(len(re.search(r"VALUES \(111,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 112)

    def test_only_the_waived_columns_and_the_two_checks_change_and_every_step_is_re_run_safe(self):
        for table in ("invoice_lifecycle_transactions", "invoice_returns"):
            self.assertRegex(self.forward, rf"ALTER TABLE {table} ADD COLUMN waived_amount DECIMAL\(16,2\) NOT NULL DEFAULT 0\.00")
        for guard in ("NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS", "CONSTRAINT_NAME='chk_lifecycle_making_model_v2'", "CONSTRAINT_NAME='chk_return_amounts_v3'"):
            self.assertIn(guard, self.forward)
        stripped = sql.strip_comments(self.forward).replace("ON DUPLICATE KEY UPDATE", "")
        self.assertNotRegex(stripped, r"(?i)\b(UPDATE|DELETE|TRUNCATE)\b\s")
        self.assertNotIn("DROP COLUMN", stripped)

    def test_the_rollback_refuses_with_a_later_migration_or_waived_rows_then_restores_031_and_033(self):
        for needle in ("ROLLBACK_111_REFUSED_later_migration_exists", "version > 111", "ROLLBACK_111_REFUSED_waived_amounts_exist", "waived_amount <> 0",
                       "DROP CHECK chk_lifecycle_making_model_v2", "ADD CONSTRAINT chk_lifecycle_making_model CHECK", "DROP CHECK chk_return_amounts_v3",
                       "ADD CONSTRAINT chk_return_amounts_v2 CHECK", "DROP COLUMN waived_amount", "DELETE FROM schema_migrations WHERE version = 111"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_111_REFUSED_waived_amounts_exist"), self.rollback.index("DROP COLUMN waived_amount"))

    def test_the_rollback_restores_exactly_the_old_checks_without_the_waived_part(self):
        old_exchange = check_body(self.rollback, "chk_lifecycle_making_model")
        old_return = check_body(self.rollback, "chk_return_amounts_v2")
        self.assertNotIn("waived", old_exchange)
        self.assertNotIn("waived", old_return)
        self.assertIn("money_received - money_paid + account_signed = signed_amount", old_exchange)
        self.assertIn("money_paid + account_amount = total_amount", old_return)


class ExchangeCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = check_body(sql.read(FORWARD_NAME), "chk_lifecycle_making_model_v2")

    def ok(self, **overrides):
        return evaluate(self.body, exchange(**overrides))

    def test_a_shop_payment_short_of_the_difference_is_valid_only_with_the_waived_part(self):
        # the screenshot: the shop owes the customer 345, pays 300 and waives 45
        self.assertTrue(self.ok(money_paid=Decimal("300"), waived_amount=Decimal("45")))
        self.assertFalse(self.ok(money_paid=Decimal("300")))                                       # 45 unexplained: still refused
        self.assertFalse(self.ok(money_paid=Decimal("300"), waived_amount=Decimal("40")))
        self.assertTrue(self.ok(money_paid=Decimal("345")))                                        # exact settlement: the old shape, waived 0

    def test_a_customer_payment_short_of_the_difference_uses_the_same_rule(self):
        base = dict(signed_amount=Decimal("345"), valued_delta_grams=Decimal("1"), making_refund=Decimal("0"))
        self.assertTrue(self.ok(**base, money_received=Decimal("300"), waived_amount=Decimal("45")))
        self.assertFalse(self.ok(**base, money_received=Decimal("300")))
        self.assertTrue(self.ok(**base, money_received=Decimal("200"), account_signed=Decimal("100"), waived_amount=Decimal("45")))     # money + account + waived

    def test_the_waived_part_is_never_negative_nor_above_the_difference_and_zero_difference_waives_nothing(self):
        self.assertFalse(self.ok(money_paid=Decimal("400"), waived_amount=Decimal("-55")))
        self.assertFalse(self.ok(money_paid=Decimal("0"), waived_amount=Decimal("346")))
        self.assertTrue(self.ok(money_paid=Decimal("0"), waived_amount=Decimal("345")))             # everything waived: no payment at all
        self.assertTrue(self.ok(signed_amount=Decimal("0"), valued_delta_grams=Decimal("0"), gold_amount=Decimal("0")))
        self.assertFalse(self.ok(signed_amount=Decimal("0"), valued_delta_grams=Decimal("0"), gold_amount=Decimal("0"), waived_amount=Decimal("1")))


class ReturnCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = check_body(sql.read(FORWARD_NAME), "chk_return_amounts_v3")

    def ok(self, **overrides):
        return evaluate(self.body, returned(**overrides))

    def test_a_finalized_return_needs_paid_plus_account_plus_waived_to_equal_the_total(self):
        self.assertTrue(self.ok(money_paid=Decimal("300"), waived_amount=Decimal("45")))
        self.assertFalse(self.ok(money_paid=Decimal("300")))
        self.assertTrue(self.ok(money_paid=Decimal("345")))
        self.assertTrue(self.ok(money_paid=Decimal("200"), account_amount=Decimal("100"), waived_amount=Decimal("45")))
        self.assertFalse(self.ok(money_paid=Decimal("300"), waived_amount=Decimal("-45")))

    def test_only_a_finalized_return_can_carry_a_settlement_or_a_waived_part(self):
        self.assertTrue(self.ok(return_status="DRAFT"))
        self.assertFalse(self.ok(return_status="DRAFT", waived_amount=Decimal("45")))
        self.assertFalse(self.ok(return_status="APPROVED", money_paid=Decimal("300")))


if __name__ == "__main__":
    unittest.main()
