"""InvoicePrintService + bill_overlay: the preprinted-A5 black overlay of a finalized invoice.

Printing must:
- come only from persisted rows (never from live QML/ViewModel state);
- never write to any table, never fail the invoice when the printer fails;
- print only the variable black data (never the stationery's logos/footer);
- show إجمالي الفاتورة as the main amount, the money that actually moved, and NEVER the
  customer's internal account (على العميل / للعميل / ledger balances);
- fit one A5 sheet between the preprinted gold lines: at most 10 sale rows.
"""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
import os
import re
import unittest
from unittest.mock import patch

from app.session import AppSession
from services import bill_overlay
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.bill_overlay import BillCalibration, LayoutError, code128_modules, compose, validate
from services.invoice_print_service import InvoicePrintService, PrintError, configure_bill_printer

D = Decimal
PERMISSIONS = frozenset({"APP_LOGIN", "INVOICE_CREATE", "INVOICE_FINALIZE"})
# 25,000 = 21,800 actually received + 3,200 deferred on the customer's internal account.
# None of these strings is a substring of another, so absence checks are meaningful.
TOTAL, RECEIVED, DEFERRED = D("25000.00"), D("21800.00"), D("3200.00")


def sale_line(index=0, **overrides):
    line = {"line_id": str(index + 1), "line_type": "SELL_INVENTORY", "karat_value": 21,
            "weight_grams": D("3.600"), "quantity": 1, "item_name_snapshot": "خاتم", "barcode_snapshot": "01260925",
            "factory_snapshot": "لوكس", "category_snapshot": "خواتم", "size_snapshot": None,
            "gold_price_per_gram": D("6232.0000"), "making_total": D("564.80"), "gross_total": D("22435.20"),
            "discount_value": None, "line_total": TOTAL, "notes": None}
    line.update(overrides)
    return line


def old_gold_line(index=0):
    return {"line_id": f"b{index}", "line_type": "BUY_OLD_GOLD", "karat_value": 18, "weight_grams": D("2.000"),
            "quantity": 1, "item_name_snapshot": None, "factory_snapshot": None, "discount_value": None,
            "line_total": D("9000.00"), "notes": None}


def invoice_row(**overrides):
    row = {
        "invoice_id": "42", "invoice_number": "26092699", "invoice_type": "SELL", "invoice_status": "FINALIZED",
        "customer_id": "1", "employee_id": "5", "employee_name": "بائع تجريبي",
        "invoice_date": datetime(2026, 9, 26, 12, 0), "finalized_at": datetime(2026, 9, 26, 22, 47, 5),
        "sell_total": TOTAL, "buy_total": D("0.00"), "net_amount": TOTAL,
        "net_direction": "CUSTOMER_PAYS_SHOP", "settlement_status": "PARTIAL", "notes": None,
        "customer_code_snapshot": "C000001", "customer_name_snapshot": "سليا محمد", "mobile_snapshot": "0100",
        "place_snapshot": "القناطر", "balance_before": D("0.00"), "balance_after": DEFERRED,
        "dead_cash_warning_active": 0,
        "lines": [sale_line()],
        "payments": [{"payment_id": "1", "payment_method_code": "PHYSICAL_CASH", "payment_method_name": "نقدي",
                      "direction": "IN", "amount": RECEIVED}],
        "payment_accounts": [{"payment_id": "1", "bank_account_id": None, "bank_account_name": None}],
    }
    row.update(overrides)
    return row


def texts(ops):
    return [op.text for op in ops if op.kind == "text"]


def joined(ops):
    return "\n".join(texts(ops))


class FakeInvoiceRepo:
    def __init__(self, connection):
        self.state = connection
        self.state.setdefault("reads", 0)

    def get_invoice(self, invoice_id):
        self.state["reads"] += 1
        row = self.state["invoices"].get(str(invoice_id))
        return {**row, "lines": list(row["lines"]), "payments": list(row["payments"])} if row else None

    def get_payment_accounts(self, invoice_id):
        row = self.state["invoices"].get(str(invoice_id))
        return row["payment_accounts"] if row else []


def make_service(invoices=None, permissions=PERMISSIONS, print_document=None, calibration=None, **kwargs):
    session = AppSession()
    session._establish(AuthenticationResult(1, 5, "بائع", permissions, _VERIFIED), int(session.generation))
    state = {"invoices": invoices if invoices is not None else {"42": invoice_row()}}

    @contextmanager
    def transaction():
        yield state
    return InvoicePrintService(session, transaction_factory=transaction, repository=FakeInvoiceRepo,
                               print_document=print_document, calibration=calibration or BillCalibration(),
                               **kwargs), state


def ops_for(invoice, calibration=None):
    return compose(bill_overlay.build_overlay(invoice), calibration or BillCalibration())


class PrototypeLabelRunsTests(unittest.TestCase):
    def test_the_date_time_and_total_are_three_independent_runs_so_the_printer_cannot_move_the_colon(self):
        ops = ops_for(invoice_row())                                                           # the prototype style (the old program's look)
        by_text = {op.text: op for op in ops if op.kind == "text" and op.spans}
        for label in ("التاريخ", "الوقت", "إجمالي الفاتورة"):
            op = next(o for t, o in by_text.items() if t.startswith(label + " : "))
            self.assertEqual([part[0] for part in op.spans][:2], [label, " : "])               # the word, then a standalone colon, then the value
            self.assertEqual(len(op.spans), 3)


