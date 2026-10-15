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
REVERSED_TEXT = "معكوسة"                      # a reversed invoice keeps the stored status POSTED: its reversed state is its correction record


def _signed(value, formatter):
    value = Decimal(str(value))
    return ("−" if value < 0 else "+") + formatter(abs(value))


def reversal_fields(preview):
    """The exact effect of reversing an invoice, in words: the opposite entries and the supplier's balances before and after (display only)."""
    from app.viewmodels.supplier_viewmodel import gold_position_text, making_position_text
    karat = preview["karat_value"]
    return {"id": preview["id"], "code": preview["code"], "supplierName": preview["supplier_name"], "karat": karat,
            "reversed": bool(preview["reversed"]), "reversalCode": preview["reversal_code"] or "",
            "canReverse": bool(preview["can_reverse"]),
            "blockers": [b["message"] for b in preview["blockers"]],
            "goldEntryText": f"حركة الذهب العكسية: {_signed(preview['gold_delta'], grams)} جرام عيار {karat}",
            "makingEntryText": (f"حركة المصنعيات العكسية: {_signed(preview['making_delta'], money)} ج"
                                if Decimal(str(preview["making_delta"])) != 0 else "لا توجد مصنعيات على هذه الفاتورة"),
            "goldBeforeText": gold_position_text(rules.direction(preview["gold_before"]), preview["gold_before"], karat),
            "goldAfterText": gold_position_text(rules.direction(preview["gold_after"]), preview["gold_after"], karat),
            "makingBeforeText": making_position_text(rules.direction(preview["making_before"]), preview["making_before"]),
            "makingAfterText": making_position_text(rules.direction(preview["making_after"]), preview["making_after"])}
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


def category_fields(group):
    """The category a group's MAKING RATE resolves to under the existing category rules (shown read-only), or the exact
    reason it cannot be resolved (no matching category / several). Nothing here is stored or chosen on a screen."""
    code, name = str(group["category_code"] or "").strip(), str(group["category_name"] or "").strip()
    text = code if not name or name == code else f"{code} — {name}"
    return {"categoryStatus": group["category_status"], "categoryResolved": group["category_status"] == rules.CATEGORY_RESOLVED,
            "categoryText": text, "categoryProblem": group["category_problem"]}


def allowance_fields(group):
    """'المتاح للإدخال شامل السماحية' — what a group can still take, tolerance (0.333 g) included. Shown as zero when
    negative (display only); `overLimit` marks legacy data that already exceeds the maximum."""
    allowance = group["intake_allowance"]
    over = allowance < 0
    return {"allowanceText": grams(max(allowance, 0)),
            "allowanceLine": f"المتاح للإدخال شامل السماحية: {grams(max(allowance, 0))} جرام",
            "overLimit": bool(over),
            "overLimitText": (f"تجاوزت هذه المجموعة الحد الأقصى المسموح للإدخال (وزن المورد + {rules.INTAKE_TOLERANCE} جرام)"
                              "، ولا يمكن إدخال قطع إضافية منها.") if over else ""}


