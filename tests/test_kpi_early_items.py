"""Owner 2026-11-13, the KPI items to catch early (dead stock with its pieces by barcode, invoice differences, an employee's 21K making, market loss, sold-out fast sellers), the fixed 1.5% scrap line,
the 2% rule that judges crafted pieces only, and the piece lookup that says how long a piece has been in stock. Fakes only."""
from datetime import date, datetime
from decimal import Decimal as D
import unittest
from unittest import mock

from services import kpi_rules as k
from tests import test_report_service as base
from tests import test_webapp_extras as web


def sale_line(employee, name, weight, making, karat=21, item_type="خاتم", gross=None, cost="50", line_type="SELL_INVENTORY"):
    return {"invoice_id": 1, "invoice_number": "1", "invoice_date": datetime(2026, 10, 5), "employee_id": employee, "employee": name, "line_type": line_type, "karat_value": karat, "weight_grams": D(weight),
            "making_total": D(making), "gross_total": D(gross) if gross is not None else D(weight) * 6000, "discount_amount": None, "scrap_kind": None, "item_type": item_type, "piece_cost": D(cost),
            "category_cost": None, "real_cost": None, "reversal_id": None}


class ThresholdTests(unittest.TestCase):
    def test_dead_stock_share_is_green_yellow_red_at_15_and_30_percent(self):
        self.assertEqual(k.dead_stock_item(D("100"), D("1000"), 5)["status"], k.GREEN)
        self.assertEqual(k.dead_stock_item(D("150"), D("1000"), 5)["status"], k.YELLOW)
        self.assertEqual(k.dead_stock_item(D("300"), D("1000"), 5)["status"], k.YELLOW)
        self.assertEqual(k.dead_stock_item(D("301"), D("1000"), 5)["status"], k.RED)
        item = k.dead_stock_item(D("2876"), D("6941"), 931)
        self.assertEqual((item["status"], item["value"]), (k.RED, "41.4% من ذهب المحل"))

    def test_dead_stock_without_a_sheet_or_the_right_is_gray_never_a_zero(self):
        self.assertEqual(k.dead_stock_item(None, D("1000"), None)["status"], k.GRAY)
        no_sheet = k.dead_stock_item(D("200"), None, 3)
        self.assertEqual(no_sheet["status"], k.GRAY)
        self.assertIn("200", no_sheet["value"])

    def test_invoice_differences_are_judged_against_the_net_making(self):
        self.assertEqual(k.invoice_diff_item(D("4"), D("0"), D("100"))["status"], k.GREEN)
        self.assertEqual(k.invoice_diff_item(D("10"), D("0"), D("100"))["status"], k.YELLOW)
        item = k.invoice_diff_item(D("59475"), D("0"), D("179335.51"))
        self.assertEqual(item["status"], k.RED)
        self.assertIn("33.2%", item["value"])
        self.assertEqual(k.invoice_diff_item(None, None, D("100"))["status"], k.GRAY)
        self.assertEqual(k.invoice_diff_item(D("5"), D("0"), None)["status"], k.GRAY)

    def test_the_invoice_difference_category_is_recognised_in_its_spellings(self):
        for label in ("فرق فواتير", "فرق فاتورة", "فرق فاتوره"):
            self.assertTrue(k.is_invoice_difference(label), label)
        self.assertFalse(k.is_invoice_difference("مصروفات"))
        self.assertTrue(k.is_mandatory_expense("مرتبات"))

    def test_the_scrap_discount_has_a_fixed_1_5_line_and_shows_what_the_shortfall_cost(self):
        def scrap(percent):
            return [{"line_type": "BUY_OLD_GOLD", "scrap_kind": None, "gross_total": D("10000"), "discount_amount": D(percent) * 100, "weight_grams": D("10")}]
        self.assertEqual(k.scrap_item(scrap("1.5"))["status"], k.GREEN)
        yellow = k.scrap_item(scrap("1.3"))
        self.assertEqual(yellow["status"], k.YELLOW)
        self.assertIn("20", yellow["compare"])                                    # (1.5 - 1.3)% of 10,000
        self.assertEqual(k.scrap_item(scrap("1.0"))["status"], k.RED)
        self.assertEqual(k.scrap_item([])["status"], k.GRAY)

    def test_market_loss_is_judged_on_the_gold_sent_out_in_the_last_90_days(self):
        self.assertEqual(k.market_loss_item(D("1"), D("4"), D("1000"))["status"], k.GREEN)
        self.assertEqual(k.market_loss_item(D("1"), D("7.4"), D("1000"))["status"], k.YELLOW)
        self.assertEqual(k.market_loss_item(D("1"), D("11"), D("1000"))["status"], k.RED)
        self.assertEqual(k.market_loss_item(D("1"), D("1"), D("0"))["status"], k.GRAY)
        self.assertEqual(k.market_loss_item(None, None, None)["status"], k.GRAY)

    def test_sold_out_fast_sellers_count_none_few_many(self):
        self.assertEqual([k.stockout_item(n)["status"] for n in (0, 1, 3, 4)], [k.GREEN, k.YELLOW, k.YELLOW, k.RED])
        self.assertEqual(k.stockout_item(None)["status"], k.GRAY)


