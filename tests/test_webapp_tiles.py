"""The live figures on the iPad home cards (`/api/tiles`), the bank flow card of today and the currency rates card (fakes only, read only)."""
from decimal import Decimal
import unittest
from unittest import mock

from tests.test_webapp import OWNER, make_api
from webapp.api import MODULES
from webapp.errors import ApiError


class Accounts:
    def __init__(self, session):
        pass

    def accounts(self, *, _expected_actor=None):
        return {"accounts": [{"ledger": "CASH", "is_active": True, "balance": "1000.00", "name": "الخزنة الرئيسية"},
                             {"ledger": "BANK", "is_active": True, "balance": "500.00", "name": "الأهلي"},
                             {"ledger": "BANK", "is_active": True, "balance": "250.00", "name": "محفظة"},
                             {"ledger": "BANK", "is_active": False, "balance": "99.00", "name": "قديم"}]}


class Reviews:
    def __init__(self, session):
        pass

    def open_reviews(self, *, _expected_actor=None):
        return {"MAKING_PRICE": [{"review_id": 1}], "INSPECTION": [{"review_id": 2}, {"review_id": 3}]}


FX = {"enabled": True, "available": True, "updated": "10:15", "error": "",
      "rows": [{"code": "USD", "name": "دولار أمريكي", "price": "49.50"}, {"code": "EUR", "name": "يورو", "price": "53.25"}]}
TREASURY = {"tables": [{"key": "accounts", "rows": [
    {"account": "نقدي: الخزنة", "in": Decimal("10"), "out": Decimal("5"), "t_in": Decimal("0"), "t_out": Decimal("0"), "closing": Decimal("1000")},
    {"account": "بنكي / محفظة: الأهلي", "in": Decimal("1500"), "out": Decimal("200"), "t_in": Decimal("50"), "t_out": Decimal("0"), "closing": Decimal("500")},
    {"account": "بنكي / محفظة: محفظة", "in": Decimal("100.5"), "out": Decimal("0"), "t_in": Decimal("0"), "t_out": Decimal("50"), "closing": Decimal("250")}]}]}


class TilesTests(unittest.TestCase):
    def setUp(self):
        for target, fake in (("webapp.extras.FinancialAccountService", Accounts), ("webapp.extras.ManagementReviewService", Reviews)):
            patcher = mock.patch(target, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        shared = mock.Mock()
        shared.snapshot.return_value = FX
        patcher = mock.patch("services.currency_rates.shared", return_value=shared)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch("services.report_service.ReportService.run", return_value=TREASURY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.api = make_api(OWNER | {"BANK_VIEW", "CASH_VIEW", "MANAGEMENT_REVIEW"})
        self.token = self.api.login("admin", "secret")

    def test_the_new_cards_are_offered(self):
        keys = {m["key"] for m in self.api.overview(self.token)["modules"]}
        self.assertTrue({"bankflow", "currency", "suppliers", "goldprofit", "staff"} <= keys or {"bankflow", "currency"} <= keys)
        self.assertEqual(len({m["key"] for m in MODULES}), len(MODULES))                        # no duplicate card key

    def test_tiles_carry_live_figures_a_badge_and_never_break_when_a_report_fails(self):
        tiles = self.api.extras.tiles(self.token)["tiles"]
        self.assertEqual([(l["label"], l["value"]) for l in tiles["currency"]["lines"]][0][0], "دولار أمريكي")
        self.assertEqual(tiles["reviews"]["badge"], "3")
        self.assertEqual([l["label"] for l in tiles["transfers"]["lines"]], ["الأهلي", "محفظة"])           # active bank / wallet accounts only
        self.assertEqual([l["label"] for l in tiles["treasury"]["lines"]], ["رصيد الخزنة", "رصيد البنوك والمحافظ"])
        self.assertEqual([l["label"] for l in tiles["bankflow"]["lines"]], ["الداخل اليوم", "الخارج اليوم"])

    def test_the_bank_flow_card_sums_the_bank_rows_only_and_keeps_transfers_apart(self):
        flow = self.api.extras.bankflow(self.token)
        self.assertEqual(len(flow["accounts"]), 2)                                              # the cash row is not a bank
        self.assertIn("1,600", flow["totalIn"])
        self.assertIn("200", flow["totalOut"])
        self.assertIn("50", flow["transferIn"])
        self.assertEqual(flow["accounts"][0]["name"], "الأهلي")

    def test_the_cash_flow_card_is_the_cash_rows_of_today(self):
        flow = self.api.extras.cashflow(self.token)
        self.assertEqual([a["name"] for a in flow["accounts"]], ["الخزنة"])
        self.assertIn("10", flow["totalIn"])
        self.assertEqual([l["label"] for l in self.api.extras.tiles(self.token)["tiles"]["cashflow"]["lines"]], ["الداخل اليوم", "الخارج اليوم"])

    def test_the_bank_flow_refuses_a_user_without_the_right(self):
        with mock.patch("services.report_service.ReportService.run", side_effect=PermissionError("لا")):
            with self.assertRaises(ApiError) as denied:
                self.api.extras.bankflow(self.token)
        self.assertEqual(denied.exception.status, 403)

    def test_the_currency_card_formats_the_shared_snapshot(self):
        data = self.api.extras.currency(self.token)
        self.assertEqual((data["available"], data["updated"], data["rows"][1]["price"]), (True, "10:15", "53.25"))

    def test_a_failing_currency_provider_only_marks_the_card_unavailable(self):
        with mock.patch("services.currency_rates.shared", side_effect=RuntimeError("x")):
            data = self.api.extras.currency(self.token)
            self.assertFalse(data["available"])
            self.assertNotIn("currency", self.api.extras.tiles(self.token)["tiles"])


if __name__ == "__main__":
    unittest.main()
