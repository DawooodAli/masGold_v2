"""Pieces under 1 g are sold for a making PER PIECE (owner 2026-11-04): they never enter a per-gram average,
the accepted per-piece range is a System Setting, and the employee report shows them apart."""
from decimal import Decimal as D
import unittest

from services import setting_rules
from services.invoice_service import InvoiceService
from tests import test_report_service as trs


class Line:
    def __init__(self, making, weight):
        self.making_total, self.weight_grams = D(making), D(weight)


RANGES = {1: {"code": "B", "sale_making_min_per_gram": D("200"), "sale_making_max_per_gram": D("400"),
              "manager_approval_below_per_gram": D("250")}}
SMALL = (D("300"), D("450"))


class SmallPieceRuleTests(unittest.TestCase):
    def test_the_limit_is_exactly_one_gram(self):
        self.assertTrue(setting_rules.is_small_piece("0.999"))
        self.assertFalse(setting_rules.is_small_piece("1.000"))

    def test_settings_have_defaults_and_a_validated_range(self):
        values = setting_rules.effective({}, ("small_piece_making_min", "small_piece_making_max"))
        self.assertEqual((values["small_piece_making_min"], values["small_piece_making_max"]), (D("300"), D("450")))
        self.assertIsNone(setting_rules.writer_problem("small_piece_making_min", "350"))
        self.assertIsNotNone(setting_rules.writer_problem("small_piece_making_max", "abc"))
        self.assertIn("small_piece_making_min", setting_rules.DISCOUNT_KEYS)        # carried in the invoice limits

    def test_a_small_piece_never_distorts_the_per_gram_group(self):
        groups = InvoiceService._making_groups([Line("2500", "10.000"), Line("350", "0.680")], [1, 1], RANGES, SMALL)
        regular = next(g for g in groups if not g.get("small_piece"))
        small = next(g for g in groups if g.get("small_piece"))
        self.assertEqual((regular["average_making_per_gram"], regular["weight_grams"], regular["status"]), ("250.00", "10.000", "WITHIN_RANGE"))
        self.assertEqual((small["pieces"], small["average_making_per_piece"], small["status"], small["needs_approval"]), (1, "350.00", "WITHIN_RANGE", False))

    def test_a_small_piece_outside_the_per_piece_range_is_reviewed_never_approved(self):
        for making, status in (("299.99", "OUT_OF_RANGE"), ("300", "WITHIN_RANGE"), ("450", "WITHIN_RANGE"), ("450.01", "OUT_OF_RANGE")):
            with self.subTest(making):
                group = InvoiceService._making_groups([Line(making, "0.500")], [1], RANGES, SMALL)[0]
                self.assertEqual((group["status"], group["needs_approval"]), (status, False))

    def test_a_small_piece_alone_is_judged_per_piece_even_without_a_category(self):
        group = InvoiceService._making_groups([Line("350", "0.400")], [None], {}, SMALL)[0]
        self.assertEqual(group["status"], "WITHIN_RANGE")

    def test_a_negative_small_piece_is_below_gold(self):
        group = InvoiceService._making_groups([Line("-10", "0.400")], [1], RANGES, SMALL)[0]
        self.assertTrue(group["below_gold"])
        self.assertEqual(group["status"], "OUT_OF_RANGE")

    def test_two_small_pieces_are_each_checked(self):
        group = InvoiceService._making_groups([Line("350", "0.400"), Line("700", "0.300")], [1, 1], RANGES, SMALL)[0]
        self.assertEqual((group["pieces"], group["status"], group["average_making_per_piece"]), (2, "OUT_OF_RANGE", "525.00"))

    def test_the_range_comes_from_the_limits(self):
        self.assertEqual(InvoiceService._small_range({"small_piece_making_min": D("350"), "small_piece_making_max": D("400")}), (D("350"), D("400")))


class EmployeeReportTests(trs.Case):
    PERIOD = {"from": "2026-10-01", "to": "2026-10-31"}

    def test_small_pieces_are_a_separate_column_and_leave_the_per_gram_average_alone(self):
        original = trs.FakeRepo.employee_lines

        def with_small(repo, start, end):
            rows = original(repo, start, end)
            extra = dict(rows[0], weight_grams=D("0.500"), making_total=D("350.00"))
            return rows + [extra, dict(extra, weight_grams=D("0.800"), making_total=D("400.00"))]

        trs.FakeRepo.employee_lines = with_small
        try:
            result = self.service.run("employees", self.PERIOD)
        finally:
            trs.FakeRepo.employee_lines = original
        ali = next(t for t in result["tables"] if t["title"] == "علي")
        row = next(r for r in ali["rows"] if r["karat"] == "عيار 21")
        self.assertEqual(row["making"], D("106.67"))                       # unchanged: the small pieces are not in it
        self.assertEqual(row["sold"], D("21.300"))                         # their grams still count as sold
        self.assertEqual(row["small"], "2 • 375.00 ج/قطعة")
        self.assertTrue(any(c["key"] == "small" for c in ali["columns"]))


if __name__ == "__main__":
    unittest.main()