class OverlayContentTests(unittest.TestCase):
    def test_invoice_total_is_the_main_printed_amount_in_figures_and_words(self):
        page = joined(ops_for(invoice_row()))
        self.assertIn("إجمالي الفاتورة : ⁦25,000⁩ جنيه", page)
        self.assertIn("خمسة و عشرون ألف جنيها", page)          # num2words(25000, 'ar') + ' جنيها'

    def test_a_sale_prints_the_total_but_no_settlement_detail(self):
        page = joined(ops_for(invoice_row()))
        self.assertNotIn("المستلم من العميل", page)                     # owner, 2026-10-05
        self.assertNotIn("نقدي", page)
        self.assertNotIn("21,800", page)
        self.assertIn("إجمالي الفاتورة : ⁦25,000⁩ جنيه", page)

    def test_customer_account_is_never_printed_incoming(self):
        page = joined(ops_for(invoice_row()))
        for private in ("على العميل", "3,200", "3200", "ثلاثة آلاف و مئتان", "رصيد", "C000001"):
            self.assertNotIn(private, page)

    def test_customer_account_is_never_printed_outgoing(self):
        row = invoice_row(net_direction="SHOP_PAYS_CUSTOMER", balance_after=-DEFERRED,
                          payments=[{"payment_id": "1", "payment_method_code": "PHYSICAL_CASH",
                                     "payment_method_name": "نقدي", "direction": "OUT", "amount": RECEIVED}])
        ops = ops_for(row)
        page = joined(ops)
        self.assertIn("المدفوع للعميل : ⁦21,800⁩ جنيه", page)
        self.assertIn("إجمالي الفاتورة : ⁦25,000⁩ جنيه", page)
        self.assertFalse(any(re.match(r"\s*للعميل", t) for t in texts(ops)))   # no bare للعميل line
        self.assertNotIn("3,200", page)

    def test_stored_account_data_is_left_unchanged_by_printing(self):
        service, state = make_service()
        before = dict(state["invoices"]["42"])
        service.render_overlay("42")
        self.assertEqual(state["invoices"]["42"]["balance_after"], DEFERRED)
        self.assertEqual(state["invoices"]["42"], before)

    def test_a_single_full_payment_keeps_the_prototype_one_line_money_box(self):
        row = invoice_row(balance_after=D("0.00"), settlement_status="PAID",
                          payments=[{"payment_id": "1", "payment_method_code": "PHYSICAL_CASH",
                                     "payment_method_name": "نقدي", "direction": "IN", "amount": TOTAL}])
        ops = ops_for(row)
        box = next(op for op in ops if op.kind == "box")
        self.assertAlmostEqual(box.h, BillCalibration().length(bill_overlay.PROTO_MONEY_H_ONE_LINE))
        self.assertNotIn("المستلم من العميل", joined(ops))

    def test_buy_invoice_total_box_has_smaller_vertical_padding_without_changing_the_layout(self):
        row = invoice_row(net_direction="SHOP_PAYS_CUSTOMER", balance_after=-DEFERRED, payments=[
            {"payment_id": "1", "payment_method_code": "PHYSICAL_CASH", "payment_method_name": "نقدي",
             "direction": "OUT", "amount": D("6800.00")},
            {"payment_id": "2", "payment_method_code": "INSTAPAY", "payment_method_name": "InstaPay",
             "direction": "OUT", "amount": D("15000.00")},
        ], payment_accounts=[{"payment_id": "2", "bank_account_name": "البنك الأهلي المصري - فرع القناطر"}])
        ops = ops_for(row)
        box = next(op for op in ops if op.kind == "box")
        old_h = BillCalibration().length(2 * bill_overlay.C_TYPOGRAPHY.money_line_h + 2 * 1.41)
        self.assertLess(box.h, old_h)
        self.assertAlmostEqual(box.w, BillCalibration().length(bill_overlay.PROTO_TABLE_W))
        self.assertAlmostEqual(box.x, BillCalibration().x(bill_overlay.PROTO_TABLE_X))
        self.assertAlmostEqual(BillCalibration().scale, (148.0 - 2 * 4.233) / 148.0, places=4)
        self.assertAlmostEqual(BillCalibration().offset_y_mm, -6.0)
        self.assertAlmostEqual(BillCalibration().offset_x_mm, 0.0)
        table_lines = [op for op in ops if op.region == "table" and op.kind == "line"]
        self.assertTrue(table_lines)
        self.assertGreater(box.y, max(op.y + op.h for op in table_lines))
        money_text = [op for op in ops if op.region == "money" and op.kind == "text"]
        self.assertTrue(money_text)
        self.assertTrue(all(box.x <= op.x <= box.x + box.w for op in money_text))
        self.assertTrue(all(box.y <= op.y <= box.y + box.h for op in money_text))
        self.assertEqual(len([op for op in ops if op.kind == "box"]), 1)

    def test_split_sale_payment_prints_no_method_and_no_amount(self):
        row = invoice_row(payments=[
            {"payment_id": "1", "payment_method_code": "PHYSICAL_CASH", "payment_method_name": "نقدي",
             "direction": "IN", "amount": D("6800.00")},
            {"payment_id": "2", "payment_method_code": "INSTAPAY", "payment_method_name": "InstaPay",
             "direction": "IN", "amount": D("15000.00")}],
            payment_accounts=[{"payment_id": "2", "bank_account_id": "7", "bank_account_name": "البنك الأهلي"}])
        page = joined(ops_for(row))
        self.assertNotIn("المستلم من العميل", page)
        for detail in ("نقدي", "InstaPay", "البنك الأهلي", "6,800", "15,000", "21,800"):
            self.assertNotIn(detail, page)

    def test_header_uses_the_finalized_snapshot_and_finalize_time(self):
        page = joined(ops_for(invoice_row()))
        self.assertIn("الاسم : سليا محمد", page)
        self.assertIn("العنوان: القناطر", page)
        self.assertIn("التاريخ : ⁦26-09-2026⁩", page)
        self.assertIn("الوقت : ⁦22:47⁩", page)                 # finalize time, not print time

    def test_sale_rows_use_the_prototype_columns_and_formats(self):
        row = invoice_row(lines=[sale_line(0, weight_grams=D("0.4"), karat_value=18, notes="مقاس 18")])
        page = texts(ops_for(row))
        for value in ("1", "خاتم", "لوكس", "0.400", "18", "مقاس 18", "ملاحظات\nNotes", "رقم\nSerial"):
            self.assertIn(value, page)

    def test_old_gold_bought_from_the_customer_is_not_a_printed_row(self):
        row = invoice_row(lines=[sale_line(0), old_gold_line(0), old_gold_line(1)])
        overlay = bill_overlay.build_overlay(row)
        self.assertEqual(len(overlay.rows), 1)

    def test_barcode_is_code128_of_the_saved_invoice_number(self):
        ops = ops_for(invoice_row())
        bars = [op for op in ops if op.kind == "bar"]
        modules = code128_modules("26092699")
        self.assertEqual(len(bars), (len(modules) + 1) // 2)
        scale = BillCalibration().scale
        widths = [round(op.w / scale / bill_overlay.PROTO_BAR_MODULE_MM) for op in bars]
        self.assertEqual(widths, modules[0::2])
        self.assertIn("26092699", texts(ops))

    def test_code128_matches_the_prototype_bars(self):
        # The prototype's reportlab barcode for 26092603 (measured from its PDF): 22 bars, 79 modules.
        modules = code128_modules("26092603")
        self.assertEqual((sum(modules), len(modules[0::2])), (79, 22))
        self.assertTrue(all(sum(int(c) for c in p) == 11 for p in bill_overlay._CODE128))

    def test_only_black_overlay_primitives_are_drawn(self):
        ops = ops_for(invoice_row())
        self.assertEqual({op.kind for op in ops}, {"text", "line", "box", "bar"})   # no image, no logo
        page = joined(ops)
        for static in ("MAS GOLD", "مجوهرات", "masjewelery", "0108", "القناطر الخيرية", "لا ترد"):
            self.assertNotIn(static, page)

    def test_table_grid_and_money_box_are_drawn(self):
        ops = ops_for(invoice_row(lines=[sale_line(i) for i in range(3)]))
        lines = [op for op in ops if op.kind == "line"]
        horizontal = [op for op in lines if op.h == 0]
        vertical = [op for op in lines if op.w == 0]
        self.assertEqual((len(horizontal), len(vertical)), (2 + 3, 8))
        self.assertEqual(len([op for op in ops if op.kind == "box"]), 1)


class ArabicWordsTests(unittest.TestCase):
    # Expected strings are num2words 0.5.14 (lang='ar') output — the prototype's library.
    CASES = {0: "صفر", 1: "واحد", 12: "اثنا عشر", 21: "واحد و عشرون", 200: "مئتا", 250: "مئتان و خمسون",
             1001: "ألف و واحد", 2000: "ألفا", 2500: "ألفان و خمسمائة", 3000: "ثلاثة آلاف",
             8350: "ثمانية آلاف و ثلاثمائة و خمسون", 11000: "أحد عشر ألف", 23000: "ثلاثة و عشرون ألف",
             150750: "مائة و خمسون ألفاً و سبعمائة و خمسون", 2000000: "مليونا",
             3450000: "ثلاثة ملايين و أربعمائة و خمسون ألف"}

    def test_port_matches_num2words(self):
        for number, words in self.CASES.items():
            self.assertEqual(bill_overlay.arabic_cardinal(number), words, number)

    def test_money_wording_is_the_prototypes(self):
        self.assertEqual(bill_overlay.money_in_words(D("8350")), "ثمانية آلاف و ثلاثمائة و خمسون جنيها")
        self.assertEqual(bill_overlay.money_in_words(D("100.50")), "مائة جنيها و خمسون قرشا")


class LayoutTests(unittest.TestCase):
    def test_ten_sale_rows_fit_one_sheet_between_the_gold_lines(self):
        calibration = BillCalibration()
        row = invoice_row(lines=[sale_line(i) for i in range(10)] + [old_gold_line(0)])
        ops = ops_for(row, calibration)
        self.assertTrue(validate(ops, calibration))
        header = [op for op in ops if op.region == "header"]
        body = [op for op in ops if op.region in ("table", "money")]
        self.assertLessEqual(max(op.y + op.h for op in header), calibration.upper_gold_y_mm)
        self.assertGreaterEqual(min(op.y for op in body), calibration.upper_gold_y_mm)
        self.assertLessEqual(max(op.y + op.h for op in ops), calibration.lower_gold_y_mm)
        self.assertLessEqual(max(op.y + op.h for op in ops), bill_overlay.PAPER_HEIGHT_MM)   # one page only

    def test_eleven_sale_rows_are_refused_not_paginated(self):
        with self.assertRaises(ValueError):
            bill_overlay.build_overlay(invoice_row(lines=[sale_line(i) for i in range(11)]))

    def test_content_crossing_the_footer_line_is_refused(self):
        calibration = BillCalibration(lower_gold_y_mm=100.0)
        with self.assertRaises(LayoutError):
            validate(ops_for(invoice_row(lines=[sale_line(i) for i in range(10)]), calibration), calibration)

    def test_header_crossing_the_upper_gold_line_is_refused(self):
        calibration = BillCalibration(upper_gold_y_mm=15.0)
        with self.assertRaises(LayoutError):
            validate(ops_for(invoice_row(), calibration), calibration)

    def test_calibration_offsets_move_everything_by_exactly_that_much(self):
        base = ops_for(invoice_row(), BillCalibration(offset_y_mm=0.0))
        moved = ops_for(invoice_row(), BillCalibration(offset_x_mm=-2.0, offset_y_mm=1.5))
        for a, b in zip(base, moved):
            self.assertAlmostEqual(b.x - a.x, -2.0)
            self.assertAlmostEqual(b.y - a.y, 1.5)
            self.assertEqual((a.w, a.h, a.text), (b.w, b.h, b.text))

    def test_physical_mapping_reproduces_the_prototype_shrink(self):
        calibration = BillCalibration(offset_y_mm=0.0)              # the pure A5 + Sumatra model
        self.assertAlmostEqual(calibration.scale, (148 - 2 * 4.233) / 148, places=4)
        # The prototype's grid spans 27.53 .. 182.47 mm on its PDF page -> centred on the paper.
        self.assertAlmostEqual((calibration.x(27.53) + calibration.x(182.47)) / 2, 105.0, places=1)
        self.assertAlmostEqual(calibration.y(0), 4.233, places=2)


class FakePageLayout:
    pass


class RecordingPrinter:
    """A QPrinter stand-in recording the order of page-setup calls. `accept_a5=False` behaves
    like a driver that keeps its 76.2 x 50.8 mm custom default."""
    def __init__(self, accept_a5=True):
        from PySide6.QtCore import QMarginsF, QSizeF
        from PySide6.QtGui import QPageLayout, QPageSize
        self.calls, self.accept_a5, self._name = [], accept_a5, ""
        self._size = QPageSize(QSizeF(76.2, 50.8), QPageSize.Millimeter)
        self._orientation = QPageLayout.Portrait
        self._QPageLayout, self._QMarginsF = QPageLayout, QMarginsF

    def setPrinterName(self, name):
        from PySide6.QtCore import QSizeF
        from PySide6.QtGui import QPageSize
        self.calls.append("printer")
        self._name = name
        self._size = QPageSize(QSizeF(76.2, 50.8), QPageSize.Millimeter)       # the driver default resets

    def printerName(self):
        return self._name

    def setFullPage(self, value):
        self.calls.append("fullpage")

    def setPageSize(self, size):
        self.calls.append("A5")
        if self.accept_a5:
            self._size = size

    def setPageOrientation(self, orientation):
        self.calls.append("landscape")
        if self.accept_a5:
            self._orientation = orientation

    def pageLayout(self):
        self.calls.append("readback")
        layout = self._QPageLayout(self._size, self._orientation, self._QMarginsF(0, 0, 0, 0))
        layout.setMinimumMargins(self._QMarginsF(12, 12, 12, 12))
        return layout


class PrinterSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtGui import QGuiApplication
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_a5_landscape_is_applied_after_printer_selection_and_read_back(self):
        printer = RecordingPrinter()
        ok, message, printable = configure_bill_printer(printer, "Brother HL-L2360D series")
        self.assertTrue(ok, message)
        self.assertEqual(printer.calls, ["printer", "fullpage", "A5", "landscape", "readback"])
        self.assertAlmostEqual(printable[0], 4.233, places=2)                  # the driver's hardware margin

    def test_a_driver_that_keeps_its_76x51_default_is_refused(self):
        ok, message, printable = configure_bill_printer(RecordingPrinter(accept_a5=False), "Brother HL-L2360D series")
        self.assertFalse(ok)
        self.assertIsNone(printable)
        self.assertIn("A5", message)

    def test_real_virtual_pdf_driver_accepts_a5_landscape(self):
        from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
        names = [p.printerName() for p in QPrinterInfo.availablePrinters()]
        if "Microsoft Print to PDF" not in names:
            self.skipTest("Microsoft Print to PDF is not installed")
        printer = QPrinter(QPrinter.HighResolution)                           # configured only; nothing printed
        ok, message, printable = configure_bill_printer(printer, "Microsoft Print to PDF")
        self.assertTrue(ok, message)
        rect = printer.pageLayout().fullRect(printer.pageLayout().Unit.Millimeter)
        self.assertEqual((round(rect.width()), round(rect.height())), (210, 148))

    def test_overlay_pdf_is_one_a5_landscape_page(self):
        service, _ = make_service(invoices={"42": invoice_row(lines=[sale_line(i) for i in range(10)])})
        path = service.render_to_pdf("42")
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        with open(path, "rb") as handle:
            data = handle.read()
        self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", data)), 1)          # exactly one sheet
        box = [float(v) for v in re.search(rb"/MediaBox\s*\[([^\]]+)\]", data).group(1).split()]
        self.assertEqual((round((box[2] - box[0]) * 25.4 / 72), round((box[3] - box[1]) * 25.4 / 72)), (210, 148))

    def test_no_image_is_ever_painted_by_the_overlay(self):
        from PySide6.QtGui import QImage, QPainter

        class Guard(QPainter):
            def drawImage(self, *args):
                raise AssertionError("the overlay must not draw images")
            drawPixmap = drawImage
        image = QImage(400, 300, QImage.Format_RGB32)
        painter = Guard(image)
        try:
            bill_overlay.paint(painter, ops_for(invoice_row()), 400 / 210)
        finally:
            painter.end()


class PrintServiceTests(unittest.TestCase):
    def test_printing_never_touches_the_database_for_writes(self):
        jobs = []
        service, state = make_service(print_document=lambda ops: (jobs.append(ops), (True, "ok", None))[1])
        before = dict(state["invoices"]["42"])
        outcome = service.print_invoice("42")
        self.assertTrue(outcome.printed)
        self.assertEqual(len(jobs), 1)                                          # one attempt, one job
        self.assertEqual(state["invoices"]["42"], before)

    def test_printed_data_is_loaded_from_the_saved_invoice(self):
        jobs = []
        service, state = make_service(print_document=lambda ops: (jobs.append(joined(ops)), (True, "ok", None))[1])
        service.print_invoice("42")
        self.assertEqual(state["reads"], 1)
        self.assertIn("26092699", jobs[0])

    def test_eleven_sale_rows_are_reported_and_nothing_is_sent(self):
        jobs = []
        service, _ = make_service(invoices={"42": invoice_row(lines=[sale_line(i) for i in range(11)])},
                                  print_document=lambda ops: (jobs.append(ops), (True, "ok", None))[1])
        outcome = service.print_invoice("42")
        self.assertEqual((outcome.printed, outcome.message, jobs), (False, "الحد الأقصى للفاتورة 10 أصناف بيع", []))

    def test_missing_invoice_is_refused(self):
        service, _ = make_service(invoices={})
        with self.assertRaises(PrintError):
            service.render_overlay("42")

    def test_printing_requires_a_billing_permission(self):
        service, _ = make_service(permissions=frozenset({"APP_LOGIN"}))
        with self.assertRaises(PermissionError):
            service.print_invoice("42")

    def test_printer_failure_never_raises_and_invoice_stays_finalized(self):
        service, state = make_service(print_document=lambda ops: (False, "الطابعة غير متصلة", None))
        outcome = service.print_invoice("42")
        self.assertEqual((outcome.printed, outcome.message), (False, "الطابعة غير متصلة"))
        self.assertEqual(state["invoices"]["42"]["invoice_status"], "FINALIZED")

    def test_printer_exception_is_reported_not_raised(self):
        def broken(ops):
            raise RuntimeError("driver crashed")
        service, _ = make_service(print_document=broken)
        outcome = service.print_invoice("42")
        self.assertFalse(outcome.printed)
        self.assertIn("تعذرت الطباعة", outcome.message)

    def test_reprint_is_idempotent(self):
        jobs = []
        service, state = make_service(print_document=lambda ops: (jobs.append(joined(ops)), (True, "ok", None))[1])
        service.print_invoice("42")
        service.print_invoice("42")
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0], jobs[1])

    def test_no_printer_configured_is_reported_cleanly(self):
        service, _ = make_service(printer_name="")
        outcome = service.print_invoice("42")
        self.assertEqual((outcome.printed, outcome.message),
                         (False, "لم يتم تحديد طابعة الفواتير. اخترها من الإعدادات."))

    def test_configured_printer_that_is_not_connected_is_reported(self):
        service, state = make_service(printer_name="طابعة غير موجودة 12345")
        outcome = service.print_invoice("42")
        self.assertFalse(outcome.printed)
        self.assertIn("غير متاحة", outcome.message)
        self.assertEqual(state["invoices"]["42"]["invoice_status"], "FINALIZED")

    def test_calibration_sample_uses_the_same_pipeline_and_hides_its_internal_account(self):
        jobs = []
        service, _ = make_service(print_document=lambda ops: (jobs.append(ops), (True, "ok", None))[1])
        outcome = service.print_test_page()
        self.assertTrue(outcome.printed)
        ops = jobs[0]
        self.assertEqual(len(bill_overlay.build_overlay(service.sample_invoice()).rows), 10)
        page = joined(ops)
        self.assertIn("إجمالي الفاتورة", page)
        self.assertNotIn("3,000", page.replace("23,000", ""))                   # the sample's deferred amount
        self.assertNotIn("على العميل", page)



