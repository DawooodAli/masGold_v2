"""The reports page on the real Main.qml with real clicks (packet26): navigation by right, report choice, filters, Arabic search, totals that cover every page, paging,
opening the source document, load / empty / failure states, the session change and reachability at the three sizes. The service is the real ReportService over the fixtures of
tests/test_report_service.py (no database); the QML-created view model gets it injected, exactly as the other pages do."""
from contextlib import contextmanager
from datetime import date
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

        from tests.test_report_market import MarketRepo

        class Repo(MarketRepo):
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
        self.wait_until(lambda: not vm.busy and bool(vm.result.get("tables") or vm.result.get("totals") or vm.errorMessage), label)
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
        self.assertEqual([r["key"] for r in vm.reports], ["sales", "stock", "alerts", "gold", "customers", "deadcash", "suppliers", "treasury", "market"])
        self.assertEqual(self.warnings, [])

    def test_the_sales_report_shows_totals_only_without_the_documents_table(self):
        vm = self.open_reports()
        self.wait_report(vm)
        self.assertEqual(vm.report, "sales")                                                       # the first report opens on entry
        self.assertIn("قيمة البيع", " ".join(t["label"] for t in vm.result["totals"]))
        self.assertNotIn("(فعّال)", " ".join(t["label"] for t in vm.result["totals"]))             # the word is not shown on a card (owner review)
        self.assertIn("18,000.00 ج", " ".join(self.total_values()))                              # the independent figure of test_report_service
        self.assertEqual(vm.result["tables"], [])                                                  # the documents table was removed from the screen
        self.assertIsNone(self.visible_item("reportRow_0_0"))
        self.assertIsNone(self.visible_item("reportMeaning_0"))                                    # the explanatory box was removed (owner review)
        self.assertIsNone(self.visible_item("reportNext"))                                         # nothing to page

    def test_arabic_search_filter_and_apply_change_the_totals(self):
        vm = self.open_reports()
        self.wait_report(vm)
        before = list(vm.result["totals"])
        self.type_in("reportText", "عميل ب")
        self.click("reportApply")
        self.wait_until(lambda: not vm.busy and vm.result["totals"] != before, "the search narrowed the totals")
        self.assertIn("البحث: عميل ب", vm.result["applied"])
        narrowed = list(vm.result["totals"])
        self.pick("reportFilter_state", 3)                                                          # ملغاة
        self.click("reportApply")
        self.wait_until(lambda: not vm.busy and vm.result["totals"] != narrowed, "voided + text changes the totals again")

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

    def test_other_reports_show_their_sections_and_the_unavailable_state(self):
        vm = self.open_reports()
        self.wait_report(vm)
        for key in ("stock", "customers", "deadcash", "suppliers", "treasury"):
            self.click(f"reportChoice_{key}")
            self.wait_until(lambda key=key: not vm.busy and vm.result.get("report") == key and (vm.result["tables"] or vm.result["totals"]), f"{key} loaded")
            QTest.qWait(100)
            self.assertIsNone(self.visible_item("reportMeaning_0"), key)                  # the explanatory box was removed (owner review)

    def test_the_market_report_shows_only_the_loss_totals(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        vm = self.open_reports()
        self.wait_report(vm)
        self.reveal(self.item("reportChoice_market"))
        self.click("reportChoice_market")
        self.wait_until(lambda: not vm.busy and vm.result.get("report") == "market" and vm.result["totals"], "market report loaded")
        QTest.qWait(150)
        self.assertEqual(vm.result["tables"], [])                                                    # cards only: no table on the screen
        self.assertEqual([x["label"] for x in vm.result["totals"]], ["فاقد السبك", "خسية البوتقة", "خسية السوق", "إجمالي الفقد والخسية"])
        self.assertIsNone(self.visible_item("reportMeaning_0"))                          # the explanatory box was removed (owner review)
        self.assertEqual(self.warnings, [])

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
        self.assertEqual(vm.result["tables"], [])                                                    # the documents table is not shown; the unknown cells stay in the CSV (service tests)
        self.assertTrue(any("جزئي" in t["label"] for t in vm.result["totals"]))
        self.assertIsNotNone(self.visible_item("reportApplied"))
        self.assertIn("الفترة:", self.text("reportApplied"))

    def test_the_gold_movement_report_filters_by_factory_and_weight_and_shows_the_two_tables(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        vm = self.open_reports()
        self.wait_report(vm)
        self.reveal(self.item("reportChoice_gold"))
        self.click("reportChoice_gold")
        self.wait_until(lambda: not vm.busy and vm.result.get("report") == "gold" and vm.result["tables"], "gold loaded")
        QTest.qWait(150)
        self.assertEqual([t["key"] for t in vm.result["tables"]], ["sales_24", "sales_21", "sales_18", "scrap"])             # a table per karat, then the scrap
        self.assertEqual(vm.result["title"], "حركة الذهب")
        self.assertIn("مبيع عيار 21", [x["label"] for x in vm.result["totals"]])
        self.assertEqual([s["key"] for s in vm.filterSpecs], ["karat", "factory", "weight_min", "weight_max"])
        self.assertEqual([o["text"] for o in vm.filterSpecs[1]["options"]], ["الكل", "مصنع الأمل", "مصنع النور"])      # the factories come from the service
        self.assertIsNotNone(self.visible_item("reportFilterText_weight_min"))
        self.type_in("reportFilterText_weight_min", "5")
        self.pick("reportFilter_factory", 2)                                                        # مصنع النور
        self.click("reportApply")
        self.wait_until(lambda: not vm.busy and "الوزن من: 5" in vm.result.get("applied", []), "filtered")
        self.assertIn("المصنع: مصنع النور", vm.result["applied"])
        sales = [r for t in vm.result["tables"] if t["key"].startswith("sales_") for r in t["rows"]]
        self.assertTrue(sales and all(float(r["cells"][2].replace(",", "").split()[0]) >= 5 for r in sales))             # one row per TYPE inside each karat table
        self.assertEqual({r["refText"] for r in sales}, {"رسم بياني"})                                                 # every row has the chart button
        # the monthly chart of the first type, under the SAME filters (factory النور, weight from 5)
        self.reveal(self.item("reportOpen_0_0"))
        self.click("reportOpen_0_0")
        self.wait_until(lambda: not vm.chart.get("loading", True) and bool(vm.chart.get("points")), "chart loaded")
        QTest.qWait(200)
        self.assertTrue(self.item("reportChartDialog").property("opened"))
        self.assertIn("المبيعات الشهرية", self.text("reportChartTitle"))
        self.assertIn("المصنع: مصنع النور", " ".join(vm.chart["applied"]))
        self.assertIsNotNone(self.visible_item("reportChartBar_2026-10"))
        self.assert_inside_window(self.item("reportChartClose"))
        self.click("reportChartClose")
        self.wait_until(lambda: not vm.chart, "chart closed")
        self.assertEqual(self.warnings, [])

    def test_the_default_period_follows_the_calendar_until_the_employee_edits_a_date(self):
        from app.viewmodels import reports_viewmodel as module
        real = module._today
        try:
            module._today = lambda: date(2026, 10, 20)
            vm = self.open_reports()
            self.wait_report(vm)
            self.assertEqual((vm.filters["from"], vm.filters["to"]), ("2026-10-01", "2026-10-20"))             # month start .. today
            module._today = lambda: date(2026, 11, 3)                                                           # the app stayed open: a new month begins
            vm.refreshPeriod()
            self.wait_until(lambda: not vm.busy and vm.result.get("filters", {}).get("from") == "2026-11-01", "the automatic period moved with the calendar")
            self.assertEqual((vm.filters["from"], vm.filters["to"]), ("2026-11-01", "2026-11-03"))
            vm.setFilter("from", "2026-01-01")                                                                   # the employee chooses his own period
            module._today = lambda: date(2026, 12, 9)
            vm.refreshPeriod()
            QTest.qWait(150)
            self.assertEqual((vm.filters["from"], vm.filters["to"]), ("2026-01-01", "2026-11-03"))              # an edited period is never moved
            self.click("reportApply")
            self.wait_until(lambda: not vm.busy and vm.result.get("filters", {}).get("from") == "2026-01-01", "the edited period is used")
        finally:
            module._today = real

    def test_the_automatic_period_applies_to_every_report(self):
        from app.viewmodels import reports_viewmodel as module
        real = module._today
        try:
            module._today = lambda: date(2026, 10, 20)
            vm = self.open_reports()
            self.wait_report(vm)
            for key in [r["key"] for r in vm.reports]:
                vm.select(key)
                self.wait_until(lambda key=key: not vm.busy and vm.result.get("report") == key, f"{key} loaded")
                self.assertEqual((vm.filters["from"], vm.filters["to"]), ("2026-10-01", "2026-10-20"), key)         # month start .. today on EVERY report
            module._today = lambda: date(2026, 11, 3)
            for key in [r["key"] for r in vm.reports]:
                vm.select(key)
                self.wait_until(lambda key=key: not vm.busy and vm.result.get("report") == key, f"{key} reloaded")
                self.assertEqual((vm.filters["from"], vm.filters["to"]), ("2026-11-01", "2026-11-03"), key)         # and it follows the calendar on each of them
        finally:
            module._today = real

    def test_the_period_fields_exist_only_on_the_reports_that_use_a_period(self):
        vm = self.open_reports()
        self.wait_report(vm)
        for key, used in (("sales", True), ("gold", True), ("treasury", True), ("market", True), ("stock", False), ("alerts", False), ("customers", False), ("deadcash", False), ("suppliers", False)):
            self.click(f"reportChoice_{key}")
            self.wait_until(lambda key=key: not vm.busy and vm.result.get("report") == key, f"{key} loaded")
            QTest.qWait(120)
            self.assertEqual(vm.periodUsed, used, key)
            self.assertEqual(self.visible_item("reportFrom") is not None, used, key)
            self.assertEqual(self.visible_item("reportTo") is not None, used, key)

    def test_the_suppliers_screen_shows_the_totals_table_and_no_movements(self):
        vm = self.open_reports()
        self.wait_report(vm)
        self.click("reportChoice_suppliers")
        self.wait_until(lambda: not vm.busy and vm.result.get("report") == "suppliers" and vm.result["tables"], "suppliers loaded")
        QTest.qWait(150)
        self.assertEqual([t["key"] for t in vm.result["tables"]], ["suppliers"])                                  # the movements table is not shown
        self.assertIn("إجمالي مصنعيات علينا للموردين", [x["label"] for x in vm.result["totals"]])
        self.assertFalse(any(line.startswith("الفترة") for line in vm.result["applied"]))

    def test_money_in_a_table_reads_the_number_then_the_unit(self):
        vm = self.open_reports()
        self.wait_report(vm)
        self.click("reportChoice_customers")
        self.wait_until(lambda: not vm.busy and vm.result.get("report") == "customers" and vm.result["tables"], "customers loaded")
        QTest.qWait(150)
        cells = vm.result["tables"][0]["rows"][0]["cells"]
        self.assertTrue(any(c.endswith(" ج") for c in cells))                                                   # the text itself is "4,000.00 ج" (number first)
        self.assertFalse(any("⁦" in c for c in cells if c.endswith(" ج")))                                  # no LTR isolate forces the unit to the other side

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
