"""Static contract of migration 096 (supplier account differences): text only, nothing is executed on MySQL."""
import hashlib
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
UP = (ROOT / "database" / "schema" / "096_supplier_account_adjustment.sql").read_text(encoding="utf-8")
DOWN = (ROOT / "database" / "schema" / "rollback" / "096_supplier_account_adjustment_rollback.sql").read_text(encoding="utf-8")


class AccountAdjustmentSchemaTests(unittest.TestCase):
    def test_the_migration_requires_095_and_is_guarded(self):
        self.assertIn("version=95", UP)
        self.assertIn("CREATE TABLE IF NOT EXISTS supplier_account_adjustments", UP)
        self.assertIn("ON DUPLICATE KEY UPDATE", UP)

    def test_the_table_refuses_a_zero_delta_a_blank_reason_a_wrong_shape_and_a_repeated_request(self):
        for text in ("chk_saa_delta CHECK (delta <> 0)", "chk_saa_reason", "chk_saa_kind", "uq_saa_request", "fk_saa_supplier", "fk_saa_karat"):
            self.assertIn(text, UP)

    def test_both_movement_types_and_the_owner_only_right_exist(self):
        self.assertIn("INSERT INTO supplier_gold_movement_types", UP)
        self.assertIn("INSERT INTO supplier_making_movement_types", UP)
        self.assertEqual(UP.count("'ACCOUNT_ADJUSTMENT'"), 2)
        self.assertIn("SUPPLIER_ACCOUNT_ADJUST", UP)
        self.assertIn("r.code = 'OWNER'", UP)
        self.assertNotIn("'MANAGER'", UP)

    def test_the_rollback_refuses_when_an_adjustment_exists(self):
        self.assertIn("ROLLBACK_096_REFUSED", DOWN)
        self.assertIn("FROM supplier_account_adjustments", DOWN)

    def test_the_manifest_carries_the_checksum(self):
        digest = hashlib.sha256((ROOT / "database" / "schema" / "096_supplier_account_adjustment.sql").read_bytes()).hexdigest()
        self.assertIn(f"{digest}  096_supplier_account_adjustment.sql", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
