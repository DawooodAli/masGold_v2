"""InvoiceViewModel logic without threads or a database (calls run inline)."""
from decimal import Decimal
import unittest
from unittest.mock import patch

import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication

from app.session import AppSession
from app.viewmodels.invoice_viewmodel import InvoiceViewModel, LinesModel, balance_text, grams, money
from database.connection import DatabaseConnection
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.gold_price_service import PriceChanged


PERMISSIONS = frozenset({"APP_LOGIN", "INVOICE_CREATE", "INVOICE_FINALIZE", "PAYMENT_RECORD"})
BOARD = {"price_token": "T1", "price_status": "FRESH", "requires_price_acknowledgement": False,
         "price_warnings": [], "prices": [{"karat_id": 2, "karat": 18, "shop_buy": "5320.00", "shop_sell": "5341.00"},
                                          {"karat_id": 1, "karat": 21, "shop_buy": "6207.00", "shop_sell": "6232.00"}]}
ITEM = {"inventory_item_id": "100", "barcode": "01260925", "item_name": "خاتم", "karat_id": 1, "karat": 21,
        "weight_grams": "10.500"}


def preview(**overrides):
    result = {"price_token": "T1", "price_status": "FRESH", "requires_price_acknowledgement": False,
              "price_warnings": [], "invoice_type": "SELL", "sale_total": "67011.00", "old_gold_total": "0.00",
              "net_amount": "67011.00", "net_direction": "CUSTOMER_PAYS_SHOP", "money_received": "60000.00",
              "money_paid": "0.00", "residual_amount": "7011.00", "residual_direction": "CUSTOMER_OWES_SHOP",
              "settlement_status": "PARTIAL", "customer_balance_before": "-50.00",
              "customer_balance_after": "6961.00", "dead_cash_warning_active": False,
              "requires_dead_cash_confirmation": False, "blocking_problems": [],
              "sale_lines": [{"gold_price_per_gram": "6232.0000", "making_total": "1575.00",
                              "line_total": "67011.00"}], "old_gold_lines": []}
    result.update(overrides)
    return result


class Service:
    def __init__(self):
        self.calls, self.preview_result, self.finalize_error = [], preview(), None

    def price_board(self, *, _expected_actor=None):
        self.calls.append("price_board")
        return BOARD

    def find_item_by_barcode(self, barcode, *, _expected_actor=None):
        self.calls.append(("barcode", barcode))
        return dict(ITEM)

    def preview(self, draft, *, _expected_actor=None):
        self.calls.append(("preview", draft))
        return self.preview_result

    def finalize(self, draft, token, *, stale_acknowledged, dead_cash_confirmed, _expected_actor=None):
        self.calls.append(("finalize", draft, token, stale_acknowledged, dead_cash_confirmed))
        if self.finalize_error:
            raise self.finalize_error
        return {"invoice_id": "901", "invoice_number": "26092501", "finalized_at": "2026-09-25 14:30:05",
                "invoice_type": "SELL", "sale_total": "67011.00", "old_gold_total": "0.00", "net_amount": "67011.00",
                "net_direction": "CUSTOMER_PAYS_SHOP", "money_received": "60000.00", "money_paid": "0.00",
                "residual_amount": "7011.00", "residual_direction": "CUSTOMER_OWES_SHOP",
                "settlement_status": "PARTIAL"}


class Master:
    def get_employees(self):
        return [{"id": 5, "employee_code": "E5", "name": "علي", "notes": None}]


class InlineViewModel(InvoiceViewModel):
    """Runs each background call inline through the real completion path."""

    def _start(self, operation, call):
        actor = self._session.current_actor()
        if actor is None:
            return
        self._request += 1
        self._latest[operation] = self._request
        self._busy_ops.add(operation)
        result, error, code, kind = None, "", "", ""
        try:
            result = call(_expected_actor=actor)
        except (ValueError, PermissionError) as exc:
            error, code, kind = str(exc), getattr(exc, "code", ""), type(exc).__name__
        self._completed(self._request, str(actor.generation), operation, result, error, code, kind)


class InvoiceViewModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # QGuiApplication so the order of test modules in one process never matters.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        guard = patch.object(DatabaseConnection, "connect", side_effect=AssertionError("no database"))
        guard.start()
        self.addCleanup(guard.stop)
        self.session = AppSession()
        self.session._establish(AuthenticationResult(17, 7, "موظف", PERMISSIONS, _VERIFIED), int(self.session.generation))
        self.service = Service()
        self.vm = InlineViewModel(self.session, service=self.service, master_data=Master(), preview_delay_ms=0)
        self.vm.activate()

    def preview_now(self):
        self.vm._timer.stop()
        self.vm._start_preview()

    def test_construction_touches_no_database_or_service(self):
        vm = InvoiceViewModel(self.session)
        self.assertIsNone(vm._service)
        self.assertEqual((vm.saleCount, vm.canFinalize, vm.hasSuccess), (0, False, False))

    def test_reference_data_and_price_rows(self):
        self.assertEqual(self.vm.salespeople, [{"employeeId": "5", "name": "علي"}])
        self.assertEqual(self.vm.priceRows[1], {"karatId": 1, "karat": "21", "buy": "6,207.00", "sell": "6,232.00"})
        self.assertEqual(self.vm.priceStatusText, "")

    def test_draft_mapping_sends_choices_only(self):
        self.vm.setSalesperson("5")
        self.vm.setCustomer("12")
        self.vm.addBarcode("01260925")
        self.vm.setSaleMaking(0, "TOTAL", "500")
        self.vm.addOldGold(1, "5")
        self.vm.setOldGoldDiscount(0, "PERCENT", "1.5")
        self.vm.setCashAmount("1000")
        self.vm.setWalletAmount("0")
        draft = self.vm._draft()
        self.assertEqual((draft.salesperson_employee_id, draft.customer_id), (5, "12"))
        self.assertEqual((draft.sale_lines[0].making_per_gram, draft.sale_lines[0].making_total), (None, "500"))
        self.assertEqual((draft.old_gold_lines[0].karat_id, draft.old_gold_lines[0].weight_grams,
                          draft.old_gold_lines[0].discount_type, draft.old_gold_lines[0].discount_value),
                         (1, "5", "PERCENT", "1.5"))
        self.assertEqual([(p.method_code, p.amount) for p in draft.payments], [("PHYSICAL_CASH", "1000")])
        self.vm.setOldGoldDiscount(0, "NONE", "9")
        self.vm.setSaleMaking(0, "PER_GRAM", "")
        draft = self.vm._draft()
        self.assertEqual((draft.old_gold_lines[0].discount_type, draft.old_gold_lines[0].discount_value), (None, None))
        self.assertEqual((draft.sale_lines[0].making_per_gram, draft.sale_lines[0].making_total), (None, None))

    def test_preview_drives_summary_and_finalize_gate(self):
        self.vm.addBarcode("01260925")
        self.preview_now()
        self.assertEqual(self.vm.previewError, "اختر البائع.")
        self.assertFalse(self.vm.canFinalize)
        self.vm.setSalesperson("5")
        self.vm.setSaleMaking(0, "PER_GRAM", "150")
        self.preview_now()
        self.assertTrue(self.vm.canFinalize)
        summary = self.vm.summary
        self.assertEqual((summary["saleTotal"], summary["netText"], summary["residualText"]),
                         ("67,011.00 ج", "على العميل 67,011.00 ج", "متبقي على العميل 7,011.00 ج"))
        self.assertEqual((self.vm.customerBalanceText, self.vm.customerBalanceAfterText),
                         ("للعميل 50.00 ج", "على العميل 6,961.00 ج"))
        self.assertEqual(self.vm.saleLinesModel.rows()[0]["lineTotal"], "67,011.00 ج")
        self.vm.setCashAmount("1")                               # edit after preview: gate closes
        self.assertFalse(self.vm.canFinalize)
        self.assertEqual(self.vm.summary["saleTotal"], "")

    def test_stale_preview_results_are_ignored(self):
        self.vm.setSalesperson("5")
        self.vm.addBarcode("01260925")
        self.vm.setSaleMaking(0, "PER_GRAM", "150")
        version = self.vm._version
        self.vm._on_preview((version - 1, preview(sale_total="1.00")), "", "", "")
        self.assertFalse(self.vm.previewReady)
        self.vm._on_preview((version, preview()), "", "", "")
        self.assertTrue(self.vm.previewReady)

    def test_blocking_problems_and_confirmation_flags(self):
        self.service.preview_result = preview(blocking_problems=[{"code": "CUSTOMER_REQUIRED",
                                                                  "message": "يجب اختيار عميل."}],
                                              requires_dead_cash_confirmation=True, dead_cash_warning_active=True,
                                              requires_price_acknowledgement=True)
        self.vm.setSalesperson("5")
        self.vm.addBarcode("01260925")
        self.vm.setSaleMaking(0, "PER_GRAM", "150")
        self.preview_now()
        self.assertEqual(self.vm.blockingProblems, ["يجب اختيار عميل."])
        self.assertFalse(self.vm.canFinalize)
        self.assertTrue(self.vm.deadCashWarning and self.vm.requiresDeadCashConfirmation
                        and self.vm.requiresPriceAcknowledgement)

    def test_payment_label_follows_net_direction(self):
        self.service.preview_result = preview(net_direction="SHOP_PAYS_CUSTOMER")
        self.vm.setSalesperson("5")
        self.vm.addOldGold(1, "5")
        self.preview_now()
        self.assertEqual(self.vm.paymentLabel, "مبلغ مدفوع للعميل")

    def test_duplicate_barcode_and_missing_old_gold_inputs(self):
        self.vm.addBarcode("01260925")
        self.vm.addBarcode("01260925")
        self.assertEqual((self.vm.saleCount, self.vm.errorMessage), (1, "القطعة مضافة بالفعل إلى الفاتورة."))
        self.vm.addOldGold(9, "5")
        self.assertEqual(self.vm.errorMessage, "اختر عيار الكسر.")
        self.vm.addOldGold(1, "")
        self.assertEqual((self.vm.errorMessage, self.vm.oldGoldCount), ("أدخل وزن الكسر.", 0))

    def test_finalize_success_keeps_salesperson_and_reference_data(self):
        self.vm.setSalesperson("5")
        self.vm.addBarcode("01260925")
        self.vm.setSaleMaking(0, "PER_GRAM", "150")
        self.preview_now()
        numbers = []
        self.vm.finalized.connect(numbers.append)
        self.vm.finalizeInvoice(True, False)
        _, _, token, stale, dead = self.service.calls[-2]         # price board refresh follows
        self.assertEqual((token, stale, dead), ("T1", True, False))
        self.assertEqual(numbers, ["26092501"])
        self.assertEqual(self.vm.success["invoiceNumber"], "26092501")
        self.assertEqual(self.vm.success["residualText"], "متبقي على العميل 7,011.00 ج")
        self.assertEqual((self.vm.saleCount, self.vm.salespersonId, len(self.vm.priceRows)), (0, "5", 2))
        self.vm.newInvoice()
        self.assertFalse(self.vm.hasSuccess)

    def test_finalize_price_change_refreshes_prices_and_keeps_draft(self):
        self.vm.setSalesperson("5")
        self.vm.addBarcode("01260925")
        self.vm.setSaleMaking(0, "PER_GRAM", "150")
        self.preview_now()
        self.service.finalize_error = PriceChanged("تغيرت أسعار الذهب؛ راجع الأسعار وأكدها من جديد.")
        boards = self.service.calls.count("price_board")
        self.vm.finalizeInvoice(False, False)
        self.assertIn("تغيرت أسعار الذهب", self.vm.errorMessage)
        self.assertEqual(self.service.calls.count("price_board"), boards + 1)
        self.assertEqual(self.vm.saleCount, 1)
        self.assertFalse(self.vm.canFinalize)                    # a new preview is required

    def test_results_after_logout_are_dropped(self):
        self.vm.setSalesperson("5")
        self.vm.addBarcode("01260925")
        generation = str(self.session.current_actor().generation)
        self.session.logout()
        self.vm._completed(self.vm._request, generation, "barcode", dict(ITEM, inventory_item_id="101"), "", "", "")
        self.assertEqual(self.vm.saleCount, 1)

    def test_old_gold_requires_an_explicit_karat_for_every_line(self):
        self.assertEqual(self.vm.oldGoldKaratId, -1)                  # no preselected karat
        self.vm.addOldGold(-1, "5")
        self.assertEqual((self.vm.oldGoldCount, self.vm.errorMessage), (0, "اختر عيار الكسر."))
        self.vm.setOldGoldKarat(9)                                     # unknown karat is not accepted
        self.assertEqual(self.vm.oldGoldKaratId, -1)
        self.vm.setOldGoldKarat(1)
        self.vm.addOldGold(-1, "5")
        self.assertEqual((self.vm.oldGoldCount, self.vm.oldGoldLinesModel.rows()[0]["karat"]), (1, "21"))
        self.assertEqual(self.vm.oldGoldKaratId, -1)                  # next line needs its own choice
        self.vm.setOldGoldKarat(2)
        self.vm.newInvoice()
        self.assertEqual(self.vm.oldGoldKaratId, -1)

    def test_price_board_follows_the_global_price_set(self):
        class Source(QObject):
            changed = Signal()

            def __init__(self):
                super().__init__()
                self.priceToken, self.status = "T1", "FRESH"

        source = Source()
        vm = InlineViewModel(self.session, service=self.service, master_data=Master(), preview_delay_ms=0)
        vm.attach_price_source(source)
        boards = self.service.calls.count("price_board")
        source.priceToken = "T0"
        source.changed.emit()                                          # page not opened yet: no read
        self.assertEqual(self.service.calls.count("price_board"), boards)
        vm.activate()                                                  # board T1
        self.assertEqual(self.service.calls.count("price_board"), boards + 1)
        source.priceToken = "T1"
        source.changed.emit()                                          # same set as the board: no reload
        self.assertEqual(self.service.calls.count("price_board"), boards + 1)
        source.priceToken = "T2"
        source.changed.emit()                                          # newly published set: reload
        source.changed.emit()                                          # repeated signal: no extra reload
        self.assertEqual(self.service.calls.count("price_board"), boards + 2)
        source.status = "STALE_ALLOWED"
        source.changed.emit()                                          # status change also reloads
        self.assertEqual(self.service.calls.count("price_board"), boards + 3)

    def test_lines_model_updates_rows_in_place(self):
        model = LinesModel(("a",))
        resets, changes = [], []
        model.modelReset.connect(lambda: resets.append(1))
        model.dataChanged.connect(lambda *args: changes.append(1))
        model.set_rows([{"a": "1"}])
        model.set_rows([{"a": "2"}])
        model.set_rows([{"a": "2"}])
        self.assertEqual((len(resets), len(changes)), (1, 1))

    def test_formatting_helpers(self):
        self.assertEqual(money("1234.5"), "1,234.50 ج")
        self.assertEqual(grams("10.5"), "10.500 جم")
        self.assertEqual((balance_text("0"), balance_text("-12.5")), ("لا يوجد رصيد", "للعميل 12.50 ج"))
        self.assertEqual(money(None), "")


if __name__ == "__main__":
    unittest.main()
