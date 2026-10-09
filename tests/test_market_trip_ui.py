"""Market trips on the real Main.qml: the OWNER-only navigation, the issue / return / close / reopen / reverse flow with real mouse
clicks at the three target sizes, retained input on refusal, no double submission, and the post-commit refresh of the money views
(top-bar balances, wallet / bank balances, الحسابات المالية, the Scrap consumers) without the periodic timer. The services are the
real ones over the in-memory database; the balance services are counting stand-ins that re-read the same ledgers."""
import time
from decimal import Decimal as D
from unittest.mock import patch
import unittest

from PySide6.QtCore import QMetaObject, QObject, QPointF, Qt
from PySide6.QtTest import QTest

from app.formatting import money
from tests import test_invoice_page_qml as harness
from tests.market_trip_fakes import AccountsRepo, Corrections, Customers, Invoices, OWNER, ScrapRepo, TripRepo, build

TODAY = "2026-10-10"


class Ledgers:
    """Balance services reading the fake database's ledgers; every read is counted."""

    def __init__(self, db):
        self.db = db
        self.strip_calls = self.account_calls = self.view_calls = self.scrap_calls = 0
        self.slow_first_strip = 0.0

    def can_view(self):
        return True

    def strip(self, **kwargs):
        self.strip_calls += 1
        value, first = self.db.cash(), self.strip_calls == 1
        if first and self.slow_first_strip:
            time.sleep(self.slow_first_strip)
        return {"cash_balance": str(value), "old_gold": []}

    def financial_accounts(self, **kwargs):
        self.account_calls += 1
        return []

    def cash_view(self, start="", end="", label="", **kwargs):
        self.view_calls += 1
        return {"account": {"name": "Main Safe", "balance": str(self.db.cash())}}

    def get_balances(self):
        self.scrap_calls += 1
        return []


class MarketTripPageTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        vms = self.controller.operational_viewmodels
        self.tvm, self.balances, self.reviews, self.accounts = vms["marketTripVM"], vms["balancesVM"], vms["reviewsVM"], vms["financialAccountsVM"]
        self.conversion = vms["oldGoldConversionVM"]
        session = self.controller.session
        self.db, _, self.authorization, service, _ = build(session=session)
        self.tvm._service = service
        for target, value in (("repositories.market_trip_repository.MarketTripRepository", TripRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)
        self.ledgers = Ledgers(self.db)
        self.balances._timer.stop()                                              # the periodic refresh must play no part
        self.balances._service = self.ledgers
        self.reviews._balance_service = self.ledgers
        self.accounts._service = self.ledgers
        self.conversion._service = self.ledgers

    # ------------------------------------------------------------ helpers
    def owner(self):
        self.grant(*OWNER)
        self.tvm.changed.emit()
        QTest.qWait(60)

    def open_page(self):
        self.owner()
        self.root.setProperty("currentPage", 15)
        QTest.qWait(250)
        self.wait_idle()

    def wait_idle(self):
        self.wait_until(lambda: not self.tvm.busy, "trip view model idle")
        self.wait_until(lambda: not self.balances._workers and not self.reviews._workers and not self.accounts._workers, "refreshes done")
        QTest.qWait(80)

    def type_in(self, name, text):
        self.click(name)
        self.type_text(text)
        QTest.qWait(50)

    def pick(self, name, index):
        combo = self.item(name)
        self.click(combo)
        QTest.qWait(80)
        for child in combo.findChildren(QObject):
            if child.metaObject().indexOfMethod("close()") >= 0 and child.property("visible"):
                QMetaObject.invokeMethod(child, "close")
                QTest.qWait(150)
        combo.setProperty("currentIndex", index)
        combo.activated[int].emit(index)
        QTest.qWait(150)

    def closed(self, name):
        self.wait_until(lambda: not self.item(name).property("visible"), f"{name} closed")

    def counts(self):
        return (self.ledgers.strip_calls, self.ledgers.account_calls, self.ledgers.scrap_calls)

    def new_trip(self, cash="10000", gold=None):
        """Create a trip through the dialog with real clicks; gold is [(combo index, weight)]."""
        self.click("newTripButton")
        self.wait_until(lambda: self.item("tripEntryDialog").property("opened"), "entry dialog")
        self.wait_until(lambda: self.tvm.context.get("karats"), "context")
        self.type_in("tripCashField", cash)
        for i, (index, weight) in enumerate(gold or []):
            if i:
                self.click("addGoldRowButton")
            self.pick(f"tripGoldKarat_{i}", index)
            self.type_in(f"tripGoldWeight_{i}", weight)
        self.click("confirmTripEntryButton")
        self.wait_until(lambda: len(self.db.trips) >= 1, "trip created")
        self.closed("tripEntryDialog")
        self.wait_idle()

    def visible_texts(self, name, parent=None):
        parent = parent or self.root.contentItem()
        found = [str(parent.property("text"))] if parent.objectName() == name and parent.property("visible") else []
        for child in parent.childItems():
            found += self.visible_texts(name, child)
        return found

    # ------------------------------------------------------------ tests
    def test_only_the_owner_sees_the_navigation_and_the_page(self):
        self.assertIsNone(self.visible_item("marketTripsNavButton"))
        self.owner()
        self.assertIsNotNone(self.visible_item("marketTripsNavButton"))
        self.grant(revoke=("MARKET_TRIP_MANAGE",))
        self.tvm.changed.emit()
        QTest.qWait(80)
        self.assertIsNone(self.visible_item("marketTripsNavButton"))
        self.assertEqual(self.warnings, [])

    def test_the_whole_flow_with_real_clicks_at_1080(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.open_page()
        self.assert_inside_window(self.item("newTripButton"))
        before = self.counts()
        self.new_trip("10000", [(2, "20.000"), (3, "5.500")])                      # entry 1: 21K and 18K (entries: placeholder, 24K, 21K, 18K)
        self.assertEqual(self.counts()[0], before[0] + 1)                           # the money views reloaded once, no timer
        self.assertEqual(self.balances.cashText, money(D("40000.00")))
        self.assertEqual((self.db.cash(), self.db.physical(1), self.db.physical(2)), (D("40000.00"), D("80.000"), D("44.500")))
        self.wait_until(lambda: self.tvm.detail.get("id"), "trip selected")
        self.assertEqual(self.text("tripTitle").replace("⁦", "").replace("⁩", ""), "عهدة MT000001")
        self.assertIn("إرجاع كل النقدية وكل الذهب", self.text("tripLimitation"))                  # the limitation is explained
        outstanding = " | ".join(self.visible_texts("figureOutstanding"))
        for part in ("10,000.00 ج", "20.000 جرام", "5.500 جرام"):                                 # custody per karat, apart from the shop
            self.assertIn(part, outstanding)
        self.assertFalse(self.item("closeTripButton").property("enabled"))                        # outstanding custody
        self.assertIn("لا يمكن الإغلاق", self.text("closeHint"))
        self.assertIn("10,000.00", self.text("closeHint"))
        self.assertTrue(self.text("shopCash").startswith("الخزنة الرئيسية: "))                    # shop balance apart from custody
        self.assertEqual(self.text("tripMessage"), "تم إخراج العهدة MT000001-1")
        # ---- a partial return
        self.click("returnButton")
        self.wait_until(lambda: self.item("tripEntryDialog").property("opened"), "return dialog")
        self.type_in("tripCashField", "3000")
        for name in ("confirmTripEntryButton", "cancelTripEntryButton", "tripCashField", "tripDateField", "tripNotesField", "addGoldRowButton"):
            self.assert_inside_window(self.item(name))
        self.click("confirmTripEntryButton")
        self.wait_until(lambda: len(self.db.entries) == 2, "return posted")
        self.closed("tripEntryDialog")
        self.wait_idle()
        self.assertEqual(self.db.cash(), D("43000.00"))
        self.assertEqual(self.balances.cashText, money(D("43000.00")))
        self.assertEqual(self.warnings, [])

    def test_a_refusal_keeps_every_field_and_a_double_click_posts_once(self):
        self.open_page()
        self.click("newTripButton")
        self.wait_until(lambda: self.item("tripEntryDialog").property("opened"), "entry dialog")
        self.wait_until(lambda: self.tvm.context.get("karats"), "context")
        self.pick("tripGoldKarat_0", 1)                                                    # 24K: no Scrap at all
        self.type_in("tripGoldWeight_0", "5")
        self.type_in("tripCashField", "100")
        self.type_in("tripNotesField", "ملاحظة")
        self.click("confirmTripEntryButton")
        self.wait_until(lambda: self.tvm.errorMessage != "", "refusal")
        QTest.qWait(100)
        self.assertTrue(self.item("tripEntryDialog").property("visible"))
        self.assertIn("غير كافٍ", self.text("tripEntryError"))
        self.assertEqual((self.text("tripCashField"), self.text("tripGoldWeight_0"), self.text("tripNotesField")), ("100", "5", "ملاحظة"))
        self.assertEqual((self.db.trips, self.db.entries), ({}, {}))
        self.pick("tripGoldKarat_0", 2)                                                    # 21K has grams: the corrected retry
        button = self.item("confirmTripEntryButton")
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        self.wait_until(lambda: len(self.db.entries) >= 1, "entry posted")
        self.closed("tripEntryDialog")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual((len(self.db.trips), len(self.db.entries), self.db.cash()), (1, 1, D("49900.00")))

    def test_close_reopen_and_reversal_through_the_dialogs(self):
        self.open_page()
        self.new_trip("500", [])
        self.click("returnButton")
        self.wait_until(lambda: self.item("tripEntryDialog").property("opened"), "return dialog")
        self.type_in("tripCashField", "500")
        self.click("confirmTripEntryButton")
        self.wait_until(lambda: len(self.db.entries) == 2, "full return")
        self.closed("tripEntryDialog")
        self.wait_idle()
        self.assertTrue(self.item("closeTripButton").property("enabled"))
        self.assertIn("يمكن إغلاق", self.text("closeHint"))
        self.click("closeTripButton")
        self.wait_until(lambda: self.db.trips[1]["status"] == "CLOSED", "closed")
        self.wait_idle()
        self.assertEqual(self.text("tripStatus"), "مغلقة")
        self.assertIsNone(self.visible_item("issueMoreButton"))                              # a closed trip offers no entry action
        self.assertIsNone(self.visible_item("reverseEntryButton"))
        self.click("reopenTripButton")
        self.wait_until(lambda: self.item("tripReasonDialog").property("opened"), "reason dialog")
        self.assertFalse(self.item("confirmTripReasonButton").property("enabled"))           # a reason is mandatory
        self.type_in("tripReasonField", "مراجعة")
        self.click("confirmTripReasonButton")
        self.wait_until(lambda: self.db.trips[1]["status"] == "OPEN", "reopened")
        self.closed("tripReasonDialog")
        self.wait_idle()
        self.assertIsNotNone(self.visible_item("reverseEntryButton"))
        before = self.counts()
        buttons = []

        def collect(node):
            if node.objectName() == "reverseEntryButton" and node.property("visible"):
                buttons.append(node)
            for child in node.childItems():
                collect(child)
        collect(self.root.contentItem())
        self.assertEqual(len(buttons), 2)
        self.click(buttons[-1])                                                           # the return (newest entry)
        self.wait_until(lambda: self.item("tripReasonDialog").property("opened"), "reversal dialog")
        self.type_in("tripReasonField", "مرتجع بالخطأ")
        self.click("confirmTripReasonButton")
        self.wait_until(lambda: len(self.db.corrections) == 1, "reversed")
        self.closed("tripReasonDialog")
        self.wait_idle()
        self.assertEqual(self.db.cash(), D("49500.00"))
        self.assertGreater(self.counts()[0], before[0])                                     # the money views reloaded
        self.assertIn("معكوسة بالعملية RV000001", " ".join(self.visible_texts("entryState")))
        self.assertEqual(self.warnings, [])

    def test_every_money_view_reloads_once_after_a_commit_and_never_after_a_refusal(self):
        self.accounts.activate()
        self.wait_idle()
        self.open_page()
        before = (self.ledgers.strip_calls, self.ledgers.account_calls, self.ledgers.view_calls, self.ledgers.scrap_calls)
        self.tvm.issue("", "700", [], TODAY, "", self.tvm.newRequestKey())
        self.wait_until(lambda: len(self.db.entries) == 1, "posted")
        self.wait_idle()
        after = (self.ledgers.strip_calls, self.ledgers.account_calls, self.ledgers.view_calls, self.ledgers.scrap_calls)
        self.assertEqual(tuple(a - b for a, b in zip(after, before)), (1, 1, 1, 1))
        self.tvm.issue("", "", [{"karatId": "3", "weight": "1"}], TODAY, "", self.tvm.newRequestKey())            # refused: no 24K (karat id 3)
        self.wait_idle()
        self.assertEqual((self.ledgers.strip_calls, self.ledgers.account_calls, self.ledgers.view_calls, self.ledgers.scrap_calls), after)

    def test_a_refresh_requested_during_a_running_load_ends_with_a_fresh_read(self):
        self.open_page()
        self.ledgers.slow_first_strip = 0.7
        self.ledgers.strip_calls = 0
        self.balances.refresh()                                                          # reads the OLD value, slowly
        QTest.qWait(80)
        self.tvm.issue("", "1000", [], TODAY, "", self.tvm.newRequestKey())
        self.wait_until(lambda: self.ledgers.strip_calls >= 2, "second read")
        QTest.qWait(1000)
        self.assertEqual(self.balances.cashText, money(D("49000.00")))                     # the stale result did not win

    def test_a_failing_balance_refresh_is_not_a_failed_trip_entry(self):
        self.open_page()

        def broken(**kwargs):
            raise RuntimeError("balance read failed")
        self.ledgers.strip = broken
        self.tvm.issue("", "100", [], TODAY, "", self.tvm.newRequestKey())
        self.wait_until(lambda: len(self.db.entries) == 1, "posted")
        self.wait_idle()
        self.assertEqual(self.tvm.errorMessage, "")
        self.assertIn("MT000001-1", self.tvm.message)
        self.assertEqual(len(self.db.entries), 1)

    def test_dialogs_fit_at_every_target_size(self):
        self.open_page()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(150)
                self.assert_inside_window(self.item("newTripButton"))
                self.click("newTripButton")
                self.wait_until(lambda: self.item("tripEntryDialog").property("opened"), "entry dialog")
                self.wait_until(lambda: self.tvm.context.get("karats"), "context")
                for name in ("tripCashField", "tripDateField", "tripGoldKarat_0", "tripGoldWeight_0", "addGoldRowButton", "tripNotesField",
                             "confirmTripEntryButton", "cancelTripEntryButton"):
                    self.assert_inside_window(self.item(name))
                self.item("tripEntryDialog").close()
                self.closed("tripEntryDialog")


# The base class carries the invoice-page tests: only this module's own tests are collected here.
for _inherited in [n for n in dir(harness.InvoicePageTests) if n.startswith("test_")]:
    setattr(MarketTripPageTests, _inherited, None)


if __name__ == "__main__":
    unittest.main()
