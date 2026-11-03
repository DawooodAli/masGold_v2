"""The extra cards of the iPad app: today's figures, a customer statement, a piece lookup, the open reviews, the CSV download and the transfers card (the second write). Fakes only."""
from decimal import Decimal as D
from unittest import mock
import unittest

from services.correction_service import CorrectionError
from tests.test_webapp import ApiError, OWNER, make_api

PRIVATE = {"FINANCIAL_ACCOUNT_OPERATE", "PRIVATE_ACCOUNT_MANAGE"}


class FakeFinancial:
    calls = []
    fail = None

    def __init__(self, session):
        pass

    def accounts(self, *, _expected_actor=None):
        return {"accounts": [{"key": "CASH:1", "ledger": "CASH", "account_id": "1", "name": "الخزنة", "account_type": "CASH", "is_active": True, "balance": "10000.00"},
                             {"key": "BANK:3", "ledger": "BANK", "account_id": "3", "name": "محفظة المحل", "account_type": "MOBILE_WALLET", "is_active": True, "balance": "2500.50"},
                             {"key": "BANK:4", "ledger": "BANK", "account_id": "4", "name": "بنك مصر", "account_type": "BANK", "is_active": True, "balance": "800.00"},
                             {"key": "BANK:5", "ledger": "BANK", "account_id": "5", "name": "قديم", "account_type": "BANK", "is_active": False, "balance": "0.00"}],
                "can_operate": True, "can_correct": False}

    def private_account(self, *, _expected_actor=None):
        return {"available": True, "balance": "500.00", "total_in": "700.00", "total_out": "200.00",
                "entries": [{"date": "2026-11-01 10:00", "text": "إلى الحساب الخاص", "account": "بنك مصر", "amount": "700.00", "note": "", "voided": False, "balance": "700.00"}]}

    def transfer(self, source, destination, amount, note, key, *, _expected_actor=None):
        FakeFinancial.calls.append(("transfer", source, destination, amount, note, key))
        if FakeFinancial.fail:
            raise FakeFinancial.fail
        return {"operation_id": "9", "code": "TR000009", "duplicate": False}

    def private_transfer(self, account, direction, amount, note, key, *, _expected_actor=None):
        FakeFinancial.calls.append(("private", account, direction, amount, note, key))
        if FakeFinancial.fail:
            raise FakeFinancial.fail
        return {"operation_id": "10", "code": "MD000010", "duplicate": False}


from services.management_review_service import InspectionError