class InFlightGuardTests(unittest.TestCase):
    """One accepted request is one job: a second request for the same invoice while the first is being
    handed to the print system is refused without printing, from any screen or thread."""

    def test_concurrent_requests_for_one_invoice_submit_one_job(self):
        import threading
        started, release, jobs = threading.Event(), threading.Event(), []

        def slow(ops):
            jobs.append(ops)
            started.set()
            release.wait(10)
            return True, "ok", None
        first, _ = make_service(print_document=slow)
        second, _ = make_service(print_document=slow)               # another screen's own service instance
        result = {}
        worker = threading.Thread(target=lambda: result.update(first=first.print_invoice("42")))
        worker.start()
        self.assertTrue(started.wait(10))
        duplicate = second.print_invoice("42")                       # while the first is in flight
        self.assertFalse(duplicate.printed)
        self.assertEqual(duplicate.message, "جاري إرسال هذه الفاتورة للطابعة بالفعل.")
        self.assertEqual(len(jobs), 1)
        release.set()
        worker.join(10)
        self.assertTrue(result["first"].printed)
        self.assertEqual(len(jobs), 1)

    def test_the_guard_is_released_after_success_failure_and_exception(self):
        jobs = []
        outcomes = iter([(True, "ok", None), (False, "الطابعة غير متصلة", None), RuntimeError("boom"),
                         (True, "ok", None)])

        def printer(ops):
            jobs.append(ops)
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        service, _ = make_service(print_document=printer)
        self.assertTrue(service.print_invoice("42").printed)
        self.assertFalse(service.print_invoice("42").printed)
        self.assertFalse(service.print_invoice("42").printed)       # an unexpected printer error never locks it
        self.assertTrue(service.print_invoice("42").printed)        # a later, separate request is a new job
        self.assertEqual(len(jobs), 4)

    def test_different_invoices_do_not_block_each_other(self):
        import threading
        started, release, seen = threading.Event(), threading.Event(), []
        invoices = {"42": invoice_row(), "43": invoice_row()}

        def printer(ops):
            seen.append(1)
            started.set()
            release.wait(10)
            return True, "ok", None
        service, _ = make_service(invoices=invoices, print_document=printer)
        worker = threading.Thread(target=lambda: service.print_invoice("42"))
        worker.start()
        self.assertTrue(started.wait(10))
        other = make_service(invoices=invoices, print_document=lambda ops: (True, "ok", None))[0].print_invoice("43")
        self.assertTrue(other.printed)
        release.set()
        worker.join(10)

    def test_printing_writes_no_job_or_event_row(self):
        import copy
        service, state = make_service(print_document=lambda ops: (True, "ok", None))
        before = copy.deepcopy(state["invoices"])
        service.print_invoice("42")
        service.print_invoice("42")
        self.assertEqual(state["invoices"], before)
        self.assertEqual(set(state), {"invoices", "reads"})        # no print_jobs / invoice_events appeared


