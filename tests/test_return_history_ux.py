"""Return History presentation and event-driven refresh; no database/business writes."""
import copy
from datetime import date
from decimal import Decimal as D
import unittest

from PySide6.QtCore import QCoreApplication, QMetaObject, QObject, Qt
from PySide6.QtTest import QTest

from services.invoice_history_service import InvoiceHistoryService
from tests import test_invoice_history as fixture
from tests import test_invoice_history_qml as qml_fixture
from tests.test_customer_picker import inside, result_rows_findings, sample_rows, scene_rect, selected_card_findings, stub_customers


def sale(kind="OLD_GOLD_STOCK", value="50000", weight="8"):
    return {"line_id": "1", "kind": kind, "line_total": D(value) if value is not None else None,
            "weight": D(weight), "inventory_item_id": "29" if kind == "INVENTORY" else None,
            "item_name": "من الكسر", "barcode": "123" if kind == "INVENTORY" else None}


def stored_return(rid, weight, value, *, reversed_by=None, mode="HISTORICAL", source="1"):
    return {"return_id": rid, "code": f"RT{int(rid):06d}", "status": "FINALIZED", "reversed_by": reversed_by,
            "total_amount": value, "tracking_lines": [{"line_id": rid, "source_invoice_line_id": source,
            "weight_grams": weight, "valuation_mode": mode, "historical_line_value": value if mode == "HISTORICAL" else None,
            "gold_amount": value if mode == "CURRENT_BUY" else "0", "making_refund": "0"}]}


