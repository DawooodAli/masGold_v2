"""Return History presentation and event-driven refresh; no database/business writes."""
import copy
from datetime import date
from decimal import Decimal as D
import unittest

from PySide6.QtCore import QCoreApplication

from services.invoice_history_service import InvoiceHistoryService
from tests import test_invoice_history as fixture
from tests import test_invoice_history_qml as qml_fixture


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
        self.assertIn("18,751.00", card["valuation"])
        self.assertIn("فرق التقريب", card["valuation"])
        self.assertEqual(card["total"], "18,750.00 ج")
        row.update(valuation_mode="CURRENT_BUY", rounding_adjustment="0", gold_amount="18000", making_refund="750")
        row["lines"][0].update(shop_buy_price_per_gram="6000", gold_amount="18000", making_refund="750")
        card = vm.returns[0]
        self.assertIn("6,000.00", card["pieces"][0]["price"])
        self.assertEqual((card["pieces"][0]["gold"], card["pieces"][0]["making"]), ("18,000.00 ج", "750.00 ج"))


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
        self.assertEqual(self.item("historyReturnTotal_0").property("text"), "18,750.00 ج")
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
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            self.root.setWidth(width); self.root.setHeight(height)
            QCoreApplication.processEvents()
            self.assertEqual(vm.returnTracking[0]["remaining"], "المتبقي: 0.000 جرام — 0.00 ج")
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
    for cls in (SummaryTests, RefreshTests, HistoryUxQmlTests):
        suite.addTests(cls(n) for n in loader.getTestCaseNames(cls)
                       if cls is not HistoryUxQmlTests or n.startswith("test_ux_"))
    return suite
