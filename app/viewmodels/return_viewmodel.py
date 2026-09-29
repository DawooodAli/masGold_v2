"""مرتجع (Return V1): the workspace opened from an invoice in سجل الفواتير.

QML only forwards selections (lines, F7 grams, payment legs, the account part, notes); every
number shown comes from ReturnService.context() / preview() and is formatted here. Nothing is
calculated in QML. After 72 hours the same screen requests the manager's approval, and resumes
an open / approved Return of this invoice when it is reopened."""
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from app.formatting import grams, ltr, money
from app.workers.invoice_worker import InvoiceWorker

METHODS = [{"code": "PHYSICAL_CASH", "text": "نقدي"}, {"code": "MOBILE_WALLET", "text": "محفظة"},
           {"code": "INSTAPAY", "text": "InstaPay"}, {"code": "BANK_TRANSFER", "text": "تحويل بنكي"}]
METHOD_TEXT = {m["code"]: m["text"] for m in METHODS}
ACCOUNT_TYPE = {"INSTAPAY": "BANK", "BANK_TRANSFER": "BANK", "MOBILE_WALLET": "MOBILE_WALLET"}
APPROVAL_TEXT = {"NOT_REQUIRED": "", "REQUIRED": "يحتاج موافقة المدير", "PENDING": "بانتظار موافقة المدير",
                 "APPROVED": "وافق المدير على هذا المرتجع", "REJECTED": "رفض المدير هذا المرتجع"}
MAX_LEGS = 6


def _decimal(text):
    try:
        return Decimal(str(text).strip()) if str(text or "").strip() else Decimal("0.00")
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


