"""Invoice printing: read-only, after finalize, on the authoritative saved invoice.

Never prints from live QML/ViewModel field values. Every figure on the bill comes from
`InvoiceRepository.get_invoice()` / `get_payment_accounts()` — the same rows the finalized
transaction wrote — so a reprint later can call `print_invoice(invoice_id)` exactly as Billing
does, without any Billing screen open.

The shop prints on PREPRINTED A5 landscape stationery (logos, gold lines, watermark, footer and
QR are already on the paper). What is printed is only the black variable overlay the old masGold
prototype printed — see services/bill_overlay.py for the measured prototype geometry, the
physical mapping and the calibration values.

One print path for everything: Settings → طباعة فاتورة تجريبية and Billing → حفظ وطباعة both go
through `_print_ops()` with the same Bill printer (Settings → الطباعة) and the same layout; only
the data differs (a built-in sample vs the persisted invoice).

Printing is idempotent: it never writes to any table, never allocates a payment or a movement,
and never changes inventory, old gold or the customer ledger.

Printer failures (not configured, offline, the driver refusing A5 landscape, a layout that would
cross the preprinted footer) are reported back as an unsuccessful PrintOutcome; they never raise,
so a failure can never be mistaken for a reason to undo the already-finalized invoice.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import os
import tempfile

from services import bill_overlay
from services.bill_overlay import MAX_SALE_ITEMS_TEXT, PAPER_HEIGHT_MM, PAPER_WIDTH_MM, LayoutError
from services.invoice_service import invoice_transaction
from repositories.invoice_repository import InvoiceRepository

PAGE_TOLERANCE_MM = 1.0


class PrintError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PrintOutcome:
    invoice_number: str
    printed: bool
    message: str
    pdf_path: str | None = None


class _Discard(Exception):
    pass


def configure_bill_printer(printer, name):
    """Prepare a QPrinter for the preprinted A5 bill. Returns (ok, message, printable_mm).

    Order matters: selecting a printer resets its page setup to the driver default (the
    Brother reports a 76.2 x 50.8 mm custom page), so the printer is chosen FIRST and A5
    landscape is applied AFTER, then read back — the driver's answer is what counts."""
    from PySide6.QtGui import QPageLayout, QPageSize

    printer.setPrinterName(name)                                   # 1. select (resets page setup)
    if printer.printerName() != name:
        return False, f"طابعة الفواتير '{name}' غير متاحة حالياً.", None
    printer.setFullPage(True)                                      # 2. (0, 0) = the paper's corner
    printer.setPageSize(QPageSize(QPageSize.A5))                   # 3. A5
    printer.setPageOrientation(QPageLayout.Landscape)              # 4. landscape
    layout = printer.pageLayout()                                  # 5. read back what was accepted
    paper = layout.fullRect(QPageLayout.Millimeter)
    if (layout.pageSize().id() != QPageSize.A5 or layout.orientation() != QPageLayout.Landscape
            or abs(paper.width() - PAPER_WIDTH_MM) > PAGE_TOLERANCE_MM
            or abs(paper.height() - PAPER_HEIGHT_MM) > PAGE_TOLERANCE_MM):
        return (False, f"طابعة الفواتير لم تقبل ورق A5 أفقي (قبلت {paper.width():.0f} × {paper.height():.0f} مم). "
                       "لم تتم الطباعة.", None)
    margins = layout.minimumMargins()                              # hardware margins, points
    to_mm = 25.4 / 72.0
    printable = (margins.left() * to_mm, margins.top() * to_mm,
                 PAPER_WIDTH_MM - margins.right() * to_mm, PAPER_HEIGHT_MM - margins.bottom() * to_mm)
    return True, "", printable


