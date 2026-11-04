"""The iPad camera as a stocktake scanner (owner request 2026-11-04): the web scan reaches the desktop session, nothing else."""
import unittest

from PySide6.QtGui import QGuiApplication

from app.viewmodels.stocktake_viewmodel import StocktakeViewModel
from tests.test_inventory_stock import K21, Store, piece, service_for
from tests.test_webapp import ApiError, make_api
from webapp import stocktake_link


class LinkTests(unittest.TestCase):
    def tearDown(self):
        stocktake_link.clear_handler()

    def test_no_screen_means_closed_and_no_answer(self):
        stocktake_link.clear_handler()
        self.assertFalse(stocktake_link.is_open())
        self.assertIsNone(stocktake_link.scan("123"))

    def test_a_registered_handler_gets_the_code(self):
        stocktake_link.set_handler(lambda code: {"result": "OK", "code": code})
        self.assertTrue(stocktake_link.is_open())
        self.assertEqual(stocktake_link.scan("55"), {"result": "OK", "code": "55"})

    def test_withdrawing_somebody_elses_handler_does_nothing(self):
        mine, other = (lambda c: 1), (lambda c: 2)
        stocktake_link.set_handler(mine)
        stocktake_link.clear_handler(other)
        self.assertTrue(stocktake_link.is_open())
        stocktake_link.clear_handler(mine)
        self.assertFalse(stocktake_link.is_open())


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.api = make_api()
        self.token = self.api.login("admin", "secret")
        self.addCleanup(stocktake_link.clear_handler)

    def test_status_and_scan_when_the_desktop_screen_is_closed(self):
        stocktake_link.clear_handler()
        self.assertEqual(self.api.extras.stocktake_status(self.token), {"open": False})
        self.assertEqual(self.api.extras.stocktake_scan(self.token, {"code": "01261001"})["result"], "CLOSED")

    def test_scan_goes_to_the_desktop_and_returns_its_answer(self):
        seen = []
        stocktake_link.set_handler(lambda code: seen.append(code) or {"result": "OK", "message": "✓", "scanned": 1, "remaining": 4, "weight": "3.60"})
        answer = self.api.extras.stocktake_scan(self.token, {"code": " 01261001 "})
        self.assertEqual((seen, answer["result"], answer["remaining"]), (["01261001"], "OK", 4))

    def test_an_empty_code_and_a_user_without_inventory_view_are_refused(self):
        with self.assertRaises(ApiError):
            self.api.extras.stocktake_scan(self.token, {"code": "  "})
        weak = make_api({"APP_LOGIN", "REPORTS_VIEW"})
        token = weak.login("admin", "secret")
        with self.assertRaises(ApiError) as cm:
            weak.extras.stocktake_scan(token, {"code": "1"})
        self.assertEqual(cm.exception.status, 403)
        with self.assertRaises(ApiError):
            weak.extras.stocktake_status(token)

    def test_the_card_is_listed_only_for_inventory_users(self):
        keys = [m["key"] for m in self.api.overview(self.token)["modules"]]
        self.assertIn("stocktake", keys)
        weak = make_api({"APP_LOGIN", "REPORTS_VIEW"})
        self.assertNotIn("stocktake", [m["key"] for m in weak.overview(weak.login("admin", "secret"))["modules"]])


class ViewModelBridgeTests(unittest.TestCase):
    def setUp(self):
        self.app = QGuiApplication.instance() or QGuiApplication([])
        store = Store([piece(i, K21, "خاتم", "2.000", barcode=f"{i:08d}") for i in (1, 2, 3)])
        self.vm = StocktakeViewModel(service=service_for(store))
        self.vm.start()
        self.flashes = []
        self.vm.webScanned.connect(self.flashes.append)
        self.addCleanup(stocktake_link.clear_handler)

    def test_a_scan_from_the_ipad_counts_in_the_desktop_session(self):
        self.vm.screenOpened()
        self.assertTrue(stocktake_link.is_open())
        answer = self.vm._web_scan_inline("00000002")
        self.assertEqual((answer["result"], answer["scanned"], answer["remaining"]), ("OK", 1, 2))
        self.assertEqual(self.vm.scannedCount, 1)
        self.assertEqual(self.flashes, ["OK"])

    def test_duplicates_and_unknown_codes_are_reported_and_change_nothing(self):
        self.vm.screenOpened()
        self.vm._web_scan_inline("00000002")
        self.assertEqual(self.vm._web_scan_inline("00000002")["result"], "DUPLICATE")
        self.assertEqual(self.vm._web_scan_inline("99999999")["result"], "UNKNOWN")
        self.assertEqual(self.vm.scannedCount, 1)

    def test_a_closed_screen_refuses_and_withdraws_the_handler(self):
        self.vm.screenOpened()
        self.vm.screenClosed()
        self.assertFalse(stocktake_link.is_open())
        self.assertEqual(self.vm._web_scan_inline("00000001")["result"], "CLOSED")
        self.assertEqual(self.vm.scannedCount, 0)

    def test_a_scan_from_another_thread_is_counted_on_the_gui_thread(self):
        import threading
        import time
        self.vm.screenOpened()
        out = {}
        thread = threading.Thread(target=lambda: out.update(stocktake_link.scan("00000003")), daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while thread.is_alive() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        thread.join(1)
        self.assertEqual((out.get("result"), out.get("scanned")), ("OK", 1))
        self.assertEqual(self.vm.scannedCount, 1)


if __name__ == "__main__":
    unittest.main()
