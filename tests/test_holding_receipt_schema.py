"""Migration 051 (physical receipts from intermediary holdings): static replay and contract of the forward and rollback files. No database is used; the live behaviour
(constraints, composite keys, NULL bypasses, mixed source columns, exact rollback, idempotence, clean-chain equality) was proven by the rehearsal on masgold_rehearsal."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal import chain as chain_module
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "051_intermediary_physical_receipts.sql"
ROLLBACK = "051_intermediary_physical_receipts_rollback.sql"
FROZEN = {"047_supplier_gold_whole_lot_settlement.sql": "b68de49b8f908cb9941173d1a2674478a331cfdfded9193d8139b9b002354165",
          "048_intermediary_lot_holdings.sql": "921ed1a96da324a1a97133d01c052ba3dca0edf0e5d7c99f053d8156d325c46f",
          "049_intermediary_logo_holdings.sql": "2ec47b5e3850ce8f4227d620bf23d6b5f10351478cfc85143b08b657e82540f5",
          "050_supplier_gold_holding_settlement.sql": "623f25b8a4f9f990bc3ad9b0891046f5e7e0c4b50f09e418f65b5fdfc77b9f3d"}
TABLES = ("holding_receipts", "receipt_pieces", "receipt_stock_movements")
IHM = "intermediary_holding_movements"


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def flat(text):
    return re.sub(r"\s+", "", text)


class PhysicalReceiptSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.statements = sql.statements(cls.forward)
        cls.checks = sql.checks(cls.forward)
        cls.code = " ".join(l for l in cls.forward.splitlines() if not l.lstrip().startswith("--"))

    def test_the_chain_replays_cleanly_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        self.assertEqual(self.model.problems, [])
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual((len(files), chain_module.LAST), (67, 67))
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))
        for name in TABLES:
            self.assertEqual(self.model.tables[name]["source"], FORWARD, name)

    def test_it_requires_050_registers_051_and_adds_no_permission_or_movement_type(self):
        self.assertIn("version=50", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_051_REFUSED_missing_050", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(51,")
        name = re.search(r"VALUES \(51,'([^']*)'", self.forward).group(1)
        self.assertLessEqual(len(name), 150)
        self.assertEqual(re.findall(r"INSERT INTO (\w+)", self.code), ["schema_migrations"])
        for forbidden in ("permissions", "role_permissions", "supplier_gold_movements", "old_gold_movements", "cash_movements", "market_trip_lot_movements",
                          "market_trip_lot_shop_movements", "inventory_items"):
            self.assertNotIn(forbidden, self.code, forbidden)

    def test_every_alter_is_guarded_and_idempotent(self):
        raw = [s.strip() for s in sql.strip_comments(self.forward).split(";") if s.strip()]
        allowed = ("SET @masgold_sql", "PREPARE masgold_stmt", "EXECUTE masgold_stmt", "DEALLOCATE PREPARE", "CREATE TABLE IF NOT EXISTS", "INSERT INTO schema_migrations")
        for statement in raw:
            self.assertTrue(statement.startswith(allowed), statement[:80])
        self.assertEqual(sum(1 for s in self.statements if s.sql.startswith("ALTER TABLE")), sum(1 for s in self.statements if s.guarded and s.sql.startswith("ALTER TABLE")))

    def test_no_check_uses_chained_equality_or_a_null_passing_membership_test(self):
        for name, (table, expression) in self.checks.items():
            compact = flat(expression)
            self.assertNotRegex(compact, r"\)=\(", name)                                         # (a IS NULL) = (b IS NULL) style chains
            self.assertNotRegex(compact, r"=[^=<>()ANDOR]+=", name)                              # a=b=c
            for branch in re.findall(r"IN\(", compact):
                self.assertIn("ISNOTNULL", compact, name)                                          # every IN list is paired with an explicit IS NOT NULL
        shape = flat(self.checks["chk_ihm_shape"][1])
        self.assertEqual(shape.count("receipt_idISNULL"), 6)                                     # the six legacy branches state receipt_id IS NULL
        self.assertEqual(shape.count("receipt_idISNOTNULL"), 2)                                  # the two receipt branches state it IS NOT NULL
        for branch in ("movement_type='RECEIPT'", "movement_type='RECEIPT_REVERSAL'"):
            self.assertIn(branch, shape)
        self.assertIn("document_idISNULLANDdoc_kindISNULLANDsettlement_idISNULLANDreceipt_idISNOTNULLANDmovement_type='RECEIPT'ANDweight_delta=-weight_gramsANDcorrection_idISNULL", shape)
        self.assertIn("document_idISNULLANDdoc_kindISNULLANDsettlement_idISNULLANDreceipt_idISNOTNULLANDmovement_type='RECEIPT_REVERSAL'ANDweight_delta=weight_gramsANDcorrection_idISNOTNULL", shape)
        piece = flat(self.checks["chk_rp_shape"][1])
        self.assertEqual(piece, flat("grams>0 AND identity_kind='RECEIPT_PIECE' AND location='SHOP' AND assay_status='UNKNOWN' AND fineness_permille IS NULL AND pure_grams IS NULL"))
        stock = flat(self.checks["chk_rsm_shape"][1])
        self.assertIn("(movement_type='RECEIVE'ANDweight_delta=weight_gramsANDcorrection_idISNULL)", stock)
        self.assertIn("(movement_type='RECEIVE_REVERSAL'ANDweight_delta=-weight_gramsANDcorrection_idISNOTNULL)", stock)

    def test_composite_keys_tie_the_receipt_the_piece_the_stock_and_the_pool_ledger_together(self):
        pool = self.model.tables["intermediary_holding_pools"]
        self.assertEqual(pool["indexes"]["uq_ihp_receipt"]["cols"], ["id", "party_id", "lot_id", "origin_melt_entry_id"])
        fk = self.model.tables["holding_receipts"]["fks"]["fk_hr_pool"]
        self.assertEqual((fk["cols"], fk["ref"], fk["refcols"]), (["pool_id", "party_id", "lot_id", "origin_melt_entry_id"], "intermediary_holding_pools",
                                                                  ["id", "party_id", "lot_id", "origin_melt_entry_id"]))
        piece = self.model.tables["receipt_pieces"]["fks"]["fk_rp_receipt"]
        self.assertEqual((piece["cols"], piece["ref"], piece["refcols"]),
                         (["receipt_id", "pool_id", "party_id", "lot_id", "origin_melt_entry_id", "grams"], "holding_receipts",
                          ["id", "pool_id", "party_id", "lot_id", "origin_melt_entry_id", "grams"]))
        self.assertTrue(self.model.tables["holding_receipts"]["indexes"]["uq_hr_piece"]["unique"])
        stock = self.model.tables["receipt_stock_movements"]["fks"]["fk_rsm_piece"]
        self.assertEqual((stock["cols"], stock["ref"], stock["refcols"]), (["piece_id", "receipt_id", "weight_grams"], "receipt_pieces", ["id", "receipt_id", "grams"]))
        move = self.model.tables[IHM]["fks"]["fk_ihm_receipt"]
        self.assertEqual((move["cols"], move["ref"], move["refcols"]), (["receipt_id", "pool_id", "weight_grams"], "holding_receipts", ["id", "pool_id", "grams"]))
        self.assertTrue(self.model.tables["receipt_pieces"]["indexes"]["uq_rp_receipt"]["unique"])                          # one piece per document
        self.assertTrue(self.model.tables["holding_receipts"]["indexes"]["uq_hr_request"]["unique"])
        self.assertEqual(self.model.tables[IHM]["indexes"]["uq_ihm_receipt_type"]["cols"], ["receipt_id", "movement_type"])
        for table, column in (("holding_receipts", "grams"), ("receipt_pieces", "grams"), ("receipt_stock_movements", "weight_grams")):
            self.assertEqual(self.model.tables[table]["columns"][column]["type"], "decimal(12,3)")                              # FK-compatible with the ledger weight
        self.assertTrue(self.model.tables[IHM]["columns"]["receipt_id"]["nullable"])
        self.assertNotIn("ON DELETE", self.forward)
        self.assertNotRegex(self.forward, r"\b(FLOAT|DOUBLE)\b")

    def test_the_correction_source_keeps_every_earlier_source_and_adds_only_the_receipt(self):
        clause = flat(self.checks["chk_correction_source"][1])
        old = flat(sql.checks(sql.read("045_supplier_gold_settlement.sql"))["chk_correction_source"][1])
        self.assertEqual(clause.replace(",'HOLDING_RECEIPT'", ""), old)

    def test_the_rollback_guards_all_history_drops_dependents_first_and_restores_the_exact_earlier_definitions(self):
        self.assertIn("ROLLBACK_051_REFUSED_history_or_later_migration", self.rollback)
        for needle in ("FROM holding_receipts", "FROM receipt_pieces", "FROM receipt_stock_movements", "WHERE receipt_id IS NOT NULL", "source_type='HOLDING_RECEIPT'", "version>51"):
            self.assertIn(needle, self.rollback)
        order = ["ROLLBACK_051_REFUSED", "DROP FOREIGN KEY fk_ihm_receipt", "DROP COLUMN receipt_id", "DROP TABLE receipt_stock_movements", "DROP TABLE receipt_pieces",
                 "DROP TABLE holding_receipts", "DROP INDEX uq_ihp_receipt", "DELETE FROM schema_migrations WHERE version=51"]
        positions = [self.rollback.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))
        restored = {name: flat(expression) for name, (table, expression) in sql.checks(self.rollback).items()}
        self.assertEqual(restored["chk_ihm_shape"], flat(sql.checks(sql.read("050_supplier_gold_holding_settlement.sql"))["chk_ihm_shape"][1]))
        self.assertEqual(restored["chk_correction_source"], flat(sql.checks(sql.read("045_supplier_gold_settlement.sql"))["chk_correction_source"][1]))


if __name__ == "__main__":
    unittest.main()