class InvoicePrintService:
    """Renders and prints a finalized invoice. Stateless beyond the session/DB access."""

    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=InvoiceRepository,
                 printer_name=None, print_document=None, settings=None, calibration=None, style=None):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        # The Bill printer: an explicit name (tests), else this workstation's Settings → الطباعة
        # choice, read at print time. Nothing else: no .env fallback, never the Windows default.
        self._printer_override = printer_name
        self._settings = settings
        self._calibration = calibration
        self._style = style
        # Injected for tests: callable(ops) -> (printed, message, pdf_path). Production leaves
        # this None and prints through QtPrintSupport.
        self._print_document = print_document

    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if not {"INVOICE_FINALIZE", "INVOICE_CREATE", "INVOICE_REPRINT"} & set(actor.permissions):   # reprint: سجل الفواتير
            raise PermissionError("لا توجد صلاحية لطباعة الفاتورة.")
        return actor

    def _read(self, work):
        try:
            with self._transaction() as connection:
                raise _Discard(work(self._repository(connection)))
        except _Discard as done:
            return done.args[0]

    def _invoice_data(self, invoice_id):
        def read(invoices):
            invoice = invoices.get_invoice(invoice_id)
            if invoice is None:
                raise PrintError("INVOICE_NOT_FOUND", "الفاتورة غير موجودة.")
            invoice["payment_accounts"] = invoices.get_payment_accounts(invoice_id)
            return invoice
        return self._read(read)

    def _settings_instance(self):
        if self._settings is None:
            from config.printer_settings import PrinterSettings
            self._settings = PrinterSettings()
        return self._settings

    def calibration(self):
        if self._calibration is not None:
            return self._calibration
        return bill_overlay.calibration_from_settings(self._settings_instance())

    def style(self):
        if self._style is not None:
            return self._style
        return bill_overlay.style_from_settings(self._settings_instance())

    # ------------------------------------------------------------ content

    def render_overlay(self, invoice_id, *, _expected_actor=None):
        """The printed content (pure data), built only from the persisted invoice."""
        self._actor(_expected_actor)
        return bill_overlay.build_overlay(self._invoice_data(invoice_id))

    def compose(self, invoice):
        """(ops, error): the physical overlay, validated against the preprinted boundaries."""
        try:
            overlay = bill_overlay.build_overlay(invoice)
            calibration = self.calibration()
            ops = bill_overlay.compose(overlay, calibration, self.style())
            bill_overlay.validate(ops, calibration)
            return ops, ""
        except LayoutError as exc:
            return None, str(exc)
        except ValueError as exc:
            return None, MAX_SALE_ITEMS_TEXT if str(exc) == MAX_SALE_ITEMS_TEXT else f"تعذر تجهيز الفاتورة: {exc}"

    # ------------------------------------------------------------ printing

    def print_invoice(self, invoice_id, *, _expected_actor=None):
        """Save MUST already have happened: this only reads and prints."""
        self._actor(_expected_actor)
        invoice = self._invoice_data(invoice_id)
        printed, message, pdf_path = self._print_ops(invoice)
        return PrintOutcome(invoice_number=invoice["invoice_number"], printed=printed, message=message,
                            pdf_path=pdf_path)

    def _print_ops(self, invoice):
        ops, problem = self.compose(invoice)
        if ops is None:
            return False, problem, None
        try:
            if self._print_document is not None:
                return self._print_document(ops)
            return self._send_to_printer(ops)
        except Exception as exc:                                  # pragma: no cover - defensive
            return False, f"تعذرت الطباعة: {exc}", None

    def bill_printer(self):
        if self._printer_override is not None:
            return self._printer_override
        return self._settings_instance().bill_printer() or ""

    def bill_printer_ready(self):
        """(ready, message): a configured Bill printer that Windows currently reports."""
        from config.printer_settings import installed_printers
        name = self.bill_printer()
        if not name:
            return False, "لم يتم تحديد طابعة الفواتير. اخترها من الإعدادات."
        if name not in installed_printers():
            return False, f"طابعة الفواتير '{name}' غير متاحة حالياً. اختر طابعة أخرى من الإعدادات."
        return True, ""

    def _send_to_printer(self, ops):
        from PySide6.QtGui import QPainter
        from PySide6.QtPrintSupport import QPrinter

        ready, message = self.bill_printer_ready()
        if not ready:
            return False, message, None
        printer = QPrinter(QPrinter.HighResolution)
        ok, message, printable = configure_bill_printer(printer, self.bill_printer())
        if not ok:
            return False, message, None
        try:
            bill_overlay.validate(ops, self.calibration(), printable)
        except LayoutError as exc:
            return False, str(exc), None
        printer.setDocName("masGold bill")
        painter = QPainter()
        if not painter.begin(printer):
            return False, "تعذر بدء الطباعة على طابعة الفواتير.", None
        try:
            bill_overlay.paint(painter, ops, printer.resolution() / 25.4)
        finally:
            painter.end()
        return True, "تم إرسال الفاتورة إلى الطابعة.", None

    # ------------------------------------------------------------ test / calibration page (no database)

    @staticmethod
    def sample_invoice():
        """A calibration bill with the maximum 10 sale rows: 23,000 = 5,000 cash + 15,000 InstaPay.
        It carries an internal 3,000 customer-account amount on purpose — it must never print."""
        now = datetime.now()
        names = ("خاتم", "حلق دبلة", "سلسلة", "غويشة", "دبلة", "انسيال", "محبس", "تعليقة", "خلخال", "طقم")
        lines = [{"line_type": "SELL_INVENTORY", "item_name_snapshot": name, "factory_snapshot": "لوكس",
                  "weight_grams": Decimal("3.600") + index, "karat_value": 21 if index % 2 else 18, "quantity": 1,
                  "notes": "تجربة طباعة" if index == 0 else ""} for index, name in enumerate(names)]
        return {"invoice_number": "26092700", "invoice_date": now, "finalized_at": now,
                "customer_name_snapshot": "عميل تجريبي", "place_snapshot": "القناطر",
                "net_direction": "CUSTOMER_PAYS_SHOP", "net_amount": Decimal("23000.00"),
                "balance_before": Decimal("0.00"), "balance_after": Decimal("3000.00"),
                "lines": lines,
                "payments": [{"payment_id": "1", "payment_method_code": "PHYSICAL_CASH", "payment_method_name": "نقدي",
                              "direction": "IN", "amount": Decimal("5000.00")},
                             {"payment_id": "2", "payment_method_code": "INSTAPAY", "payment_method_name": "InstaPay",
                              "direction": "IN", "amount": Decimal("15000.00")}],
                "payment_accounts": [{"payment_id": "2", "bank_account_name": "البنك الأهلي"}]}

    def print_test_page(self):
        """Settings → طباعة فاتورة تجريبية: the Bill printer only; reads and writes nothing."""
        printed, message, _ = self._print_ops(self.sample_invoice())
        return PrintOutcome(invoice_number="", printed=printed, message=message)

    # ------------------------------------------------------------ previews (never sent to a printer)

    def render_to_pdf(self, invoice_id, path=None, *, _expected_actor=None):
        """The black overlay only, on a 210 x 148 mm PDF page at the physical positions."""
        self._actor(_expected_actor)
        ops, problem = self.compose(self._invoice_data(invoice_id))
        if ops is None:
            raise PrintError("LAYOUT", problem)
        return write_pdf(ops, path)