class MeasuredStationeryTests(unittest.TestCase):
    """The owner's ruler on the real stock: 210 x 145 mm, gold lines at 25.2 and 125.0 mm."""

    def test_defaults_are_the_measured_lines_and_the_old_bill_offset(self):
        c = BillCalibration()
        self.assertEqual((c.upper_gold_y_mm, c.lower_gold_y_mm), (25.2, 125.0))
        self.assertEqual(bill_overlay.MEASURED_SHEET_HEIGHT_MM, 145.0)
        self.assertEqual(c.offset_y_mm, -6.0)
        # With the offset, the grid starts where the old program's grid starts (~26.9 mm, just
        # under the upper gold line) and the header sits entirely above that line.
        ops = ops_for(invoice_row(), c)
        self.assertAlmostEqual(min(op.y for op in ops if op.region == "table"), 26.9, delta=0.1)
        self.assertLess(max(op.y + op.h for op in ops if op.region == "header"), 25.2)

    def test_ten_rows_keep_clearance_above_the_measured_footer_line(self):
        for style in bill_overlay.BILL_STYLES:
            ops = compose(bill_overlay.build_overlay(invoice_row(lines=[sale_line(i) for i in range(10)])),
                          BillCalibration(), style)
            bottom = max(op.y + op.h for op in ops)
            # prototype ≈ 11.1 mm; Variant C ≈ 8.55 mm (its money lines are taller for the 14.5 pt total)
            self.assertGreaterEqual(125.0 - bottom, 8.0 if style == "variant_c" else 10.0, style)
            self.assertTrue(validate(ops, BillCalibration()))

    def test_everything_stays_inside_the_brother_printable_area(self):
        printable = (4.233, 4.233, 210 - 4.233, 148 - 4.233)
        for style in bill_overlay.BILL_STYLES:
            ops = compose(bill_overlay.build_overlay(invoice_row(lines=[sale_line(i) for i in range(10)])),
                          BillCalibration(), style)
            self.assertTrue(validate(ops, BillCalibration(), printable))


