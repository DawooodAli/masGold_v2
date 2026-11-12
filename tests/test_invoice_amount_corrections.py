"""Owner 2026-11-12: an invoice whose settlement correction (FC) recorded more or less money than its recorded total counts at the LATEST corrected amount in the reports and on
the home page; the delta goes to the making of the sale lines (money IN) or to the value paid for the scrap lines (money OUT), spread by weight."""
import json
import unittest
from datetime import date, datetime
from decimal import Decimal as D

from services import invoice_amount_rules as rules
from services.dashboard_service import DashboardService
from tests.test_report_service import Case, FakeRepo


def fc(invoice, seq, settled, before_legs, before_account="0", direction="IN", money="0", account="0"):
    return {"invoice_id": invoice, "chain_sequence": seq, "money_direction": direction, "settled_total": D(settled),
            "before_json": json.dumps({"legs": [{"method_code": "CASH", "amount": a} for a in before_legs], "account": before_account}),
            "money_posted": D(money), "account_posted": D(account)}


class RuleTests(unittest.TestCase):
    def test_the_delta_is_the_latest_settlement_minus_the_one_before_the_first_correction(self):
        rows = [fc(7, 2, "69100", ["69150"], money="-50"), fc(7, 1, "69150", ["69200"], money="-50"),
                fc(8, 1, "1000", ["600"], "400")]                                       # a method / account move only: no change of amount
        changes = rules.invoice_changes(rows)
        self.assertEqual(changes["7"]["delta"], D("-100"))
        self.assertEqual(changes["7"]["money"], D("-100"))
        self.assertEqual(changes["8"]["delta"], D("0"))

    def test_money_in_goes_to_the_sale_lines_by_weight_and_adds_up_exactly(self):
        changes = {"7": {"direction": "IN", "delta": D("-100")}}
        lines = [{"line_id": 1, "invoice_id": 7, "line_type": "SELL_INVENTORY", "weight_grams": D("1")},
                 {"line_id": 2, "invoice_id": 7, "line_type": "SELL_UNIDENTIFIED", "weight_grams": D("2")},
                 {"line_id": 3, "invoice_id": 7, "line_type": "BUY_OLD_GOLD", "weight_grams": D("5")}]
        shares = rules.line_shares(changes, lines)
        self.assertEqual(shares, {"1": D("-33.33"), "2": D("-66.67")})                    # the scrap line of a mixed IN invoice takes nothing
        self.assertEqual(sum(shares.values()), D("-100"))

    def test_money_out_goes_to_the_scrap_lines_and_a_lower_payment_is_a_bigger_discount(self):
        changes = {"9": {"direction": "OUT", "delta": D("-20")}}
        lines = [{"line_id": 5, "invoice_id": 9, "line_type": "BUY_OLD_GOLD", "weight_grams": D("4")}]
        shares = rules.line_shares(changes, lines)
        row = {"line_id": 5, "line_type": "BUY_OLD_GOLD", "line_total": D("24000"), "discount_amount": D("360"), "making_total": None}
        fixed = rules.corrected([row], shares)[0]
        self.assertEqual((fixed["line_total"], fixed["discount_amount"]), (D("23980"), D("380")))

    def test_a_sale_line_gains_making_unknown_stays_unknown_and_unrelated_rows_are_untouched(self):
        shares = {"1": D("-50"), "2": D("10")}
        rows = [{"line_id": 1, "line_type": "SELL_UNIDENTIFIED", "making_total": D("5580.56"), "line_total": D("69200")},
                {"line_id": 2, "line_type": "SELL_INVENTORY", "making_total": None},
                {"line_id": 3, "line_type": "SELL_INVENTORY", "making_total": D("100")}, {"line_type": "SELL_INVENTORY", "making_total": D("1")}]
        fixed = rules.corrected(rows, shares)
        self.assertEqual((fixed[0]["making_total"], fixed[0]["line_total"]), (D("5530.56"), D("69150")))
        self.assertIsNone(fixed[1]["making_total"])
        self.assertIs(fixed[2], rows[2])
        self.assertIs(fixed[3], rows[3])

    def test_a_repository_without_the_reads_changes_nothing(self):
        self.assertEqual(rules.read(object()), ({}, {}))


