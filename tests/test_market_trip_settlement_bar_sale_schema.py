"""Migration 063 (settlement step 2d: the melted bar sold for custody cash): static contract of the forward and rollback files. No database is used; the live behaviour
(constraints, refusals, rollback) needs an approved rehearsal run."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "063_market_trip_settlement_bar_sale.sql"
ROLLBACK = "063_market_trip_settlement_bar_sale_rollback.sql"


class BarSaleSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.m053 = sql.read("053_market_trip_gold_sale.sql")

    def test_the_chain_replays_and_063_requires_062(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("version=62", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_063_REFUSED_missing_062", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(63,")
        self.assertLessEqual(len(re.search(r"VALUES \(63,'([^']*)'", self.forward).group(1)), 150)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 73)

    def test_the_sale_source_gains_only_the_bar_branch(self):
        self.assertIn("(source_kind = 'TRIP_BAR' AND karat_id IS NULL AND designation IS NULL AND lot_id IS NULL AND fineness_permille IS NULL AND custody_movement_id IS NULL "
                      "AND movement_type IS NULL AND weight_delta IS NULL)", self.forward)
        self.assertIn("source_kind = 'CUSTODY_GOLD'", self.forward)
        self.assertIn("source_kind = 'CUSTODY_LOT'", self.forward)
        self.assertNotIn("TRIP_BAR", self.rollback.split("ALTER TABLE market_trip_gold_sales ADD CONSTRAINT")[1])      # the rollback restores the 053 expression

    def test_the_pool_ledger_accepts_the_sale_and_the_rollback_refuses_with_history(self):
        self.assertIn("entry_kind IN ('ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','GOLD_SALE')", self.forward)
        self.assertIn("entry_kind IN ('ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO')", self.rollback)
        for needle in ("ROLLBACK_063_REFUSED_bar_sale_history_exists", "source_kind = 'TRIP_BAR'", "entry_kind = 'GOLD_SALE'", "version > 63", "DELETE FROM schema_migrations WHERE version = 63"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_063_REFUSED"), self.rollback.index("DROP CHECK"))
        self.assertNotIn("TRIGGER", self.forward.upper())


if __name__ == "__main__":
    unittest.main()