class SummaryTests(unittest.TestCase):
    def tracking(self, returns, line=None, **extra):
        lines = [copy.deepcopy(line or sale())]
        InvoiceHistoryService._return_tracking(lines, dict(returns=returns, invoice_id="1", **extra))
        return lines[0]["return_tracking"]

    def test_partial_then_complete_pinned_f7(self):
        first = stored_return("6", "3", "18750")
        t = self.tracking([first])
        self.assertEqual((t["returned_weight"], t["refund"], t["remaining_weight"], t["remaining_value"]),
                         (D("3"), D("18750"), D("5"), D("31250")))
        t = self.tracking([first, stored_return("7", "5", "31250")])
        self.assertEqual((t["returned_weight"], t["refund"], t["remaining_weight"], t["remaining_value"]),
                         (D("8"), D("50000"), D("0"), D("0")))

    def test_reversed_and_draft_returns_do_not_consume(self):
        reversed_row = stored_return("5", "3", "18750", reversed_by="RV000001")
        draft = stored_return("8", "5", "31250"); draft["status"] = "DRAFT"
        t = self.tracking([reversed_row, draft])
        self.assertEqual((t["returned_weight"], t["refund"], t["remaining_weight"], t["remaining_value"]),
                         (D("0"), D("0"), D("8"), D("50000")))

    def test_line_reversal_marker_is_excluded(self):
        row = stored_return("5", "3", "18750")
        row["tracking_lines"][0]["reversed_by_correction_id"] = "1"
        self.assertEqual(self.tracking([row])["returned_weight"], 0)

    def test_authoritative_reversal_event_excludes_return(self):
        event = {"event_type": "REVERSED", "details_json": {"source_type": "RETURN", "source_id": "5",
                                                                          "correction_code": "RV000001"}}
        t = self.tracking([stored_return("5", "3", "18750")], events=[event])
        self.assertEqual((t["returned_weight"], t["remaining_value"]), (D("0"), D("50000")))

    def test_current_buy_actual_refund_separate_from_historical_remainder(self):
        t = self.tracking([stored_return("1", "3", "18000", mode="CURRENT_BUY")])
        self.assertEqual((t["refund"], t["remaining_value"]), (D("18000"), D("31250")))

    def test_final_rounded_refund_allocated_once_and_remainder_uses_basis(self):
        row = stored_return("1", "1", "100.01")
        row["total_amount"] = "300"
        row["tracking_lines"].append(dict(row["tracking_lines"][0], line_id="2", source_invoice_line_id="2",
                                           historical_line_value="200.02"))
        lines = [sale(value="100.01", weight="1"), dict(sale(value="200.02", weight="1"), line_id="2")]
        InvoiceHistoryService._return_tracking(lines, {"returns": [row]})
        self.assertEqual(sum(l["return_tracking"]["refund"] for l in lines), D("300"))
        self.assertTrue(all(l["return_tracking"]["remaining_value"] == 0 for l in lines))

    def test_active_exchange_consumes_original_and_reversal_restores_it(self):
        x = {"lifecycle_id": "2", "returned": [{"inventory_item_id": "29"}]}
        t = self.tracking([], sale("INVENTORY"), exchanges=[x])
        self.assertTrue(t["exchanged"]); self.assertEqual(t["remaining_weight"], 0)
        event = {"event_type": "REVERSED", "details_json": {"source_type": "EXCHANGE", "source_id": "2",
                                                                          "correction_code": "RV000002"}}
        t = self.tracking([], sale("INVENTORY"), exchanges=[x], events=[event])
        self.assertFalse(t["exchanged"]); self.assertEqual(t["remaining_weight"], D("8"))

    def test_unknown_original_value_stays_unknown(self):
        self.assertIsNone(self.tracking([], sale(value=None))["remaining_value"])

    def test_mode_aware_card_and_rounding_preserve_stored_amounts(self):
        vm = fixture.InvoiceHistoryViewModel(fixture.AppSession())
        self.addCleanup(vm.shutdown)
        row = {"code": "RT000006", "status_text": "تم المرتجع", "occurred_at": "2026-10-04 12:00:00",
               "actor": "مدير", "making_window_open": True, "requires_approval": False,
               "valuation_mode": "HISTORICAL", "refund_basis": "18751", "rounding_adjustment": "-1",
               "gold_amount": "0", "making_refund": "0", "total_amount": "18750", "account_amount": "0",
               "payments": [{"method_code": "PHYSICAL_CASH", "amount": "18750"}],
               "lines": [{"line_type": "SELL_OLD_GOLD", "karat": 21, "weight_grams": "3",
                          "source_weight_grams": "8", "shop_buy_price_per_gram": None,
                          "historical_value": "18751", "gold_amount": "0", "making_refund": "0",
                          "disposition": {"code": "OLD_GOLD", "text": "عاد إلى الكسر"}}]}
        vm._detail = {"returns": [row]}
        card = vm.returns[0]
        self.assertEqual(card["pieces"][0]["price"], "")
        self.assertIn("18,750", card["valuation"])
        self.assertIn("فرق التقريب", card["valuation"])
        self.assertEqual(card["total"], "18,750 ج")
        row.update(valuation_mode="CURRENT_BUY", rounding_adjustment="0", gold_amount="18000", making_refund="750")
        row["lines"][0].update(shop_buy_price_per_gram="6000", gold_amount="18000", making_refund="750")
        card = vm.returns[0]
        self.assertIn("6,000", card["pieces"][0]["price"])
        self.assertEqual((card["pieces"][0]["gold"], card["pieces"][0]["making"]), ("18,000 ج", "750 ج"))


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.vm = fixture.InvoiceHistoryViewModel(fixture.AppSession())
        self.calls = []
        self.vm._service = object()
        self.vm._start = lambda operation, call: self.calls.append(operation)
        self.addCleanup(self.vm.shutdown)

    def test_reentry_and_event_keep_filters_page_and_detail(self):
        self.vm.activate(); self.calls.clear()
        self.vm._text, self.vm._type, self.vm._status = "261004", "SELL", "PARTIAL"
        self.vm._date_from, self.vm._date_to = date(2026, 10, 1), date(2026, 10, 4)
        self.vm._explicit_range, self.vm._page = True, 3
        self.vm._detail = {"invoice_id": "123"}
        self.vm.refreshInvoice("123")
        self.assertEqual(self.calls, ["search", "detail"])
        self.assertEqual((self.vm.searchText, self.vm.typeFilter, self.vm.statusFilter, self.vm.page),
                         ("261004", "SELL", "PARTIAL", 3))
        self.assertEqual((self.vm.dateFrom, self.vm.dateTo), ("2026-10-01", "2026-10-04"))
        self.calls.clear(); self.vm.activate()
        self.assertEqual(self.calls, ["search", "detail"])

    def test_empty_page_moves_back_to_nearest_valid_page(self):
        pages = []; self.vm._run_search = pages.append
        self.vm._on_search({"page": 3, "items": [], "has_more": False}, "")
        self.assertEqual(pages, [2])

    def test_offpage_before_first_activation_does_not_load_history(self):
        self.vm.refreshInvoice("123")
        self.assertEqual(self.calls, [])


