"""The old-customers import: cleaning rules and the generated SQL (pure, no database)."""
import unittest

from tools import import_golddb_customers as imp


class CleanCustomersTests(unittest.TestCase):
    def test_names_are_kept_and_only_the_blanks_around_them_are_trimmed(self):
        result = imp.clean_customers([("4", "  محمد حجاب ", "أجهور"), ("5", "هناء عدلي", ""), ("6", "تقى", "NULL")])
        self.assertEqual([(c["id"], c["name"], c["place"]) for c in result["customers"]], [(4, "محمد حجاب", "أجهور"), (5, "هناء عدلي", None), (6, "تقى", None)])
        self.assertEqual((result["problems"], result["trimmed"]), ([], 1))

    def test_a_bad_id_a_blank_name_a_duplicate_or_a_too_long_value_is_a_problem_never_guessed(self):
        rows = [("x", "اسم", ""), ("7", "  ", ""), ("8", "اسم", ""), ("8", "اسم تاني", ""), ("9", "ا" * 151, ""), ("10", "اسم", "م" * 151)]
        result = imp.clean_customers(rows)
        self.assertEqual([c["id"] for c in result["customers"]], [8])
        self.assertEqual(sorted(p[1] for p in result["problems"]), ["bad id", "blank name", "duplicate id", "value too long", "value too long"])

    def test_the_old_number_is_kept_and_the_code_follows_the_system_rule(self):
        sql = imp.insert_sql([{"id": 143, "name": "اسم 'فيه' علامة", "place": None}])
        self.assertIn("(143, 'C000143', ", sql)
        self.assertIn("NULL, NULL,", sql)                                                      # mobile and place stay empty
        self.assertNotIn("فيه", sql)                                                           # text goes in as hex, no quoting to get wrong
        self.assertTrue(sql.startswith("SET NAMES utf8mb4;\nSTART TRANSACTION;"))
        self.assertTrue(sql.rstrip().endswith("COMMIT;"))

    def test_apply_needs_the_explicit_target_and_never_runs_unrecognised(self):
        self.assertEqual(imp.main([]), 2)
        self.assertEqual(imp.main(["apply"]), 2)
        self.assertEqual(imp.main(["apply", "dev"]), 2)


if __name__ == "__main__":
    unittest.main()