def cash_row(total, cash, **overrides):
    return invoice_row(net_amount=total, sell_total=total, lines=[sale_line(line_total=total)],
                       payments=[{"payment_id": "1", "payment_method_code": "PHYSICAL_CASH",
                                  "payment_method_name": "نقدي", "direction": "IN", "amount": cash}],
                       balance_after=total - cash, **overrides)


class ReceivedFromCustomerNotPrintedTests(unittest.TestCase):
    """BUG-PRINT-02: the physical invoice never prints "المستلم من العميل : amount"; the total stays."""

    CASES = {"fully paid in cash": cash_row(D("70000.00"), D("70000.00")),
             "partly on the customer account": cash_row(D("32000.00"), D("29000.00")),
             "split cash + InstaPay": invoice_row(
                 payments=[{"payment_id": "1", "payment_method_code": "PHYSICAL_CASH", "payment_method_name": "نقدي",
                            "direction": "IN", "amount": D("6800.00")},
                           {"payment_id": "2", "payment_method_code": "INSTAPAY", "payment_method_name": "InstaPay",
                            "direction": "IN", "amount": D("15000.00")}],
                 payment_accounts=[{"payment_id": "2", "bank_account_name": "البنك الأهلي"}])}

    def pages(self, invoice):
        overlay = bill_overlay.build_overlay(invoice)
        for style in bill_overlay.BILL_STYLES:                                   # the default AND the prototype
            yield style, joined(compose(overlay, BillCalibration(), style))

    def test_the_line_and_its_amount_are_absent_and_the_total_remains(self):
        for label, invoice in self.CASES.items():
            total = f"{invoice['net_amount']:,.0f}"
            for style, page in self.pages(invoice):
                with self.subTest(case=label, style=style):
                    self.assertNotIn("المستلم من العميل", page)
                    self.assertIn("إجمالي الفاتورة", page)
                    self.assertIn(total, page)

    def test_no_sale_prints_any_method_amount_or_account_detail(self):
        details = {"fully paid in cash": ("نقدي", "70,000 ج\n", "نقدي 70,000", "نقدي : 70,000"),
                   "partly on the customer account": ("نقدي", "29,000", "نقدي 29,000", "على العميل", "3,000",
                                                       "على العميل 3,000", "رصيد"),
                   "split cash + InstaPay": ("نقدي", "InstaPay", "البنك الأهلي", "6,800", "15,000", "21,800")}
        for label, invoice in self.CASES.items():
            for style, page in self.pages(invoice):
                for detail in details[label]:
                    with self.subTest(case=label, style=style, detail=detail):
                        self.assertNotIn(detail, page)

    def test_the_total_and_the_amount_in_words_are_still_printed(self):
        for label, invoice in self.CASES.items():
            words = bill_overlay.money_in_words(D(str(invoice["net_amount"])))
            for style, page in self.pages(invoice):
                with self.subTest(case=label, style=style):
                    self.assertIn("إجمالي الفاتورة", page)
                    self.assertIn(words, page)

    def test_the_sale_total_box_hugs_its_text_and_nothing_else_moves(self):
        """Owner-approved micro-fix: only the bottom total box's HEIGHT shrinks (14.3 -> 11.29 proto mm)."""
        cal, c = BillCalibration(), bill_overlay.C_TYPOGRAPHY
        for label in ("partly on the customer account", "split cash + InstaPay", "fully paid in cash"):
            invoice = self.CASES[label]
            ops = compose(bill_overlay.build_overlay(invoice), cal, bill_overlay.STYLE_VARIANT_C)
            box = next(op for op in ops if op.kind == "box")
            old_height = cal.length(2 * c.money_line_h + 2 * bill_overlay.PROTO_MONEY_PAD_Y_TWO_LINES)     # 14.3
            with self.subTest(case=label):
                self.assertAlmostEqual(box.h, cal.length(bill_overlay.PROTO_MONEY_H_ONE_LINE))             # 11.29
                self.assertLess(box.h, old_height)
                self.assertAlmostEqual(old_height - box.h, cal.length(14.3 - 11.29))
                # same left edge and width as the item table; same top relationship to it
                table = [op for op in ops if op.region == "table" and op.kind == "line"]
                table_left = min(op.x for op in table)
                table_bottom = max(op.y + op.h for op in table)
                self.assertAlmostEqual(box.x, table_left)
                self.assertAlmostEqual(box.w, cal.length(bill_overlay.PROTO_TABLE_W))
                self.assertAlmostEqual(box.y, table_bottom + cal.length(bill_overlay.PROTO_MONEY_GAP), places=6)
                # the total and the words stay inside the box, vertically centred, nothing touches the border
                texts_in_box = [op for op in ops if op.region == "money" and op.kind == "text"]
                self.assertEqual(len(texts_in_box), 2)                         # no settlement line, no second row
                for op in texts_in_box:
                    self.assertGreater(op.y, box.y + cal.length(1.0))
                    self.assertLess(op.y + op.h, box.y + box.h - cal.length(1.0))
                    self.assertGreaterEqual(op.x, box.x)
                    self.assertLessEqual(op.x + op.w, box.x + box.w)
                top_gap = texts_in_box[0].y - box.y
                bottom_gap = (box.y + box.h) - (texts_in_box[0].y + texts_in_box[0].h)
                self.assertAlmostEqual(top_gap, bottom_gap, places=6)
                self.assertEqual(len([op for op in ops if op.kind == "box"]), 1)

    def test_the_table_scale_and_calibration_are_unchanged(self):
        cal = BillCalibration()
        self.assertEqual((cal.offset_x_mm, cal.offset_y_mm), (0.0, -6.0))
        self.assertAlmostEqual(cal.scale, 0.9428, places=3)
        self.assertEqual((bill_overlay.PROTO_TABLE_TOP, bill_overlay.PROTO_TABLE_HEADER_H,
                          bill_overlay.PROTO_TABLE_ROW_H, bill_overlay.PROTO_TABLE_X, bill_overlay.PROTO_MONEY_GAP),
                         (30.42, 14.11, 6.35, 27.53, 2.54))
        self.assertEqual(bill_overlay.MAX_SALE_ITEMS, 10)
        self.assertEqual((bill_overlay.PAPER_WIDTH_MM, bill_overlay.PAPER_HEIGHT_MM), (210.0, 148.0))
        # a BUY invoice keeps its two-line box
        row = invoice_row(net_direction="SHOP_PAYS_CUSTOMER", balance_after=-DEFERRED,
                          payments=[{"payment_id": "1", "payment_method_code": "PHYSICAL_CASH",
                                     "payment_method_name": "نقدي", "direction": "OUT", "amount": RECEIVED}])
        box = next(op for op in compose(bill_overlay.build_overlay(row), cal, bill_overlay.STYLE_VARIANT_C)
                   if op.kind == "box")
        self.assertAlmostEqual(box.h, cal.length(14.3))

    def test_a_shop_paid_buy_invoice_still_prints_its_paid_to_customer_line(self):
        row = invoice_row(net_direction="SHOP_PAYS_CUSTOMER", balance_after=-DEFERRED,
                          payments=[{"payment_id": "1", "payment_method_code": "PHYSICAL_CASH",
                                     "payment_method_name": "نقدي", "direction": "OUT", "amount": RECEIVED}])
        for style, page in self.pages(row):
            with self.subTest(style=style):
                self.assertIn("المدفوع للعميل", page)
                self.assertNotIn("المستلم من العميل", page)


