"""BOOK core on the real Main.qml with real mouse clicks at the three target sizes (1440x900, 1280x800, 1080x680): the BOOK balance card, the BOOK dialogs
(issue / buy / sell / return), the cash-only entry dialog of a BOOK trip, the hidden legacy actions, the blocked closure, a reversal and the unchanged legacy
screen. The services are the real ones over the in-memory database (tests/market_trip_fakes.py); BOOK trips are built explicitly by the test (the production
screens never create one). NOT real-MySQL tests."""
from decimal import Decimal as D
import unittest

from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness
from tests import test_market_trip_ui as base

TODAY = "2026-10-10"
PRICES = {21: D("6000.0000"), 18: D("5142.8571"), 24: D("7000.0000")}


def key(n):
    return "88888888-8888-4888-8888-%012d" % n


class BookPageTests(base.MarketTripPageTests):
    def setUp(self):
        super().setUp()
        service = self.tvm._service
        service._book_creation_enabled = True                       # only the test builds a BOOK trip: the screens never do
        service._price_reference = lambda: dict(PRICES)
        self.n = 0

    # ------------------------------------------------------------ helpers
    def k(self):
        self.n += 1
        return key(self.n)

    def make_book_trip(self, karat="1", weight="20", cash=""):
        self.owner()                                                 # the session needs its rights before any service call
        trip = self.tvm._service.book_issue("", [{"basis": "MASTER_KARAT", "karat_id": karat, "weight": weight}], TODAY, "", self.k())["trip_id"]
        if cash:
            self.tvm._service.issue(trip, cash, [], TODAY, "", self.k())
        return int(trip)

    def show(self, trip):
        self.open_page()
        self.tvm.refresh()
        self.wait_idle()
        self.tvm.selectTrip(str(trip))
        self.wait_until(lambda: self.tvm.detail.get("id") == str(trip), "detail loaded")
        self.wait_idle()
        QTest.qWait(150)

    def resize(self, width, height):
        self.root.setWidth(width)
        self.root.setHeight(height)
        QTest.qWait(150)

    def balance_text(self):
        return self.text("bookBalancePosted").replace("⁦", "").replace("⁩", "")

    def book_line(self, index, basis, karat, fineness, weight, price=""):
        """Fill one row of the BOOK dialog with real clicks / typing. basis: 0 = master karat, 1 = assay."""
        if basis:
            self.pick(f"bookBasis_{index}", basis)
        if basis == 0:
            self.pick(f"bookKarat_{index}", karat)
        else:
            self.type_in(f"bookFineness_{index}", fineness)
        self.type_in(f"bookWeight_{index}", weight)
        if price:
            self.type_in(f"bookPrice_{index}", price)

    def open_book(self, button, dialog_title):
        self.click(button)
        self.wait_until(lambda: self.item("tripBookDialog").property("opened"), "book dialog")
        self.wait_until(lambda: self.tvm.context.get("karats"), "context")
        self.assertEqual(self.text("bookDialogTitle"), dialog_title)

    def submit(self):
        self.click("confirmBookButton")
        self.closed("tripBookDialog")
        self.wait_idle()

    # ------------------------------------------------------------ tests
    def test_a_book_trip_shows_its_balance_and_only_the_book_actions_at_every_size(self):
        trip = self.make_book_trip(cash="1000")
        self.show(trip)
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.resize(width, height)
                self.assertTrue(self.item("bookBalanceSection").property("visible"))
                self.assertIn("20.000 جرام مكافئ عيار 21", self.balance_text())
                self.assertIn("ليس مخزونًا فعليًا", self.text("bookBalanceNote"))
                self.assertIn("ليس خسارة ولا زيادة", self.text("bookBalanceDrift"))
                self.assertEqual(self.text("tripMode"), "دفتري")
                for name in ("bookIssueButton", "bookBuyButton", "bookSellButton", "bookReturnButton", "issueMoreButton", "returnButton"):
                    self.assert_inside_window(self.item(name))
                    self.assertTrue(self.item(name).property("enabled"), name)
                for name in ("meltButton", "heatButton", "lotIssueButton", "lotReturnButton", "custodyGoldBuyButton", "custodySaleButton", "custodyGoldSettleButton",
                             "custodyLotSettleButton", "custodyLotHoldButton", "custodyLogoHoldButton"):
                    self.assertIsNone(self.visible_item(name), f"{name} must not be offered on a BOOK trip")
                self.assertEqual(self.text("issueMoreButton"), "تحويل نقدية من الخزنة للعهدة")
        self.assertEqual(self.warnings, [])

    def test_the_list_marks_the_book_trip_and_a_legacy_trip_has_no_book_widgets(self):
        book = self.make_book_trip()
        self.assertTrue(book)
        legacy = int(self.tvm._service.issue("", "500", [], TODAY, "", self.k())["trip_id"])
        self.open_page()
        self.tvm.refresh()
        self.wait_idle()
        self.assertEqual([t["modeText"] for t in self.tvm.trips], ["", "دفتري"])                       # newest first: the legacy trip, then the book trip
        self.assertIn("رصيد دفتري 20.000", self.tvm.trips[1]["outstandingText"])
        self.assertIsNotNone(self.visible_item("tripModeBadge"))
        self.tvm.selectTrip(str(legacy))
        self.wait_until(lambda: self.tvm.detail.get("id") == str(legacy), "legacy detail")
        self.wait_idle()
        self.assertFalse(self.item("bookBalanceSection").property("visible"))
        self.assertIsNone(self.visible_item("bookIssueButton"))
        self.assertIsNotNone(self.visible_item("meltButton"))
        self.assertIsNone(self.visible_item("tripMode"))

    def test_book_closure_stays_blocked_in_the_screen(self):
        trip = self.make_book_trip()
        self.tvm._service.book_return(str(trip), [{"basis": "MASTER_KARAT", "karat_id": "1", "weight": "20"}], TODAY, "", self.k())
        self.show(trip)
        self.assertFalse(self.item("closeTripButton").property("enabled"))
        self.assertIn("اعتماد تسوية", self.text("closeHint"))
        self.tvm.closeTrip(str(trip))                                                                    # even a forced call is refused by the service
        self.wait_idle()
        self.assertEqual(self.db.trips[trip]["status"], "OPEN")
        self.assertNotEqual(self.tvm.errorMessage, "")

    def test_the_whole_book_flow_with_real_clicks_at_1080(self):
        self.resize(1080, 680)
        trip = self.make_book_trip(cash="")
        self.show(trip)
        # ---- cash into the trip through the (cash-only) entry dialog
        self.click("issueMoreButton")
        self.wait_until(lambda: self.item("tripEntryDialog").property("opened"), "cash dialog")
        self.assertIsNone(self.visible_item("addGoldRowButton"))                                          # a BOOK trip takes CASH here, never legacy gold rows
        self.type_in("tripCashField", "100000")
        self.assert_inside_window(self.item("confirmTripEntryButton"))
        self.click("confirmTripEntryButton")
        self.wait_until(lambda: "100,000" in self.tvm.detail["cash"]["outstanding"], "cash in")
        self.closed("tripEntryDialog")
        self.wait_idle()
        self.assertEqual(self.db.cash(), D("50000.00") - D("100000.00"))
        # ---- BUY with an assay line: the preview states the posted equivalent and the total BEFORE saving
        self.open_book("bookBuyButton", "شراء ذهب (دفتري)")
        for name in ("bookCounterpartyCombo", "bookDateField", "bookReferenceField", "bookNotesField", "confirmBookButton", "cancelBookButton", "addBookLineButton"):
            self.assert_inside_window(self.item(name))
        self.assertFalse(self.item("confirmBookButton").property("enabled"))                              # nothing typed yet
        self.type_in("bookNameField", "تاجر السوق")
        self.book_line(0, 1, 0, "916.7", "5", "32000")
        self.wait_until(lambda: self.item("bookPreviewTotal").property("visible"), "preview")
        self.assertIn("5.238", self.text("bookPreviewLine_0"))
        self.assertIn("5.238", self.text("bookPreviewTotal"))
        self.assertIn("32,000.00", self.text("bookPreviewPrice"))
        self.assertTrue(self.item("confirmBookButton").property("enabled"))
        self.submit()
        self.assertIn("68,000.00", self.tvm.detail["cash"]["outstanding"])

        self.assertIn("25.238 جرام مكافئ عيار 21", self.balance_text())
        # ---- SELL more than the balance: the preview refuses it and the save button stays disabled
        self.open_book("bookSellButton", "بيع ذهب (دفتري)")
        self.type_in("bookNameField", "مشتري")
        self.book_line(0, 0, 2, "", "30", "180000")
        self.wait_until(lambda: self.item("bookPreviewError").property("visible"), "refusal")
        self.assertIn("الرصيد الدفتري المتاح", self.text("bookPreviewError"))
        self.assertFalse(self.item("confirmBookButton").property("enabled"))
        self.cancel_book()
        # ---- RETURN: a master karat to the Scrap, an assay to the assayed stock
        self.open_book("bookReturnButton", "رجوع ذهب للمحل (دفتري)")
        self.book_line(0, 0, 2, "", "5")
        self.click("addBookLineButton")
        QTest.qWait(80)
        self.book_line(1, 1, 0, "866.7", "1.8")
        self.wait_until(lambda: "1.783" in self.text("bookPreviewLine_1"), "assay line preview")
        self.submit()
        self.assertEqual(self.db.physical(1), D("100.000") - D("20.000") + D("5.000"))
        (move,) = self.db.assayed_moves
        self.assertEqual((move["grams_delta"], move["eq21_posted_delta"]), (D("1.800"), D("1.783")))
        self.assertIsNotNone(self.visible_item("bookStockItem"))
        self.assertIn("1.800", self.text("bookStockItem"))
        self.assertEqual(self.warnings, [])

    def cancel_book(self):
        self.click("cancelBookButton")
        self.closed("tripBookDialog")

    def test_a_book_entry_is_reversed_from_the_history_and_the_blocked_one_explains_why(self):
        self.resize(1080, 680)
        trip = self.make_book_trip(cash="50000")
        service = self.tvm._service
        sale = service.book_sell(str(trip), [{"basis": "MASTER_KARAT", "karat_id": "1", "weight": "6", "price": "36000"}], "", "مشتري", TODAY, "", "", self.k(), False)
        self.show(trip)
        self.open_archive()
        buttons = []

        def collect(node):
            if node.objectName() == "reverseEntryButton" and node.property("visible"):
                buttons.append(node)
            for child in self.child_items(node):
                collect(child)
        collect(self.root.contentItem())
        self.assertEqual(len(buttons), 3)                                                                  # cash issue, BOOK issue, BOOK sale (newest first)
        self.click(buttons[0])                                                                             # the BOOK sale
        self.wait_until(lambda: self.item("tripReasonDialog").property("opened"), "reversal dialog")
        self.assertIn("الثمن المخزَّن", self.text("tripReasonNote"))
        self.type_in("tripReasonField", "بيع بالخطأ")
        self.click("confirmTripReasonButton")
        self.wait_until(lambda: len(self.db.corrections) == 1, "reversed")
        self.closed("tripReasonDialog")
        self.wait_idle()
        self.assertIn("20.000 جرام مكافئ عيار 21", self.balance_text())
        self.assertEqual(sale["kind"], "BK_SELL")
        self.assertIn("معكوسة بالعملية RV000001", " ".join(self.visible_texts("entryState")))
        self.assertEqual(self.warnings, [])


for _name in [n for n in dir(base.MarketTripPageTests) if n.startswith("test_") and n not in vars(BookPageTests)]:
    setattr(BookPageTests, _name, None)                      # the inherited UI tests run in their own modules
if __name__ == "__main__":
    unittest.main()
