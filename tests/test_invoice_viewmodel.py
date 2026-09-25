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
        self.assertEqual(self.vm.priceRows[1], {"karatId": 1, "karat": "21", "buy": "6,207.00", "sell": "6,232.00",
                                                "buyShort": "6,207", "sellShort": "6,232"})
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

    # ------------------------------------------------------------ Stage 1: one-screen workflow

    def test_invoice_making_applies_to_every_line_unless_overridden(self):
        self.vm.setSalesperson("5")
        self.vm.addBarcode("01260925")
        self.service.find_item_by_barcode = lambda b, _expected_actor=None: {**ITEM, "inventory_item_id": "101"}
        self.vm.addBarcode("02260925")
        self.vm.setInvoiceMaking("150")
        draft = self.vm._draft()
        self.assertEqual([l.making_per_gram for l in draft.sale_lines], ["150", "150"])
        self.vm.setSaleMaking(1, "PER_GRAM", "200")               # this piece only
        draft = self.vm._draft()
        self.assertEqual([l.making_per_gram for l in draft.sale_lines], ["150", "200"])
        self.assertEqual([r["makingOverride"] for r in self.vm.saleLinesModel.rows()], [False, True])
        self.vm.setSaleMaking(1, "PER_GRAM", "")                  # cleared -> back to the invoice making
        self.assertEqual([l.making_per_gram for l in self.vm._draft().sale_lines], ["150", "150"])

    def test_invoice_old_gold_discount_percent_applies_to_every_line(self):
        self.vm.addOldGold(1, "5")
        self.vm.addOldGold(2, "3")
        self.vm.setOldGoldDiscountPercent("1.5")
        lines = self.vm._draft().old_gold_lines
        self.assertEqual([(l.discount_type, l.discount_value) for l in lines], [("PERCENT", "1.5")] * 2)
        self.vm.setOldGoldDiscountPercent("0")
        lines = self.vm._draft().old_gold_lines
        self.assertEqual([(l.discount_type, l.discount_value) for l in lines], [(None, None)] * 2)

    def test_pay_full_amount_fills_the_amount_due_after_wallet(self):
        self.vm.setSalesperson("5")
        self.vm.addBarcode("01260925")
        self.vm.payFullAmount()                                   # no preview yet: nothing happens
        self.assertEqual(self.vm.cashAmount, "")
        self.preview_now()
        self.vm.setWalletAmount("1000")
        self.preview_now()
        self.vm.payFullAmount()
        self.assertEqual(self.vm.cashAmount, "66011.00")
        self.assertEqual([(p.method_code, p.amount) for p in self.vm._draft().payments],
                         [("PHYSICAL_CASH", "66011.00"), ("MOBILE_WALLET", "1000")])

    def test_salesman_wording_in_summary(self):
        self.vm.setSalesperson("5")
        self.vm.addBarcode("01260925")
        self.preview_now()
        s = self.vm.summary
        self.assertEqual((s["dueLabel"], s["dueAmount"], s["paidLabel"], s["paidAmount"]),
                         ("المطلوب من العميل", "67,011.00 ج", "المدفوع من العميل", "60,000.00 ج"))
        self.assertEqual((s["remainingLabel"], s["remainingAmount"]),
                         ("متبقي على العميل (يُسجل على حسابه)", "7,011.00 ج"))
        self.service.preview_result = preview(net_direction="SHOP_PAYS_CUSTOMER", money_received="0.00",
                                              money_paid="67011.00", residual_amount="0.00",
                                              residual_direction="NONE", settlement_status="SETTLED")
        self.vm.setCashAmount("67011")
        self.preview_now()
        s = self.vm.summary
        self.assertEqual((s["dueLabel"], s["paidLabel"], s["paidAmount"], s["remainingLabel"], s["remainingAmount"]),
                         ("المستحق للعميل", "المدفوع للعميل", "67,011.00 ج", "لا يوجد متبقٍ", ""))

    def test_weight_totals_per_karat(self):
        self.vm.addBarcode("01260925")
        self.vm.addOldGold(1, "5")
        self.vm.addOldGold(1, "2.25")
        self.vm.addOldGold(2, "x")                                # invalid weight is not counted
        totals = self.vm.weightTotals
        self.assertEqual(totals["sold"], {"18": "—", "21": "10.500", "24": "—"})
        self.assertEqual(totals["bought"], {"18": "—", "21": "7.250", "24": "—"})

    def test_salesperson_defaults_to_the_linked_employee_and_survives_new_invoice(self):
        session = AppSession()
        session._establish(AuthenticationResult(18, 5, "علي", PERMISSIONS, _VERIFIED), int(session.generation))
        vm = InlineViewModel(session, service=self.service, master_data=Master(), preview_delay_ms=0)
        vm.activate()
        self.assertEqual(vm.salespersonId, "5")
        self.assertEqual(self.vm.salespersonId, "")               # actor employee 7 is not a salesperson
        vm.addBarcode("01260925")
        vm.newInvoice()
        self.assertEqual((vm.saleCount, vm.salespersonId), (0, "5"))

    def test_unchanged_customer_keeps_the_success_banner(self):
        self.vm.setSalesperson("5")
        self.vm.addBarcode("01260925")
        self.preview_now()
        self.vm.finalizeInvoice(False, False)
        self.assertTrue(self.vm.hasSuccess)
        self.vm.setCustomer("")                                   # the picker clears after saving
        self.vm.setSalesperson("5")
        self.assertTrue(self.vm.hasSuccess)
        self.vm.addBarcode("01260925")                           # the next invoice starts
        self.assertFalse(self.vm.hasSuccess)

    # ------------------------------------------------------------ Stage 2: بحث عن قطعة

    def search_service(self):
        found = [{"inventory_item_id": "103", "barcode": None, "item_name": "دبلة", "karat_id": 1, "karat": 21,
                  "weight_grams": "3.500", "factory_name": "مصنع النور", "size": "18", "category_name": None},
                 {"inventory_item_id": "104", "barcode": "04260925", "item_name": "دبلة", "karat_id": 1,
                  "karat": 21, "weight_grams": "3.520", "factory_name": None, "size": None, "category_name": None}]

        def search(**kwargs):
            self.service.calls.append(("search", kwargs))
            return {"items": [r for r in found if r["inventory_item_id"] not in kwargs["exclude_ids"]],
                    "truncated": False}
        self.service.search_sellable_items = search

    def test_search_shows_candidates_and_never_picks_one(self):
        self.search_service()
        self.vm.searchItems("دبلة", 1, "3.5", "0.05", "")
        self.assertEqual(self.vm.saleCount, 0)                          # nothing added by searching
        rows = self.vm.searchResults
        self.assertEqual([r["title"].replace("‏", "") for r in rows],
                         ["دبلة | عيار 21 | 3.500 جم | مصنع النور | مقاس 18", "دبلة | عيار 21 | 3.520 جم"])
        self.assertEqual([r["barcodeText"] for r in rows], ["بدون باركود", "04260925"])
        self.assertEqual([(r["itemName"], r["weightText"], r["karatText"], r["detailText"]) for r in rows],
                         [("دبلة", "3.500 جم", "عيار 21", "مصنع النور • مقاس 18"), ("دبلة", "3.520 جم", "عيار 21", "")])
        self.assertEqual(self.vm.searchMessage, "2 قطعة — اختر القطعة التي أمامك")
        call = self.service.calls[-1][1]
        self.assertEqual((call["karat_id"], call["weight"], call["tolerance"], call["exclude_ids"]),
                         (1, "3.5", "0.05", ()))

    def test_explicit_choice_adds_the_exact_piece_marked_search(self):
        self.search_service()
        self.vm.setSalesperson("5")
        self.vm.searchItems("", -1, "", "0.05", "")
        self.assertFalse(self.vm.addSearchedItem("999"))                # not a shown candidate
        self.assertTrue(self.vm.addSearchedItem("103"))
        self.assertEqual(self.vm.saleCount, 1)
        line = self.vm._draft().sale_lines[0]
        self.assertEqual((line.inventory_item_id, line.identified_by), ("103", "SEARCH"))
        self.assertEqual(self.vm.saleLinesModel.rows()[0]["identifiedBy"], "SEARCH")
        self.assertEqual([r["inventoryItemId"] for r in self.vm.searchResults], ["104"])
        self.vm.searchItems("", -1, "", "0.05", "")                     # the added piece is excluded
        self.assertEqual(self.service.calls[-1][1]["exclude_ids"], ("103",))
        self.vm.addBarcode("01260925")
        self.assertEqual([l.identified_by for l in self.vm._draft().sale_lines], ["SEARCH", "SCAN"])

    def test_barcode_not_found_offers_search(self):
        from services.invoice_service import InvoiceError

        def missing(barcode, _expected_actor=None):
            raise InvoiceError("BARCODE_NOT_FOUND", "لا توجد قطعة بالباركود 9.")
        self.service.find_item_by_barcode = missing
        self.vm.addBarcode("9")
        self.assertTrue(self.vm.barcodeNotFound)
        self.service.find_item_by_barcode = lambda b, _expected_actor=None: dict(ITEM)
        self.vm.addBarcode("01260925")
        self.assertFalse(self.vm.barcodeNotFound)

    def test_missing_making_uses_salesman_wording(self):
        from services.invoice_calculator import InvoiceCalculationError

        def refuse(draft, _expected_actor=None):
            raise InvoiceCalculationError("MAKING_REQUIRED", "حدد المصنعية إما للجرام أو إجمالياً (وليس الاثنين).")
        self.service.preview = refuse
        self.vm.setSalesperson("5")
        self.vm.addBarcode("01260925")
        self.preview_now()
        self.assertEqual(self.vm.previewError, "أدخل المصنعية/جم (Ctrl+M).")

    def test_formatting_helpers(self):
        self.assertEqual(money("1234.5"), "1,234.50 ج")
        self.assertEqual(grams("10.5"), "10.500 جم")
        self.assertEqual((balance_text("0"), balance_text("-12.5")), ("لا يوجد رصيد", "للعميل 12.50 ج"))
        self.assertEqual(money(None), "")


if __name__ == "__main__":
    unittest.main()