class VariantCTests(unittest.TestCase):
    """The approved direction: same geometry as the prototype, clearer hierarchy."""

    def c_ops(self, invoice):
        return compose(bill_overlay.build_overlay(invoice), BillCalibration(), bill_overlay.STYLE_VARIANT_C)

    def test_default_style_is_variant_c_and_the_prototype_stays_available(self):
        self.assertEqual(bill_overlay.DEFAULT_BILL_STYLE, "variant_c")
        self.assertIn("prototype", bill_overlay.BILL_STYLES)
        service, _ = make_service()
        self.assertIn(service.style(), bill_overlay.BILL_STYLES)

    def test_same_boxes_rows_and_heights_as_the_prototype(self):
        invoice = invoice_row(lines=[sale_line(i) for i in range(10)])
        proto = compose(bill_overlay.build_overlay(invoice), BillCalibration(), "prototype")
        variant = self.c_ops(invoice)
        geometry = lambda ops, kind: sorted((round(o.x, 3), round(o.y, 3), round(o.w, 3), round(o.h, 3))
                                            for o in ops if o.kind == kind)
        for kind in ("line", "bar"):                                     # identical grid, rows and barcode
            self.assertEqual(geometry(proto, kind), geometry(variant, kind), kind)
        # The money box keeps its position and width; only its lines are taller (ink-checked 14.5 pt
        # total), and never by more than 2 x (6.0 - 4.66) prototype mm.
        (px, py, pw, ph), = geometry(proto, "box")
        (vx, vy, vw, vh), = geometry(variant, "box")
        self.assertEqual((px, py, pw), (vx, vy, vw))
        self.assertLessEqual(vh - ph, BillCalibration().length(2 * (6.0 - bill_overlay.PROTO_MONEY_LINE_H)) + 0.001)

    def test_total_amount_is_the_strongest_money_element(self):
        ops = self.c_ops(invoice_row())
        total = next(o for o in ops if o.spans and o.spans[0][0].startswith("إجمالي الفاتورة"))
        label, colon, amount = total.spans                                  # word, standalone colon, amount
        self.assertFalse(amount[2] or label[2])                           # owner: both regular; size carries it
        self.assertGreater(amount[1], label[1])
        others = [o for o in ops if o.region == "money" and o is not total]
        self.assertTrue(all(max((s[1] for s in o.spans), default=o.size_pt) < amount[1] for o in others))
        self.assertIn("\u206625,000\u2069 ج", amount[0])

    def test_payment_is_secondary_and_methods_tertiary(self):
        """BUY invoices keep their payment line and methods; a SALE prints neither (BUG-PRINT-03)."""
        ops = self.c_ops(invoice_row(net_direction="SHOP_PAYS_CUSTOMER", balance_after=-DEFERRED, payments=[
            {"payment_id": "1", "payment_method_code": "PHYSICAL_CASH", "payment_method_name": "نقدي",
             "direction": "OUT", "amount": D("6800.00")},
            {"payment_id": "2", "payment_method_code": "INSTAPAY", "payment_method_name": "InstaPay",
             "direction": "OUT", "amount": D("15000.00")},
            {"payment_id": "3", "payment_method_code": "MOBILE_WALLET", "payment_method_name": "محفظة",
             "direction": "OUT", "amount": D("0.00")}],
            payment_accounts=[{"payment_id": "2", "bank_account_id": "7", "bank_account_name": "البنك الأهلي"}]))
        page = joined(ops)
        self.assertIn("المدفوع للعميل", page)                              # a BUY invoice keeps its payment line
        self.assertNotIn("المستلم من العميل", page)
        self.assertIn("InstaPay - الأهلي \u206615,000\u2069", page)
        self.assertNotIn("محفظة", page)                                   # a zero-value method is not printed
        methods = next(o for o in ops if "InstaPay" in o.text)
        self.assertLess(methods.size_pt, BillCalibration().pt(bill_overlay.C_TYPOGRAPHY.total))
        self.assertGreater(methods.gray, 0)

    def test_customer_account_never_printed_in_variant_c(self):
        for direction, sign in (("CUSTOMER_PAYS_SHOP", 1), ("SHOP_PAYS_CUSTOMER", -1)):
            ops = self.c_ops(invoice_row(net_direction=direction, balance_after=sign * DEFERRED,
                                         payments=[{"payment_id": "1", "payment_method_code": "PHYSICAL_CASH",
                                                    "payment_method_name": "نقدي",
                                                    "direction": "IN" if sign > 0 else "OUT", "amount": RECEIVED}]))
            page = joined(ops)
            for private in ("على العميل", "3,200", "3200", "رصيد", "PARTIAL", "C000001"):
                self.assertNotIn(private, page)
            self.assertFalse(any(re.match(r"\s*للعميل", t) for t in texts(ops)))

    def test_arabic_headings_dominate_english(self):
        ops = self.c_ops(invoice_row())
        arabic = next(o for o in ops if o.text == "الصنف")
        english = next(o for o in ops if o.text == "Category")
        self.assertTrue(arabic.bold and not english.bold)
        self.assertGreater(arabic.size_pt, english.size_pt)
        self.assertGreater(english.gray, 0)
        self.assertLessEqual(english.y + english.h, arabic.y + BillCalibration().length(bill_overlay.PROTO_TABLE_HEADER_H))

    def test_subtle_grey_grid_and_only_real_rows(self):
        ops = self.c_ops(invoice_row(lines=[sale_line(i) for i in range(4)]))
        grid = [o for o in ops if o.kind in ("line", "box")]
        self.assertTrue(all(o.gray == 100 and abs(o.width_pt - BillCalibration().pt(0.8)) < 1e-9 for o in grid))
        horizontal = [o for o in ops if o.kind == "line" and o.h == 0]
        self.assertEqual(len(horizontal), 2 + 4)                          # no forced empty rows

    def test_missing_brand_is_a_dash_and_long_notes_never_grow_the_row(self):
        row = invoice_row(lines=[sale_line(0, factory_snapshot=None,
                                           notes="فص زركون أزرق مع نقش الاسم على الوجه الداخلي للخاتم")])
        ops = self.c_ops(row)
        self.assertIn("—", texts(ops))
        self.assertNotIn("NA", texts(ops))
        note = next(o for o in ops if o.text.startswith("فص زركون"))
        self.assertAlmostEqual(note.h, BillCalibration().length(bill_overlay.PROTO_TABLE_ROW_H))
        self.assertGreater(note.min_pt, 0)                                # one step smaller, then "…"

    def test_old_gold_is_never_a_row_in_variant_c(self):
        ops = self.c_ops(invoice_row(lines=[sale_line(0), old_gold_line(0), old_gold_line(1)]))
        serials = [o.text for o in ops if o.region == "table" and o.text.isdigit() and len(o.text) <= 2]
        self.assertNotIn("2", serials)

    def test_variant_c_paints_without_images(self):
        from PySide6.QtGui import QGuiApplication, QImage, QPainter
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QGuiApplication.instance() or QGuiApplication([])
        image = QImage(840, 592, QImage.Format_RGB32)
        painter = QPainter(image)
        try:
            bill_overlay.paint(painter, self.c_ops(invoice_row(lines=[sale_line(i) for i in range(10)])), 4.0)
        finally:
            painter.end()



