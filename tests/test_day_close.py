"""تسليم وإقفال اليوم: the end-of-day cash count record. In-memory fakes, no database; plus real clicks at every target size."""
import unittest
from contextlib import contextmanager
from datetime import date
from decimal import Decimal as D
from uuid import uuid4

from PySide6.QtTest import QTest

from services.day_close_service import DayCloseError, DayCloseService
from tests import test_invoice_page_qml as harness
from tests.auth_fakes import FakeAuthorization
from tests.test_inventory_stock import session_for

CLOSER = frozenset({"APP_LOGIN", "DAY_CLOSE"})


class Book:
    def __init__(self, cash="1000.00"):
        self.closes, self.audits = [], []
        self.cash = [{"id": 1, "name": "الخزنة الرئيسية", "balance": D(cash)}]
        self.bank = [{"id": 2, "name": "إنستا باي", "balance": D("250.50")}]
        self.drafts = 2


class Repo:
    def __init__(self, connection):
        self.book = connection.book

    def has_schema(self):
        return True

    def cash_accounts(self):
        return self.book.cash

    def bank_accounts(self):
        return self.book.bank

    def draft_invoices(self):
        return self.book.drafts

    def by_key(self, key):
        row = next((c for c in self.book.closes if c["key"] == key), None)
        return None if row is None else {"id": row["id"], "counted_cash": row["counted"], "expected_cash": row["expected"], "closed_by_user_id": row["user"], "business_date": row["date"]}

    def insert(self, *, business_date, expected, counted, difference, reason, drafts, balances, note, request_key, user_id):
        self.book.closes.append({"id": str(len(self.book.closes) + 1), "date": business_date, "expected": expected, "counted": counted, "difference": difference, "reason": reason,
                                 "drafts": drafts, "balances": balances, "note": note, "key": request_key, "user": user_id})
        return self.book.closes[-1]["id"]

    def audit(self, **values):
        self.book.audits.append(values)

    def recent(self):
        return [{"id": c["id"], "business_date": c["date"], "expected_cash": c["expected"], "counted_cash": c["counted"], "difference": c["difference"], "difference_reason": c["reason"],
                 "draft_invoices": c["drafts"], "note": c["note"], "closed_at": "2026-11-08 22:00:00", "username": "admin"} for c in reversed(self.book.closes)]


def service_for(book, permissions=CLOSER):
    @contextmanager
    def transaction():
        yield type("C", (), {"book": book})()
    session = session_for(permissions, 9)
    return DayCloseService(session, transaction_factory=transaction, repository=Repo, authorization=FakeAuthorization(session), today=lambda: date(2026, 11, 8))


class DayCloseTests(unittest.TestCase):
    def setUp(self):
        self.book = Book()
        self.service = service_for(self.book)

    def test_overview_shows_expected_cash_only_from_cash_accounts_and_the_drafts(self):
        overview = self.service.overview()
        self.assertEqual((overview["expected"], overview["drafts"], overview["date"]), ("1000.00", 2, "2026-11-08"))
        self.assertEqual(overview["bank"][0]["balance"], "250.50")             # bank is shown, never counted in the cash

    def test_a_matching_count_closes_without_a_reason(self):
        result = self.service.close("1000", "", "", str(uuid4()))
        self.assertEqual((result["difference"], result["replayed"]), ("0.00", False))
        self.assertEqual(self.book.closes[0]["reason"], None)
        self.assertEqual(self.book.audits[0]["metadata"]["drafts"], 2)

    def test_a_difference_needs_a_reason_and_is_derived_not_typed(self):
        with self.assertRaises(DayCloseError) as caught:
            self.service.close("980.50", "", "", str(uuid4()))
        self.assertEqual(caught.exception.code, "REASON_REQUIRED")
        self.assertEqual(self.book.closes, [])
        result = self.service.close("980.50", "مصروف ضيافة بدون تسجيل", "", str(uuid4()))
        self.assertEqual(result["difference"], "-19.50")
        self.assertEqual(self.book.closes[0]["difference"], D("-19.50"))

    def test_amount_validation(self):
        for bad in ("", "abc", "-5", "10.005", "NaN"):
            with self.assertRaises(DayCloseError) as caught:
                self.service.close(bad, "x", "", str(uuid4()))
            self.assertEqual(caught.exception.code, "AMOUNT_INVALID")
        with self.assertRaises(DayCloseError):
            self.service.close("1000", "", "", "not-a-key")

    def test_an_exact_retry_replays_and_a_different_count_with_the_same_key_is_refused(self):
        key = str(uuid4())
        self.service.close("1000", "", "", key)
        again = self.service.close("1000", "", "", key)
        self.assertTrue(again["replayed"])
        self.assertEqual(len(self.book.closes), 1)
        with self.assertRaises(DayCloseError) as caught:
            self.service.close("900", "x", "", key)
        self.assertEqual(caught.exception.code, "REQUEST_CONFLICT")

    def test_the_right_is_required_for_both_reading_and_closing(self):
        nobody = service_for(self.book, frozenset({"APP_LOGIN"}))
        self.assertFalse(nobody.can_close())
        with self.assertRaises(PermissionError):
            nobody.overview()
        with self.assertRaises(PermissionError):
            nobody.close("1000", "", "", str(uuid4()))
        self.assertTrue(self.service.can_close())


class ScreenTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        self.book = Book()

        @contextmanager
        def transaction():
            yield type("C", (), {"book": self.book})()
        self.grant("DAY_CLOSE", "CASH_VIEW")
        session = self.controller.session
        service = DayCloseService(session, transaction_factory=transaction, repository=Repo, authorization=FakeAuthorization(session), today=lambda: date(2026, 11, 8))
        self.close_vm = self.controller.operational_viewmodels["dayCloseVM"]
        self.close_vm._service = service
        self.close_vm.changed.emit()

    def test_the_cashier_counts_and_closes_with_real_clicks_at_all_sizes(self):
        self.open_settings_dialog(4, "cashDayClose", "dayCloseDialog")
        self.wait_until(lambda: self.close_vm.expectedText.startswith("1,000.00"), "expected cash loaded")
        self.assertFalse(self.item("dcSave").property("enabled"))
        self.type_into("dcCounted", "990")
        self.wait_until(lambda: "عجز" in str(self.item("dcDifference").property("text")), "the live difference shows")
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            self.scroll_dialog_to("dayCloseDialog", "dcSave")
            self.assert_inside_window(self.item("dcSave"))
        self.click("dcSave")
        self.wait_until(lambda: "اكتب سببه" in str(self.item("dcMessage").property("text")), "a missing reason is refused")
        self.assertEqual(self.book.closes, [])
        self.type_into("dcReason", "مصروف ضيافة")
        self.click("dcSave")
        self.wait_until(lambda: len(self.book.closes) == 1, "closed")
        self.assertEqual((self.book.closes[0]["difference"], self.book.closes[0]["reason"]), (D("-10.00"), "مصروف ضيافة"))
        self.assertEqual(self.warnings, [])


for _cls in (ScreenTests,):
    for _name in dir(harness.InvoicePageTests):
        if _name.startswith("test_") and _name not in _cls.__dict__:
            setattr(_cls, _name, None)


if __name__ == "__main__":
    unittest.main()
