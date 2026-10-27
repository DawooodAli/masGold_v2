"""InvoiceViewModel logic without threads or a database (calls run inline)."""
from decimal import Decimal
import unittest
from unittest.mock import patch

import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication

from app.session import AppSession
from app.viewmodels.invoice_viewmodel import (
    InvoiceViewModel, LinesModel, account_text, balance_text, grams, money,
)
from database.connection import DatabaseConnection
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.gold_price_service import PriceChanged
from services.invoice_service import InvoiceError


PERMISSIONS = frozenset({"APP_LOGIN", "INVOICE_CREATE", "INVOICE_FINALIZE", "PAYMENT_RECORD"})
BOARD = {"price_token": "T1", "price_status": "FRESH", "requires_price_acknowledgement": False,
         "price_warnings": [], "prices": [{"karat_id": 2, "karat": 18, "shop_buy": "5320.00", "shop_sell": "5341.00"},
                                          {"karat_id": 1, "karat": 21, "shop_buy": "6207.00", "shop_sell": "6232.00"}]}
REFERENCE = {"item_types": [{"id": 2, "name": "خاتم"}, {"id": 4, "name": "دبلة"}],
             "factories": [{"id": 5, "name": "لوكس"}],
             "old_gold_stock": [{"karat_id": 1, "karat": 21, "grams": "120.500"}],
             "settings": {"settlement_self_limit": "50.00", "settlement_approval_limit": "500.00",
                          "old_gold_discount_default_percent": "1.50"},
             "payment_methods": [{"code": "PHYSICAL_CASH", "requires_bank_account": False,
                                  "affects_physical_cash": True, "account_type": None},
                                 {"code": "MOBILE_WALLET", "requires_bank_account": True,
                                  "affects_physical_cash": False, "account_type": "MOBILE_WALLET"},
                                 {"code": "INSTAPAY", "requires_bank_account": True,
                                  "affects_physical_cash": False, "account_type": "BANK"},
                                 {"code": "BANK_TRANSFER", "requires_bank_account": True,
                                  "affects_physical_cash": False, "account_type": "BANK"}],
             "bank_accounts": [{"id": "7", "name": "حساب المحل", "account_type": "BANK", "bank_name": "CIB",
                                "account_label": "جاري"},
                               {"id": "8", "name": "حساب ثانٍ", "account_type": "BANK", "bank_name": "NBE",
                                "account_label": ""},
                               {"id": "9", "name": "محفظة المحل", "account_type": "MOBILE_WALLET",
                                "bank_name": "فودافون كاش", "account_label": ""}]}
ITEM = {"inventory_item_id": "100", "barcode": "01260925", "item_name": "خاتم", "karat_id": 1, "karat": 21,
        "weight_grams": "10.500", "factory_name": "لوكس", "size": None, "item_notes": "فص أزرق"}


def preview(**overrides):
    result = {"price_token": "T1", "price_status": "FRESH", "requires_price_acknowledgement": False,
              "price_warnings": [], "invoice_type": "SELL", "sale_total": "67011.00", "old_gold_total": "0.00",
              "net_amount": "67011.00", "net_direction": "CUSTOMER_PAYS_SHOP", "money_received": "60000.00",
              "money_paid": "0.00", "residual_amount": "7011.00", "residual_direction": "CUSTOMER_OWES_SHOP",
              "residual_signed": "7011.00", "adjustment_signed": "0.00", "account_residual_amount": "7011.00",
              "account_residual_direction": "CUSTOMER_OWES_SHOP",
              "settlement_status": "PARTIAL", "customer_balance_before": "-50.00",
              "customer_balance_after": "6961.00", "dead_cash_warning_active": False,
              "requires_dead_cash_confirmation": False, "blocking_problems": [],
              "gold_value_total": "65436.00", "effective_sale_total": "67011.00", "making_total": "1575.00",
              "average_making_per_gram": "150.00", "making_review_required": False,
              "payment_direction": "IN", "account_signed": "7011.00", "account_amount": "7011.00",
              "gap_signed": None, "full_amount": None,
              "settlement_needs_approval": False, "new_customer_same_mobile": [],
              "sale_lines": [{"gold_price_per_gram": "6232.0000", "making_total": "1575.00",
                              "line_total": "67011.00"}], "described_lines": [], "old_gold_lines": []}
    result.update(overrides)
    return result


class Service:
    def __init__(self):
        self.calls, self.preview_result, self.finalize_error = [], preview(), None

    def price_board(self, *, _expected_actor=None):
        self.calls.append("price_board")
        return BOARD

    def reference_lists(self, *, _expected_actor=None):
        self.calls.append("reference")
        return REFERENCE

    def find_item_by_barcode(self, barcode, *, _expected_actor=None):
        self.calls.append(("barcode", barcode))
        return dict(ITEM)

    def item_search_facets(self, **kwargs):
        self.calls.append(("facets", kwargs))
        return {"item_types": [{"id": 4, "name": "دبلة", "count": 2}],
                "karats": [{"id": 1, "name": "21", "count": 2}] if kwargs.get("item_type_id") else [],
                "factories": [{"id": 5, "name": "لوكس", "count": 1}] if kwargs.get("karat_id") else []}

    def search_customers(self, text, *, _expected_actor=None):
        self.calls.append(("customers", text))
        return [{"customer_id": "12", "customer_code": "C000012", "name": "أحمد علي", "mobile": "0100",
                 "place": "طنطا", "balance": "3250.00", "dead_cash_warning": True}]

    def verify_settlement_approver(self, username, password, *, _expected_actor=None):
        from services.invoice_service import _APPROVER_PROOF, SettlementApprover
        if password != "ok":
            raise PermissionError("تعذر تسجيل الدخول. تحقق من بيانات الدخول أو راجع المسؤول.")
        return SettlementApprover(3, "المدير", _APPROVER_PROOF)

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
                "account_residual_amount": "7011.00", "account_residual_direction": "CUSTOMER_OWES_SHOP",
                "settlement_status": "PARTIAL", "customer_created": True, "unidentified_cases": 1,
                "making_review_required": False}


class Master:
    def get_employees(self):
        return [{"id": 5, "employee_code": "E5", "name": "علي", "notes": None}]