class CorrectedRepo(FakeRepo):
    """FakeRepo plus an FC on invoice 1 (a sale: 10 g + 5 g, now 150 EGP less) and on invoice 3 (a scrap purchase of 8 g, now 80 EGP less paid)."""
    def invoice_settlement_corrections(self):
        return [fc(1, 1, "9850", ["6000"], "4000", money="-150"), fc(3, 1, "2920", ["3000"], direction="OUT", money="80")]

    def lines_of_invoices(self, ids):
        assert sorted(ids) == ["1", "3"]
        return [{"line_id": 11, "invoice_id": 1, "line_type": "SELL_INVENTORY", "weight_grams": D("10")},
                {"line_id": 12, "invoice_id": 1, "line_type": "SELL_INVENTORY", "weight_grams": D("5")},
                {"line_id": 31, "invoice_id": 3, "line_type": "BUY_OLD_GOLD", "weight_grams": D("8")}]

    def employee_lines(self, start, end):
        rows = super().employee_lines(start, end)
        ids = {(1, "10.000"): 11, (1, "5.000"): 12, (3, "8.000"): 31}
        return [dict(r, line_id=ids.get((r["invoice_id"], f"{r['weight_grams']:.3f}"), 900 + n)) for n, r in enumerate(rows)]


class ReportTests(Case):
    def setUp(self):
        super().setUp()
        self.service._repository = CorrectedRepo

    def test_the_employee_averages_use_the_corrected_amounts(self):
        tables = {t["title"]: t for t in self.service.run("employees", {"from": "2026-10-01", "to": "2026-10-31"})["tables"]}
        row21 = next(r for r in tables["علي"]["rows"] if r["karat"] == "عيار 21")
        self.assertEqual(row21["making"], D("96.67"))       # (1000 - 100 + 600 - 50) / 15 g; the scrap-sale line has unknown making
        self.assertEqual(row21["paid"], D("5990.00"))       # (48000 - 80) / 8 g

    def test_the_sales_report_counts_the_corrected_value_and_money(self):
        rows = {r["number"]: r for r in self.rows(self.service.run("sales", {"from": "2026-10-01", "to": "2026-10-31"}))}
        self.assertEqual((rows["INV0001"]["sales"], rows["INV0001"]["in"]), (D("9850"), D("5850")))
        self.assertEqual((rows["INV0003"]["oldgold"], rows["INV0003"]["out"]), (D("2920"), D("2920")))


class DashboardTests(unittest.TestCase):
    def test_the_home_page_figures_of_today_use_the_corrected_amount(self):
        today = date(2026, 11, 12)
        raw = {"invoices": {"sell_total": D("69200"), "buy_total": D("0")}, "sales_days": [{"day": today, "total": D("69200")}],
               "making_groups": [{"grp": "PIECES", "grams": D("10.54"), "known_grams": D("10.54"), "making": D("5580.56")}],
               "scrap": [], "top_types": []}
        changes = {"5075": {"direction": "IN", "delta": D("-50")}}
        lines = [{"line_id": 1, "invoice_id": 5075, "line_type": "SELL_UNIDENTIFIED", "weight_grams": D("10.54"), "making_total": D("5580.56"), "karat_value": 21,
                  "item_type": None, "invoice_status": "FINALIZED", "finalized_at": datetime(2026, 11, 12, 12, 56), "reversed": 0}]
        fixed = DashboardService.corrected_figures(raw, changes, lines, rules.line_shares(changes, lines), today)
        self.assertEqual(fixed["invoices"]["sell_total"], D("69150"))
        self.assertEqual(fixed["sales_days"][0]["total"], D("69150"))
        self.assertEqual(fixed["making_groups"][0]["making"], D("5530.56"))
        self.assertEqual(raw["invoices"]["sell_total"], D("69200"))                       # the raw read is not changed in place


if __name__ == "__main__":
    unittest.main()
