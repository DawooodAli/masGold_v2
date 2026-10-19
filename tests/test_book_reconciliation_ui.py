"""R1: the read-only «مطابقة العهدة» card on the real Main.qml with real mouse clicks at the three target sizes (1440x900, 1280x800, 1080x680). The services are the real
ones over the in-memory database (tests/market_trip_fakes.py); BOOK trips are built by the test (the production screens never create one). NOT real-MySQL tests.

The harness class is reused through its MODULE (importing the TestCase class by name would make unittest collect every inherited test a second time), and
`load_tests` returns only this module's own tests."""
import unittest

from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness
from tests import test_market_trip_book_ui as book_ui

TODAY = book_ui.TODAY
CARD_ITEMS = ("bookReconA", "bookReconB", "bookReconLoss", "bookReconRounding", "bookReconStatus", "bookReconClosureNote", "bookReconMoreToggle")


class ReconCardTests(book_ui.BookPageTests):
    def card(self, name):
        return self.text(name).replace("⁦", "").replace("⁩", "")

    def settle(self):
        self.wait_idle()
        QTest.qWait(200)

    def refresh(self):
        self.tvm.refresh()
        self.settle()

    def book_return(self, trip, weight):
        self.tvm._service.book_return(str(trip), [{"basis": "MASTER_KARAT", "karat_id": "1", "weight": weight}], TODAY, "", self.k())

    # ------------------------------------------------------------ tests (only these run: see load_tests)
    def test_the_card_shows_the_labels_and_values_and_fits_at_every_size_with_real_clicks(self):
        trip = self.make_book_trip(weight="20")
        self.book_return(trip, "5")
        self.show(trip)
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.resize(width, height)
                self.assertTrue(self.item("bookReconSection").property("visible"))
                self.assertIn("15.000000", self.card("bookReconA"))
                self.assertIn("15.000", self.card("bookReconB"))
                self.assertIn("15.000", self.card("bookReconLoss"))
                self.assertIn("0.000", self.card("bookReconRounding"))
                self.assertIn("ليس فاقدًا ولا زيادة", self.text("bookReconRounding"))
                self.assertIn("تقدير مشروط باكتمال تسجيل الحركات واعتماد المالك", self.text("bookReconLossNote"))
                self.assertIn("لم تُكتشف مشكلة ضمن الفحوص المتاحة", self.text("bookReconStatus"))
                self.assertIn("ليس ضمانًا", self.text("bookReconStatus"))
                self.assertIn("معاينة فقط", self.text("bookReconEffect"))
                self.assertIn("اعتماد تسوية", self.text("bookReconClosureNote"))
                for name in CARD_ITEMS:
                    self.assert_inside_window(self.item(name))
                self.assertFalse(self.item("bookReconMore").property("visible"))
                self.click("bookReconMoreToggle")                                                  # a real click opens the details
                self.assertTrue(self.item("bookReconMore").property("visible"))
                self.assertIn("فرق الترحيل", self.card("bookReconDetails"))
                self.assertIsNotNone(self.visible_item("bookReconCoverage"))
                self.assert_inside_window(self.item("bookReconDetails"))
                self.click("bookReconMoreToggle")
                self.assertFalse(self.item("bookReconMore").property("visible"))
        self.assertEqual(self.warnings, [])

    def test_a_positive_remainder_below_the_posting_precision_is_shown_as_such(self):
        trip = self.make_book_trip(weight="1")
        self.tvm._service.book_return(str(trip), [{"basis": "FINENESS", "fineness": "874.9", "weight": "1"}], TODAY, "", self.k())
        self.show(trip)
        self.assertIn("0.000114", self.card("bookReconA"))
        self.assertIn("أقل من دقة الترحيل", self.text("bookReconANote"))
        self.assertIn("0.000", self.card("bookReconLoss"))

    def test_diagnostics_are_visible_and_withdraw_the_estimate(self):
        trip = self.make_book_trip(weight="20")
        self.book_return(trip, "5")
        self.show(trip)
        self.db.book_lines[0]["eq21_posted"] = self.db.book_lines[0]["eq21_posted"] + 1       # a stored value that no longer matches its facts
        self.refresh()
        self.assertGreaterEqual(len(self.tvm.reconciliation["diagnostics"]), 1)
        self.assertIsNotNone(self.visible_item("bookReconDiagnostic"))
        self.assertIn("غير موثوقة", self.text("bookReconStatus"))
        self.assertEqual(self.card("bookReconLoss"), "—")
        self.assertEqual(self.text("bookReconEffect"), "")

    def test_the_card_follows_a_movement_a_reversal_and_a_change_of_trip(self):
        first = self.make_book_trip(weight="20")
        self.show(first)
        self.assertIn("20.000000", self.card("bookReconA"))
        self.book_return(first, "5")
        self.refresh()
        self.assertIn("15.000000", self.card("bookReconA"))
        entry = next(e for e in self.db.entries.values() if e["entry_kind"] == "BK_RETURN")
        self.tvm._service.reverse(entry["id"], "خطأ إدخال")
        self.refresh()
        self.assertIn("20.000000", self.card("bookReconA"))
        second = self.make_book_trip(karat="2", weight="10")
        self.tvm.refresh()
        self.settle()
        self.tvm.selectTrip(str(second))
        self.wait_until(lambda: self.tvm.reconciliation.get("tripId") == str(second), "second preview")
        self.settle()
        self.assertIn("8.571429", self.card("bookReconA"))
        self.assertNotIn("20.000000", self.card("bookReconA"))

    def test_a_legacy_trip_keeps_its_screen_without_the_card(self):
        self.make_book_trip(weight="20")
        legacy = int(self.tvm._service.issue("", "500", [], TODAY, "", self.k())["trip_id"])
        self.open_page()
        self.tvm.refresh()
        self.settle()
        self.tvm.selectTrip(str(legacy))
        self.wait_until(lambda: self.tvm.detail.get("id") == str(legacy), "legacy detail")
        self.settle()
        self.assertFalse(self.item("bookReconSection").property("visible"))
        self.assertIsNone(self.visible_item("bookReconA"))
        self.assertIsNotNone(self.visible_item("meltButton"))

    def test_the_card_adds_no_financial_action_and_never_opens_the_closure(self):
        trip = self.make_book_trip(weight="5")
        self.book_return(trip, "5")                                                          # A = 0, B = 0, cash = 0
        self.show(trip)
        self.assertIn("0.000000", self.card("bookReconA"))
        self.assertFalse(self.item("closeTripButton").property("enabled"))
        self.assertIs(self.tvm.detail["closable"], False)
        buttons = []

        def walk(node):
            for child in self.child_items(node):
                if child.objectName().startswith("bookRecon") and child.metaObject().className().startswith("Btn"):
                    buttons.append(child.objectName())
                walk(child)
        walk(self.item("bookReconSection"))
        self.assertEqual(sorted(set(buttons)), ["bookReconMoreToggle"])                      # the only control is the details toggle
        entries, lines = len(self.db.entries), len(self.db.book_lines)
        self.click("bookReconMoreToggle")
        self.assertEqual((len(self.db.entries), len(self.db.book_lines)), (entries, lines))  # clicking it writes nothing

    def test_a_damaged_line_shows_a_partial_balance_and_no_estimate(self):
        trip = self.make_book_trip(weight="20")
        self.book_return(trip, "5")
        self.show(trip)
        self.db.book_lines[0]["direction"] = None                                             # a stored fact that cannot be used (diagnostic contract; the real DB would refuse it). Other screens' summaries still read the line, so a value they tolerate is used
        self.refresh()
        self.assertIn("(جزئي)", self.card("bookReconA"))
        self.assertIn("أرقام جزئية", self.text("bookReconANote"))
        self.assertEqual(self.card("bookReconLoss"), "—")
        self.assertEqual(self.text("bookReconEffect"), "")
        self.assertIsNotNone(self.visible_item("bookReconDiagnostic"))
        self.assertIn("غير موثوقة", self.text("bookReconStatus"))

    def test_an_unreadable_part_shows_unavailable_never_a_zero_balance(self):
        trip = self.make_book_trip(weight="20")
        self.show(trip)
        self.db.unreadable = {"lines"}
        self.refresh()
        self.assertEqual(self.card("bookReconA"), "غير متاح")
        self.assertEqual(self.card("bookReconB"), "غير متاح")
        self.assertNotIn("0.000000", self.card("bookReconA"))
        self.assertIn("ليس رصيدًا صفرًا", self.text("bookReconANote"))
        self.assertIn("غير مكتملة", self.text("bookReconStatus"))
        self.assertFalse(self.item("closeTripButton").property("enabled"))
        self.db.unreadable = set()
        self.refresh()                                                                       # the next successful read restores the normal card
        self.assertIn("20.000000", self.card("bookReconA"))


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name in loader.getTestCaseNames(ReconCardTests):
        if name in ReconCardTests.__dict__:
            suite.addTest(ReconCardTests(name))
    return suite


if __name__ == "__main__":
    unittest.main()