class EmployeeMakingTests(unittest.TestCase):
    def lines(self):
        return ([sale_line(1, "علي", "10", "4000") for _ in range(3)]                   # 400 per gram, 30 g
                + [sale_line(2, "حسين", "10", "3000") for _ in range(3)]                # 300 per gram
                + [sale_line(2, "حسين", "10", "100", item_type="سبيكة") for _ in range(3)])   # bars never count

    def test_an_employee_far_under_the_others_is_red_and_bars_are_left_out(self):
        rows = k.making21_rows(self.lines())
        self.assertEqual([(r["employee"], r["per_gram"], r["status"]) for r in rows], [("حسين", D("300.00"), k.RED), ("علي", D("400.00"), k.GREEN)])
        item = k.making21_item(rows)
        self.assertEqual(item["status"], k.RED)
        self.assertIn("حسين", item["value"])

    def test_nobody_with_enough_grams_is_gray(self):
        self.assertEqual(k.making21_item(k.making21_rows([sale_line(1, "علي", "5", "2000")]))["status"], k.GRAY)


class TwoPercentRuleTests(unittest.TestCase):
    def test_the_fixed_rule_judges_crafted_pieces_only(self):
        crafted = sale_line(1, "علي", "10", "1000", gross="60000")                      # profit (1000 - 500) / 60000 = 0.8%
        bar = sale_line(1, "علي", "10", "1000", gross="60000", item_type="سبيكة")
        scrap_sale = sale_line(1, "علي", "10", "1000", gross="60000", line_type="SELL_OLD_GOLD")
        flagged = k.flagged_lines([crafted, bar, scrap_sale], D("1.5"))
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["kind"], "ربح مصنعية قليل")
        self.assertEqual(k.employee_rows([bar, scrap_sale], D("1.5")), [{"employee": "علي", "lines": 0, "low_making": 0, "low_discount": 0, "status": k.GRAY,
                                                                          "status_text": k.STATUS_TEXT[k.GRAY], "note": k.employee_status(0, 0)[1]}])


NOW = datetime(2026, 10, 20, 12)


class DeadPiecesAndKpiTests(base.Case):
    """The alerts and the KPI list the dead pieces themselves, and the KPI carries the five new items."""
    permissions = frozenset(base.ALL | {"MANAGEMENT_REVIEW"})                              # the stocktake sheets (the dead-stock share) need the three position rights

    def service_with_old_stock(self):
        def piece(i, karat, weight, barcode, acquired, invoice=None, kind="خاتم"):
            return {"item_id": i, "weight_grams": D(weight), "size": "", "stock_classification": "NEW_STOCK", "inventory_status": "IN_STOCK", "acquisition_source": "MARKET_PURCHASE", "acquired_at": acquired,
                    "karat_id": 1, "karat_value": karat, "item_type": kind, "category": "B", "factory": "لوكس", "barcode": barcode, "invoice_id": invoice, "invoice_number": None, "invoice_line_type": None}

        class Repo(base.FakeRepo):
            def stock_items(self, statuses):
                return [piece(1, 21, "10.000", "OLD1", datetime(2025, 1, 1)), piece(2, 21, "5.000", "OLD2", datetime(2025, 3, 1)), piece(3, 21, "4.000", "NEWER", datetime(2026, 10, 1)),
                        piece(4, 21, "6.000", "SOLDBEFORE", datetime(2025, 1, 1), invoice=9), piece(5, 18, "7.000", "OLD18", datetime(2025, 2, 1), kind="دبلة")]
        return base.ReportService(self.session, transaction_factory=self.service._transaction, repository=Repo, authorization=self.authorization, today=lambda: base.TODAY)

    def test_the_alerts_list_every_dead_piece_with_its_barcode_type_weight_and_age(self):
        result = self.service_with_old_stock().run("alerts", {})
        table = next(t for t in result["tables"] if t["key"] == "dead_pieces")
        self.assertTrue(table["paged"])
        self.assertEqual([r["barcode"] for r in table["rows"]], ["OLD1", "OLD2", "OLD18"])      # type (خاتم before دبلة), factory, then heavier first; the recent and the previously sold piece are not dead
        first = table["rows"][0]
        self.assertEqual((first["type"], first["karat"], first["weight"], first["days"], first["since"]), ("خاتم", "عيار 21", D("10.000"), (base.TODAY - date(2025, 1, 1)).days, date(2025, 1, 1)))
        self.assertEqual([c["title"] for c in table["columns"]][:4], ["الباركود", "الصنف", "العيار", "الوزن (جرام)"])

    def test_searching_a_barcode_narrows_the_dead_pieces(self):
        result = self.service_with_old_stock().run("alerts", {"text": "OLD2"})
        table = next(t for t in result["tables"] if t["key"] == "dead_pieces")
        self.assertEqual([r["barcode"] for r in table["rows"]], ["OLD2"])

    def test_the_kpi_lists_the_dead_pieces_and_carries_the_early_items(self):
        base.FakeRepo.tables = {**base.FakeRepo.tables, "position_snapshots": True}
        result = self.service_with_old_stock().run("kpi", {"from": "2026-10-14", "to": "2026-10-20"})
        items = {r["key"]: r for r in result["tables"][0]["rows"]}
        for key in ("dead_stock", "making21", "market_loss", "stockout"):
            self.assertIn(key, items)
        self.assertNotIn("invoice_diff", items)                                                 # owner 2026-11-13: the temporary «فرق فواتير» category is retired, so is its indicator
        dead = items["dead_stock"]
        self.assertIn("21 جرام (مكافئ 21) • 3 قطعة", dead["compare"])                          # 10 + 5 g of 21K + 7 g of 18K (= 6 g of 21K): three dead pieces, 21 g equivalent
        table = next(t for t in result["tables"] if t["key"] == "kpi_dead")
        self.assertEqual(len(table["rows"]), 3)

    def test_without_the_inventory_right_the_dead_items_are_gray(self):
        limited = base.session_for(frozenset(set(base.ALL) - {"INVENTORY_VIEW"}))
        service = base.ReportService(limited, transaction_factory=self.service._transaction, repository=base.FakeRepo, authorization=base.FakeAuthorization(limited), today=lambda: base.TODAY)
        items = {r["key"]: r for r in service.run("kpi", {"from": "2026-10-14", "to": "2026-10-20"})["tables"][0]["rows"]}
        self.assertEqual(items["dead_stock"]["status"], k.STATUS_TEXT[k.GRAY])
        self.assertEqual(items["stockout"]["status"], k.STATUS_TEXT[k.GRAY])


