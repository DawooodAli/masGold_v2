"""Static contract of migration 092 (supplier invoice group gold adjustment): text only, nothing is executed on MySQL."""
import hashlib
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
UP = (ROOT / "database" / "schema" / "092_supplier_group_adjustment.sql").read_text(encoding="utf-8")
DOWN = (ROOT / "database" / "schema" / "rollback" / "092_supplier_group_adjustment_rollback.sql").read_text(encoding="utf-8")


class AdjustmentSchemaTests(unittest.TestCase):
    def test_the_migration_requires_091_and_is_guarded(self):
        self.assertIn("version=91", UP)
        self.assertIn("CREATE TABLE IF NOT EXISTS supplier_invoice_group_adjustments", UP)
        self.assertIn("ON DUPLICATE KEY UPDATE", UP)

    def test_the_table_refuses_a_zero_delta_a_blank_reason_and_a_repeated_request(self):
        for text in ("chk_siga_delta CHECK (delta_grams <> 0)", "chk_siga_reason", "uq_siga_request", "fk_siga_invoice", "fk_siga_group"):
            self.assertIn(text, UP)

    def test_the_movement_type_and_the_right_exist(self):
        self.assertIn("'GROUP_ADJUSTMENT'", UP)
        self.assertIn("SUPPLIER_INVOICE_ADJUST", UP)
        self.assertIn("('OWNER','MANAGER')", UP)

    def test_the_rollback_refuses_when_an_adjustment_exists(self):
        self.assertIn("ROLLBACK_092_REFUSED", DOWN)
        self.assertIn("FROM supplier_invoice_group_adjustments", DOWN)

    def test_the_manifest_carries_the_checksum(self):
        digest = hashlib.sha256((ROOT / "database" / "schema" / "092_supplier_group_adjustment.sql").read_bytes()).hexdigest()
        self.assertIn(f"{digest}  092_supplier_group_adjustment.sql", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
