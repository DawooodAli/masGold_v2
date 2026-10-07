"""فواتير شراء الموردين: invoice list / detail, new invoice with making groups, posting and piece-by-piece intake.

Formats what SupplierPurchaseService returns. Authorization and every accounting rule live in the service (it
re-checks each right inside the transaction); the live totals of the invoice form come from the same pure rules
module the service uses, never from QML. Request keys (one per dialog) make a double click idempotent. Weight
variance (فرق الوزن) is shown as information only — it never reaches a balance.
"""
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

from PySide6.QtCore import QObject, Property, Signal, Slot

from app.formatting import date_text, datetime_text, grams, money, percent
from services import supplier_rules as rules

STATUS_TEXT = {"DRAFT": "مسودة", "POSTED": "مرحّلة", "CANCELLED": "ملغاة"}
ITEM_STATUS_TEXT = {"IN_STOCK": "بالمخزون", "SOLD": "مباعة", "RETURNED_PENDING": "مرتجعة — قيد الفحص",
                    "CONVERTED_TO_OLD_GOLD": "محولة إلى كسر", "SCRAPPED": "مستبعدة", "REMOVED": "مستبعدة"}


def variance_text(value):
    """'فرق الوزن: −0.080' — neutral wording: either scale may differ, so it is never called a shortage."""
    return f"فرق الوزن: {grams(value, signed=True)}"


def intake_fields(progress):
    return {"actualText": grams(progress["actual_weight"]), "supplierText": grams(progress["supplier_weight"]),
            "varianceText": variance_text(progress["variance"]),
            "remainingText": f"المتبقي للإدخال (مرجعي): {grams(progress['remaining_reference'])}",
            "percentText": percent(progress["percent"], 1), "overSupplier": bool(progress["over_supplier_weight"])}


