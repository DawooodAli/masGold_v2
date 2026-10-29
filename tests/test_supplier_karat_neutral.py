import unittest

from services import supplier_rules as rules


class NeutralKaratChangeTests(unittest.TestCase):
    history = [{"karat_id": 3, "karat_value": 21}]

    def test_a_neutral_history_allows_the_correction(self):
        rules.check_supplier_karat_change(3, 2, self.history, neutral=True)

    def test_a_history_still_in_force_keeps_the_lock(self):
        with self.assertRaises(rules.SupplierRuleError) as ctx:
            rules.check_supplier_karat_change(3, 2, self.history, neutral=False)
        self.assertEqual(ctx.exception.code, "KARAT_LOCKED")


if __name__ == "__main__":
    unittest.main()
