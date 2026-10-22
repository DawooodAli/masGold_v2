"""The old master data import: cleaning rules and generated SQL (pure, no database)."""
import unittest

from tools import import_golddb_master as imp


class CleanMasterTests(unittest.TestCase):
    def test_a_repeated_factory_symbol_leaves_the_later_factory_without_a_code(self):
        data = imp.clean_master([("11", "FG", "فلور"), ("16", "FG", "فري"), ("19", "SS", "سادة "), ("2", "lz", "لازوردي"), ("3", "LZ", "تاني")], [], [])
        self.assertEqual([(f["code"], f["name"]) for f in data["factories"]], [("FG", "فلور"), (None, "فري"), ("SS", "سادة"), ("lz", "لازوردي"), (None, "تاني")])
        self.assertEqual([(n[1], n[2]) for n in data["factories_without_code"]], [("فري", "FG"), ("تاني", "LZ")])        # reported, never invented

    def test_item_types_keep_their_names_and_employees_get_ordered_codes(self):
        data = imp.clean_master([], [("1", "خاتم"), ("2", "دبلة"), ("3", "خاتم")], [("5", "حسين"), ("2", "علي"), ("3", "مصطفي")])
        self.assertEqual([t["name"] for t in data["item_types"]], ["خاتم", "دبلة"])
        self.assertEqual([p[2] for p in data["problems"]], ["duplicate name"])
        self.assertEqual([(e["code"], e["name"]) for e in data["employees"]], [("EMP001", "علي"), ("EMP002", "مصطفي"), ("EMP003", "حسين")])

    def test_a_blank_name_is_a_problem(self):
        data = imp.clean_master([("1", "A", " ")], [("1", "NULL")], [("1", "")])
        self.assertEqual(len(data["problems"]), 3)

    def test_the_sql_is_one_transaction_with_hex_text_and_null_codes(self):
        sql = imp.insert_sql({"factories": [{"code": None, "name": "فري"}], "item_types": [{"name": "خاتم"}], "employees": [{"code": "EMP001", "name": "علي"}]})
        self.assertTrue(sql.startswith("SET NAMES utf8mb4;\nSTART TRANSACTION;"))
        self.assertTrue(sql.rstrip().endswith("COMMIT;"))
        self.assertIn("(NULL, CONVERT(X'", sql)
        self.assertNotIn("فري", sql)

    def test_apply_needs_the_explicit_target(self):
        self.assertEqual(imp.main([]), 2)
        self.assertEqual(imp.main(["apply"]), 2)
        self.assertEqual(imp.main(["apply", "dev"]), 2)


if __name__ == "__main__":
    unittest.main()
