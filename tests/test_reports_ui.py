"""The reports page on the real Main.qml with real clicks (packet26): navigation by right, report choice, filters, Arabic search, totals that cover every page, paging,
opening the source document, load / empty / failure states, the session change and reachability at the three sizes. The service is the real ReportService over the fixtures of
tests/test_report_service.py (no database); the QML-created view model gets it injected, exactly as the other pages do."""
from contextlib import contextmanager
from decimal import Decimal as D
import unittest

from PySide6.QtCore import QMetaObject, QObject
from PySide6.QtTest import QTest

from services.report_service import ReportService
from tests import test_invoice_page_qml as harness
from tests.auth_fakes import FakeAuthorization
from tests.test_report_service import ALL, FakeRepo, TODAY


class ReportsPageTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        FakeRepo.extra_invoices = 0
        FakeRepo.legacy = False
        FakeRepo.recons, FakeRepo.recon_docs, FakeRepo.groups, FakeRepo.calls = [], [], None, []
        self.broken = False

        @contextmanager
        def snapshot():
            yield object()
        service_self = self

        class Repo(FakeRepo):
            def sales_returns(self, start, end):
                if service_self.broken:
                    raise RuntimeError("read failed")
                return super().sales_returns(start, end)
        session = self.controller.session
        self.service = ReportService(session, transaction_factory=snapshot, repository=Repo, authorization=FakeAuthorization(session), today=lambda: TODAY)

    # ------------------------------------------------------------ helpers
    def page(self):
        return self.root.findChild(QObject, "reportsPage")

    def rvm(self):
        vm = self.root.findChild(QObject, "reportsVM")
        vm._service = self.service
        return vm

    def open_reports(self, permissions=ALL):
        self.grant(*permissions)
        vm = self.rvm()
        vm.changed.emit()
        QTest.qWait(80)
        self.root.setProperty("currentPage", 5)
        QTest.qWait(250)
        return vm

    def wait_report(self, vm, label="report loaded"):
        self.wait_until(lambda: not vm.busy and bool(vm.result.get("tables") or vm.errorMessage), label)
        QTest.qWait(150)

    def type_in(self, name, text):
        self.click(name)
        self.type_text(text)
        QTest.qWait(60)

    def total_values(self):
        out = []
        index = 0
        while True:
            item = self.visible_item(f"reportTotalValue_{index}")
            if item is None:
                return out
            out.append(str(item.property("text")).replace("⁦", "").replace("⁩", ""))
            index += 1

    # ------------------------------------------------------------ tests
    def test_the_navigation_follows_the_reports_right(self):
        self.grant(revoke=("REPORTS_VIEW",))
        self.assertIsNone(self.visible_item("reportsNavButton"))
        vm = self.open_reports()
        self.assertIsNotNone(self.visible_item("reportsNavButton"))
        self.assertEqual([r["key"] for r in vm.reports], ["sales", "stock", "gold", "customers", "suppliers", "treasury"])
        self.assertEqual(self.warnings, [])

    def test_sales_report_shows_totals_rows_and_opens_the_invoice(self):
        vm = self.open_reports()
        self.wait_report(vm)
        self.assertEqual(vm.report, "sales")                                                       # the first report opens on entry
        self.assertIn("قيمة البيع", " ".join(t["label"] for t in vm.result["totals"]))
        self.assertIn("18,000.00 ج", " ".join(self.total_values()))                              # the independent figure of test_report_service
        self.assertIsNotNone(self.visible_item("reportMeaning_0"))
        self.assertIsNotNone(self.visible_item("reportRow_0_0"))
        self.reveal(self.item("reportOpen_0_0"))
        self.click("reportOpen_0_0")
        self.wait_until(lambda: self.root.property("currentPage") == 1, "opened the invoice screen")      # the existing Invoice History screen

    def test_arabic_search_filter_and_apply(self):
        vm = self.open_reports()
        self.wait_report(vm)
        self.type_in("reportText", "عميل ب")
        self.click("reportApply")
        self.wait_until(lambda: not vm.busy and len(vm.result["tables"][0]["rows"]) == 1, "filtered")
        self.assertEqual(vm.result["tables"][0]["rows"][0]["cells"][2], "INV0002")
        self.pick("reportFilter_state", 3)                                                          # ملغاة
        self.click("reportApply")
        self.wait_until(lambda: not vm.busy and len(vm.result["tables"][0]["rows"]) == 0, "voided + text: nothing")
        self.assertIsNotNone(self.visible_item("reportTableEmpty_0"))

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

    def test_totals_stay_the_same_across_pages(self):
        FakeRepo.extra_invoices = 120
        vm = self.open_reports()
        self.wait_report(vm)
        first = list(vm.result["totals"])
        self.assertEqual((vm.result["tables"][0]["page"], vm.result["tables"][0]["rowCount"], vm.result["tables"][0]["hasMore"]), (1, 127, True))
        self.reveal(self.item("reportNext"))
        self.click("reportNext")
        self.wait_until(lambda: not vm.busy and vm.result["tables"][0]["page"] == 2, "page 2")
        self.assertEqual(vm.result["totals"], first)                                               # the totals cover the whole result, not the page
        self.reveal(self.item("reportNext"))
        self.click("reportNext")
        self.wait_until(lambda: not vm.busy and vm.result["tables"][0]["page"] == 3, "page 3")
        self.assertEqual((len(vm.result["tables"][0]["rows"]), vm.result["tables"][0]["hasMore"]), (27, False))
        self.reveal(self.item("reportPrevious"))
        self.click("reportPrevious")
        self.wait_until(lambda: not vm.busy and vm.result["tables"][0]["page"] == 2, "back to page 2")

    def test_other_reports_show_their_sections_and_the_unavailable_state(self):
        vm = self.open_reports()
        self.wait_report(vm)
        for key in ("stock", "customers", "suppliers", "treasury"):
            self.click(f"reportChoice_{key}")
            self.wait_until(lambda key=key: not vm.busy and vm.result.get("report") == key and vm.result["tables"], f"{key} loaded")
            QTest.qWait(100)
            self.assertIsNotNone(self.visible_item("reportMeaning_0"), key)
        self.click("reportChoice_gold")
        self.wait_until(lambda: not vm.busy and vm.result.get("report") == "gold", "gold loaded")
        FakeRepo.tables["market_trip_book_lines"] = False
        self.pick("reportFilter_source", 3)                                                         # عهد السوق الدفترية
        self.click("reportApply")
        self.wait_until(lambda: not vm.busy and vm.result["tables"] and not vm.result["tables"][0]["available"], "unavailable source")
        self.assertIsNotNone(self.visible_item("reportTableUnavailable_0"))                         # never a successful empty report
        FakeRepo.tables["market_trip_book_lines"] = True

    def test_a_failed_read_is_shown_as_a_failure_and_clears_the_old_report(self):
        vm = self.open_reports()
        self.wait_report(vm)
        self.broken = True
        self.click("reportApply")
        self.wait_until(lambda: not vm.busy and vm.errorMessage != "", "failure shown")
        self.assertIsNotNone(self.visible_item("reportError"))
        self.assertEqual(vm.result, {})                                                             # not the old numbers, not an empty success
        self.assertIsNone(self.visible_item("reportRow_0_0"))

    def test_unknown_legacy_values_and_partial_totals_are_shown_as_such(self):
        FakeRepo.legacy = True
        vm = self.open_reports()
        self.wait_report(vm)
        cells = [c for row in vm.result["tables"][0]["rows"] for c in row["cells"]]
        self.assertIn("غير معلوم", cells)                                                            # not 0.00
        self.assertTrue(any("جزئي" in t["label"] for t in vm.result["totals"]))
        self.assertIsNotNone(self.visible_item("reportApplied"))
        self.assertIn("الفترة:", self.text("reportApplied"))

    def test_the_book_report_shows_the_current_balance_after_the_reconciliation(self):
        from tests.test_report_service import UNITS, group, recon
        FakeRepo.groups = {2: [group(2, "IN", "10.000", "10.000")]}
        FakeRepo.recons = [recon(2, 3, "10.000", "10.000", "0.000", a_star_units=10 * UNITS)]
        vm = self.open_reports()
        self.wait_report(vm)
        self.click("reportChoice_gold")
        self.wait_until(lambda: not vm.busy and vm.result.get("report") == "gold", "gold")
        self.pick("reportFilter_source", 3)
        self.click("reportApply")
        self.wait_until(lambda: not vm.busy and vm.result["tables"][0]["key"] == "current", "book")
        titles = [t["title"] for t in vm.result["tables"]]
        self.assertTrue(any("الرصيد الحالي بعد التسويات" in t for t in titles))
        self.assertIn("0.000", " ".join(vm.result["tables"][0]["rows"][0]["cells"]))
        self.assertFalse(any("أول الفترة" in c["title"] for t in vm.result["tables"] for c in t["columns"]))
        self.assertEqual(self.warnings, [])

    def test_the_session_change_clears_everything(self):
        vm = self.open_reports()
        self.wait_report(vm)
        self.controller.session.changed.emit()
        QTest.qWait(80)
        self.assertEqual((vm.report, vm.result), ("", {}))

    def test_the_controls_are_reachable_at_every_target_size(self):
        vm = self.open_reports()
        self.wait_report(vm)
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(200)
                for name in ("reportApply", "reportExport", "reportChoice_sales", "reportChoice_treasury"):
                    self.assert_inside_window(self.item(name))
        self.assertEqual(self.warnings, [])

    def test_export_writes_the_whole_result(self):
        import tempfile
        from pathlib import Path
        FakeRepo.extra_invoices = 60
        vm = self.open_reports()
        self.wait_report(vm)
        with tempfile.TemporaryDirectory() as folder:
            original = self.service.export_csv
            self.service.export_csv = lambda report, filters, **kw: original(report, filters, folder, **kw)
            self.click("reportExport")
            self.wait_until(lambda: vm.message.startswith("تم حفظ الملف"), "exported")
            written = list(Path(folder).glob("sales-*.csv"))
            self.assertEqual(len(written), 1)
            self.assertGreater(written[0].read_text(encoding="utf-8-sig").count("INV"), 60)


for _name in dir(harness.InvoicePageTests):                                                         # the inherited billing tests belong to their own module
    if _name.startswith("test_") and _name not in ReportsPageTests.__dict__:
        setattr(ReportsPageTests, _name, None)


if __name__ == "__main__":
    unittest.main()