class EnlargedTypographyTests(unittest.TestCase):
    """The owner's review of the first physical sheet: same geometry, larger text, still 10 rows."""

    def ops(self, invoice, typography=None):
        return compose(bill_overlay.build_overlay(invoice), BillCalibration(), bill_overlay.STYLE_VARIANT_C,
                       typography)

    def test_enlarged_is_the_production_typography_and_v1_is_preserved(self):
        t, v1 = bill_overlay.C_TYPOGRAPHY, bill_overlay.C_TYPE_PRINTED_V1
        self.assertIs(t, bill_overlay.C_TYPE_ENLARGED)
        self.assertEqual((v1.name, v1.body, v1.total, v1.heading_ar), (10.5, 10.0, 12.5, 11.0))   # the printed sheet
        self.assertEqual((t.name, t.address, t.date_time, t.number), (13.0, 12.0, 12.0, 12.5))
        self.assertEqual((t.heading_ar, t.heading_en, t.body, t.notes_min), (12.0, 9.0, 11.0, 9.0))
        self.assertEqual((t.total, t.paid, t.methods, t.words), (14.5, 13.0, 10.5, 11.0))

    def test_hierarchy_total_over_paid_over_methods_and_name_over_label(self):
        t = bill_overlay.C_TYPOGRAPHY
        self.assertGreater(t.total, t.paid)
        self.assertGreater(t.paid, t.methods)
        self.assertGreater(t.name, t.label)
        self.assertGreater(t.total, t.total_label)
        self.assertGreater(t.heading_ar, t.heading_en)

    def test_geometry_of_rows_and_table_is_unchanged(self):
        invoice = invoice_row(lines=[sale_line(i) for i in range(10)])
        v1 = self.ops(invoice, bill_overlay.C_TYPE_PRINTED_V1)
        big = self.ops(invoice)
        table = lambda ops: sorted((round(o.x, 3), round(o.y, 3), round(o.w, 3), round(o.h, 3))
                                   for o in ops if o.kind == "line")
        self.assertEqual(table(v1), table(big))                                 # same grid, same 6.35 mm rows
        self.assertEqual(len(bill_overlay.build_overlay(invoice).rows), 10)

    def test_ten_rows_and_the_money_box_still_clear_the_footer_line(self):
        ops = self.ops(invoice_row(lines=[sale_line(i) for i in range(10)]))
        bottom = max(o.y + o.h for o in ops)
        self.assertGreaterEqual(125.0 - bottom, 8.0)                            # ≈ 8.55 mm measured
        self.assertTrue(validate(ops, BillCalibration(), (4.233, 4.233, 205.767, 143.767)))

    def test_long_names_step_down_before_any_ellipsis(self):
        ops = self.ops(invoice_row(customer_name_snapshot="عبد الرحمن محمد عبد العزيز الشرقاوي"))
        name = next(o for o in ops if o.spans and o.spans[0][0].startswith("الاسم"))
        self.assertGreater(name.min_pt, 0)
        self.assertEqual(name.min_pt, BillCalibration().pt(bill_overlay.C_TYPOGRAPHY.name_min))

    def test_payment_line_keeps_the_amount_and_shortens_the_bank(self):
        self.assertEqual(bill_overlay.short_account_name("البنك الأهلي المصري - فرع القناطر"), "الأهلي المصري")
        self.assertEqual(bill_overlay.short_account_name("CIB"), "CIB")
        row = invoice_row(net_direction="SHOP_PAYS_CUSTOMER", balance_after=-DEFERRED, payments=[
            {"payment_id": "1", "payment_method_code": "PHYSICAL_CASH", "payment_method_name": "نقدي",
             "direction": "OUT", "amount": D("6800.00")},
            {"payment_id": "2", "payment_method_code": "INSTAPAY", "payment_method_name": "InstaPay",
             "direction": "OUT", "amount": D("15000.00")}],
            payment_accounts=[{"payment_id": "2", "bank_account_name": "البنك الأهلي المصري - فرع القناطر"}])
        page = joined(self.ops(row))
        self.assertIn("InstaPay - الأهلي المصري ⁦15,000⁩", page)
        self.assertNotIn("فرع القناطر", page)