class HistoryUxQmlTests(qml_fixture.InvoiceHistoryPageTests):
    def test_ux_refresh_actual_sale_and_return_signal_without_apply(self):
        vm = self.open_history()
        # Execute the same read callbacks synchronously to assert event behavior without polling.
        def start(operation, call):
            result = call(self.controller.session.current_actor())
            getattr(vm, "_on_" + operation)(result, "")
            vm.changed.emit()
        vm._start = start
        service, state, _ = fixture.make(rows=[fixture.summary_row("26100404", fixture.FINALIZED_AT)])
        self.history.real = service
        self.vm.finalized.emit("26100404")
        self.assertEqual(vm.results[0]["number"], "26100404")
        vm.openInvoice("21")
        calls = len(self.history.calls)
        service._returns = lambda *_: [{"return_id": "6", "code": "RT000006", "status": "FINALIZED",
            "status_text": "تم المرتجع", "occurred_at": "2026-10-04 12:00:00", "actor": "مدير",
            "valuation_mode": "HISTORICAL", "making_window_open": True, "lines": [],
            "gold_amount": "0", "making_refund": "0", "refund_basis": "18750", "total_amount": "18750",
            "rounding_adjustment": "0", "payments": [], "account_amount": "0"}]
        self.controller.operational_viewmodels["returnVM"].finalized.emit("21")
        self.assertGreater(len(self.history.calls), calls)
        self.assertEqual(vm.detail["number"], "26092701")
        self.assertEqual(vm.returns[0]["code"], "RT000006")
        QCoreApplication.processEvents()
        self.assertEqual(self.item("historyReturnHeadline_0").property("text"), "استرداد بالقيمة التاريخية")
        self.assertEqual(self.item("historyReturnTotal_0").property("text"), "18,750 ج")
        self.assertEqual(self.warnings, [])

    def test_ux_corrections_show_only_the_new_split_at_every_size(self):
        """Owner 2026-11-25: a settlement correction is the new split, one part per line; a reversal is its title and reason only."""
        from datetime import datetime
        vm = self.open_history()
        vm.openInvoice("21")
        self.wait_until(lambda: vm.detail)
        after = {"direction": "IN", "legs": [{"method_code": "PHYSICAL_CASH", "amount": "20000.00"}, {"method_code": "INSTAPAY", "amount": "5000.00"}], "account": "5000.00"}
        base = {"source_type": "INVOICE", "source_reference": "26092701", "actor": "admin", "occurred_at": datetime(2026, 11, 25, 20, 18), "before": {}, "posted": []}
        vm._detail["corrections"] = [dict(base, code="FC000083", correction_type="SETTLEMENT_CORRECTION", reason="خطأ", after=after),
                                     dict(base, code="RV000084", correction_type="FULL_REVERSAL", reason="خطأ", after={})]
        vm.changed.emit()
        QCoreApplication.processEvents()
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            self.root.setWidth(width)
            self.root.setHeight(height)
            QCoreApplication.processEvents()
            section = self.item("historyCorrectionsSection")
            self.assertTrue(section.property("visible"), f"corrections hidden at {width}x{height}")
            blocks = [self.item("historyCorrection_0"), self.item("historyCorrection_1")]
            self.assertTrue(all(b is not None and b.property("width") > 0 for b in blocks))
            self.assertLessEqual(blocks[0].property("width"), section.property("width"))
            scroll = self.item("historyDetailScroll").property("contentItem")                         # bring the section into view for the screenshot (and prove it is reachable)
            scroll.setProperty("contentY", max(0, section.mapToItem(scroll.property("contentItem"), 0, 0).y() - 20))
            QCoreApplication.processEvents()
            self.shot(f"corrections-{width}x{height}")
        self.assertEqual(self.warnings, [])

    def test_ux_change_customer_dialog_is_reachable_and_clear_at_every_size(self):
        """Owner 2026-11-25: «تغيير العميل» sits in the customer card; the dialog shows the new customer and the two accounts as short lines and cannot be saved without a reason and the confirmation."""
        vm = self.open_history()
        vm.openInvoice("21")
        self.wait_until(lambda: vm.detail)
        vm._detail["can_change_customer"] = True
        vm.changed.emit()
        QCoreApplication.processEvents()
        facts = {"invoice_number": "26092701", "old": {"id": "1", "code": "C000001", "name": "اسلم بدران"}, "new": {"id": "2", "code": "C000002", "name": "أسلم بدران"},
                 "account_effect": "29400.00", "old_balance": "29400.00", "old_balance_after": "0.00", "new_balance": "-500.00", "new_balance_after": "28900.00"}
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            self.root.setWidth(width)
            self.root.setHeight(height)
            QCoreApplication.processEvents()
            button = self.item("historyChangeCustomer")
            self.assertTrue(button.property("visible"), f"button hidden at {width}x{height}")
            self.assert_inside_window(button)
            self.click(button)
            dialog = self.item("historyCustomerDialog")
            self.wait_until(lambda: dialog.property("opened"), "dialog did not open")
            vm._on_customerpreview(facts, None)
            vm.changed.emit()
            QCoreApplication.processEvents()
            for name in ("historyCustomerPicker", "historyCustomerPreview", "historyCustomerReason", "historyCustomerConfirm", "historyCustomerSave", "historyCustomerCancel"):
                self.assert_inside_window(self.item(name))
            save = self.item("historyCustomerSave")
            self.assertFalse(save.property("enabled"), "saved without a reason and the confirmation")
            self.item("historyCustomerReason").setProperty("text", "اسم العميل اتكتب بالغلط")
            self.assertFalse(save.property("enabled"), "saved without the confirmation")
            self.item("historyCustomerConfirm").setProperty("checked", True)
            QCoreApplication.processEvents()
            self.assertTrue(save.property("enabled"))
            self.shot(f"change-customer-{width}x{height}")
            self.click("historyCustomerCancel")
            self.wait_until(lambda: not dialog.property("visible"), "cancel did not close the dialog")                       # fully gone (no exit transition left over the page)
        self.assertEqual(self.warnings, [])

    # ------------------------------------------------------------ Task 08: every CustomerPicker host, against its own boundary
    def picker_host_findings(self, picker_name, boundary, size, label):
        """With a customer selected, the picker lies inside `boundary` (the page rectangle or the dialog's content rectangle, never the window),
        its card fits, and the result popup stays inside the window with every line inside its row."""
        picker = self.item(picker_name)
        self.assertTrue(picker.property("visible"), f"{label}: {picker_name} is not visible")
        problems = []
        prect = scene_rect(picker)
        if not inside(prect, boundary):
            problems.append(f"{label}: {picker_name} {prect} leaves its host boundary {boundary}")
        problems += selected_card_findings(picker, label)
        QMetaObject.invokeMethod(picker, "open", Qt.DirectConnection)
        self.wait_until(lambda: picker.findChild(QObject, "customerPickerPopup").property("opened"), f"{label}: popup of {picker_name} did not open")
        QTest.qWait(150)
        problems += result_rows_findings(picker, size, label)
        QMetaObject.invokeMethod(picker, "close", Qt.DirectConnection)
        self.wait_until(lambda: not picker.findChild(QObject, "customerPickerPopup").property("visible"), f"{label}: popup of {picker_name} did not close")
        return problems

    def select_in(self, picker_name):
        picker = self.item(picker_name)
        rows = sample_rows()
        vm = stub_customers(picker, rows)
        self.assertTrue(vm.selectCustomer(str(rows[0]["customer_id"])))
        QCoreApplication.processEvents()
        self.assertTrue(picker.property("hasSelection"))

    def page_boundary(self, picker_name):
        """The rectangle of the page that holds the picker: the page-stack child on the picker's ancestor chain."""
        node = self.item(picker_name)
        while node is not None and not (node.parentItem() is not None and node.parentItem().objectName() == "pageStack"):
            node = node.parentItem()
        self.assertIsNotNone(node, f"{picker_name} is not inside the page stack")
        return scene_rect(node)

    def test_ux_customer_picker_hosts_keep_the_pilot_card_and_popup_inside_their_boundary_at_every_size(self):
        """Task 08: Customers quick picker, merge dialog picker, cash-page picker and invoice-history customer dialog picker."""
        sizes = ((1440, 900), (1280, 800), (1080, 680))
        findings = []
        # 1. Customers page quick picker (page index 3)
        self.root.setProperty("currentPage", 3)
        QTest.qWait(300)
        self.select_in("customersQuickPicker")
        for width, height in sizes:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(250)
            findings += self.picker_host_findings("customersQuickPicker", self.page_boundary("customersQuickPicker"), (width, height), f"customers page {width}x{height}")
        # 2. Merge dialog picker (the dialog's own begin(); the preview binding stays inert while no customer is open)
        merge = self.item("customerMergeDialog")
        QMetaObject.invokeMethod(merge, "begin", Qt.DirectConnection)
        self.wait_until(lambda: merge.property("opened"), "merge dialog did not open")
        self.select_in("customerMergePicker")
        for width, height in sizes:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(250)
            findings += self.picker_host_findings("customerMergePicker", scene_rect(merge.property("contentItem")), (width, height), f"merge dialog {width}x{height}")
            self.assertTrue(inside(scene_rect(merge.property("contentItem")), (0, 0, width, height)), f"merge dialog content leaves the {width}x{height} window")
        QMetaObject.invokeMethod(merge, "close", Qt.DirectConnection)
        self.wait_until(lambda: not merge.property("visible"), "merge dialog did not close")
        # 3. Financial accounts cash-page picker: the dialog's entry section is shown by its `mode` (no view-model call is made)
        self.root.setProperty("currentPage", 4)
        QTest.qWait(300)
        cash = self.item("customerCashDialog")
        cash.setProperty("mode", "IN")
        QMetaObject.invokeMethod(cash, "open", Qt.DirectConnection)
        self.wait_until(lambda: cash.property("opened"), "cash dialog did not open")
        self.select_in("customerCashCustomer")
        for width, height in sizes:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(250)
            findings += self.picker_host_findings("customerCashCustomer", scene_rect(cash.property("contentItem")), (width, height), f"cash page dialog {width}x{height}")
            self.assertTrue(inside(scene_rect(cash.property("contentItem")), (0, 0, width, height)), f"cash dialog content leaves the {width}x{height} window")
        QMetaObject.invokeMethod(cash, "close", Qt.DirectConnection)
        self.wait_until(lambda: not cash.property("visible"), "cash dialog did not close")
        # 4. Invoice-history change-customer dialog
        vm = self.open_history()
        vm.openInvoice("21")
        self.wait_until(lambda: vm.detail)
        vm._detail["can_change_customer"] = True
        vm.changed.emit()
        QCoreApplication.processEvents()
        for width, height in sizes:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QCoreApplication.processEvents()
            self.click(self.item("historyChangeCustomer"))
            dialog = self.item("historyCustomerDialog")
            self.wait_until(lambda: dialog.property("opened"), "history customer dialog did not open")
            self.select_in("historyCustomerPicker")
            QTest.qWait(150)
            findings += self.picker_host_findings("historyCustomerPicker", scene_rect(dialog.property("contentItem")), (width, height), f"history dialog {width}x{height}")
            self.assertTrue(inside(scene_rect(dialog.property("contentItem")), (0, 0, width, height)), f"history dialog content leaves the {width}x{height} window")
            self.click("historyCustomerCancel")
            self.wait_until(lambda: not dialog.property("visible"), "history dialog did not close")
        self.assertEqual(findings, [], "\n  " + "\n  ".join(findings))
        self.assertEqual(self.warnings, [])

    def test_ux_summary_historical_cards_all_sizes(self):
        vm = self.open_history()
        vm.openInvoice("21")
        self.wait_until(lambda: vm.detail)
        line = sale()
        InvoiceHistoryService._return_tracking([line], {"returns": [stored_return("6", "3", "18750"),
                                                                  stored_return("7", "5", "31250")]})
        vm._detail["sale_lines"] = [line]
        vm._detail["sale_lines"][0].update(line_number=1, line_type="SELL_OLD_GOLD", karat=21, quantity=1,
                                           making_total=D("0"), gold_price=D("6250"))
        from services.return_service import return_summary
        row = {"return_id": "6", "return_status": "FINALIZED", "source_finalized_at": fixture.FINALIZED_AT,
               "finalized_at": fixture.FINALIZED_AT, "making_window_open": True, "requires_approval": False,
               "valuation_mode": "HISTORICAL", "gold_amount": "0", "making_refund": "0", "total_amount": "18750",
               "refund_basis": "18750", "rounding_adjustment": "0", "money_paid": "18750", "account_amount": "0",
               "payments": [{"payment_method_code": "PHYSICAL_CASH", "amount": "18750"}],
               "lines": [{"line_type": "SELL_OLD_GOLD", "karat_value": 21, "weight_grams": "3",
                          "source_weight_grams": "8", "valuation_mode": "HISTORICAL", "historical_line_value": "18750",
                          "shop_buy_price_per_gram": None, "gold_amount": "0", "making_refund": "0"}]}
        vm._detail["returns"] = [dict(return_summary(row), reversed_by="RV000001")]
        vm.changed.emit()
        self.item("historyDetailRoot").setProperty("trackingExpanded", True)       # folded by default
        QCoreApplication.processEvents()
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            self.root.setWidth(width); self.root.setHeight(height)
            QCoreApplication.processEvents()
            self.assertEqual(vm.returnTracking[0]["remaining"], "المتبقي: 0.00 جرام — 0 ج")
            self.assertEqual(vm.returns[0]["headline"], "استرداد بالقيمة التاريخية")
            self.assertEqual(vm.returns[0]["status"], "معكوس عبر RV000001")
            self.assertEqual(vm.returns[0]["pieces"][0]["price"], "")
            for name in ("historyTrackingOriginal_0", "historyTrackingReturned_0", "historyTrackingRemaining_0",
                         "historyReturnValuation_0", "historyReturnStatus_0"):
                item = self.item(name)
                self.assertLessEqual(item.property("contentWidth"), item.width() + 1, name)
                self.assertGreaterEqual(item.height(), item.property("contentHeight") - 1, name)
            self.assertEqual(self.warnings, [])


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for cls in (SummaryTests, RefreshTests, HistoryUxQmlTests, ExchangedInvoiceViewTests, ListExchangeNoteTests):
        suite.addTests(cls(n) for n in loader.getTestCaseNames(cls)
                       if cls is not HistoryUxQmlTests or n.startswith("test_ux_"))
    return suite


