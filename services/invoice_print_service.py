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

Printing is idempotent: it never allocates a payment or a movement and never changes the invoice,
inventory, old gold or the customer ledger. Its ONE write is the history note of a paper printed at the
full sale total (owner 2026-11-12, only when the customer asks): an insert-only invoice_events row, after
the paper was sent, that can never undo or change the print.

Printer failures (not configured, offline, the driver refusing A5 landscape, a layout that would
cross the preprinted footer) are reported back as an unsuccessful PrintOutcome; they never raise,
so a failure can never be mistaken for a reason to undo the already-finalized invoice.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import logging
import os
import tempfile
import threading

from services import bill_overlay
from services.bill_overlay import MAX_SALE_ITEMS_TEXT, PAPER_HEIGHT_MM, PAPER_WIDTH_MM, LayoutError
from services.invoice_service import invoice_transaction, round_to_egp5
from repositories.invoice_repository import InvoiceRepository

PAGE_TOLERANCE_MM = 1.0

# One wording for every print / reprint screen (the ViewModels show these, QML never invents them).
PRINT_BUSY_TEXT = "جاري إرسال أمر الطباعة..."
PRINT_SENT_TEXT = "تم إرسال الفاتورة للطابعة."
PRINT_FAILED_TEXT = "تعذر إرسال الفاتورة للطابعة."
PRINT_IN_PROGRESS_TEXT = "جاري إرسال هذه الفاتورة للطابعة بالفعل."

# Invoices whose print job is being handed to the print system right now, for the whole process:
# Billing's retry and Invoice History's reprint share it, so one accepted request is one job even
# if a second request arrives (double click, key repeat, a second screen).
_IN_FLIGHT: set[str] = set()
_IN_FLIGHT_LOCK = threading.Lock()


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


NOT_PRINTABLE_TEXT = "شراء الكسر فقط حفظ فقط ولا يُطبع."


def is_old_gold_purchase_only(lines):
    """From the SAVED lines (never from the screen): customer old gold and no shop-sold line. This transaction
    is save-only by the owner's rule — it has no customer invoice to print or reprint."""
    kinds = {line.get("line_type") for line in lines or []}
    return "BUY_OLD_GOLD" in kinds and not kinds - {"BUY_OLD_GOLD"}


SALE_TOTAL_NOTE = "طباعة بإجمالي البيع"
SALE_TOTAL_MIXED_ONLY_TEXT = "الطباعة بإجمالي البيع لفواتير البيع مع الكسر فقط."
SALE_TOTAL_UNKNOWN_TEXT = "إجمالي البيع غير معروف لهذه الفاتورة، اطبعها بالطريقة العادية."


def is_mixed(lines):
    """A sale with the customer's old gold in the same invoice (from the SAVED lines)."""
    kinds = {line.get("line_type") for line in lines or []}
    return "BUY_OLD_GOLD" in kinds and bool(kinds & set(bill_overlay.SALE_LINE_TYPES))