class SupplierPurchaseViewModel(QObject):
    changed = Signal()
    invoicePosted = Signal(str)          # invoice id: supplier balances changed
    stockChanged = Signal()              # a piece (and its barcode) was added to inventory
    draftSaved = Signal(str)             # a draft invoice was saved (the dialog can close)
    invoiceReversed = Signal(str)        # a posted invoice was fully reversed (052): the confirmation dialog can close

    def __init__(self, session, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._workers, self._request, self._latest, self._busy = set(), 0, {}, set()
        self._shutdown = False
        self._invoices, self._detail = [], {}
        self._invoices_state = "idle"          # idle | loading | loaded | failed (an empty list is only "no invoices" when loaded)
        self._supplier_filter, self._open_invoice = "", ""
        self._error, self._message, self._done_text = "", "", ""
        self._error_from_write = False         # a rejected write's message is not cleared by a later successful read
        self._last_piece = {}
        self._reversal = {}

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
        supplier = str(supplier_id or "")
        if supplier != self._supplier_filter:
            self._invoices = []                # never show the previous supplier's invoices under the new one
        self._supplier_filter = supplier
        self.refresh()

    @Slot()
    def refresh(self):
        if not self.canView:
            return
        service, supplier = self._service_instance(), self._supplier_filter or None
        self._invoices_state = "loading"
        self._start("list", lambda _expected_actor: service.list_invoices(supplier, None,
                                                                         _expected_actor=_expected_actor))
        if self._open_invoice:
            self._load_detail()

    def _on_list(self, result, error):
        if error:
            self._error, self._invoices_state = error, "failed"
            return
        self._invoices, self._error, self._invoices_state = result, "", "loaded"

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
            self._detail = result
            if not self._error_from_write:
                self._error = ""

    @Slot()
    def clearMessage(self):
        if self._message or self._error:
            self._message = self._error = ""
            self._error_from_write = False
            self.changed.emit()

    # ------------------------------------------------------------ live form totals (the same pure rules)
    @Slot("QVariantList", result="QVariantMap")
    def previewGroups(self, groups):
        """Totals of the invoice form: supplier weight, gross making and each group's making (the karat is the supplier's)."""
        try:
            normalized = rules.normalize_groups(
                [{"weight": g.get("weight"), "making_per_gram": g.get("rate"), "description": g.get("description")}
                 for g in groups])
        except rules.SupplierRuleError as exc:
            return {"valid": False, "error": str(exc), "weightText": "—", "makingText": "—",
                    "groupMaking": []}
        weight = normalized["total_weight"]
        return {"valid": True, "error": "", "weightText": grams(weight), "makingText": money(normalized["gross_making"]),
                "groupMaking": [money(g["making_total"]) for g in normalized["groups"]]}

    @Slot(str, str, result=str)
    def pieceWeightProblem(self, group_id, weight):
        """The refusal the service would give for this piece weight in this group, from the loaded detail ('' when
        none is known: empty / unreadable input is the service's to report). A convenience for the form only — the
        service re-checks inside the transaction whatever this says (the detail may be stale)."""
        group = next((g for g in (self._detail or {}).get("groups", []) if g["id"] == str(group_id)), None)
        if group is None:
            return ""
        try:
            piece = rules.parse_weight(weight)
            rules.check_intake(group["supplier_weight"], group["actual_weight"], piece)
        except rules.SupplierRuleError as exc:
            return str(exc) if exc.code in ("GROUP_WEIGHT_LIMIT", "GROUP_OVER_LIMIT") else ""
        return ""

    # ------------------------------------------------------------ writes (each re-checked by the service)
    def _write(self, text, call):
        if "write" in self._busy:
            return
        self._message = self._error = ""
        self._error_from_write = False
        self._done_text = text
        service = self._service_instance()
        self._start("write", lambda _expected_actor: call(service, _expected_actor))
        self.changed.emit()

    def _on_write(self, result, error):
        if error:
            self._error, self._error_from_write = error, True
            if self._open_invoice:
                self._load_detail()            # the allowance may have changed (another submission): show the truth
            return
        self._message = self._done_text
        result = result or {}
        if result.get("kind") == "piece":
            self._last_piece = result
            self.stockChanged.emit()
        if result.get("reversed"):
            self._message = f"{self._done_text} ({result['code']})"
            self._reversal = {}
            self.invoiceReversed.emit(result["id"])
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

    @Slot(str, str, int, str, str, "QVariantList", str)
    def updateDraft(self, invoice_id, supplier_id, karat_id, reference, invoice_date, groups, notes):
        """Save an edited DRAFT under the same invoice id (never a new invoice). The service re-checks that it is still a
        draft, the right to edit and every rule of a new invoice; no ledger or balance is touched."""
        grouped = self._groups(groups)

        def call(service, expected):
            return service.update_draft(invoice_id, supplier_id, karat_id, reference, invoice_date, grouped, notes,
                                        _expected_actor=expected)
        self._write("تم حفظ تعديلات المسودة.", call)

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

    @Slot(str)
    def loadReversal(self, invoice_id):
        """The exact effect of reversing this invoice (read-only), for the confirmation dialog."""
        service = self._service_instance()
        self._reversal, self._error, self._message, self._error_from_write = {}, "", "", False
        self._start("revprev", lambda _expected_actor: service.reversal_preview(invoice_id, _expected_actor=_expected_actor))
        self.changed.emit()

    def _on_revprev(self, result, error):
        if error:
            self._error = error
            return
        self._reversal = result

    @Slot(str, str)
    def reverseInvoice(self, invoice_id, reason):
        """Full reversal through the ONE canonical correction; the service re-checks the owner right, the eligibility and the reason in its transaction."""
        def call(service, expected):
            done = service.reverse_invoice(invoice_id, reason, _expected_actor=expected)
            return {"id": str(invoice_id), "reversed": True, "code": done["code"]}
        self._write("تم عكس فاتورة المورد.", call)

    @Slot(str, str, int, str, str, int, str, str, str, str)
    def addPiece(self, invoice_id, group_id, item_type_id, weight, request_key, factory_id, size,
                 barcode_mode, barcode, notes):
        """The category is NOT a parameter: the service resolves it from the group's making rate."""
        def call(service, expected):
            piece = service.add_piece(invoice_id, group_id, item_type_id, weight, request_key,
                                      factory_id=factory_id or None, size=size,
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
    def canReverse(self):
        return self._can("SUPPLIER_INVOICE_REVERSE") and self._can("TRANSACTION_CORRECT")

    @Property(bool, notify=changed)
    def reversing(self):
        return "write" in self._busy

    @Property("QVariantMap", notify=changed)
    def reversal(self):
        return reversal_fields(self._reversal) if self._reversal else {}

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
        return {"inventoryItemId": piece["inventory_item_id"], "categoryCode": piece.get("category_code", ""),
                "barcode": piece["barcode"] or "بدون باركود",
                "hasBarcode": bool(piece["barcode"]), "weightText": grams(piece["weight"]),
                "makingText": money(piece["making_per_gram"]) + " / جرام", "duplicate": bool(piece["duplicate"])}

    @Property(str, notify=changed)
    def invoicesState(self):
        return self._invoices_state

    @Property("QVariantList", notify=changed)
    def invoices(self):
        return [{"id": i["id"], "code": i["code"], "supplierName": i["supplier_name"],
                 "reference": i["supplier_reference"] or "—", "date": date_text(i["invoice_date"]),
                 "karat": i["karat_value"], "weightText": grams(i["supplier_weight"]),
                 "makingText": money(i["gross_making"]), "status": i["status"],
                 "reversed": bool(i.get("reversed")), "reversalCode": i.get("reversal_code") or "",
                 "statusText": REVERSED_TEXT if i.get("reversed") else STATUS_TEXT.get(i["status"], i["status"]),
                 "pieceCount": i["piece_count"],
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
            "karat": d["karat_value"], "status": d["status"],
            "statusText": REVERSED_TEXT if d.get("reversed") else STATUS_TEXT.get(d["status"], d["status"]),
            "reversed": bool(d.get("reversed")), "reversalCode": d.get("reversal_code") or "",
            "reversalReason": d.get("reversal_reason") or "",
            "reversedAt": datetime_text(d["reversed_at"]) if d.get("reversed_at") else "",
            # a reversed invoice keeps the stored status POSTED but accepts no intake and no second reversal
            "posted": d["status"] == "POSTED" and not d.get("reversed"), "draft": d["status"] == "DRAFT",
            "canReverse": d["status"] == "POSTED" and not d.get("reversed"),
            "weightText": grams(d["supplier_weight"]),
            "makingText": money(d["gross_making"]), "notes": d["notes"],
            "postedAt": datetime_text(d["posted_at"]) if d["posted_at"] else "",
            "pieceCount": d["piece_count"], **intake_fields(d["intake"]),
            # the saved values of a draft, in the form the invoice editor takes them (raw strings, not display texts)
            "form": {"id": d["id"], "code": d["code"], "supplierId": d["supplier_id"], "karatId": int(d["karat_id"]), "karatValue": int(d["karat_value"]),
                     "reference": d["supplier_reference"] or "", "notes": d["notes"] or "",
                     "date": d["invoice_date"].isoformat() if hasattr(d["invoice_date"], "isoformat") else str(d["invoice_date"]),
                     "groups": [{"weight": format(g["supplier_weight"], "f"), "rate": format(g["making_per_gram"], "f"),
                                 "description": g["description"]} for g in d["groups"]]},
            "groups": [{"id": g["id"], "no": g["group_no"], "description": g["description"],
                        "weightText": grams(g["supplier_weight"]), "rateText": money(g["making_per_gram"]) + " / جرام",
                        "makingText": money(g["making_total"]), "pieceCount": g["piece_count"],
                        **intake_fields(g), **allowance_fields(g), **category_fields(g)} for g in d["groups"]],
            "pieces": [{"id": p["id"], "itemId": p["inventory_item_id"], "groupNo": p["group_no"],
                        "weightText": grams(p["weight"]), "rateText": money(p["making_per_gram"]),
                        "itemType": p["item_type"], "size": p["size"],
                        "barcode": p["barcode"] or "بدون باركود", "hasBarcode": bool(p["barcode"]),
                        "statusText": ITEM_STATUS_TEXT.get(p["status"], p["status"])} for p in d["pieces"]],
        }