class ExtrasTests(unittest.TestCase):
    def setUp(self):
        FakeFinancial.calls, FakeFinancial.fail = [], None
        patcher = mock.patch("webapp.extras.FinancialAccountService", FakeFinancial)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.api = make_api(OWNER | PRIVATE)
        self.token = self.api.login("admin", "secret")
        self.extras = self.api.extras

    # ---- today
    def test_today_has_sales_balances_gold_and_prices_and_leaves_out_what_is_not_allowed(self):
        class Position:
            def __init__(self, session):
                pass

            def position(self, *, _expected_actor=None):
                return {"summary": {"total_eq21": D("5190.123")}}

        class Prices:
            def __init__(self, session):
                pass

            def snapshot(self):
                return {"status": "FRESH", "prices": [{"karat": "21", "shop_buy_price_per_gram": "6139.0000", "shop_sell_price_per_gram": "6163.0000"},
                                                      {"karat": "22", "shop_buy_price_per_gram": "1.0", "shop_sell_price_per_gram": "1.0"}]}
        with mock.patch("webapp.extras.GoldPositionService", Position), mock.patch("webapp.extras.GoldPriceService", Prices):
            data = self.extras.today(self.token)
        labels = [c["label"] for c in data["cards"]]
        self.assertIn("قيمة البيع اليوم", labels)
        self.assertIn("رصيد الخزنة", labels)
        self.assertIn("رصيد البنوك والمحافظ", labels)
        self.assertIn("الذهب في المحل (مكافئ 21)", labels)
        banks = next(c for c in data["cards"] if c["label"] == "رصيد البنوك والمحافظ")
        self.assertIn("3,300", banks["value"])                                         # only the ACTIVE bank / wallet accounts are added
        self.assertEqual(data["prices"], [{"karat": "21", "buy": "6,139", "sell": "6,163", "buyRaw": "6139.0000", "sellRaw": "6163.0000"}])
        self.assertEqual(data["priceStatus"], "FRESH")

        class Denied(Position):
            def position(self, *, _expected_actor=None):
                raise PermissionError("owner only")
        with mock.patch("webapp.extras.GoldPositionService", Denied), mock.patch("webapp.extras.GoldPriceService", Prices):
            self.assertNotIn("الذهب في المحل (مكافئ 21)", [c["label"] for c in self.extras.today(self.token)["cards"]])

    # ---- statement
    def test_a_customer_statement_is_shown_with_the_desktop_labels(self):
        rows = [{"row_kind": "L", "id": 5, "occurred_at": "2026-09-27 20:00:00", "source_type": "INVOICE", "source_reference": "26092700", "invoice_id": 21, "invoice_number": "26092700",
                 "amount_delta": D("1000.00"), "description": "متبقي فاتورة", "running_balance": D("1000.00"), "actor_name": "علي"}]

        class Account:
            def __init__(self, session):
                pass

            def statement(self, customer, start="", end="", source="", search="", page=1, *, _expected_actor=None):
                return {"customer": {"name": "أحمد", "customer_code": "C000017", "mobile": "0100", "balance": D("1000.00")}, "rows": rows, "has_more": False, "page": page}
        with mock.patch("webapp.extras.CustomerAccountService", Account):
            data = self.extras.statement(self.token, {"customer": "17", "page": "1"})
        self.assertEqual(data["customer"]["balance"], "على العميل: 1,000 ج")
        row = data["rows"][0]
        self.assertEqual((row["source"], row["invoiceId"], row["invoiceNumber"], row["balance"]), ("فاتورة", "21", "26092700", "على العميل: 1,000 ج"))
        with self.assertRaises(ApiError) as nobody:
            self.extras.statement("", {"customer": "17"})
        self.assertEqual(nobody.exception.status, 401)

    # ---- a piece
    def test_a_sold_piece_is_found_with_its_invoice_and_a_missing_one_is_not(self):
        class Inventory:
            def __init__(self, session=None):
                pass

            def find_piece(self, text):
                return {"inventory_item_id": 12919, "barcode_value": "EGG0074082", "item_type_name": "سبيكة", "karat_value": 24, "weight_grams": D("20"), "factory_name": "MB", "category_code": "Z",
                        "inventory_status": "SOLD", "sale": {"invoice_id": 21, "invoice_number": "26092700", "invoice_date": "2026-09-27", "customer_name": "أحمد"}} if text == "EGG0074082" else None
        with mock.patch("webapp.extras.InventoryService", Inventory):
            found = self.extras.piece(self.token, {"text": "EGG0074082"})["piece"]
            self.assertEqual((found["number"], found["invoiceId"], found["customer"]), ("#12919", "21", "أحمد"))
            self.assertFalse(self.extras.piece(self.token, {"text": "nope"})["found"])
            self.assertFalse(self.extras.piece(self.token, {"text": " "})["found"])
        limited = make_api({"APP_LOGIN", "REPORTS_VIEW"})
        with self.assertRaises(ApiError) as denied:
            limited.extras.piece(limited.login("admin", "secret"), {"text": "x"})
        self.assertEqual(denied.exception.status, 403)

    # ---- reviews
    def test_the_open_reviews_are_listed_read_only(self):
        class Reviews:
            def __init__(self, session):
                pass

            def can_resolve(self, actor=None):
                return True

            def open_reviews(self, *, _expected_actor=None):
                return {"MAKING_PRICE": [{"review_id": 1, "invoice_id": 21, "invoice_number": "26092700", "summary": "بيع بأقل من قيمة الذهب", "created_at": "2026-11-02", "created_by_name": "علي"}],
                        "CASH_SETTLEMENT": [], "INVENTORY_MATCH": [{"case_id": 3, "invoice_number": "26092701", "reason": "باركود تالف", "sold_at": "2026-11-01"}]}
        with mock.patch("webapp.extras.ManagementReviewService", Reviews):
            data = self.extras.reviews(self.token)
        self.assertEqual(data["total"], 2)
        self.assertEqual([s["key"] for s in data["sections"]], ["MAKING_PRICE", "INVENTORY_MATCH"])
        self.assertEqual(data["sections"][0]["rows"][0]["text"], "بيع بأقل من قيمة الذهب")
        self.assertEqual([r["reviewId"] for sec in data["sections"] for r in sec["rows"]], ["1", ""])     # only the simple reviews can be closed from the iPad

    def test_review_done_needs_the_password_and_reaches_the_service_with_the_session_actor(self):
        calls, outer = [], self

        class Reviews:
            def __init__(self, session):
                pass

            def mark_reviewed(self, review_id, *, _expected_actor=None):
                calls.append((review_id, _expected_actor.user_id))
                if outer.refuse:
                    raise outer.refuse
        self.refuse = None
        with mock.patch("webapp.extras.ManagementReviewService", Reviews):
            with self.assertRaises(ApiError) as wrong:
                self.extras.review_done(self.token, {"review_id": "5", "password": "bad"})
            self.assertEqual((wrong.exception.status, calls), (400, []))
            self.assertEqual(self.extras.review_done(self.token, {"review_id": "5", "password": "secret", "user_id": "999"}), {"ok": True})
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "5")
            self.assertNotEqual(calls[0][1], "999")
            self.refuse = InspectionError("REVIEW_ALREADY_RESOLVED", "تمت مراجعة هذا البند من قبل.")
            with self.assertRaises(ApiError) as again:
                self.extras.review_done(self.token, {"review_id": "5", "password": "secret"})
            self.assertEqual(again.exception.status, 409)

    # ---- csv
    def test_a_report_downloads_as_csv_with_every_row(self):
        name, data = self.extras.export(self.token, {"key": "sales", "from": "2026-10-01", "to": "2026-10-31"})
        self.assertTrue(name.endswith(".csv") and name.startswith("sales-"))
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))                              # UTF-8 with BOM for Excel
        with self.assertRaises(ApiError):
            self.extras.export(self.token, {"key": "nope"})

    # ---- transfers
    def test_the_transfers_card_lists_only_active_bank_and_wallet_accounts_and_the_private_account(self):
        data = self.extras.transfers_overview(self.token)
        self.assertEqual([a["key"] for a in data["accounts"]], ["BANK:3", "BANK:4"])        # never the main safe, never an inactive account
        self.assertTrue(data["canOperate"] and data["canPrivate"])
        self.assertIn("500", data["private"]["balance"])
        plain = make_api(OWNER | {"FINANCIAL_ACCOUNT_OPERATE"})
        self.assertFalse(plain.extras.transfers_overview(plain.login("admin", "secret"))["canPrivate"])

    def body(self, **kw):
        values = {"kind": "BETWEEN", "from": "BANK:3", "to": "BANK:4", "amount": "100", "note": "n", "password": "secret", "request_key": "11111111-1111-4111-8111-111111111111"}
        values.update(kw)
        return values

    def test_a_transfer_needs_the_current_password_and_goes_through_the_service(self):
        result = self.extras.transfer(self.token, self.body())
        self.assertEqual(result["code"], "TR000009")
        self.assertEqual(FakeFinancial.calls, [("transfer", "BANK:3", "BANK:4", "100", "n", "11111111-1111-4111-8111-111111111111")])
        self.extras.transfer(self.token, self.body(kind="TO_PRIVATE", account="BANK:4"))
        self.extras.transfer(self.token, self.body(kind="FROM_PRIVATE", account="BANK:3"))
        self.assertEqual([c[:3] for c in FakeFinancial.calls[1:]], [("private", "BANK:4", "TO"), ("private", "BANK:3", "FROM")])
        self.assertEqual([p for _, p in self.api._authentication.verified], ["secret"] * 3)

    def test_a_wrong_password_the_main_safe_an_unknown_kind_and_service_refusals_move_nothing(self):
        with self.assertRaises(ApiError) as wrong:
            self.extras.transfer(self.token, self.body(password="bad"))
        self.assertEqual((wrong.exception.status, FakeFinancial.calls), (400, []))
        for bad in (self.body(**{"from": "CASH:1"}), self.body(**{"to": "BANK:5"}), self.body(kind="NOPE"), self.body(kind="TO_PRIVATE", account="CASH:1")):
            with self.assertRaises(ApiError) as refused:
                self.extras.transfer(self.token, bad)
            self.assertEqual(refused.exception.status, 400)
        self.assertEqual(FakeFinancial.calls, [])
        for failure, status in ((CorrectionError("AMOUNT_INVALID", "اكتب المبلغ بالأرقام."), 400), (CorrectionError("REQUEST_CONFLICT", "طلب مختلف"), 409),
                                (PermissionError("لا توجد صلاحية"), 403), (RuntimeError("internal detail"), 500)):
            FakeFinancial.fail = failure
            with self.assertRaises(ApiError) as caught:
                self.extras.transfer(self.token, self.body())
            self.assertEqual(caught.exception.status, status)
            if status == 500:
                self.assertNotIn("internal", caught.exception.message)
        with self.assertRaises(ApiError) as nobody:
            self.extras.transfer("", self.body())
        self.assertEqual(nobody.exception.status, 401)