class PieceAgeTests(web.ExtrasTests):
    """Scan a barcode: how long the piece has been in stock, where it came from and whether it is dead. (Inherits the fixture only; its own tests are not repeated.)"""

    def run(self, result=None):                                    # the inherited tests are not this class's job
        if self._testMethodName.startswith("test_piece_age"):
            return super().run(result)
        return None

    def lookup(self, piece, dead_days=180):
        class Inventory:
            def __init__(self, session=None):
                pass

            def find_piece(self, text):
                return piece

        class Reports:
            def __init__(self, session):
                pass

            def dead_stock_days(self, **kwargs):
                return dead_days
        with mock.patch("webapp.extras.InventoryService", Inventory), mock.patch("webapp.extras.ReportService", Reports):
            return self.extras.piece(self.token, {"text": "X"})["piece"]

    def base(self, **extra):
        today = date.today()
        return {"inventory_item_id": 7, "barcode_value": "05260124", "item_type_name": "خاتم", "karat_value": 21, "weight_grams": D("4.5"), "factory_name": "لوكس", "category_code": "B",
                "inventory_status": "IN_STOCK", "acquisition_source": "LEGACY_MIGRATION", "sale": None, "sales": [], **extra}

    def test_piece_age_a_piece_that_never_sold_and_is_old_is_dead(self):
        from datetime import timedelta
        found = self.lookup(self.base(acquired_at=datetime.combine(date.today() - timedelta(days=300), datetime.min.time())))
        self.assertEqual((found["daysInStock"], found["dead"], found["neverSold"], found["source"]), ("300", True, True, "من البرنامج القديم"))
        self.assertIn("في المخزون من 300 يوم", found["ageText"])

    def test_piece_age_a_recent_piece_or_one_that_came_back_from_a_sale_is_not_dead(self):
        from datetime import timedelta
        recent = self.lookup(self.base(acquired_at=datetime.combine(date.today() - timedelta(days=20), datetime.min.time())))
        self.assertEqual((recent["daysInStock"], recent["dead"]), ("20", False))
        came_back = self.lookup(self.base(acquired_at=datetime.combine(date.today() - timedelta(days=400), datetime.min.time()), sales=[{"invoice_id": 1}]))
        self.assertFalse(came_back["dead"])
        self.assertFalse(came_back["neverSold"])

    def test_piece_age_a_sold_piece_shows_how_long_it_was_in_stock_before_the_sale(self):
        found = self.lookup(self.base(inventory_status="SOLD", acquired_at=datetime(2026, 1, 1), sales=[{"invoice_id": 3}],
                                      sale={"invoice_id": 3, "invoice_number": "26", "invoice_date": datetime(2026, 4, 11), "customer_name": "أحمد"}))
        self.assertEqual(found["daysInStock"], "100")
        self.assertIn("كانت في المخزون 100 يوم قبل البيع", found["ageText"])
        self.assertFalse(found["dead"])

    def test_piece_age_a_missing_date_is_left_empty_never_guessed(self):
        found = self.lookup(self.base(acquired_at=None))
        self.assertEqual((found["acquiredAt"], found["daysInStock"], found["ageText"], found["dead"]), ("", "", "", False))


if __name__ == "__main__":
    unittest.main()