class FinalWeightTests(unittest.TestCase):
    """The owner's notes on the enlarged physical sheet: bold labels, words and methods — same sizes."""

    def test_header_labels_words_and_methods_are_bold_without_growing(self):
        t = bill_overlay.C_TYPOGRAPHY
        ops = compose(bill_overlay.build_overlay(invoice_row(net_direction="SHOP_PAYS_CUSTOMER", balance_after=-DEFERRED, payments=[
            {"payment_id": "1", "payment_method_code": "PHYSICAL_CASH", "payment_method_name": "نقدي",
             "direction": "OUT", "amount": D("6800.00")},
            {"payment_id": "2", "payment_method_code": "INSTAPAY", "payment_method_name": "InstaPay",
             "direction": "OUT", "amount": D("15000.00")}])), BillCalibration(), bill_overlay.STYLE_VARIANT_C)
        pt = BillCalibration().pt
        for label in ("الاسم", "العنوان", "التاريخ", "الوقت"):
            span = next(o.spans[0] for o in ops if o.spans and o.spans[0][0].startswith(label))
            self.assertTrue(span[2], label)
            self.assertAlmostEqual(span[1], pt(t.label))
        words = next(o for o in ops if o.text.endswith("جنيها"))
        methods = next(o for o in ops if "InstaPay" in o.text and not o.spans)
        self.assertTrue(words.bold and methods.bold)
        self.assertEqual((words.size_pt, methods.size_pt), (pt(t.words), pt(t.methods)))
        total = next(o for o in ops if o.spans and o.spans[0][0].startswith("إجمالي الفاتورة")).spans[2]
        self.assertGreater(total[1], max(words.size_pt, methods.size_pt))   # the total stays the strongest



class HeaderSeparatorTests(unittest.TestCase):
    """Owner (2026-10-02): "Label : Value" is drawn as three independent runs — Arabic word, a standalone
    colon run, the value — so bidi can never move the colon. Only the runs changed (no geometry)."""
    COLON = " : "

    def ops(self, **overrides):
        return compose(bill_overlay.build_overlay(invoice_row(**overrides)), BillCalibration(),
                       bill_overlay.STYLE_VARIANT_C)

    def header(self, **overrides):
        return {o.spans[0][0]: [p[0] for p in o.spans] for o in self.ops(**overrides) if o.spans and o.region == "header"}

    def test_the_four_header_fields_are_label_colon_value_runs(self):
        header = self.header()
        self.assertEqual(header["الاسم"], ["الاسم", self.COLON, "سليا محمد"])
        self.assertEqual(header["العنوان"], ["العنوان", self.COLON, "القناطر"])
        self.assertEqual(header["التاريخ"], ["التاريخ", self.COLON, "⁦26-09-2026⁩"])     # one LTR unit
        self.assertEqual(header["الوقت"], ["الوقت", self.COLON, "⁦22:47⁩"])
        for runs in header.values():
            self.assertNotIn(":", runs[0])                         # never a colon inside the Arabic run

    def test_long_and_empty_customer_fields(self):
        long_name = "عبد الرحمن محمد عبد العزيز الشرقاوي"
        header = self.header(customer_name_snapshot=long_name, place_snapshot="")
        self.assertEqual(header["الاسم"][2], long_name)
        self.assertEqual(header["العنوان"][2], "—")

    def test_no_leader_or_old_separator_anywhere(self):
        for o in self.ops():
            text = "".join(p[0] for p in o.spans) if o.spans else (o.text or "")
            self.assertNotIn(":-", text)
            self.assertNotIn("..", text)
            self.assertNotIn("…", text)
        number = next(o for o in self.ops() if o.region == "header" and o.text == "26092699")
        self.assertFalse(number.spans)

    def test_total_line_is_label_colon_amount_runs(self):
        for amount, printed in (("950", "950"), ("23175", "23,175"), ("1234567", "1,234,567"),
                                ("23175.50", "23,175.50")):
            with self.subTest(amount):
                ops = self.ops(net_amount=Decimal(amount))
                total = next(o for o in ops if o.spans and o.spans[0][0] == "إجمالي الفاتورة")
                self.assertEqual([p[0] for p in total.spans], ["إجمالي الفاتورة", self.COLON, "⁦" + printed + "⁩ ج"])
                self.assertEqual(total.align, "right")

    def test_runs_paint_right_to_left_in_order(self):
        """Offscreen paint: the label is rightmost, the colon next, the value leftmost; nothing overlaps."""
        from PySide6.QtGui import QGuiApplication, QImage, QPainter
        QGuiApplication.instance() or QGuiApplication([])
        bill_overlay.ensure_fonts()
        ops = [o for o in self.ops(customer_name_snapshot="عبد الرحمن محمد عبد العزيز الشرقاوي")
               if o.spans and o.spans[0][0] in ("الاسم", "إجمالي الفاتورة")]
        image = QImage(2480, 1748, QImage.Format_RGB32)
        painter = QPainter(image)
        drawn, original = [], QPainter.drawText

        def record(self_, rect, text, *rest):
            drawn.append((text, rect.left(), rect.right()))
            return original(self_, rect, text, *rest)
        with patch.object(QPainter, "drawText", record):
            bill_overlay.paint(painter, ops, 2480 / 210.0)
        painter.end()
        for start in (0, 3):
            (label, l0, r0), (colon, l1, r1), (value, l2, r2) = drawn[start:start + 3]
            self.assertEqual(colon, self.COLON)
            self.assertGreaterEqual(l0 + 0.01, r1)                  # colon wholly left of the label
            self.assertGreaterEqual(l1 + 0.01, r2)                  # value wholly left of the colon

    def test_label_weight_size_and_geometry_are_unchanged(self):
        t = bill_overlay.C_TYPOGRAPHY
        ops = self.ops(lines=[sale_line(i) for i in range(10)], customer_name_snapshot="عبد الرحمن محمد عبد العزيز الشرقاوي")
        for o in ops:
            if o.spans and o.region == "header":
                self.assertTrue(o.spans[0][2])                                   # still bold
                self.assertAlmostEqual(o.spans[0][1], BillCalibration().pt(t.label))
        name = next(o for o in ops if o.spans and o.spans[0][0].startswith("الاسم"))
        self.assertGreater(name.min_pt, 0)                                      # long names still step down / elide
        bottom = max(o.y + o.h for o in ops)
        self.assertLess(bottom, 116.45)                                         # still inside the same sheet
        # The bottom total box of a SALE now hugs its text (one-line box, 11.29 proto mm instead of 14.3):
        # 10 rows end at 113.12 mm physical, about 2.8 mm higher than before. Owner-approved, 2026-10-05.
        self.assertAlmostEqual(bottom, 113.12, delta=0.05)


if __name__ == "__main__":
    unittest.main()