class Printer:
    """Records every print_invoice call; never touches a database."""

    def __init__(self):
        self.calls = []
        self.outcome = ("901", True, "تم إرسال الفاتورة للطابعة.")

    def print_invoice(self, invoice_id, *, _expected_actor=None):
        from services.invoice_print_service import PrintOutcome
        self.calls.append(invoice_id)
        number, printed, message = self.outcome
        return PrintOutcome(invoice_number=number, printed=printed, message=message)


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
        self.printer = Printer()
        self.vm = InlineViewModel(self.session, service=self.service, master_data=Master(),
                                  print_service=self.printer, preview_delay_ms=0)
        self.vm.activate()

    def preview_now(self):
        self.vm._timer.stop()
        self.vm._start_preview()

    def sale_ready(self):
        self.vm.setSalesperson("5")
        self.vm.addBarcode("01260925")
        self.vm.setInvoiceTotal("60000")                 # إجمالي الفاتورة; the one cash leg follows it
        self.preview_now()

    # ------------------------------------------------------------ basics

    def test_construction_touches_no_database_or_service(self):
        vm = InvoiceViewModel(self.session)
        self.assertIsNone(vm._service)
        self.assertEqual((vm.saleCount, vm.canFinalize, vm.hasSuccess), (0, False, False))

    def test_reference_data_and_default_old_gold_discount(self):
        self.assertEqual(self.vm.salespeople, [{"employeeId": "5", "name": "علي"}])
        self.assertEqual(self.vm.priceRows[1]["karat"], "21")
        self.assertEqual(self.vm.oldGoldDiscountPercent, "1.5")               # settings default, editable
        self.assertEqual([t["name"] for t in self.vm.itemTypes], ["خاتم", "دبلة"])
        self.assertEqual(self.vm.oldGoldStock, [{"karatId": 1, "karat": "21", "grams": "120.500", "breakdown": ""}])  # nothing held
        self.vm.setOldGoldDiscountPercent("2")
        self.vm.newInvoice()
        self.assertEqual(self.vm.oldGoldDiscountPercent, "1.5")               # each invoice starts at the default

    def test_draft_mapping_sends_choices_only(self):
        self.vm.setSalesperson("5")
        self.vm.addBarcode("01260925")
        self.vm.addOldGold(1, "5")
        self.vm.setPaymentDirection("OUT")
        self.vm.setInvoiceTotal("1000")
        self.vm.setAccountAmount("50")
        self.vm.setAccountDirection("SHOP_OWES")
        self.vm.setNewCustomer("عميل جديد", "0100", "المنصورة")
        draft = self.vm._draft()
        # No price of any kind: only choices, weights and the real money.
        self.assertFalse(hasattr(draft, "agreed_sale_total"))
        self.assertFalse(hasattr(draft.sale_lines[0], "making_total"))
        self.assertEqual((draft.salesperson_employee_id, draft.customer_id, draft.payment_direction,
                          draft.account_amount, draft.account_direction), (5, None, "OUT", "50", "SHOP_OWES"))
        self.assertEqual((draft.old_gold_lines[0].discount_type, draft.old_gold_lines[0].discount_value), ("PERCENT", "1.5"))
        # 50 of the 1,000 moved to the customer account: 950 is the real money now.
        self.assertEqual([(p.method_code, p.amount, p.direction) for p in draft.payments], [("PHYSICAL_CASH", "950", None)])
        self.assertEqual((draft.new_customer.name, draft.new_customer.place), ("عميل جديد", "المنصورة"))
        self.assertEqual(draft.invoice_total, "1000")                          # the authoritative total
        self.assertFalse(hasattr(self.vm, "setAgreedSaleTotal"))

    def test_preview_drives_money_strip_and_finalize_gate(self):
        self.vm.addBarcode("01260925")
        self.preview_now()
        self.assertEqual(self.vm.previewError, "اختر البائع.")
        self.sale_ready()
        self.assertTrue(self.vm.canFinalize)
        s = self.vm.summary
        self.assertEqual((s["goldValue"], s["effectiveSale"], s["averageMaking"]), ("65,436.00 ج", "67,011.00 ج", "150.00"))
        self.assertNotIn("makingTotal", s)                      # the salesman never sees total making
        self.assertEqual((s["dueLabel"], s["dueAmount"], s["paidAmount"], s["differenceAmount"]),
                         ("المستلم من العميل", "60,000.00 ج", "60,000.00 ج", ""))
        self.assertEqual(s["accountEffect"], "على حساب العميل (عليه) 7,011.00 ج")
        self.assertEqual(self.vm.saleLinesModel.rows()[0]["lineTotal"], "67,011.00")
        self.vm.setInvoiceTotal("1")                             # edit after preview: gate closes
        self.assertFalse(self.vm.canFinalize)

    def test_stale_preview_results_are_ignored(self):
        self.sale_ready()
        version = self.vm._version
        self.vm._on_preview((version - 1, preview(sale_total="1.00")), "", "", "")
        self.assertEqual(self.vm.summary["saleTotal"], "67,011.00 ج")

    def test_blocking_problems_and_confirmation_flags(self):
        self.service.preview_result = preview(blocking_problems=[{"code": "CUSTOMER_REQUIRED",
                                                                  "message": "يجب اختيار عميل."}],
                                              requires_dead_cash_confirmation=True, dead_cash_warning_active=True,
                                              requires_price_acknowledgement=True, making_review_required=True)
        self.sale_ready()
        self.assertEqual(self.vm.blockingProblems, ["يجب اختيار عميل."])
        self.assertFalse(self.vm.canFinalize)
        self.assertTrue(self.vm.deadCashWarning and self.vm.requiresDeadCashConfirmation
                        and self.vm.requiresPriceAcknowledgement and self.vm.makingReviewRequired)

    def test_duplicate_barcode_and_missing_old_gold_inputs(self):
        self.vm.addBarcode("01260925")
        self.vm.addBarcode("01260925")
        self.assertEqual((self.vm.saleCount, self.vm.errorMessage), (1, "هذه القطعة مضافة بالفعل إلى الفاتورة"))
        self.vm.addOldGold(9, "5")
        self.assertEqual(self.vm.errorMessage, "اختر عيار الكسر.")
        self.vm.addOldGold(1, "")
        self.assertEqual((self.vm.errorMessage, self.vm.oldGoldCount), ("أدخل وزن الكسر.", 0))

    def test_finalize_success_resets_and_keeps_salesperson(self):
        self.sale_ready()
        numbers = []
        self.vm.finalized.connect(numbers.append)
        self.vm.finalizeInvoice(True, False)
        finalize = next(c for c in self.service.calls if c[0] == "finalize")
        self.assertEqual(finalize[2:], ("T1", True, False))
        self.assertEqual(numbers, ["26092501"])
        self.assertEqual(self.vm.success["notes"], "تم تسجيل العميل الجديد • قطعة يدوية تحتاج مراجعة المدير")
        self.assertEqual((self.vm.saleCount, self.vm.salespersonId, self.vm.paymentAmount), (0, "5", ""))

    # ---- Save Only vs Save + Print: printing is a separate step after finalize
    def test_save_only_finalizes_and_never_prints(self):
        self.sale_ready()
        self.vm.finalizeInvoice(False, False, False)
        self.assertEqual(self.vm.success["invoiceNumber"], "26092501")
        self.assertEqual(self.printer.calls, [])
        self.assertFalse(self.vm.printBusy)
        self.assertEqual(self.vm.printMessage, "")

    def test_save_and_print_finalizes_once_and_submits_one_print_job(self):
        self.sale_ready()
        self.vm.finalizeInvoice(False, False, True)
        finalize_calls = [c for c in self.service.calls if c[0] == "finalize"]
        self.assertEqual(len(finalize_calls), 1)                        # exactly one finalize
        self.assertEqual(self.printer.calls, ["901"])                   # exactly one print job, the real id
        self.assertTrue(self.vm.printSucceeded)
        self.assertFalse(self.vm.printFailed)

    # ---- كسر فقط = حفظ فقط: old gold only has no print step; SALE and MIXED keep theirs
    def old_gold_only_ready(self):
        self.service.preview_result = preview(payment_direction="OUT", gold_value_total=None, gap_signed=None,
                                              invoice_type="BUY", sale_total="0.00", old_gold_total="61138.95")
        self.vm.setSalesperson("5")
        self.vm.addOldGold(1, "10")
        self.vm.setLegAmount(0, "61140")
        self.preview_now()

    def test_old_gold_only_is_not_printable_and_a_print_request_is_dropped(self):
        self.old_gold_only_ready()
        self.assertFalse(self.vm.printable)
        self.assertTrue(self.vm.canFinalize)
        self.vm.finalizeInvoice(False, False, True)                    # even an explicit print request
        self.assertEqual(len([c for c in self.service.calls if c[0] == "finalize"]), 1)    # saved normally
        self.assertEqual(self.printer.calls, [])                       # the print service is never called
        self.assertFalse(self.vm.printBusy or self.vm.printFailed or self.vm.printSucceeded)

    def test_sale_and_mixed_are_printable_and_still_print(self):
        self.sale_ready()
        self.assertTrue(self.vm.printable)
        self.vm.addOldGold(1, "5")                                    # mixed
        self.assertTrue(self.vm.printable)
        self.preview_now()
        self.vm.finalizeInvoice(False, False, True)
        self.assertEqual(self.printer.calls, ["901"])

    def test_print_failure_leaves_the_invoice_finalized(self):
        self.printer.outcome = ("901", False, "الطابعة غير متصلة.")
        self.sale_ready()
        self.vm.finalizeInvoice(False, False, True)
        self.assertEqual(self.vm.success["invoiceNumber"], "26092501")   # the save itself succeeded
        self.assertTrue(self.vm.printFailed)
        self.assertEqual(self.vm.printMessage, "الطابعة غير متصلة.")
        self.assertEqual(self.vm.printStatusText, "تعذر إرسال الفاتورة للطابعة. الطابعة غير متصلة.")
        self.assertEqual(len(self.printer.calls), 1)

    def test_retry_print_submits_another_job_for_the_same_invoice(self):
        self.printer.outcome = ("901", False, "لا يوجد ورق.")
        self.sale_ready()
        self.vm.finalizeInvoice(False, False, True)
        self.printer.outcome = ("901", True, "تم إرسال الفاتورة للطابعة.")
        finalizes = len([c for c in self.service.calls if c[0] == "finalize"])
        self.vm.retryPrint()
        self.assertEqual(self.printer.calls, ["901", "901"])             # one new print attempt only
        self.assertTrue(self.vm.printSucceeded)
        self.assertEqual(len([c for c in self.service.calls if c[0] == "finalize"]), finalizes)  # never re-saved

    # ------------------------------------------------------------ one A5 bill: at most 10 sale items

    def test_the_eleventh_sale_item_is_refused_at_once(self):
        for _ in range(10):
            self.assertEqual(self.vm.addManualLine(4, 1, 5, "4", "18", "نقش", "BARCODE_LOST"), "")
        self.assertEqual(self.vm.saleCount, 10)
        self.assertEqual(self.vm.addManualLine(4, 1, 5, "4", "18", "نقش", "BARCODE_LOST"),
                         "الحد الأقصى للفاتورة 10 أصناف بيع")
        self.assertEqual(self.vm.addOldGoldStockLine(4, "", 1, 0, "2", "", ""), "الحد الأقصى للفاتورة 10 أصناف بيع")
        lookups = len(self.service.calls)
        self.vm.addBarcode("01260925")                                   # refused before any lookup
        self.assertEqual(len(self.service.calls), lookups)
        self.assertEqual(self.vm.errorMessage, "الحد الأقصى للفاتورة 10 أصناف بيع")
        self.assertEqual(self.vm.saleCount, 10)

    def test_customer_old_gold_is_not_counted_in_the_ten(self):
        for _ in range(10):
            self.vm.addManualLine(4, 1, 5, "4", "18", "نقش", "BARCODE_LOST")
        self.vm.addOldGold(1, "5")
        self.assertEqual(len(self.vm._olds), 1)                          # old gold still accepted
        self.assertEqual(self.vm.saleCount, 10)

    def test_finalize_price_change_refreshes_prices_and_keeps_draft(self):
        self.sale_ready()
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
        self.assertEqual(self.vm.oldGoldKaratId, -1)
        self.vm.addOldGold(-1, "5")
        self.assertEqual((self.vm.oldGoldCount, self.vm.errorMessage), (0, "اختر عيار الكسر."))
        self.vm.setOldGoldKarat(1)
        self.vm.addOldGold(-1, "5")
        self.assertEqual((self.vm.oldGoldCount, self.vm.oldGoldKaratId), (1, -1))

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
        vm.activate()
        source.priceToken = "T2"
        source.changed.emit()
        source.changed.emit()
        self.assertEqual(self.service.calls.count("price_board"), boards + 2)

    def test_lines_model_updates_rows_in_place(self):
        model = LinesModel(("a",))
        resets, changes = [], []
        model.modelReset.connect(lambda: resets.append(1))
        model.dataChanged.connect(lambda *args: changes.append(1))
        model.set_rows([{"a": "1"}])
        model.set_rows([{"a": "2"}])
        model.set_rows([{"a": "2"}])
        self.assertEqual((len(resets), len(changes)), (1, 1))

    # ---- final corrections: direction, F8, methods, settlement, review notices
    def test_direction_label_follows_who_pays(self):
        self.assertEqual(self.vm.paymentLabel, "المستلم من العميل")            # before any preview
        self.sale_ready()
        self.assertEqual((self.vm.paymentDirection, self.vm.paymentLabel), ("IN", "المستلم من العميل"))
        self.assertFalse(self.vm.directionChoosable)                           # a sale only is always IN
        self.service.preview_result = preview(payment_direction="OUT", net_direction="SHOP_PAYS_CUSTOMER",
                                              money_received="0.00", money_paid="55000.00")
        self.vm.addOldGold(1, "20")
        self.preview_now()
        self.assertTrue(self.vm.directionChoosable)                            # an exchange can go either way
        self.assertEqual((self.vm.paymentDirection, self.vm.paymentLabel), ("OUT", "المدفوع للعميل"))
        self.assertEqual((self.vm.summary["dueLabel"], self.vm.summary["dueAmount"]), ("المدفوع للعميل", "55,000.00 ج"))
        self.vm.setPaymentDirection("IN")
        self.assertEqual(self.vm._draft().payment_direction, "IN")

    def test_f8_fills_only_a_real_full_amount(self):
        self.sale_ready()
        self.vm.setInvoiceTotal("")
        self.preview_now()
        self.assertFalse(self.vm.fullAmountAvailable)
        self.vm.payFullAmount()                                   # sold pieces: إجمالي الفاتورة is typed
        self.assertEqual(self.vm.paymentAmount, "")
        # Old gold only: the full amount exists (old-gold value minus the account part), in the OUT direction.
        self.vm.removeSaleLine(0)
        self.vm.addOldGold(1, "10")
        self.service.preview_result = preview(payment_direction="OUT", full_amount="61138.95", gold_value_total=None)
        self.preview_now()
        self.vm.addPaymentLeg()
        self.vm.setLegAmount(1, "1138.95")
        self.preview_now()
        self.vm.payFullAmount()
        self.assertEqual(self.vm.paymentAmount, "60000.00")                    # the other leg is kept

    def test_payment_legs_methods_and_real_accounts(self):
        self.sale_ready()
        self.assertEqual([(m["code"], m["text"], m["available"]) for m in self.vm.paymentMethods],
                         [("PHYSICAL_CASH", "نقدي", True), ("MOBILE_WALLET", "محفظة", True),
                          ("INSTAPAY", "InstaPay", True), ("BANK_TRANSFER", "تحويل بنكي", True)])
        self.assertFalse(self.vm.paymentNeedsAccount)
        self.vm.setPaymentMethod("MOBILE_WALLET")                               # the only wallet: chosen
        self.assertEqual((self.vm.paymentNeedsAccount, self.vm.bankAccountId), (True, "9"))
        self.vm.setPaymentMethod("INSTAPAY")                                    # two bank accounts: choose
        self.assertEqual([a["id"] for a in self.vm.bankAccounts], ["7", "8"])
        self.assertEqual(self.vm.bankAccountId, "")
        self.vm.setBankAccount("9")                                             # a wallet is not a bank
        self.assertEqual(self.vm.bankAccountId, "")
        self.vm.setBankAccount("7")
        self.vm.setInvoiceTotal("67011")
        self.vm.setLegAmount(0, "60000")
        self.vm.addPaymentLeg()                                                  # starts with the remainder
        self.assertEqual(self.vm.paymentLegs[1]["amount"], "7011")
        payments = self.vm._draft().payments
        self.assertEqual([(p.method_code, p.amount, p.bank_account_id) for p in payments],
                         [("INSTAPAY", "60000", "7"), ("PHYSICAL_CASH", "7011", None)])
        self.vm.setPaymentMethod("CARD_POS")                                   # not offered: ignored
        self.assertEqual(self.vm.paymentMethod, "INSTAPAY")
        self.vm.removePaymentLeg(1)
        self.assertEqual(len(self.vm.paymentLegs), 1)
        self.preview_now()
        self.assertEqual(self.vm.summary["paidLabel"], "المستلم من العميل (InstaPay)")

    def test_methods_without_an_account_stay_visible_but_unavailable(self):
        self.sale_ready()
        self.vm._reference = dict(REFERENCE, bank_accounts=[])
        self.assertEqual([(m["code"], m["available"], m["unavailableText"]) for m in self.vm.paymentMethods],
                         [("PHYSICAL_CASH", True, ""), ("MOBILE_WALLET", False, "لا توجد محفظة مفعلة"),
                          ("INSTAPAY", False, "لا يوجد حساب بنكي مفعّل"),
                          ("BANK_TRANSFER", False, "لا يوجد حساب بنكي مفعّل")])
        self.assertEqual([m["code"] for m in self.vm.availablePaymentMethods], ["PHYSICAL_CASH"])
        for code, text in (("MOBILE_WALLET", "لا توجد محفظة مفعلة"), ("INSTAPAY", "لا يوجد حساب بنكي مفعّل"),
                           ("BANK_TRANSFER", "لا يوجد حساب بنكي مفعّل")):
            with self.subTest(method=code):
                self.vm.setPaymentMethod(code)                                  # selectable, never usable
                leg = self.vm.paymentLegs[0]
                self.assertEqual((leg["method"], leg["unavailableText"], leg["needsAccount"], leg["account"]),
                                 (code, text, False, ""))
                self.assertIsNone(self.vm._draft().payments[0].bank_account_id)  # the service refuses it
        # Only a wallet: محفظة usable; InstaPay / bank transfer still need a bank account.
        self.vm._reference = dict(REFERENCE, bank_accounts=[{"id": "9", "name": "محفظة", "account_type": "MOBILE_WALLET"}])
        self.assertEqual([m["available"] for m in self.vm.paymentMethods], [True, True, False, False])
        self.vm._reference = dict(REFERENCE, bank_accounts=[{"id": "7", "name": "CIB", "account_type": "BANK"}])
        self.assertEqual([m["available"] for m in self.vm.paymentMethods], [True, False, True, True])
        self.vm.setPaymentMethod("PHYSICAL_CASH")
        self.vm.setPaymentMethod("BANK_TRANSFER")                               # the only bank: chosen
        self.assertEqual(self.vm.bankAccountId, "7")

    def test_manual_reasons_say_why_the_piece_was_not_identified(self):
        self.assertEqual([(r["code"], r["text"]) for r in self.vm.manualReasons],
                         [("NO_BARCODE", "لم يُنشأ لها باركود"), ("BARCODE_LOST", "الباركود مفقود أو تالف"),
                          ("NOT_FOUND", "لم أجد القطعة في البحث"), ("OTHER", "سبب آخر")])
        self.sale_ready()
        self.assertEqual(self.vm.addManualLine(4, 1, 5, "4", "", " ", "OTHER"),
                         "اكتب السبب في الملاحظات عند اختيار «سبب آخر».")
        self.assertEqual(self.vm.addManualLine(4, 1, 5, "4", "", "قطعة أمانة", "OTHER"), "")

    # ---- customer account: its own direction, never part of the price
    def test_account_part_is_taken_out_of_the_single_payment_leg(self):
        # Owner 2026-10-02: the unpaid remainder becomes على العميل automatically while typing the cash.
        self.sale_ready()
        self.vm.setInvoiceTotal("23000")
        for typed, account in (("2", "22998"), ("20", "22980"), ("200", "22800"), ("20000", "3000")):
            self.vm.setLegAmount(0, typed)                                      # typed digit by digit
            self.assertEqual(self.vm.accountAmount, account)
        self.assertEqual(self.vm.accountDirection, "CUSTOMER_OWES")              # follows the money (IN)
        self.assertEqual(self.vm.accountEffectText, "سيضاف على العميل: 3,000.00 ج")
        draft = self.vm._draft()
        self.assertEqual(([p.amount for p in draft.payments], draft.account_amount, draft.account_direction),
                         (["20000"], "3000", "CUSTOMER_OWES"))
        self.vm.setAccountAmount("5000")                                        # automatic: never typed
        self.assertEqual(self.vm.accountAmount, "3000")
        self.vm.setLegAmount(0, "23000")                                        # back: all paid now
        self.assertEqual(self.vm.accountAmount, "")
        self.vm.setAccountDirection("SHOP_OWES")                               # not offered on a sale
        self.assertEqual(self.vm.accountDirection, "CUSTOMER_OWES")

    def test_outgoing_account_part_lowers_the_money_paid_to_the_customer(self):
        self.sale_ready()
        self.vm.addOldGold(1, "30")
        self.vm.setPaymentDirection("OUT")
        self.vm.setInvoiceTotal("23000")
        self.vm.setAccountAmount("3000")
        self.assertEqual((self.vm.paymentAmount, self.vm.accountDirection), ("20000", "SHOP_OWES"))
        self.assertEqual(self.vm.accountEffectText, "سيضاف للعميل: 3,000.00 ج")
        self.assertEqual(self.vm._draft().account_direction, "SHOP_OWES")

    def test_split_legs_must_add_up_to_what_is_required_now(self):
        self.sale_ready()
        self.vm.setInvoiceTotal("23000")
        self.vm.setLegAmount(0, "5000")
        self.assertEqual(self.vm.accountAmount, "18000")                         # remainder → على العميل
        self.vm.addPaymentLeg()
        self.assertEqual((self.vm.paymentLegs[1]["amount"], self.vm.accountAmount), ("18000", ""))
        self.vm.setLegAmount(1, "19000")                                        # 5,000 + 19,000 > 23,000
        self.assertEqual(self.vm.paymentLegs[1]["amount"], "18000")             # refused, last value kept
        self.assertIn("لا يمكن أن يتجاوز إجمالي الفاتورة", self.vm.paymentWarning)
        self.vm.setLegAmount(1, "15000")
        self.assertEqual((self.vm.accountAmount, self.vm._required_problem()), ("3000", ""))
        self.assertEqual(self.vm.paymentAmount, "5000")                         # never reduced arbitrarily
        self.assertEqual([p.amount for p in self.vm._draft().payments], ["5000", "15000"])
        self.vm.removePaymentLeg(1)
        self.assertEqual(self.vm.accountAmount, "18000")                         # its money goes back on the account

    # ---- إجمالي الفاتورة: one total; payments and the account only allocate it
    def total_ready(self, total="10000"):
        self.vm.setSalesperson("5")
        self.vm.addBarcode("01260925")
        self.assertTrue(self.vm.totalMode)
        self.vm.setInvoiceTotal(total)

    def amounts(self):
        return [leg["amount"] for leg in self.vm.paymentLegs]

    def test_example_a_total_defaults_one_cash_leg_to_the_full_amount(self):
        self.total_ready()
        self.assertEqual((self.vm.invoiceTotal, self.amounts(), self.vm.paymentMethod), ("10000", ["10000"], "PHYSICAL_CASH"))
        self.assertEqual((self.vm.remainingText, self.vm._required_problem()), ("", ""))
        self.vm.setInvoiceTotal("12000")                                        # the untouched leg follows
        self.assertEqual(self.amounts(), ["12000"])
        self.assertEqual(self.vm._draft().invoice_total, "12000")

    def test_example_b_reduced_cash_then_add_starts_with_the_remainder(self):
        self.total_ready()
        self.vm.setLegAmount(0, "6000")
        self.assertEqual((self.vm.remainingText, self.vm.accountAmount), ("", "4000"))   # على العميل, automatic
        self.assertTrue(self.vm.canAddPayment)
        self.vm.addPaymentLeg()
        self.assertEqual((self.amounts(), self.vm.accountAmount), (["6000", "4000"], ""))
        self.vm.setLegMethod(1, "MOBILE_WALLET")
        self.assertEqual((self.vm.remainingText, self.vm.canAddPayment), ("", False))
        draft = self.vm._draft()
        self.assertEqual(([(p.method_code, p.amount) for p in draft.payments], draft.invoice_total),
                         ([("PHYSICAL_CASH", "6000"), ("MOBILE_WALLET", "4000")], "10000"))
        self.assertEqual(self.vm.invoiceTotal, "10000")                        # never changed by the split

    def test_example_c_an_overpayment_is_refused_and_the_last_value_kept(self):
        self.total_ready()
        self.vm.setLegAmount(0, "6000")
        self.vm.addPaymentLeg()
        self.vm.setLegAmount(1, "5000")                                         # 6,000 + 5,000 > 10,000
        self.assertEqual(self.amounts(), ["6000", "4000"])                     # last valid value
        self.assertEqual(self.vm.paymentWarning, "مجموع طرق الدفع لا يمكن أن يتجاوز إجمالي الفاتورة")
        self.assertEqual(self.vm.invoiceTotal, "10000")
        self.vm.setLegAmount(1, "3000")                                         # a valid edit clears the warning
        self.assertEqual((self.vm.paymentWarning, self.vm.accountAmount), ("", "1000"))

    def test_example_d_three_methods_each_start_with_the_current_remainder(self):
        self.total_ready()
        self.vm.setLegAmount(0, "4000")
        self.vm.addPaymentLeg()
        self.assertEqual(self.amounts(), ["4000", "6000"])
        self.vm.setLegAmount(1, "3000")
        self.vm.addPaymentLeg()
        self.assertEqual(self.amounts(), ["4000", "3000", "3000"])
        self.vm.setLegMethod(1, "MOBILE_WALLET")
        self.vm.setLegMethod(2, "BANK_TRANSFER")
        self.vm.setLegAccount(2, "7")
        self.assertEqual((self.vm._required_problem(), self.vm.canAddPayment), ("", False))

    def test_example_e_the_remainder_is_on_the_customer_account_automatically(self):
        self.total_ready()
        self.vm.setLegAmount(0, "6000")
        self.assertEqual((self.vm.accountAmount, self.vm._required_problem()), ("4000", ""))
        self.assertTrue(self.vm.accountAutomatic)
        draft = self.vm._draft()
        self.assertEqual(([p.amount for p in draft.payments], draft.account_amount, draft.invoice_total),
                         (["6000"], "4000", "10000"))
        self.vm.setAccountAmount("5000")                                        # never typed by hand
        self.vm.setAccountDirection("CUSTOMER_OWES")
        self.assertEqual(self.vm.accountAmount, "4000")

    def test_nothing_to_add_when_nothing_remains_and_no_zero_legs(self):
        self.total_ready()
        self.assertFalse(self.vm.canAddPayment)
        self.vm.addPaymentLeg()
        self.assertEqual(self.amounts(), ["10000"])
        self.assertEqual(self.vm.paymentWarning, "لا يوجد مبلغ متبقٍ لإضافة طريقة دفع.")

    def test_a_smaller_total_never_redistributes_a_manual_split(self):
        self.total_ready()
        self.vm.setLegAmount(0, "6000")
        self.vm.addPaymentLeg()                                                 # 6,000 + 4,000
        self.vm.setInvoiceTotal("9000")                                         # below the real payments
        self.assertEqual((self.vm.invoiceTotal, self.amounts()), ("9000", ["6000", "4000"]))   # never redistributed
        self.assertIn("أقل من المبالغ المدفوعة", self.vm.paymentWarning)
        self.assertIn("يتجاوز إجمالي الفاتورة", self.vm._required_problem())   # Save stays blocked
        self.vm.setInvoiceTotal("11000")                                        # larger: 1,000 على العميل
        self.assertEqual((self.amounts(), self.vm.accountAmount), (["6000", "4000"], "1000"))

    def test_payments_need_a_total_first(self):
        self.vm.setSalesperson("5")
        self.vm.addBarcode("01260925")
        self.vm.setLegAmount(0, "5000")
        self.assertEqual((self.amounts(), self.vm.paymentWarning), ([""], "اكتب إجمالي الفاتورة أولاً."))

    # ---- old-gold discount: the last valid value is kept
    def test_invalid_discount_restores_the_last_valid_value(self):
        self.sale_ready()
        self.vm.addOldGold(1, "5")
        self.preview_now()
        self.assertEqual(self.vm.oldGoldDiscountPercent, "1.5")
        for rejected in ("1.00", "1.24", "5.01", "10.00", "abc", ""):
            with self.subTest(rejected=rejected):
                self.assertEqual(self.vm.commitOldGoldDiscount(rejected), "1.5")          # restored, not clamped
                self.assertEqual(self.vm.discountWarning, "النسبة المسموح بها من \u20661.25%\u2069 إلى \u20665%\u2069")
                self.assertEqual(self.vm._draft().old_gold_lines[0].discount_value, "1.5")
        self.assertEqual(self.vm.commitOldGoldDiscount("2.25"), "2.25")
        self.assertEqual(self.vm.discountWarning, "")
        self.assertEqual(self.vm.commitOldGoldDiscount("0.50"), "2.25")                   # back to the new last valid
        for boundary in ("1.25", "2.00", "5.00"):
            self.assertEqual(self.vm.commitOldGoldDiscount(boundary), boundary.rstrip("0").rstrip("."))
        self.assertEqual(self.vm._draft().old_gold_lines[0].discount_value, "5")

    def test_rejected_discount_is_never_previewed(self):
        self.sale_ready()
        self.vm.addOldGold(1, "5")
        self.preview_now()
        calls = len([c for c in self.service.calls if c[0] == "preview"])
        self.vm.commitOldGoldDiscount("1.00")
        self.preview_now()
        last = [c for c in self.service.calls if c[0] == "preview"][-1][1]
        self.assertEqual(last.old_gold_lines[0].discount_value, "1.5")                   # the rejected 1.00 never left
        self.assertGreater(len([c for c in self.service.calls if c[0] == "preview"]), calls)

    def test_old_gold_only_may_go_down_to_one_percent(self):
        self.vm.setSalesperson("5")
        self.vm.addOldGold(1, "10")
        for value in ("1", "1.25", "1.5", "2", "5"):
            with self.subTest(accepted=value):
                self.assertEqual(self.vm.commitOldGoldDiscount(value), value)
                self.assertEqual(self.vm.discountWarning, "")
        for value in ("0.99", "5.01", "0.5", "0"):
            with self.subTest(rejected=value):
                self.assertEqual(self.vm.commitOldGoldDiscount(value), "5")        # the last valid value stays
                self.assertEqual(self.vm.discountWarning, "النسبة المسموح بها من ⁦1%⁩ إلى ⁦5%⁩")
                self.assertNotEqual(self.vm.discountWarning, "")

    def test_old_gold_gap_is_settled_or_put_on_the_account(self):
        self.service.preview_result = preview(payment_direction="OUT", gap_signed="-8.00", gold_value_total=None)
        self.sale_ready()
        self.assertTrue(self.vm.hasDifference)
        self.assertEqual(self.vm.settlementChoice, "SETTLE")                    # automatic: the «تسوية الفرق» button is gone
        self.preview_now()
        self.assertEqual(self.vm.summary["differenceAmount"], "8.00")
        self.assertEqual(self.vm._draft().settlement_choice, "SETTLE")
        self.assertFalse(self.vm.settlementNeedsReason)
        self.vm.setSettlementChoice("ACCOUNT")                                  # fills the account amount
        draft = self.vm._draft()
        self.assertEqual((draft.settlement_choice, draft.account_amount, draft.account_direction),
                         (None, "8.00", "SHOP_OWES"))

    def test_old_gold_total_is_the_rounded_final_amount(self):
        """Owner request 2026-10-26: إجمالي الفاتورة of an old-gold-only invoice = what is paid + what is on the account = the final EGP-5 amount."""
        self.service.preview_result = preview(payment_direction="OUT", gap_signed="-31081.67", difference_signed="-31080.00",
                                              final_settlement_amount="31080.00", net_amount="31081.67", gold_value_total=None)
        self.sale_ready()
        self.preview_now()
        self.assertEqual(self.vm.invoiceTotalText, money("31080.00"))

    def test_account_button_uses_the_rounded_final_amount_not_the_exact_gap(self):
        # exact 9,005.22 → final 9,005; cash 9,000: the account part is 5.00, never the exact 5.22.
        self.service.preview_result = preview(payment_direction="OUT", gap_signed="-5.22", difference_signed="-5.00",
                                              final_settlement_amount="9005.00", gold_value_total=None)
        self.sale_ready()
        self.preview_now()                                                       # the automatic settlement choice re-previews once
        self.assertTrue(self.vm.hasDifference)
        self.assertEqual((self.vm.summary["differenceAmount"], self.vm.summary["settleAmount"]), ("5.00", "5.22"))
        self.vm.setSettlementChoice("ACCOUNT")
        self.assertEqual(self.vm._draft().account_amount, "5.00")
        # Rounding already automatic (money + account == final): nothing left to resolve on screen.
        self.service.preview_result = preview(payment_direction="OUT", gap_signed="-0.22", difference_signed="0.00",
                                              final_settlement_amount="9005.00", gold_value_total=None)
        self.vm.setLegAmount(0, "9000")
        self.preview_now()
        self.assertFalse(self.vm.hasDifference)

    # ---- the customer binding follows the visible text
    def test_erasing_or_editing_the_chosen_customer_text_drops_the_customer_id(self):
        self.vm.searchCustomers("أحمد")
        self.vm.chooseCustomer("12")
        self.assertEqual((self.vm.customerId, self.vm.customerInfo["state"]), ("12", "EXISTING"))
        self.vm.setNewCustomer("أحمد علي", "0100", "طنطا")               # the same text: still bound
        self.assertEqual(self.vm.customerId, "12")
        self.vm.setNewCustomer("", "", "")                                # the employee erased the fields
        self.assertEqual((self.vm.customerId, self.vm.customerInfo["state"]), ("", "NONE"))
        self.assertIsNone(self.vm._draft().customer_id)
        self.vm.chooseCustomer("12")                                      # (the suggestion list was cleared)
        self.vm.searchCustomers("أحمد")
        self.vm.chooseCustomer("12")
        self.vm.setNewCustomer("أحمد عل", "0100", "طنطا")                 # edited: no longer that customer
        self.assertEqual(self.vm.customerId, "")
        draft = self.vm._draft()
        self.assertEqual((draft.customer_id, draft.new_customer.name), (None, "أحمد عل"))

    def test_save_closes_after_the_customer_text_is_erased(self):
        self.service.preview_result = preview(blocking_problems=[
            {"code": "CUSTOMER_NAME_REQUIRED", "message": "اسم العميل مطلوب لإتمام الفاتورة"}])
        self.vm.searchCustomers("أحمد")
        self.vm.chooseCustomer("12")
        self.sale_ready()
        self.vm.setNewCustomer("", "", "")
        self.preview_now()
        self.assertFalse(self.vm.canFinalize)
        self.assertTrue(self.vm.customerMissing)
        self.assertIsNone([c for c in self.service.calls if c[0] == "preview"][-1][1].customer_id)

    # ---- Mixed: the typed total ends on a multiple of EGP 5 (one shared rounding rule, applied once)
    def test_mixed_total_is_rounded_once_to_the_nearest_five_on_commit(self):
        self.vm.setSalesperson("5")
        self.vm.addBarcode("01260925")
        self.vm.addOldGold(1, "5")
        for typed, final in (("9340.36", "9340"), ("9342.60", "9345"), ("9340", "9340")):
            with self.subTest(typed):
                self.vm.setInvoiceTotal(typed)
                self.vm.commitInvoiceTotal()
                self.assertEqual(self.vm.invoiceTotal, final)
                self.assertEqual(bool(self.vm.totalRoundedNote), typed != final)
        self.vm.setInvoiceTotal("9341")
        self.assertEqual(self.vm.totalRoundedNote, "")                   # typing again clears the notice

    def test_sale_only_total_is_not_silently_rounded(self):
        self.vm.setSalesperson("5")
        self.vm.addBarcode("01260925")
        self.vm.setInvoiceTotal("67011")
        self.vm.commitInvoiceTotal()
        self.assertEqual((self.vm.invoiceTotal, self.vm.totalRoundedNote), ("67011", ""))   # the service refuses it

    def test_making_and_discount_notices_are_simple_for_the_salesman(self):
        self.service.preview_result = preview(making_review_status="NOT_CONFIGURED", old_gold_discount_changed=True)
        self.sale_ready()
        self.assertEqual((self.vm.summary["makingNotice"], self.vm.summary["discountChanged"]),
                         ("نطاق المصنعية غير محدد", True))
        self.service.preview_result = preview(making_review_status="OUT_OF_RANGE")
        self.vm.setInvoiceTotal("2")
        self.preview_now()
        self.assertEqual(self.vm.summary["makingNotice"], "السعر يحتاج مراجعة")

    def test_default_salesperson_and_unchanged_customer_keep_success(self):
        session = AppSession()
        session._establish(AuthenticationResult(18, 5, "علي", PERMISSIONS, _VERIFIED), int(session.generation))
        vm = InlineViewModel(session, service=self.service, master_data=Master(), preview_delay_ms=0)
        vm.activate()
        self.assertEqual(vm.salespersonId, "5")
        self.sale_ready()
        self.vm.finalizeInvoice(False, False)
        self.vm.setCustomer("")
        self.assertTrue(self.vm.hasSuccess)

    # ------------------------------------------------------------ sales table and weights

    def test_sales_table_shows_physical_details_and_described_lines(self):
        self.vm.addBarcode("01260925")
        self.assertEqual(self.vm.addManualLine(4, 1, 5, "4", "18", "نقش", "BARCODE_LOST"), "")
        self.assertEqual(self.vm.addOldGoldStockLine(0, "دبلة سادة", 1, 0, "3.2", "", ""), "")
        rows = self.vm.saleLinesModel.rows()
        self.assertEqual([(r["itemName"], r["factory"], r["size"], r["notes"], r["tag"]) for r in rows],
                         [("خاتم", "لوكس", "", "فص أزرق", ""), ("دبلة", "لوكس", "18", "نقش", "تحتاج مراجعة"),
                          ("دبلة سادة", "", "", "", "من الكسر")])
        self.assertEqual(self.vm.weightTotals["sold"], {"18": "—", "21": "17.700", "22": "—", "24": "—"})
        draft = self.vm._draft()
        self.assertEqual([(d.kind, d.reason_code, d.item_type_id) for d in draft.described_sale_lines],
                         [("UNIDENTIFIED", "BARCODE_LOST", 4), ("OLD_GOLD_STOCK", None, None)])
        self.vm.removeSaleLine(1)                                          # index over the whole table
        self.assertEqual([r["tag"] for r in self.vm.saleLinesModel.rows()], ["", "من الكسر"])

    def test_manual_line_requires_type_karat_weight_and_reason(self):
        self.assertEqual(self.vm.addManualLine(0, 1, 0, "4", "", "", "NO_BARCODE"), "اختر الصنف.")
        self.assertEqual(self.vm.addManualLine(4, 0, 0, "4", "", "", "NO_BARCODE"), "اختر العيار.")
        self.assertEqual(self.vm.addManualLine(4, 1, 0, "", "", "", "NO_BARCODE"), "اكتب الوزن.")
        self.assertEqual(self.vm.addManualLine(4, 1, 0, "4", "", "", ""), "اختر سبب الإدخال اليدوي.")
        self.assertEqual(self.vm.saleCount, 0)

    def test_weight_totals_per_karat(self):
        self.vm.addBarcode("01260925")
        self.vm.addOldGold(1, "5")
        self.vm.addOldGold(1, "2.25")
        totals = self.vm.weightTotals
        self.assertEqual(totals["sold"], {"18": "—", "21": "10.500", "22": "—", "24": "—"})
        self.assertEqual(totals["bought"], {"18": "—", "21": "7.250", "22": "—", "24": "—"})

    # ------------------------------------------------------------ cascading search

    def test_higher_level_resets_lower_filters(self):
        self.vm.setSearchType(4)
        self.vm.setSearchKarat(1)
        self.vm.setSearchFactory(5)
        self.assertEqual(self.vm.searchPath, "دبلة ← عيار 21 ← لوكس")
        self.vm.setSearchType(2)                                           # change الصنف
        self.assertEqual((self.vm.searchKaratId, self.vm.searchFactoryId), (0, 0))
        self.vm.setSearchKarat(1)
        self.vm.setSearchFactory(5)
        self.vm.setSearchKarat(1)                                          # re-choosing العيار drops المصنع
        self.assertEqual(self.vm.searchFactoryId, 0)
        self.vm.resetSearch()
        self.assertEqual((self.vm.searchTypeId, self.vm.searchKaratId, self.vm.searchPath), (0, 0, ""))

    def test_search_sends_the_cascade_and_never_picks_a_piece(self):
        found = [{"inventory_item_id": "103", "barcode": None, "item_name": "دبلة", "karat_id": 1, "karat": 21,
                  "weight_grams": "3.500", "factory_name": "لوكس", "size": "18", "category_name": None,
                  "item_notes": "محفورة"}]
        self.service.search_sellable_items = lambda **kw: self.service.calls.append(("search", kw)) or {
            "items": [r for r in found if r["inventory_item_id"] not in kw["exclude_ids"]], "truncated": False}
        self.vm.setSearchType(4)
        self.vm.setSearchKarat(1)
        self.vm.setSearchFactory(5)
        self.vm.searchStock("", "", "0.05", "18")
        call = self.service.calls[-1][1]
        self.assertEqual((call["item_type_id"], call["karat_id"], call["factory_id"], call["size"], call["weight"]),
                         (4, 1, 5, "18", ""))
        self.assertEqual(self.vm.saleCount, 0)
        self.assertEqual(self.vm.searchResults[0]["detailText"], "لوكس • مقاس 18 • محفورة")
        self.assertTrue(self.vm.addSearchedItem("103"))
        self.assertEqual(self.vm._draft().sale_lines[0].identified_by, "SEARCH")
        self.assertEqual(self.vm.saleLinesModel.rows()[0]["notes"], "محفورة")

    def test_barcode_not_found_offers_search(self):
        def missing(barcode, _expected_actor=None):
            raise InvoiceError("BARCODE_NOT_FOUND", "لا توجد قطعة بالباركود 9.")
        self.service.find_item_by_barcode = missing
        self.vm.addBarcode("9")
        self.assertTrue(self.vm.barcodeNotFound)

    # ------------------------------------------------------------ settlement

    def test_above_fifty_needs_reason_and_verified_manager(self):
        self.service.preview_result = preview(gap_signed="-120.00", settlement_needs_approval=True)
        self.sale_ready()
        self.vm.setSettlementChoice("SETTLE")
        self.preview_now()
        self.assertTrue(self.vm.settlementNeedsReason and self.vm.settlementNeedsApproval)
        self.vm.verifyApprover("manager", "wrong")
        self.assertFalse(self.vm.approverOk)
        self.assertIn("تعذر", self.vm.approverText)
        self.vm.verifyApprover("manager", "ok")
        self.assertEqual((self.vm.approverOk, self.vm.approverText), (True, "موافقة: المدير"))
        self.vm.setSettlementReason("فكة")
        draft = self.vm._draft()
        self.assertEqual((draft.settlement_reason, draft.settlement_approver.user_id), ("فكة", 3))

    # ------------------------------------------------------------ customer inline

    def test_existing_customer_by_name_or_phone(self):
        self.vm.searchCustomers("أ")                                     # too short: no query
        self.assertEqual(self.vm.customerSuggestions, [])
        self.vm.searchCustomers("0100")
        suggestion = self.vm.customerSuggestions[0]
        self.assertEqual((suggestion["name"], suggestion["accountText"], suggestion["deadCash"]),
                         ("أحمد علي", "عليه 3,250.00 ج", True))
        self.vm.chooseCustomer("12")
        info = self.vm.customerInfo
        self.assertEqual((self.vm.customerId, info["state"], info["mobile"], info["place"], info["accountText"]),
                         ("12", "EXISTING", "0100", "طنطا", "عليه 3,250.00 ج"))
        self.assertIsNone(self.vm._draft().new_customer)

    def test_new_customer_is_only_typed_until_finalize(self):
        self.vm.setNewCustomer("عميل جديد", "0111", "بنها")
        info = self.vm.customerInfo
        self.assertEqual((info["state"], info["accountText"]), ("NEW", "عميل جديد — يُسجل عند الحفظ"))
        self.assertEqual(self.vm._draft().new_customer.mobile, "0111")
        self.vm.clearCustomer()
        self.assertEqual(self.vm.customerInfo["state"], "NONE")

    def test_formatting_helpers(self):
        self.assertEqual(money("1234.5"), "1,234.50 ج")
        self.assertEqual(grams("10.5"), "10.500")
        self.assertEqual((balance_text("0"), balance_text("-12.5")), ("لا يوجد رصيد", "للعميل 12.50 ج"))
        self.assertEqual((account_text("3250"), account_text("-1200"), account_text("0")),
                         ("عليه 3,250.00 ج", "له 1,200.00 ج", "الحساب مسدد"))


if __name__ == "__main__":
    unittest.main()
