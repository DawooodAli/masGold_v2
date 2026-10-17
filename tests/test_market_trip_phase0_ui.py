"""Phase 0 of the market-trip workflow on the real Main.qml with real mouse clicks at the three target sizes: the «النشطة» / «الأرشيف» tabs, the pager beyond
50 trips, an archived trip's full detail, and close / reopen moving a trip between the lists. Real services over the in-memory database
(tests/market_trip_fakes.py). NOT real-MySQL tests."""
from datetime import datetime, timedelta
import unittest

from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness
from tests import test_market_trip_ui as base

TODAY = "2026-10-10"
BASE = datetime(2026, 10, 1, 9, 0, 0)


class TripListUiTests(base.MarketTripPageTests):
    def make(self, count, minutes=0):
        """Trips posted straight through the service (the dialogs are covered elsewhere); creation times are spread so the order is unambiguous."""
        ids = []
        for n in range(count):
            tid = int(self.tvm._service.issue("", "100", [], TODAY, "", self.tvm.newRequestKey())["trip_id"])
            self.db.trips[tid]["created_at"] = BASE + timedelta(minutes=minutes + n)
            ids.append(tid)
        return ids

    def close_via_service(self, tid, minutes):
        service = self.tvm._service
        service.return_custody(str(tid), "100", [], TODAY, "", self.tvm.newRequestKey())
        service.close(str(tid))
        self.db.trips[tid]["status_changed_at"] = BASE + timedelta(minutes=minutes)

    def reload(self):
        self.tvm.refresh()
        QTest.qWait(100)
        self.wait_idle()

    def shown(self):
        return [int(t["id"]) for t in self.tvm.trips]

    def resize(self, width, height):
        self.root.setWidth(width)
        self.root.setHeight(height)
        QTest.qWait(150)

    def test_tabs_show_open_and_closed_trips_and_an_archived_trip_keeps_its_detail_at_every_size(self):
        self.open_page()
        a, b, c = self.make(3, 10)
        self.close_via_service(a, 100)
        self.reload()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.resize(width, height)
                for name in ("tripTabActive", "tripTabArchive", "newTripButton"):
                    self.assert_inside_window(self.item(name))
                self.click("tripTabActive")
                self.wait_until(lambda: self.tvm.tripTab == "ACTIVE" and self.shown() == [c, b], "active list")
                self.assertEqual(self.text("tripTabActive"), "النشطة")
                self.assertEqual(self.text("tripTabArchive"), "الأرشيف")
                self.assertIsNone(self.visible_item("tripClosedDate"))                                    # an active row shows no closing date
                self.click("tripTabArchive")
                self.wait_until(lambda: self.tvm.tripTab == "ARCHIVE" and self.shown() == [a], "archive list")
                self.wait_idle()
                self.assertIsNotNone(self.visible_item("tripClosedDate"))
                self.assertIn("أُغلقت", self.text("tripClosedDate"))
                self.assertIsNone(self.visible_item("tripPager"))                                          # one page: no pager
                self.click(self.visible_item("tripRowItem"))
                self.wait_until(lambda: self.tvm.detail.get("id") == str(a), "archived detail")
                self.assertEqual(self.text("tripStatus"), "مغلقة")
                self.assertIsNone(self.visible_item("issueMoreButton"))                                   # a closed trip offers no entry action
                self.assertIsNotNone(self.visible_item("reopenTripButton"))
                self.assertIsNotNone(self.visible_item("archiveToggle"))
                self.assertTrue(self.text("archiveToggle").startswith("سجل الحركات"))                         # not «الأرشيف / السجل»
        self.assertEqual(self.warnings, [])

    def test_close_and_reopen_with_real_clicks_move_the_trip_between_the_lists(self):
        self.resize(1080, 680)
        self.open_page()
        a, b = self.make(2, 10)
        self.tvm._service.return_custody(str(a), "100", [], TODAY, "", self.tvm.newRequestKey())
        self.reload()
        self.tvm.selectTrip(str(a))
        self.wait_until(lambda: self.tvm.detail.get("id") == str(a), "a selected")
        self.wait_idle()
        self.assertTrue(self.item("closeTripButton").property("enabled"))
        self.click("closeTripButton")
        self.wait_until(lambda: self.db.trips[a]["status"] == "CLOSED", "closed")
        self.wait_idle()
        self.assertEqual(self.shown(), [b])                                                                # a left the active list
        self.assertEqual(self.text("tripStatus"), "مغلقة")                                                 # while its detail stays on screen
        self.click("tripTabArchive")
        self.wait_until(lambda: self.shown() == [a], "archived")
        self.wait_idle()
        self.click(self.visible_item("tripRowItem"))
        self.wait_until(lambda: self.tvm.detail.get("id") == str(a), "detail")
        self.wait_idle()
        QTest.qWait(150)
        self.click("reopenTripButton")
        self.wait_until(lambda: self.item("tripReasonDialog").property("opened"), "reason dialog")
        self.type_in("tripReasonField", "مراجعة")
        self.click("confirmTripReasonButton")
        self.wait_until(lambda: self.db.trips[a]["status"] == "OPEN", "reopened")
        self.closed("tripReasonDialog")
        self.wait_idle()
        self.assertEqual(self.shown(), [])
        self.assertEqual(self.text("tripListEmpty"), "لا توجد عهدة مغلقة في الأرشيف")                        # the archive is empty again
        self.click("tripTabActive")
        self.wait_until(lambda: self.shown() == [b, a], "back in active")
        self.assertEqual(self.warnings, [])

    def test_creating_a_custody_from_the_archive_with_real_clicks_shows_the_new_trip_in_active(self):
        self.resize(1080, 680)
        self.open_page()
        (old,) = self.make(1, 10)
        self.close_via_service(old, 100)
        self.reload()
        self.click("tripTabArchive")
        self.wait_until(lambda: self.shown() == [old], "archive list")
        self.wait_idle()
        self.click(self.visible_item("tripRowItem"))                                                      # the archived trip is selected
        self.wait_until(lambda: self.tvm.detail.get("id") == str(old), "archived detail")
        self.wait_idle()
        self.click("newTripButton")
        self.wait_until(lambda: self.item("tripEntryDialog").property("opened"), "entry dialog")
        self.wait_until(lambda: self.tvm.context.get("karats"), "context")
        self.type_in("tripCashField", "250")
        self.click("confirmTripEntryButton")
        self.wait_until(lambda: len(self.db.trips) == 2, "custody created")
        self.closed("tripEntryDialog")
        self.wait_idle()
        new = max(self.db.trips)
        self.assertEqual((self.tvm.tripTab, self.tvm.tripPage, self.tvm.selectedTripId), ("ACTIVE", 1, str(new)))
        self.assertEqual(self.shown(), [new])
        self.wait_until(lambda: self.tvm.detail.get("id") == str(new), "new detail")
        self.assertEqual(self.text("tripStatus"), "مفتوحة")
        self.assertTrue(self.visible_item("tripTabActive").property("visible"))
        self.assertEqual(self.warnings, [])

    def test_a_refused_close_leaves_the_trip_in_the_active_list(self):
        self.resize(1080, 680)
        self.open_page()
        (a,) = self.make(1, 10)
        self.reload()
        self.tvm.selectTrip(str(a))
        self.wait_until(lambda: self.tvm.detail.get("id") == str(a), "selected")
        self.assertFalse(self.item("closeTripButton").property("enabled"))                                 # the custody still holds cash
        self.tvm.closeTrip(str(a))                                                                         # even a forced call is refused by the service
        self.wait_idle()
        self.assertEqual((self.db.trips[a]["status"], self.shown()), ("OPEN", [a]))
        self.assertNotEqual(self.tvm.errorMessage, "")

    def test_the_pager_reaches_every_trip_beyond_fifty_at_every_size(self):
        self.open_page()
        made = self.make(55, 10)
        self.reload()
        newest_first = sorted(made, reverse=True)
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.resize(width, height)
                self.click("tripTabArchive")                                                               # reset to a known state, then back
                self.click("tripTabActive")
                self.wait_until(lambda: self.tvm.tripTab == "ACTIVE" and self.tvm.tripPage == 1 and len(self.shown()) == 50, "page 1")
                self.wait_idle()
                self.assertEqual(self.shown(), newest_first[:50])
                for name in ("tripPagePrevious", "tripPageLabel", "tripPageNext"):
                    self.assert_inside_window(self.item(name))
                self.assertFalse(self.item("tripPagePrevious").property("enabled"))
                self.assertEqual(self.text("tripPageLabel"), "صفحة 1")
                self.click("tripPageNext")
                self.wait_until(lambda: self.tvm.tripPage == 2 and len(self.shown()) == 5, "page 2")
                self.wait_idle()
                self.assertEqual(self.shown(), newest_first[50:])
                self.assertEqual(self.text("tripPageLabel"), "صفحة 2")
                self.assertFalse(self.item("tripPageNext").property("enabled"))
                self.click("tripPagePrevious")
                self.wait_until(lambda: self.tvm.tripPage == 1 and len(self.shown()) == 50, "back to page 1")
        self.assertEqual(self.warnings, [])


for _name in [n for n in dir(base.MarketTripPageTests) if n.startswith("test_") and n not in vars(TripListUiTests)]:
    setattr(TripListUiTests, _name, None)                    # the inherited UI tests run in their own modules
if __name__ == "__main__":
    unittest.main()