class SupplierPurchaseViewModel(QObject):
    changed = Signal()
    invoicePosted = Signal(str)          # invoice id: supplier balances changed
    stockChanged = Signal()              # a piece (and its barcode) was added to inventory
    draftSaved = Signal(str)             # a draft invoice was saved (the dialog can close)

    def __init__(self, session, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._workers, self._request, self._latest, self._busy = set(), 0, {}, set()
        self._shutdown = False
        self._invoices, self._detail = [], {}
        self._supplier_filter, self._open_invoice = "", ""
        self._error, self._message, self._done_text = "", "", ""
        self._last_piece = {}

    # ------------------------------------------------------------ plumbing
    def _service_instance(self):
        if self._service is None:
            from services.supplier_purchase_service import SupplierPurchaseService
            self._service = SupplierPurchaseService(self._session)
        return self._service

    def _start(self, operation, call):
        from app.workers.invoice_worker import InvoiceWorker
        actor = self._session.current_actor()
        if self._shutdown or actor is None:
            return
        self._request += 1
        self._latest[operation] = self._request
        self._busy.add(operation)
        worker = InvoiceWorker(call, self._session, actor, self._request, operation, self)
        worker.completed.connect(self._completed)
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        self.changed.emit()
        worker.start()

    @Slot(int, str, str, object, str, str, str)
    def _completed(self, request, generation, operation, result, error, code, kind):
        actor = self._session.current_actor()
        if self._shutdown or actor is None or str(actor.generation) != generation:
            return
        if self._latest.get(operation) != request:
            return
        self._busy.discard(operation)
        getattr(self, f"_on_{operation}")(result, error)
        self.changed.emit()

    @Slot()
    def shutdown(self):
        self._shutdown = True
        for worker in list(self._workers):
            worker.wait(5000)

    @Slot(result=str)
    def newRequestKey(self):
        """One key per dialog / piece form; resubmitting the same key can never create a second record."""
        return str(uuid4())

    # ------------------------------------------------------------ reads
    @Slot(str)
    def showSupplier(self, supplier_id):
        """Invoices of one supplier ('' = every supplier)."""
        self._supplier_filter = str(supplier_id or "")
        self.refresh()

    @Slot()
    def refresh(self):
        if not self.canView:
            return
        service, supplier = self._service_instance(), self._supplier_filter or None
        self._start("list", lambda _expected_actor: service.list_invoices(supplier, None,
                                                                         _expected_actor=_expected_actor))
        if self._open_invoice:
            self._load_detail()

    def _on_list(self, result, error):
        if error:
            self._error = error
            return
        self._invoices, self._error = result, ""

    @Slot(str)
    def openInvoice(self, invoice_id):
        self._open_invoice = str(invoice_id or "")
        self._detail, self._last_piece = {}, {}
        self.changed.emit()
        if self._open_invoice:
            self._load_detail()

    @Slot()
    def closeInvoice(self):
        self._open_invoice, self._detail = "", {}
        self.changed.emit()

    def _load_detail(self):
        service, invoice_id = self._service_instance(), self._open_invoice
        self._start("detail", lambda _expected_actor: service.invoice_detail(invoice_id,
                                                                           _expected_actor=_expected_actor))

    def _on_detail(self, result, error):
        if error:
            self._error = error
            return
        if result["id"] == self._open_invoice:
            self._detail, self._error = result, ""

    @Slot()
    def clearMessage(self):
        if self._message or self._error:
            self._message = self._error = ""
            self.changed.emit()

    # ------------------------------------------------------------ live form totals (the same pure rules)
    @Slot("QVariantList", int, result="QVariantMap")
    def previewGroups(self, groups, karat_value):
        """Totals of the invoice form: supplier weight, gross making, Eq21 and each group's making."""
        try:
            normalized = rules.normalize_groups(
                [{"weight": g.get("weight"), "making_per_gram": g.get("rate"), "description": g.get("description")}
                 for g in groups])
        except rules.SupplierRuleError as exc:
            return {"valid": False, "error": str(exc), "weightText": "—", "makingText": "—", "eq21Text": "—",
                    "groupMaking": []}
        weight = normalized["total_weight"]
        eq21 = rules.equivalent_21(weight, karat_value) if int(karat_value or 0) > 0 else None
        return {"valid": True, "error": "", "weightText": grams(weight), "makingText": money(normalized["gross_making"]),
                "eq21Text": grams(eq21) if eq21 is not None else "—",
                "groupMaking": [money(g["making_total"]) for g in normalized["groups"]]}

    # ------------------------------------------------------------ writes (each re-checked by the service)
    def _write(self, text, call):
        if "write" in self._busy:
            return
        self._message = self._error = ""
        self._done_text = text
        service = self._service_instance()
        self._start("write", lambda _expected_actor: call(service, _expected_actor))
        self.changed.emit()

    def _on_write(self, result, error):
        if error:
            self._error = error
            return
        self._message = self._done_text
        result = result or {}
        if result.get("kind") == "piece":
            self._last_piece = result
            self.stockChanged.emit()
        if result.get("posted"):
            self.invoicePosted.emit(result["id"])
        elif result.get("status") == "DRAFT" and result.get("id"):
            self.draftSaved.emit(result["id"])
        self.refresh()

    @staticmethod
    def _groups(groups):
        return [{"weight": g.get("weight"), "making_per_gram": g.get("rate"), "description": g.get("description")}
                for g in groups]

    @Slot(str, int, str, str, "QVariantList", str, str, bool)
    def saveInvoice(self, supplier_id, karat_id, reference, invoice_date, groups, notes, request_key, post_now):
        """Create the draft; with post_now also post it (two atomic steps — a failed post leaves the saved draft)."""
        grouped = self._groups(groups)

        def call(service, expected):
            draft = service.create_draft(supplier_id, karat_id, reference, invoice_date, grouped, notes, request_key,
                                         _expected_actor=expected)
            if not post_now:
                return draft
            try:
                # Derived from the draft key: a double submit posts with the SAME key, so it stays idempotent.
                posted = service.post_invoice(draft["id"], str(uuid5(UUID(request_key), "post")),
                                              _expected_actor=expected)
            except (ValueError, PermissionError) as exc:
                raise rules.SupplierRuleError(
                    getattr(exc, "code", "POST_FAILED"),
                    f"تم حفظ المسودة {draft['code']} ولكن تعذر الترحيل: {exc}") from exc
            return {**posted, "posted": True}
        self._write("تم ترحيل فاتورة المورد." if post_now else "تم حفظ مسودة الفاتورة.", call)

    @Slot(str, str)
    def postInvoice(self, invoice_id, request_key):
        self._write("تم ترحيل فاتورة المورد.",
                    lambda s, a: {**s.post_invoice(invoice_id, request_key, _expected_actor=a), "posted": True})

    @Slot(str)
    def cancelDraft(self, invoice_id):
        def call(service, expected):
            service.cancel_draft(invoice_id, _expected_actor=expected)
            return {"id": invoice_id}
        self._write("تم إلغاء المسودة.", call)

    @Slot(str, str, int, str, str, int, int, str, str, str, str)
    def addPiece(self, invoice_id, group_id, item_type_id, weight, request_key, category_id, factory_id, size,
                 barcode_mode, barcode, notes):
        def call(service, expected):
            piece = service.add_piece(invoice_id, group_id, item_type_id, weight, request_key,
                                      category_id=category_id or None, factory_id=factory_id or None, size=size,
                                      barcode_mode=barcode_mode, barcode=barcode, notes=notes, _expected_actor=expected)
            return {**piece, "kind": "piece"}
        self._write("تم إدخال القطعة بنجاح.", call)

    # ------------------------------------------------------------ properties
    def _can(self, permission):
        actor = self._session.current_actor()
        return bool(actor and permission in actor.permissions)

    @Property(bool, notify=changed)
    def canView(self):
        return self._can("SUPPLIER_VIEW")

    @Property(bool, notify=changed)
    def canEdit(self):
        return self._can("SUPPLIER_INVOICE_EDIT")

    @Property(bool, notify=changed)
    def canPost(self):
        return self._can("SUPPLIER_INVOICE_POST")

    @Property(bool, notify=changed)
    def canIntake(self):
        return self._can("SUPPLIER_INTAKE")

    @Property(bool, notify=changed)
    def busy(self):
        return bool(self._busy)

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(str, notify=changed)
    def openInvoiceId(self):
        return self._open_invoice

    @Property("QVariantMap", notify=changed)
    def lastPiece(self):
        piece = self._last_piece
        if not piece:
            return {}
        return {"inventoryItemId": piece["inventory_item_id"], "barcode": piece["barcode"] or "بدون باركود",
                "hasBarcode": bool(piece["barcode"]), "weightText": grams(piece["weight"]),
                "makingText": money(piece["making_per_gram"]) + " / جرام", "duplicate": bool(piece["duplicate"])}

    @Property("QVariantList", notify=changed)
    def invoices(self):
        return [{"id": i["id"], "code": i["code"], "supplierName": i["supplier_name"],
                 "reference": i["supplier_reference"] or "—", "date": date_text(i["invoice_date"]),
                 "karat": i["karat_value"], "weightText": grams(i["supplier_weight"]),
                 "makingText": money(i["gross_making"]), "status": i["status"],
                 "statusText": STATUS_TEXT.get(i["status"], i["status"]), "pieceCount": i["piece_count"],
                 "actualText": grams(i["intake_actual_weight"]),
                 "varianceText": variance_text(i["intake_variance"]) if i["piece_count"] else "—",
                 "percentText": percent(i["intake_percent"], 1)}
                for i in self._invoices]

    @Property("QVariantMap", notify=changed)
    def detail(self):
        d = self._detail
        if not d:
            return {}
        return {
            "id": d["id"], "code": d["code"], "supplierId": d["supplier_id"], "supplierName": d["supplier_name"],
            "reference": d["supplier_reference"] or "—", "date": date_text(d["invoice_date"]),
            "karat": d["karat_value"], "status": d["status"], "statusText": STATUS_TEXT.get(d["status"], d["status"]),
            "posted": d["status"] == "POSTED", "draft": d["status"] == "DRAFT",
            "weightText": grams(d["supplier_weight"]), "eq21Text": grams(d["eq21"]),
            "makingText": money(d["gross_making"]), "notes": d["notes"],
            "postedAt": datetime_text(d["posted_at"]) if d["posted_at"] else "",
            "pieceCount": d["piece_count"], **intake_fields(d["intake"]),
            "groups": [{"id": g["id"], "no": g["group_no"], "description": g["description"],
                        "weightText": grams(g["supplier_weight"]), "rateText": money(g["making_per_gram"]) + " / جرام",
                        "makingText": money(g["making_total"]), "pieceCount": g["piece_count"],
                        **intake_fields(g)} for g in d["groups"]],
            "pieces": [{"id": p["id"], "itemId": p["inventory_item_id"], "groupNo": p["group_no"],
                        "weightText": grams(p["weight"]), "rateText": money(p["making_per_gram"]),
                        "itemType": p["item_type"], "size": p["size"],
                        "barcode": p["barcode"] or "بدون باركود", "hasBarcode": bool(p["barcode"]),
                        "statusText": ITEM_STATUS_TEXT.get(p["status"], p["status"])} for p in d["pieces"]],
        }