class LegacyBalanceTests(unittest.TestCase):
    """رصيد قديم from the iPad: the owner's right, the current password, then the customer account service."""

    def setUp(self):
        self.calls, self.fail = [], None
        outer = self

        class Account:
            def __init__(self, session):
                pass

            def add_legacy_balance(self, customer, direction, amount, note, day, key, *, _expected_actor=None):
                outer.calls.append((customer, direction, amount, note, day, key))
                if outer.fail:
                    raise outer.fail
                return {"id": "9", "balance_before": "500.00", "balance_after": "2000.00", "duplicate": False}
        patcher = mock.patch("webapp.extras.CustomerAccountService", Account)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.api = make_api(OWNER | {"CUSTOMER_BALANCE_ADJUST"})
        self.token = self.api.login("admin", "secret")

    def body(self, **kw):
        values = {"customer": "17", "direction": "OWES", "amount": "1500", "note": "دفتر ورقي", "old_date": "2023-05-10", "password": "secret", "request_key": "11111111-1111-4111-8111-111111111111"}
        values.update(kw)
        return values

    def test_the_owner_adds_an_old_balance_after_the_password_and_the_page_never_supplies_identity(self):
        result = self.api.extras.legacy_balance(self.token, self.body(user_id="999"))
        self.assertEqual((result["balanceAfter"], result["duplicate"]), ("على العميل: 2,000 ج", False))
        self.assertEqual(self.calls, [("17", "OWES", "1500", "دفتر ورقي", "2023-05-10", "11111111-1111-4111-8111-111111111111")])
        self.assertTrue(self.api.overview(self.token)["canLegacy"])

    def test_a_wrong_password_a_missing_right_and_service_refusals_post_nothing(self):
        with self.assertRaises(ApiError) as wrong:
            self.api.extras.legacy_balance(self.token, self.body(password="bad"))
        self.assertEqual((wrong.exception.status, self.calls), (400, []))
        plain = make_api(OWNER)
        self.assertFalse(plain.overview(plain.login("admin", "secret"))["canLegacy"])
        with self.assertRaises(ApiError) as denied:
            plain.extras.legacy_balance(plain.login("admin", "secret"), self.body())
        self.assertEqual(denied.exception.status, 403)
        for failure, status in ((CorrectionError("NOTE_REQUIRED", "اكتب البيان"), 400), (CorrectionError("REQUEST_CONFLICT", "طلب مختلف"), 409), (PermissionError("للمالك فقط"), 403),
                                (ValueError("عميل غير موجود"), 400), (RuntimeError("internal detail"), 500)):
            self.fail = failure
            with self.assertRaises(ApiError) as caught:
                self.api.extras.legacy_balance(self.token, self.body())
            self.assertEqual(caught.exception.status, status)
            if status == 500:
                self.assertNotIn("internal", caught.exception.message)
        with self.assertRaises(ApiError) as nobody:
            self.api.extras.legacy_balance("", self.body())
        self.assertEqual(nobody.exception.status, 401)