class ReturnViewModel(QObject):
    changed = Signal()
    # Only when the list / a selection changes: typing F7 grams must not rebuild the rows (a rebuilt
    # Repeater delegate would lose the weight field's focus on every keystroke).
    linesChanged = Signal()
    legsChanged = Signal()           # only when a payment row is added / removed (keeps field focus)
    finalized = Signal(str)          # the original invoice id (its History detail must reload)

    def __init__(self, session, service=None, parent=None, debounce_ms=250):
        super().__init__(parent)
        self._session, self._service = session, service
        self._workers, self._request, self._latest, self._busy = set(), 0, {}, set()
        self._shutdown = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(debounce_ms)
        self._preview_timer.timeout.connect(self._run_preview)
        self._reset()

    def _reset(self):
        self._open, self._context, self._selected, self._weights = False, {}, [], {}
        self._legs, self._leg_seq, self._account, self._notes = [], 0, "", ""
        self._preview, self._result, self._return_id, self._return_status = {}, {}, "", ""
        self._error, self._info, self._ack = "", "", False
        self.legsChanged.emit()
        self.linesChanged.emit()

    # ------------------------------------------------------------ plumbing
    def _service_instance(self):
        if self._service is None:
            from services.return_service import ReturnService
            self._service = ReturnService(self._session)
        return self._service

    def _start(self, operation, call):
        if self._shutdown:
            return
        actor = self._session.current_actor()
        if actor is None:
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
        self._preview_timer.stop()
        for worker in list(self._workers):
            worker.wait(5000)

    # ------------------------------------------------------------ open / close
    @Slot(str)
    def open(self, invoice_id):
        if not invoice_id:
            return
        self._preview_timer.stop()
        self._reset()
        self._open = True
        service = self._service_instance()
        self._start("context", lambda _expected_actor: service.context(invoice_id, _expected_actor=_expected_actor))

    def _on_context(self, result, error):
        if error:
            self._error = error
            return
        self._context = result
        # Resume the latest open Return of this invoice (e.g. approved by the manager meanwhile).
        resumable = [r for r in result.get("open_returns", []) if r["status"] != "REJECTED"]
        if resumable:
            latest = resumable[-1]
            self._return_id, self._return_status = latest["return_id"], latest["status"]
            for row in latest.get("selection") or []:
                self._selected.append(str(row["source_line_id"]))
                self._weights[str(row["source_line_id"])] = row["weight_grams"]
        else:
            returnable = [l["line_id"] for l in result["lines"] if l["returnable"] and l["identified"]]
            if len(returnable) == 1 and len([l for l in result["lines"] if l["returnable"]]) == 1:
                self._selected = returnable
        if not self._legs and not result["expired"]:
            self._add_leg()
        self.linesChanged.emit()
        self._changed_input()

    @Slot()
    def close(self):
        self._preview_timer.stop()
        for operation in ("context", "preview"):
            self._busy.discard(operation)
            self._latest.pop(operation, None)
        if self._busy & {"finalize", "request", "cancel"}:
            return                                       # never hide a save that is still running
        self._reset()
        self.changed.emit()

    # ------------------------------------------------------------ selection
    def _line(self, line_id):
        return next((l for l in self._context.get("lines", []) if l["line_id"] == line_id), None)

    @Slot(str)
    def toggleLine(self, line_id):
        line = self._line(line_id)
        if line is None or not line["returnable"] or self._result:
            return
        if line_id in self._selected:
            self._selected.remove(line_id)
        else:
            self._selected.append(line_id)
            if line["partial_allowed"] and line_id not in self._weights:
                self._weights[line_id] = line["remaining_grams"]
        self.linesChanged.emit()
        self._changed_input()

    @Slot(str, str)
    def setWeight(self, line_id, text):
        """F7 grams to return (partial allowed; the service checks the remaining weight exactly)."""
        text = (text or "").strip()
        if self._weights.get(line_id) != text:
            self._weights[line_id] = text
            self._changed_input()

    # ------------------------------------------------------------ payment legs (Billing-style)
    def _add_leg(self, method="PHYSICAL_CASH"):
        self._leg_seq += 1
        self._legs.append({"key": f"p{self._leg_seq}", "method": method, "bank": "", "amount": ""})
        self.legsChanged.emit()

    @Slot()
    def addLeg(self):
        if len(self._legs) < MAX_LEGS and not self._result:
            self._add_leg()
            self._changed_input()

    @Slot(str)
    def removeLeg(self, key):
        self._legs = [l for l in self._legs if l["key"] != key]
        self.legsChanged.emit()
        self._changed_input()

    def _leg(self, key):
        return next((l for l in self._legs if l["key"] == key), None)

    @Slot(str, str)
    def setLegMethod(self, key, code):
        leg = self._leg(key)
        if leg and code in METHOD_TEXT and leg["method"] != code:
            leg["method"], leg["bank"] = code, ""
            self.legsChanged.emit()
            self._changed_input()

    @Slot(str, str)
    def setLegAccount(self, key, account_id):
        leg = self._leg(key)
        if leg:
            leg["bank"] = account_id or ""
            self._changed_input()

    @Slot(str, str)
    def setLegAmount(self, key, text):
        leg = self._leg(key)
        if leg and leg["amount"] != (text or "").strip():
            leg["amount"] = (text or "").strip()
            self._changed_input()

    @Slot(str)
    def setAccountAmount(self, text):
        self._account = (text or "").strip()
        self._changed_input()

    @Slot(str)
    def setNotes(self, text):
        self._notes = text or ""

    @Slot()
    def payRemaining(self):
        """Put what is still owed to the customer into the last payment row (display helper only)."""
        s = self.settlement
        if not s or not self._legs:
            return
        remaining = _decimal(s.get("remainingValue"))
        if remaining > 0:
            leg = self._legs[-1]
            leg["amount"] = str((_decimal(leg["amount"]) + remaining).quantize(Decimal("0.01")))
            self.legsChanged.emit()
            self._changed_input()

    @Slot(bool)
    def setPriceAcknowledged(self, value):
        self._ack = bool(value)
        self.changed.emit()

    def _changed_input(self):
        self._error = ""
        self.changed.emit()
        if self._selected:
            self._preview_timer.start()
        else:
            self._preview = {}

    # ------------------------------------------------------------ preview / actions
    def _draft(self):
        from services.invoice_service import PaymentInput
        from services.return_service import ReturnDraft, ReturnSelection
        lines = tuple(ReturnSelection(line_id, self._weights.get(line_id)
                                      if (self._line(line_id) or {}).get("partial_allowed") else None)
                      for line_id in self._selected)
        payments = tuple(PaymentInput(l["method"], l["amount"], "OUT", None, l["bank"] or None)
                         for l in self._legs if l["amount"] not in ("", "0", "0.00"))
        return ReturnDraft(source_invoice_id=self._context["invoice_id"], lines=lines, payments=payments,
                           account_amount=self._account or None, notes=self._notes.strip() or None,
                           return_id=self._return_id or None)

    def _run_preview(self):
        if not (self._context and self._selected):
            return
        service, draft = self._service_instance(), self._draft()
        self._start("preview", lambda _expected_actor: service.preview(draft, _expected_actor=_expected_actor))

    def _on_preview(self, result, error):
        self._preview, self._error = ({}, error) if error else (result, "")

    @Slot()
    def refresh(self):
        """Reload the invoice (e.g. after the manager decided) keeping nothing stale."""
        if self._context:
            self.open(self._context["invoice_id"])

    @Slot()
    def requestApproval(self):
        if not self.canRequestApproval:
            return
        service, draft = self._service_instance(), self._draft()
        self._start("request", lambda _expected_actor: service.request_approval(draft, _expected_actor=_expected_actor))

    def _on_request(self, result, error):
        if error:
            self._error = error
            return
        self._return_id, self._return_status = result["return_id"], result["status"]
        self._info = f"أُرسل المرتجع {result['code']} لموافقة المدير."
        self._preview_timer.start()

    @Slot()
    def cancelReturn(self):
        if not self._return_id or self._result:
            return
        service, return_id = self._service_instance(), self._return_id
        self._start("cancel", lambda _expected_actor: service.cancel(return_id, _expected_actor=_expected_actor))

    def _on_cancel(self, result, error):
        if error:
            self._error = error
            return
        self._info = f"تم إلغاء المرتجع {result['code']} قبل التنفيذ."
        self._return_id, self._return_status, self._selected, self._weights = "", "", [], {}
        self._preview = {}
        self.linesChanged.emit()

    @Slot()
    def finalize(self):
        if not self.canFinalize:
            return
        service, draft, token, ack = self._service_instance(), self._draft(), self._preview["price_token"], self._ack
        self._start("finalize", lambda _expected_actor: service.finalize(
            draft, token, stale_acknowledged=ack, _expected_actor=_expected_actor))

    def _on_finalize(self, result, error):
        if error:
            self._error = error
            self._preview_timer.start()                  # prices / stock may have changed: recalculate
            return
        self._result = result
        self.finalized.emit(self._context["invoice_id"])

    # ------------------------------------------------------------ properties
    @Property(bool, notify=changed)
    def isOpen(self):
        return self._open

    @Property(bool, notify=changed)
    def busy(self):
        return "context" in self._busy

    @Property(bool, notify=changed)
    def previewBusy(self):
        return "preview" in self._busy or self._preview_timer.isActive()

    @Property(bool, notify=changed)
    def saving(self):
        return bool(self._busy & {"finalize", "request", "cancel"})

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property(str, notify=changed)
    def infoMessage(self):
        return self._info

    @Property(bool, notify=changed)
    def done(self):
        return bool(self._result)

    @Property(str, notify=changed)
    def doneMessage(self):
        r = self._result
        if not r:
            return ""
        return f"تم المرتجع {r['code']} — المدفوع للعميل وحسابه: {money(r['total_amount'])}."

    @Property("QVariantMap", notify=changed)
    def header(self):
        c = self._context
        if not c:
            return {}
        return {"invoiceId": c["invoice_id"], "number": c["invoice_number"],
                "finalizedAt": ltr(c["finalized_at"][:16]), "customerName": c["customer_name"] or "بدون عميل",
                "hasCustomer": c["has_customer"], "windowText": c["window_text"],
                "windowOpen": c["making_window_open"], "approvalRequired": c["requires_approval"] and not c["expired"],
                "deadlineText": "آخر موعد للاسترداد: " + ltr(c["last_allowed_at"][:16]),
                "expired": c["expired"], "expiredText": "انتهت مدة الاسترداد" if c["expired"] else "",
                "returnCode": f"RT{int(self._return_id):06d}" if self._return_id else "",
                "ageText": f"عمر الفاتورة {c['age_days']} يوم"}

    @Property("QVariantList", notify=linesChanged)
    def lines(self):
        out = []
        for l in self._context.get("lines", []):
            selected = l["line_id"] in self._selected
            out.append({"lineId": l["line_id"], "item": l.get("item_name") or ("من الكسر" if l["partial_allowed"] else "—"),
                        "barcode": l.get("barcode") or "", "karat": str(l["karat"]),
                        "sold": grams(l["sold_grams"]), "consumed": grams(l["consumed_grams"]),
                        "remaining": grams(l["remaining_grams"]), "returnable": l["returnable"], "reason": l["reason"],
                        "partial": l["partial_allowed"], "selected": selected,
                        "weight": self._weights.get(l["line_id"], "") if l["partial_allowed"] else grams(l["sold_grams"]),
                        "makingText": ("مصنعية مستردة" if l["making_refundable"]
                                       else "مصنعية الفاتورة غير متاحة" if l["making_missing"] else "بدون مصنعية"),
                        "shopBuy": money(l["shop_buy"]) if l["shop_buy"] else "—",
                        "estimate": money(l["estimated_gold"]) if l.get("estimated_gold") else "—"})
        return out

    @Property("QVariantMap", notify=changed)
    def summary(self):
        p = self._preview
        if not p:
            return {}
        return {"gold": money(p["gold_amount"]), "making": money(p["making_refund"]), "total": money(p["total_amount"]),
                "makingText": "رد المصنعية" if p["making_window_open"] else "رد المصنعية (انتهت المدة)",
                "approvalState": p["approval_state"], "approvalText": APPROVAL_TEXT.get(p["approval_state"], ""),
                "priceWarning": "، ".join(p.get("price_warnings") or []),
                "needsAck": bool(p.get("requires_price_acknowledgement")),
                "lines": [{"item": l.get("item_name") or "—", "karat": str(l["karat"]), "weight": grams(l["weight_grams"]),
                           "price": money(l["shop_buy_price_per_gram"]), "gold": money(l["gold_amount"]),
                           "making": money(l["making_refund"])} for l in p["lines"]]}

    @Property("QVariantMap", notify=changed)
    def settlement(self):
        """إجمالي المستحق للعميل and what is entered so far (display only; the service checks equality)."""
        p = self._preview
        if not p:
            return {}
        due = Decimal(p["total_amount"])
        entered = sum((_decimal(l["amount"]) for l in self._legs), Decimal("0.00")) + _decimal(self._account)
        remaining = due - entered
        return {"due": money(due), "remainingValue": str(remaining), "complete": remaining == 0,
                "remainingText": "تمت التسوية" if remaining == 0 else
                (f"المتبقي {money(remaining)}" if remaining > 0 else f"أكثر من المستحق بـ {money(-remaining)}")}

    @Property("QVariantList", notify=legsChanged)
    def legs(self):
        return [{"key": l["key"], "method": l["method"], "amount": l["amount"], "bank": l["bank"],
                 "needsAccount": l["method"] in ACCOUNT_TYPE,
                 "accounts": [{"id": str(a["id"]), "name": a["name"]} for a in self._context.get("bank_accounts", [])
                              if a["account_type"] == ACCOUNT_TYPE.get(l["method"])]} for l in self._legs]

    @Property("QVariantList", notify=changed)
    def methods(self):
        return METHODS

    @Property(str, notify=changed)
    def accountAmount(self):
        return self._account

    @Property("QVariantList", notify=changed)
    def problems(self):
        return [p["message"] for p in self._preview.get("blocking_problems", [])]

    def _only(self, codes):
        return all(p["code"] in codes for p in self._preview.get("blocking_problems", []))

    @Property(bool, notify=changed)
    def canFinalize(self):
        p = self._preview
        return bool(p and not p["blocking_problems"] and not self._busy and not self._result
                    and not self._preview_timer.isActive() and not p["expired"]
                    and (self._ack or not p.get("requires_price_acknowledgement")))

    @Property(bool, notify=changed)
    def canRequestApproval(self):
        """After 72 hours: the selection is valid except for the missing approval (money may still be blank)."""
        p = self._preview
        return bool(p and p["requires_approval"] and not p["expired"] and not self._busy and not self._result
                    and not self._preview_timer.isActive() and p["approval_state"] in ("REQUIRED", "REJECTED")
                    and self._only({"APPROVAL_REQUIRED", "SETTLEMENT_INCOMPLETE", "AMOUNT_MISMATCH"}))

    @Property(bool, notify=changed)
    def canCancel(self):
        """Only an unfinished Return (draft / waiting / approved); a finalized one is never canceled."""
        return bool(self._return_id and not self._result and not self._busy)

    @Property(str, notify=changed)
    def approvalText(self):
        return APPROVAL_TEXT.get((self._preview or {}).get("approval_state", ""), "")