def write_pdf(ops, path=None):
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize, QPainter
    from PySide6.QtPrintSupport import QPrinter

    path = path or os.path.join(tempfile.gettempdir(), f"invoice_{datetime.now():%Y%m%d_%H%M%S_%f}.pdf")
    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(path)
    printer.setFullPage(True)
    printer.setPageLayout(QPageLayout(QPageSize(QPageSize.A5), QPageLayout.Landscape, QMarginsF(0, 0, 0, 0),
                                      QPageLayout.Millimeter))
    painter = QPainter(printer)
    try:
        bill_overlay.paint(painter, ops, printer.resolution() / 25.4)
    finally:
        painter.end()
    return path


def render_preview_png(ops, path, *, dpi=150, stationery=None, stationery_corners=None):
    """Developer preview. With `stationery` (a photo/scan of the blank paper) it is composited
    BEHIND the overlay — on this image only. `stationery_corners` = the paper's four corners in
    the photo (TL, TR, BR, BL pixels) to undo camera perspective. Never used for printing."""
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QColor, QImage, QPainter, QPolygonF, QTransform

    dots_per_mm = dpi / 25.4
    image = QImage(round(PAPER_WIDTH_MM * dots_per_mm), round(PAPER_HEIGHT_MM * dots_per_mm), QImage.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    try:
        if stationery:
            photo = QImage(stationery)
            target = QPolygonF([QPointF(0, 0), QPointF(image.width(), 0), QPointF(image.width(), image.height()),
                                QPointF(0, image.height())])
            if stationery_corners:
                source = QPolygonF([QPointF(x, y) for x, y in stationery_corners])
                transform = QTransform.quadToQuad(source, target)
                if transform is not None:
                    painter.setRenderHint(QPainter.SmoothPixmapTransform)
                    painter.setTransform(transform)
                    painter.drawImage(0, 0, photo)
                    painter.resetTransform()
            else:
                painter.drawImage(QRectF(0, 0, image.width(), image.height()), photo)
        bill_overlay.paint(painter, ops, dots_per_mm)
    finally:
        painter.end()
    image.setDotsPerMeterX(round(dots_per_mm * 1000))
    image.setDotsPerMeterY(round(dots_per_mm * 1000))
    image.save(path)
    return path