def sale_total_view(invoice):
    """The paper of a MIXED invoice at its full sale value (owner 2026-11-12, only when the customer asks): the customer's old gold
    is not deducted, so «إجمالي الفاتورة» is the sale total (rounded once to 5 ج, as on the screen) and no payment line is printed,
    exactly like a sale invoice. Only the printed figures change; the saved invoice is the same."""
    lines = invoice.get("lines") or []
    if not is_mixed(lines):
        raise PrintError("NOT_MIXED", SALE_TOTAL_MIXED_ONLY_TEXT)
    sale_lines = [line for line in lines if line["line_type"] in bill_overlay.SALE_LINE_TYPES]
    total = invoice.get("sell_total")
    if total is None:                                            # a legacy header without it: the saved sale lines, when all known
        if any(line.get("line_total") is None for line in sale_lines):
            raise PrintError("SALE_TOTAL_UNKNOWN", SALE_TOTAL_UNKNOWN_TEXT)
        total = sum((Decimal(str(line["line_total"])) for line in sale_lines), Decimal("0"))
    total = round_to_egp5(Decimal(str(total)) + Decimal(str(invoice.get("exchange_delta") or 0)))
    if total <= 0:
        raise PrintError("SALE_TOTAL_UNKNOWN", SALE_TOTAL_UNKNOWN_TEXT)
    return dict(invoice, lines=sale_lines, net_amount=total, net_direction="CUSTOMER_PAYS_SHOP", payments=[], payment_accounts=[])


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
            return self._after_exchanges(invoices, invoice_id, invoice)
        return self._read(read)

    @staticmethod
    def _after_exchanges(invoices, invoice_id, invoice):
        """The reprint shows the CURRENT pieces: an effective exchange takes its returned pieces off the sale rows, puts its replacements in their place and
        adds its signed difference to the printed total. The stored invoice is never touched; a reversed exchange no longer counts; any failure keeps the
        original content (the print never fails because of this)."""
        connection = getattr(invoices, "connection", None)
        if connection is None or invoice.get("record_origin") == "LEGACY":
            return invoice
        try:
            from repositories.invoice_lifecycle_repository import InvoiceLifecycleRepository
            exchanges = InvoiceLifecycleRepository(connection).exchange_notes([invoice_id]).get(str(invoice_id), [])
            if not exchanges:
                return invoice
            gone = {str(i) for x in exchanges for i in x["returned"]}
            lines = [l for l in invoice["lines"] if str(l.get("inventory_item_id")) not in gone]
            for x in exchanges:
                for r in x["replacements"]:
                    lines.append({"line_type": "SELL_INVENTORY", "item_name_snapshot": r["name"], "factory_snapshot": None, "weight_grams": r["weight"],
                                  "karat_value": r["karat"], "quantity": 1, "notes": "", "inventory_item_id": r["item_id"]})
            delta = sum((Decimal(str(x["signed_amount"])) for x in exchanges), Decimal("0"))
            result = dict(invoice, lines=lines, exchange_delta=delta)          # the full-sale-total paper adds it too
            if invoice.get("net_direction") not in ("SETTLED", "SHOP_PAYS_CUSTOMER"):
                total = Decimal(str(invoice["net_amount"])) + delta
                if total > 0:
                    result["net_amount"] = total
            return result
        except Exception:
            return invoice

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

    def print_invoice(self, invoice_id, *, sale_total=False, reprint=False, _expected_actor=None):
        """Save MUST already have happened: this only reads and prints. `sale_total`: a MIXED invoice printed at its full sale total
        (`sale_total_view`); that paper is noted in the invoice history (PRINTED, or REPRINTED from سجل الفواتير)."""
        actor = self._actor(_expected_actor)
        key = str(invoice_id)
        with _IN_FLIGHT_LOCK:
            if key in _IN_FLIGHT:                    # the same invoice is already being sent: no second job
                return PrintOutcome(invoice_number="", printed=False, message=PRINT_IN_PROGRESS_TEXT)
            _IN_FLIGHT.add(key)
        try:
            invoice = self._invoice_data(invoice_id)
            if is_old_gold_purchase_only(invoice.get("lines")):
                # Nothing is composed, sent or generated for a save-only transaction.
                return PrintOutcome(invoice_number=invoice["invoice_number"], printed=False,
                                    message=NOT_PRINTABLE_TEXT)
            paper = invoice
            if sale_total:
                try:
                    paper = sale_total_view(invoice)
                except PrintError as exc:
                    return PrintOutcome(invoice_number=invoice["invoice_number"], printed=False, message=str(exc))
            printed, message, pdf_path = self._print_ops(paper)
            if printed and sale_total:
                self._note_sale_total_print(actor, invoice_id, paper["net_amount"], reprint)
            return PrintOutcome(invoice_number=invoice["invoice_number"], printed=printed, message=message,
                                pdf_path=pdf_path)
        finally:
            with _IN_FLIGHT_LOCK:
                _IN_FLIGHT.discard(key)

    def _note_sale_total_print(self, actor, invoice_id, total, reprint):
        """The history note of a full-sale-total paper (who, when, which amount). The paper is already out: a failure here is
        swallowed and never reported as a print failure."""
        try:
            with self._transaction() as connection:
                self._repository(connection).insert_event(
                    invoice_id=invoice_id, event_type="REPRINTED" if reprint else "PRINTED", performed_by_user_id=actor.user_id,
                    notes=SALE_TOTAL_NOTE, details={"print_mode": "SALE_TOTAL", "printed_total": str(total)})
        except Exception as exc:                                     # pragma: no cover - defensive
            logging.getLogger(__name__).warning("sale-total print note not written for invoice %s: %s", invoice_id, exc)

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
        return True, PRINT_SENT_TEXT, None

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