if __name__ == "__main__":
    unittest.main()


class SettleAttentionActivityTests(unittest.TestCase):
    """تسوية حساب from the iPad, the attention list and the owner's activity list (fakes only)."""

    def setUp(self):
        self.calls, outer = [], self

        class Account:
            def __init__(self, session):
                pass

            def statement(self, customer, *args, _expected_actor=None):
                return {"customer": {"name": "عميل", "balance": "500.00"}, "rows": [], "has_more": False, "page": 1, "can_settle": True,
                        "accounts": [{"id": 4, "name": "فودافون", "account_type": "MOBILE_WALLET"}]}

            def finalize(self, customer, payments, key, note, *, _expected_actor=None):
                outer.calls.append((customer, [(p.method_code, p.amount, p.direction, p.bank_account_id) for p in payments], key, note))
                return {"id": "7", "code": "CS000007", "balance_after": "200.00"}

        class Reviews:
            def __init__(self, session):
                pass

            def open_reviews(self, *, _expected_actor=None):
                return {"MAKING_PRICE": [{"review_id": 1}], "CASH_SETTLEMENT": [{"review_id": 2}], "INVENTORY_MATCH": []}

        class Audit:
            def __init__(self, session):
                pass

            def recent(self, text, *, _expected_actor=None):
                return [{"occurred_at": "2026-11-03 10:00:00", "user_name": "علي", "event_type": "X", "entity_reference": "CS1", "description": text or "d"}]
        for name, fake in (("CustomerAccountService", Account), ("ManagementReviewService", Reviews), ("AuditViewService", Audit)):
            patcher = mock.patch("webapp.extras." + name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = make_api(OWNER | {"CUSTOMER_ACCOUNT_SETTLE", "USERS_MANAGE", "MANAGEMENT_REVIEW"})
        self.token = self.api.login("admin", "secret")

    def body(self, **kw):
        values = {"customer": "17", "account": "", "amount": "300", "note": "n", "password": "secret", "request_key": "11111111-1111-4111-8111-111111111111"}
        values.update(kw)
        return values

    def test_settlement_is_one_leg_with_the_direction_of_the_balance_after_the_password(self):
        result = self.api.extras.settle(self.token, self.body())
        self.assertEqual(result["code"], "CS000007")
        self.assertEqual(self.calls[0][1], [("PHYSICAL_CASH", "300", "IN", None)])
        self.api.extras.settle(self.token, self.body(account="4"))
        self.assertEqual(self.calls[1][1], [("MOBILE_WALLET", "300", "IN", "4")])
        with self.assertRaises(ApiError) as bad:
            self.api.extras.settle(self.token, self.body(account="99"))
        self.assertEqual(bad.exception.status, 400)
        with self.assertRaises(ApiError) as wrong:
            self.api.extras.settle(self.token, self.body(password="bad"))
        self.assertEqual((wrong.exception.status, len(self.calls)), (400, 2))
        plain = make_api(OWNER)
        with self.assertRaises(ApiError) as denied:
            plain.extras.settle(plain.login("admin", "secret"), self.body())
        self.assertEqual(denied.exception.status, 403)

    def test_the_statement_offers_the_settlement_form(self):
        data = self.api.extras.statement(self.token, {"customer": "17"})
        self.assertEqual((data["settle"]["can"], data["settle"]["direction"], data["settle"]["accounts"]), (True, "IN", [{"id": "4", "name": "فودافون"}]))

    def test_attention_counts_the_open_reviews(self):
        data = self.api.extras.attention(self.token)
        self.assertEqual(data["reviews"], 2)
        self.assertEqual(data["items"][0]["module"], "reviews")

    def test_activity_lists_rows_for_the_owner_and_refuses_others(self):
        data = self.api.extras.activity(self.token, {"text": "CS"})
        self.assertEqual(data["rows"][0]["user"], "علي")
        with mock.patch("webapp.extras.AuditViewService.recent", side_effect=PermissionError("للمالك فقط")):
            with self.assertRaises(ApiError) as denied:
                self.api.extras.activity(self.token, {})
        self.assertEqual(denied.exception.status, 403)