class ExchangedInvoiceViewTests(unittest.TestCase):
    """After an exchange the page shows the CURRENT invoice; the saved one stays in its own card (display only)."""

    def vm_with(self, *, reversed_exchange=False):
        vm = fixture.InvoiceHistoryViewModel(fixture.AppSession())
        original = {"line_id": "1", "kind": "INVENTORY", "inventory_item_id": "29", "barcode": "44260902",
                    "item_name": "خاتم", "karat": 21, "weight": D("2.290"), "quantity": 1, "line_total": D("154000"),
                    "identification_method": "SCAN", "category": None, "factory": None, "size": None, "notes": ""}
        exchange = {"code": "EX000001", "reversed": reversed_exchange, "signed_amount": "14000.00",
                    "returned": [{"inventory_item_id": "29", "item_name": "خاتم", "karat": 21, "weight_grams": "2.29"}],
                    "replacements": [{"inventory_item_id": "30", "item_name": "خاتم", "karat": 21, "weight_grams": "4.31",
                                      "barcode": "NEW1", "source": "SCAN"}]}
        vm._detail = {"sale_lines": [original], "old_gold_lines": [], "exchanges": [exchange],
                      "invoice_total": D("154000"), "net_direction": "CUSTOMER_PAYS_SHOP"}
        return vm

    def test_current_invoice_has_the_new_piece_and_total_and_the_original_is_kept_apart(self):
        vm = self.vm_with()
        self.assertTrue(vm.exchangedInvoice)
        rows = vm.saleLines
        self.assertEqual([r["barcode"] for r in rows], ["NEW1"])
        self.assertIn("EX000001", rows[0]["notes"])
        self.assertIn("168,000", vm.beforeExchange["currentTotal"])
        self.assertEqual([r["barcode"] for r in vm.beforeExchange["rows"]], ["44260902"])
        self.assertIn("154,000", vm.beforeExchange["total"])

    def test_a_reversed_exchange_changes_nothing(self):
        vm = self.vm_with(reversed_exchange=True)
        self.assertFalse(vm.exchangedInvoice)
        self.assertEqual([r["barcode"] for r in vm.saleLines], ["44260902"])


class ListExchangeNoteTests(unittest.TestCase):
    def test_exchanged_invoice_shows_its_date_and_total_plus_difference(self):
        from datetime import datetime
        item = {"invoice_id": "1", "invoice_number": "26102702", "invoice_type": "SELL", "settlement_status": "SETTLED",
                "invoice_total": D("154000"), "net_direction": "CUSTOMER_PAYS_SHOP", "money_moved": D("154000"),
                "finalized_at": datetime(2026, 10, 27, 17, 48),
                "exchanges": [{"occurred_at": datetime(2026, 10, 28, 17, 48), "signed_amount": D("14000"), "returned": ["1"],
                                "replacements": [{"item_id": "2", "karat": 21, "weight": D("4.31"), "name": "خاتم"}]}]}
        text = fixture.InvoiceHistoryViewModel._row(item)["exchangeText"]
        self.assertIn("2026-10-28", text)
        self.assertIn("154,000", text)
        self.assertIn("+", text)
        self.assertIn("14,000", text)
        self.assertIn("خاتم", text)
        self.assertIn("4.31", text)
        self.assertEqual(fixture.InvoiceHistoryViewModel._row(dict(item, exchanges=[]))["exchangeText"], "")
