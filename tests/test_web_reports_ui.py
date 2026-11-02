"""Settings → تقارير الأيباد on the real Main.qml with real clicks: the address shown, the copy button, the on / off button, at the three target sizes. The server itself is never started
(the offscreen platform keeps it off) and no database is used."""
from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from unittest import mock

from tests import test_invoice_page_qml as harness

SIZES = ((1440, 900), (1280, 800), (1080, 680))


class WebReportsCardTests(harness.InvoicePageTests):
    def setUp(self):
        patcher = mock.patch("webapp.embedded.write_enabled", lambda value, path=None: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()

    def test_the_card_shows_the_stable_address_copies_it_and_switches_with_real_clicks(self):
        vm = self.controller.web_reports
        self.assertFalse(vm.running)                                                   # a test platform never starts the server
        vm._addresses = ["http://desktop-s6g2sm6.tailbb84e0.ts.net:8765", "http://100.91.52.23:8765"]
        vm._servers = [object()]
        vm._status = "شغال"
        vm.changed.emit()
        self.root.setProperty("currentPage", 6)
        self.root.findChild(QObject, "settingsPage").setProperty("openSection", "webreports")
        self.wait_until(lambda: self.visual_item("settingsWebReports") is not None, "the card exists")
        self.wait_until(lambda: self.item("settingsWebReports").property("visible"), "the card is shown")
        self.scroll_settings_to("webReportsAddress_0")
        self.assertEqual(self.item("webReportsAddress_0").property("text"), "http://desktop-s6g2sm6.tailbb84e0.ts.net:8765")
        self.assertEqual(self.item("webReportsAddress_1").property("text"), "http://100.91.52.23:8765")
        self.assertIn("شغال", self.item("webReportsStatus").property("value"))
        self.click("webReportsCopy_0")
        QTest.qWait(100)
        self.assertEqual(QGuiApplication.clipboard().text(), "http://desktop-s6g2sm6.tailbb84e0.ts.net:8765")
        self.assertEqual(self.item("webReportsToggle").property("text"), "إيقاف التشغيل التلقائي")
        for width, height in SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            self.scroll_settings_to("webReportsToggle")
            self.assert_inside_window(self.item("webReportsToggle"))
            self.assert_inside_window(self.item("webReportsAddress_0"))
        vm._servers = []                                                               # nothing real is bound: stop() has nothing to shut down
        self.click("webReportsToggle")
        self.wait_until(lambda: not vm.enabled, "switched off")
        self.wait_until(lambda: self.item("webReportsToggle").property("text") == "تشغيل", "the button offers to switch it on")
