"""The old customer balances import: the owed-minus-paid rule and the generated SQL (pure, no database)."""
from decimal import Decimal as D
import unittest

from tools import import_golddb_balances as imp


class NetBalancesTests(unittest.TestCase):
    def test_owed_minus_paid_with_the_systems_sign_and_zero_balances_skipped(self):
        result = imp.net_balances([("8", "1050930", "1036305"), ("9", "100", "250"), ("10", "500", "500")], {8, 9, 10})
        self.assertEqual([(e["customer_id"], e["balance"]) for e in result["entries"]], [(8, D("14625")), (9, D("-150"))])        # positive: the customer owes the shop
        self.assertEqual(result["zero"], 1)

    def test_customers_that_are_not_in_the_list_or_have_no_number_are_ignored(self):
        result = imp.net_balances([("977", "432335", "0"), ("OO", "0", "16900"), ("", "5", "0"), ("4", "10", "0")], {4})
        self.assertEqual([e["customer_id"] for e in result["entries"]], [4])
        self.assertEqual(result["ignored"], 3)

    def test_the_sql_is_one_transaction_with_the_legacy_type_and_provenance(self):
        sql = imp.insert_sql([{"customer_id": 8, "owed": D("100"), "paid": D("40"), "balance": D("60")}])
        self.assertIn("code = 'LEGACY_ADJUSTMENT'", sql)
        self.assertIn("60.00", sql)
        self.assertIn("'golddb.cash', 8", sql)
        self.assertTrue(sql.startswith("SET NAMES utf8mb4;\nSTART TRANSACTION;"))
        self.assertTrue(sql.rstrip().endswith("COMMIT;"))

    def test_apply_needs_the_explicit_target(self):
        self.assertEqual(imp.main([]), 2)
        self.assertEqual(imp.main(["apply"]), 2)
        self.assertEqual(imp.main(["apply", "dev"]), 2)


if __name__ == "__main__":
    unittest.main()
