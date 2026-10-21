"""Migration 050 (supplier gold settlement from intermediary holdings): static replay and contract of the forward and rollback files. No database is used; the
live behaviour (constraints, composite keys, refusals, exact rollback, idempotence, clean-chain equality) was proven by the rehearsal on masgold_rehearsal."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal import chain as chain_module
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "050_supplier_gold_holding_settlement.sql"
ROLLBACK = "050_supplier_gold_holding_settlement_rollback.sql"
FROZEN = {"047_supplier_gold_whole_lot_settlement.sql": "b68de49b8f908cb9941173d1a2674478a331cfdfded9193d8139b9b002354165",
          "048_intermediary_lot_holdings.sql": "921ed1a96da324a1a97133d01c052ba3dca0edf0e5d7c99f053d8156d325c46f",
          "049_intermediary_logo_holdings.sql": "2ec47b5e3850ce8f4227d620bf23d6b5f10351478cfc85143b08b657e82540f5"}
SG = "supplier_gold_settlements"
NEW_COLUMNS = ("holding_party_id", "lot_pool_id", "logo_pool_id", "physical_declaration", "holding_weight_grams", "self_settlement_confirmed")


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def flat(text):
    return re.sub(r"\s+", "", text)


class HoldingSettlementSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.statements = sql.statements(cls.forward)
        cls.checks = sql.checks(cls.forward)

    def ddl(self, *needles):
        return [s.sql for s in self.statements if all(n in s.sql for n in needles)]

    def test_the_chain_replays_cleanly_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        self.assertEqual(self.model.problems, [])
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual((len(files), chain_module.LAST), (68, 68))
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_it_requires_049_registers_050_and_adds_no_table_permission_or_movement_type(self):
        self.assertIn("version=49", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_050_REFUSED_missing_049", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(50,")
        name = re.search(r"VALUES \(50,'([^']*)'", self.forward).group(1)
        self.assertLessEqual(len(name), 150)                                                         # schema_migrations.name is VARCHAR(150)
        code = " ".join(l for l in self.forward.splitlines() if not l.lstrip().startswith("--"))
        self.assertEqual(re.findall(r"INSERT INTO (\w+)", code), ["schema_migrations"])
        for forbidden in ("CREATE TABLE", "permissions", "market_trip_lot_movements", "chk_mte_kind", "cash_movements", "old_gold_movements", "supplier_making",
                          "market_trip_entries", "transaction_corrections"):
            self.assertNotIn(forbidden, code, forbidden)

    def test_every_statement_is_guarded_and_idempotent(self):
        raw = [s.strip() for s in sql.strip_comments(self.forward).split(";") if s.strip()]
        allowed = ("SET @masgold_sql", "PREPARE masgold_stmt", "EXECUTE masgold_stmt", "DEALLOCATE PREPARE", "INSERT INTO schema_migrations")
        for statement in raw:
            self.assertTrue(statement.startswith(allowed), statement[:80])
        guarded = [s for s in self.statements if s.guarded]
        self.assertTrue(guarded)
        for statement in guarded:
            self.assertRegex(statement.guard, r"NOT EXISTS|EXISTS", statement.sql[:60])
        self.assertEqual(sum(1 for s in self.statements if s.sql.startswith("ALTER TABLE")), len(guarded))

    def test_new_columns_and_their_nullability(self):
        columns = self.model.tables[SG]["columns"]
        for name in NEW_COLUMNS:
            self.assertEqual(self.model.tables[SG]["columns"][name] is not None, True, name)
        self.assertTrue(all(columns[n]["nullable"] for n in NEW_COLUMNS if n != "self_settlement_confirmed"))
        self.assertFalse(columns["self_settlement_confirmed"]["nullable"])
        self.assertEqual(columns["holding_weight_grams"]["type"], "decimal(12,3)")                  # equals the holding movements' weight type (FK compatible)
        self.assertEqual(self.model.tables["intermediary_holding_movements"]["columns"]["weight_grams"]["type"], "decimal(12,3)")
        self.assertEqual(self.model.tables["logo_holding_movements"]["columns"]["weight_grams"]["type"], "decimal(12,3)")
        for table in ("intermediary_holding_movements", "logo_holding_movements"):
            columns = self.model.tables[table]["columns"]
            self.assertTrue(columns["document_id"]["nullable"] and columns["doc_kind"]["nullable"] and columns["settlement_id"]["nullable"], table)
        self.assertTrue(self.model.tables[SG]["columns"]["delivered_karat_id"]["nullable"])             # unchanged from 047

    def test_the_source_and_shape_checks_enforce_the_two_new_sg_shapes(self):
        source = flat(self.checks["chk_sgs_source"][1])
        for kind in ("SHOP_SCRAP", "CUSTODY_GOLD", "CUSTODY_LOT", "HOLDING_LOT", "HOLDING_LOGO"):
            self.assertIn(f"'{kind}'", source)
        shape = flat(self.checks["chk_sgs_shape"][1])
        for needle in ("(source_kind='HOLDING_LOGO'", "(source_kind='HOLDING_LOT'", "holding_party_id ISNOTNULL".replace(" ", ""),
                       "logo_pool_idISNOTNULLANDlot_pool_idISNULLANDphysical_declarationISNULL",
                       "lot_pool_idISNOTNULLANDlogo_pool_idISNULL",
                       "physical_declarationISNOTNULLANDphysical_declarationIN('WHOLE_ORIGINAL_BAR','GRAM_ENTITLEMENT','PHYSICALLY_DIVIDED')",
                       "(physical_declaration<>'WHOLE_ORIGINAL_BAR'ORdelivered_weight_grams=delivered_lot_weight_grams)",
                       "delivered_weight_grams<=delivered_lot_weight_grams", "holding_weight_gramsISNOTNULLANDholding_weight_grams=delivered_weight_grams"):
            self.assertIn(needle, shape)
        # a NULL passes a CHECK: the declaration must be tested for NOT NULL explicitly (found by the rehearsal)
        self.assertLess(shape.index("physical_declarationISNOTNULLANDphysical_declarationIN"), shape.index("'WHOLE_ORIGINAL_BAR','GRAM"))
        # the legacy shapes still forbid every holding column
        self.assertEqual(shape.count("holding_party_idISNULLANDlot_pool_idISNULLANDlogo_pool_idISNULLANDphysical_declarationISNULLANDholding_weight_gramsISNULL"), 2)
        self.assertEqual(flat(self.checks["chk_sgs_self"][1]),
                         flat("(holding_party_id IS NULL AND self_settlement_confirmed=0) OR (holding_party_id IS NOT NULL AND "
                              "(supplier_id<>holding_party_id OR self_settlement_confirmed=1))"))
        legs = flat(self.checks["chk_sgsl_source"][1])
        self.assertTrue("'HOLDING_LOT'" in legs and "'HOLDING_LOGO'" in legs)
        self.assertIn("chk_sgsl_scrap_only", sql.read("046_supplier_gold_custody_settlement.sql"))     # a SCRAP leg stays forbidden outside SHOP_SCRAP

    def test_composite_keys_tie_the_header_the_pool_and_the_movement_together(self):
        fks = self.model.tables[SG]["fks"]
        self.assertEqual((fks["fk_sgs_lot_pool"]["cols"], fks["fk_sgs_lot_pool"]["ref"], fks["fk_sgs_lot_pool"]["refcols"]),
                         (["lot_pool_id", "holding_party_id", "delivered_lot_id", "delivered_lot_weight_grams", "delivered_fineness_permille"],
                          "intermediary_holding_pools", ["id", "party_id", "lot_id", "weight_grams", "fineness_permille"]))
        self.assertIn(("id", "party_id", "lot_id", "weight_grams", "fineness_permille"),
                      [tuple(i["cols"]) for i in self.model.tables["intermediary_holding_pools"]["indexes"].values() if i["unique"]])
        self.assertEqual((fks["fk_sgs_logo_pool"]["cols"], fks["fk_sgs_logo_pool"]["ref"], fks["fk_sgs_logo_pool"]["refcols"]),
                         (["logo_pool_id", "holding_party_id", "delivered_karat_id"], "logo_holding_pools", ["id", "party_id", "karat_id"]))
        self.assertTrue(self.model.tables["logo_holding_pools"]["indexes"]["uq_lhp_source"]["unique"])
        self.assertEqual((fks["fk_sgs_holding_party"]["cols"], fks["fk_sgs_holding_party"]["ref"]), (["holding_party_id"], "suppliers"))
        for table, prefix, key in (("intermediary_holding_movements", "ihm", "lot_pool_id"), ("logo_holding_movements", "lhm", "logo_pool_id")):
            fk = self.model.tables[table]["fks"][f"fk_{prefix}_settlement"]
            self.assertEqual((fk["cols"], fk["ref"], fk["refcols"]), (["settlement_id", "pool_id", "weight_grams"], SG, ["id", key, "holding_weight_grams"]))
            self.assertTrue(self.model.tables[SG]["indexes"]["uq_sgs_lot_holding" if key == "lot_pool_id" else "uq_sgs_logo_holding"]["unique"])
            self.assertTrue(self.model.tables[table]["indexes"][f"uq_{prefix}_settlement_type"]["unique"])
            self.assertEqual(self.model.tables[table]["indexes"][f"uq_{prefix}_settlement_type"]["cols"], ["settlement_id", "movement_type"])
            self.assertIn(f"idx_{prefix}_settlement", self.model.tables[table]["indexes"])             # the child index the foreign key needs
        self.assertNotIn("ON DELETE", self.forward)
        self.assertNotRegex(self.forward, r"\b(FLOAT|DOUBLE)\b")

    def test_the_movement_shapes_keep_every_legacy_branch_and_add_settlement_and_its_compensation(self):
        for prefix in ("ihm", "lhm"):
            shape = flat(self.checks[f"chk_{prefix}_shape"][1])
            for branch in ("movement_type='HANDOVER'ANDdoc_kind='HANDOVER'ANDweight_delta=weight_gramsANDcorrection_idISNULL",
                           "movement_type='RETURN'ANDdoc_kind='RETURN'ANDweight_delta=-weight_gramsANDcorrection_idISNULL",
                           "movement_type='HANDOVER_REVERSAL'ANDdoc_kind='HANDOVER'ANDweight_delta=-weight_gramsANDcorrection_idISNOTNULL",
                           "movement_type='RETURN_REVERSAL'ANDdoc_kind='RETURN'ANDweight_delta=weight_gramsANDcorrection_idISNOTNULL"):
                self.assertIn(branch, shape, prefix)
            self.assertEqual(shape.count("document_idISNOTNULLANDsettlement_idISNULLAND"), 4)
            self.assertIn("(movement_type='SETTLEMENT'ANDdocument_idISNULLANDdoc_kindISNULLANDsettlement_idISNOTNULLANDweight_delta=-weight_gramsANDcorrection_idISNULL)", shape)
            self.assertIn("(movement_type='SETTLEMENT_REVERSAL'ANDdocument_idISNULLANDdoc_kindISNULLANDsettlement_idISNOTNULLANDweight_delta=weight_gramsANDcorrection_idISNOTNULL)", shape)
            self.assertTrue(shape.startswith("weight_grams>0AND("))

    def test_the_restated_legacy_checks_are_the_old_clauses_plus_only_the_new_values(self):
        old = {"chk_ihm_shape": sql.checks(sql.read("048_intermediary_lot_holdings.sql"))["chk_ihm_shape"][1],
               "chk_lhm_shape": sql.checks(sql.read("049_intermediary_logo_holdings.sql"))["chk_lhm_shape"][1]}
        for name, clause in old.items():
            stripped = flat(self.checks[name][1]).replace("document_idISNOTNULLANDsettlement_idISNULLAND", "")
            self.assertTrue(flat(clause).replace("weight_grams>0AND(", "")[:-1] in stripped, name)      # every legacy branch survives verbatim

    def test_the_rollback_guards_all_history_drops_dependents_first_and_restores_the_exact_earlier_definitions(self):
        self.assertIn("ROLLBACK_050_REFUSED_history_or_later_migration", self.rollback)
        for needle in ("source_kind IN ('HOLDING_LOT','HOLDING_LOGO')", "FROM supplier_gold_settlement_legs", "FROM intermediary_holding_movements WHERE settlement_id IS NOT NULL",
                       "FROM logo_holding_movements WHERE settlement_id IS NOT NULL", "version>50"):
            self.assertIn(needle, self.rollback)
        order = ["ROLLBACK_050_REFUSED", "DROP FOREIGN KEY fk_ihm_settlement", "DROP FOREIGN KEY fk_lhm_settlement", "DROP FOREIGN KEY fk_sgs_logo_pool",
                 "DROP INDEX uq_sgs_logo_holding", "DROP COLUMN self_settlement_confirmed", "DROP INDEX uq_lhp_source", "DELETE FROM schema_migrations WHERE version=50"]
        positions = [self.rollback.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(self.rollback.index("fk_ihm_settlement"), self.rollback.index("DROP INDEX uq_sgs_lot_holding"))      # movements release the SG keys first
        restored = {name: flat(expression) for name, (table, expression) in sql.checks(self.rollback).items()}
        self.assertEqual(restored["chk_sgs_shape"], flat(sql.checks(sql.read("047_supplier_gold_whole_lot_settlement.sql"))["chk_sgs_shape"][1]))
        self.assertEqual(restored["chk_ihm_shape"], flat(sql.checks(sql.read("048_intermediary_lot_holdings.sql"))["chk_ihm_shape"][1]))
        self.assertEqual(restored["chk_lhm_shape"], flat(sql.checks(sql.read("049_intermediary_logo_holdings.sql"))["chk_lhm_shape"][1]))
        self.assertEqual(re.findall(r"'(\w+)'", re.search(r"chk_sgs_source CHECK\(source_kind IN \(([^)]*)\)\)", self.rollback).group(1)),
                         ["SHOP_SCRAP", "CUSTODY_GOLD", "CUSTODY_LOT"])
        self.assertEqual(re.findall(r"'(\w+)'", re.search(r"chk_sgsl_source CHECK\(source_kind IN \(([^)]*)\)\)", self.rollback).group(1)),
                         ["SHOP_SCRAP", "CUSTODY_GOLD", "CUSTODY_LOT"])
        self.assertIn("MODIFY COLUMN document_id BIGINT UNSIGNED NOT NULL", self.rollback)
        self.assertIn("MODIFY COLUMN doc_kind VARCHAR(8) NOT NULL", self.rollback)


if __name__ == "__main__":
    unittest.main()
