"""047 schema contracts; real constraint probes run separately on masgold_rehearsal."""
import subprocess
import unittest
from pathlib import Path
from tests.schema_model import build
from tools.schema_rehearsal.chain import load_chain

ROOT=Path(__file__).resolve().parents[1]
class WholeLotSchemaTests(unittest.TestCase):
    def test_manifest_and_earlier_migrations_are_unchanged(self):
        files=load_chain(require_committed=False);self.assertEqual(len(files),68)
        for f in files[:-1]:
            if subprocess.run(['git','cat-file','-e','HEAD:database/schema/'+f.name],cwd=ROOT,capture_output=True).returncode!=0:
                continue  # a prepared, not yet committed migration (056 before its commit) has no committed original to compare with; every committed one is still compared
            original=subprocess.check_output(['git','show','HEAD:database/schema/'+f.name],cwd=ROOT)
            self.assertEqual(original.replace(b'\r\n',b'\n'),f.path.read_bytes().replace(b'\r\n',b'\n'),f.name)

    def test_link_has_composite_document_entry_and_movement_foreign_keys(self):
        m=build();self.assertEqual(m.problems,[])
        table=m.tables['market_trip_lot_settlements']
        self.assertEqual(set(table['fks']),{'fk_mtls_entry','fk_mtls_header','fk_mtls_movement'})
        self.assertEqual(table['fks']['fk_mtls_movement']['cols'],['custody_movement_id','lot_id','entry_id','movement_type','weight_delta'])
        self.assertFalse(any(k.get('unique') and k['cols']==['lot_id'] for k in table['indexes'].values()))

    def test_explicit_null_shapes_and_original_lot_facts(self):
        text=(ROOT/'database/schema/047_supplier_gold_whole_lot_settlement.sql').read_text(encoding='utf-8')
        for clause in ('delivered_karat_id IS NULL','delivered_karat_value IS NULL','delivered_lot_id IS NOT NULL',
                       'delivered_fineness_permille IS NOT NULL','delivered_lot_weight_grams=delivered_weight_grams',
                       'REFERENCES market_trip_lots(id,entry_id,weight_grams,fineness_permille)'):
            self.assertIn(clause,text)

    def test_guarded_rollback_checks_history_even_if_reversed(self):
        text=(ROOT/'database/schema/rollback/047_supplier_gold_whole_lot_settlement_rollback.sql').read_text(encoding='utf-8')
        self.assertIn('version>47',text);self.assertNotIn('transaction_corrections',text)
        self.assertLess(text.index('ROLLBACK_047_REFUSED'),text.index('DROP TABLE'))
