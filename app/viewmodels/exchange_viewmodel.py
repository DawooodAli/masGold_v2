"""استبدال (Exchange V1): the workspace opened from an invoice in سجل الفواتير.

QML only forwards selections (returned lines, scanned replacements, typed making, the money
legs); every number shown comes from ExchangeService.preview() and is formatted here. Nothing
is calculated in QML or in this ViewModel. The read-only exchange detail uses the same object."""
from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from app.formatting import grams, ltr, money, number
from app.workers.invoice_worker import InvoiceWorker

METHODS = [{"code": "PHYSICAL_CASH", "text": "نقدي"}, {"code": "INSTAPAY", "text": "InstaPay"},
           {"code": "BANK_TRANSFER", "text": "تحويل بنكي"}, {"code": "MOBILE_WALLET", "text": "محفظة"}]
METHOD_TEXT = {m["code"]: m["text"] for m in METHODS}
def exact_grams(value, signed=False):
    """Pure / delta grams with every decimal the service kept (3.0625 stays 3.0625, never 3.063)."""
    text = str(value)
    places = len(text.split(".")[1]) if "." in text else 3
    return f"{number(value, max(3, places), signed)} جم"


ACCOUNT_TYPE = {"INSTAPAY": "BANK", "BANK_TRANSFER": "BANK", "MOBILE_WALLET": "MOBILE_WALLET"}


class ExchangeViewModel(QObject):
    changed = Signal()
    # Only when a replacement is added / removed: typing a making value must not rebuild the rows
    # (a rebuilt Repeater delegate would lose the text field's focus on every keystroke).
    replacementsChanged = Signal()
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
        self._mode, self._context, self._selected, self._replacements = "", {}, [], []
        self._method, self._amount, self._bank, self._account = "PHYSICAL_CASH", "", "", ""
        self._notes, self._preview, self._result, self._detail = "", {}, {}, {}
        self._error, self._scan_error, self._ack = "", "", False
        self.replacementsChanged.emit()

    # ------------------------------------------------------------ plumbing
    def _service_instance(self):
        if self._service is None:
            from services.exchange_service import ExchangeService
            self._service = ExchangeService(self._session)
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
        """Start an exchange on this finalized invoice."""
        if not invoice_id:
            return
        self._preview_timer.stop()
        self._reset()
        self._mode = "edit"
        service = self._service_instance()
        self._start("context", lambda _expected_actor: service.context(invoice_id, _expected_actor=_expected_actor))

    def _on_context(self, result, error):
        if error:
            self._error = error
            return
        self._context = result
        # The only exchangeable piece is chosen for the employee (still visible and changeable).
        exchangeable = [line["line_id"] for line in result["lines"] if line["exchangeable"]]
        if len(exchangeable) == 1:
            self._selected = exchangeable

    @Slot(str)
    def openDetail(self, lifecycle_id):
        """The read-only view of one finished exchange."""
        if not lifecycle_id:
            return
        self._preview_timer.stop()
        self._reset()
        self._mode = "view"
        service = self._service_instance()
        self._start("detail", lambda _expected_actor: service.detail(lifecycle_id, _expected_actor=_expected_actor))

    def _on_detail(self, result, error):
        if error:
            self._error = error
            return
        self._detail = result

    @Slot()
    def close(self):
        self._preview_timer.stop()
        for operation in ("context", "preview", "scan", "detail"):
            self._busy.discard(operation)
            self._latest.pop(operation, None)
        if "finalize" in self._busy:
            return                                   # never hide a save that is still running
        self._reset()
        self.changed.emit()

    # ------------------------------------------------------------ selections
    @Slot(str)
    def toggleReturned(self, line_id):
        line = next((l for l in self._context.get("lines", []) if l["line_id"] == line_id), None)
        if line is None or not line["exchangeable"] or self._result:
            return
        if line_id in self._selected:
            self._selected.remove(line_id)
        else:
            self._selected.append(line_id)
        self._changed_input()

    @Slot(str)
    def addReplacementBarcode(self, text):
        from app.barcode_input import canonical_barcode
        barcode, _ = canonical_barcode((text or "").strip())
        self._scan_error = ""
        if not barcode or self._result:
            self.changed.emit()
            return
        service = self._service_instance()
        self._start("scan", lambda _expected_actor: service.find_replacement_by_barcode(
            barcode, _expected_actor=_expected_actor))

    def _on_scan(self, result, error):
        if error:
            self._scan_error = error
            return
        if any(r["inventory_item_id"] == result["inventory_item_id"] for r in self._replacements):
            self._scan_error = "القطعة مضافة بالفعل."
            return
        self._replacements.append(dict(result, making=""))
        self.replacementsChanged.emit()
        self._changed_input()

    @Slot(str)
    def removeReplacement(self, item_id):
        if self._result:
            return
        self._replacements = [r for r in self._replacements if r["inventory_item_id"] != item_id]
        self.replacementsChanged.emit()
        self._changed_input()

    @Slot(str, str)
    def setMaking(self, item_id, text):
        for row in self._replacements:
            if row["inventory_item_id"] == item_id and row["making"] != (text or "").strip():
                row["making"] = (text or "").strip()
                self._changed_input()
                return

    @Slot(str)
    def setMethod(self, code):
        if code in METHOD_TEXT and code != self._method:
            self._method, self._bank = code, ""
            self._changed_input()

    @Slot(str)
    def setBankAccount(self, account_id):
        self._bank = account_id or ""
        self._changed_input()

    @Slot(str)
    def setAmount(self, text):
        self._amount = (text or "").strip()
        self._changed_input()

    @Slot(str)
    def setAccountAmount(self, text):
        self._account = (text or "").strip()
        self._changed_input()

    @Slot(str)
    def setNotes(self, text):
        self._notes = text or ""

    @Slot()
    def payFullAmount(self):
        """Put the whole difference computed by the service into the money leg."""
        if self._preview.get("amount") and self._preview.get("signed_amount") not in (None, "0.00"):
            self._amount, self._account = self._preview["amount"], ""
            self._changed_input()

    @Slot(bool)
    def setPriceAcknowledged(self, value):
        self._ack = bool(value)
        self.changed.emit()

    def _changed_input(self):
        self._error = ""
        self.changed.emit()
        if self._selected and self._replacements:
            self._preview_timer.start()
        else:
            self._preview = {}

    # ------------------------------------------------------------ preview / finalize
    def _draft(self):
        from services.exchange_service import ExchangeDraft, ReplacementInput
        from services.invoice_service import PaymentInput
        payments = ()
        if self._amount and self._amount not in ("0", "0.00"):
            payments = (PaymentInput(self._method, self._amount, None, None, self._bank or None),)
        return ExchangeDraft(
            source_invoice_id=self._context["invoice_id"], returned_line_ids=tuple(self._selected),
            replacements=tuple(ReplacementInput(r["inventory_item_id"], r["making"] or None)
                               for r in self._replacements),
            payments=payments, account_amount=self._account or None, notes=self._notes.strip() or None)

    def _run_preview(self):
        if not (self._context and self._selected and self._replacements):
            return
        service, draft = self._service_instance(), self._draft()
        self._start("preview", lambda _expected_actor: service.preview(draft, _expected_actor=_expected_actor))

    def _on_preview(self, result, error):
        if error:
            self._preview, self._error = {}, error
            return
        self._preview, self._error = result, ""

    @Slot()
    def finalize(self):
        if not self.canFinalize:
            return
        service, draft, token = self._service_instance(), self._draft(), self._preview["price_token"]
        ack = self._ack
        self._start("finalize", lambda _expected_actor: service.finalize(
            draft, token, stale_acknowledged=ack, _expected_actor=_expected_actor))

    def _on_finalize(self, result, error):
        if error:
            self._error = error
            self._preview_timer.start()                 # prices / stock may have changed: recalculate
            return
        self._result = result
        self.finalized.emit(self._context["invoice_id"])

    # ------------------------------------------------------------ properties
    @Property(bool, notify=changed)
    def isOpen(self):
        return bool(self._mode)

    @Property(str, notify=changed)
    def mode(self):
        return self._mode

    @Property(bool, notify=changed)
    def busy(self):
        return bool(self._busy & {"context", "detail"})

    @Property(bool, notify=changed)
    def previewBusy(self):
        return "preview" in self._busy or self._preview_timer.isActive()

    @Property(bool, notify=changed)
    def saving(self):
        return "finalize" in self._busy

    @Property(bool, notify=changed)
    def scanning(self):
        return "scan" in self._busy

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property(str, notify=changed)
    def scanError(self):
        return self._scan_error

    @Property(bool, notify=changed)
    def done(self):
        return bool(self._result)

    @Property(str, notify=changed)
    def doneMessage(self):
        r = self._result
        if not r:
            return ""
        text = f"تم حفظ الاستبدال {r['code']}"
        if r["direction"] != "NONE":
            text += f" — {r['direction_text']}: {money(r['signed_amount'].lstrip('-'))}"
        return text + "."

    @Property("QVariantMap", notify=changed)
    def header(self):
        c = self._context
        if not c:
            return {}
        return {"invoiceId": c["invoice_id"], "number": c["invoice_number"],
                "finalizedAt": ltr(c["finalized_at"][:16]), "customerName": c["customer_name"] or "بدون عميل",
                "hasCustomer": c["has_customer"],
                "windowText": (f"داخل مدة استرداد المصنعية ({c['refund_window_hours']} ساعة)"
                               if c["refund_window_open"] else "انتهت مدة استرداد المصنعية")}

    @Property("QVariantList", notify=changed)
    def lines(self):
        return [{"lineId": l["line_id"], "item": l.get("item_name") or "—", "barcode": l.get("barcode") or "",
                 "karat": str(l["karat"]), "weight": grams(l["weight_grams"]), "pure": exact_grams(l["pure_grams"]),
                 "exchangeable": l["exchangeable"], "reason": l["reason"],
                 "selected": l["line_id"] in self._selected}
                for l in self._context.get("lines", [])]

    @Property("QVariantList", notify=replacementsChanged)
    def replacements(self):
        return [{"itemId": r["inventory_item_id"], "item": r.get("item_name") or "—", "barcode": r.get("barcode") or "",
                 "karat": str(r["karat"]), "weight": grams(r["weight_grams"]), "pure": exact_grams(r["pure_grams"]),
                 "making": r["making"]} for r in self._replacements]

    @Property("QVariantList", notify=changed)
    def methods(self):
        return METHODS

    @Property(str, notify=changed)
    def method(self):
        return self._method

    @Property(bool, notify=changed)
    def methodNeedsAccount(self):
        return self._method in ACCOUNT_TYPE

    @Property("QVariantList", notify=changed)
    def bankAccounts(self):
        wanted = ACCOUNT_TYPE.get(self._method)
        return [{"id": a["id"], "name": a["name"]} for a in self._context.get("bank_accounts", [])
                if a["account_type"] == wanted]

    @Property(str, notify=changed)
    def amount(self):
        return self._amount

    @Property(str, notify=changed)
    def accountAmount(self):
        return self._account

    @Property("QVariantMap", notify=changed)
    def summary(self):
        p = self._preview
        if not p:
            return {}
        same_karat = p["valuation_basis"] == "SAME_KARAT"
        making_label, making_value = "", ""
        if p["making_required"]:
            making_label = "مصنعية الذهب الزائد"
            making_value = "اكتب المصنعية" if p["making_missing"] else money(p["making_charge"])
        elif p["making_refund_applies"]:
            making_label, making_value = p["making_refund_text"], money(p["making_refund"])
        price = ""
        if p["price_basis"]:
            price = f"{p['price_basis_text']} عيار {p['price_karat']}: {money(p['price_per_gram'])}/جم"
        return {
            "returnedPure": exact_grams(p["returned_pure_grams"]), "replacementPure": exact_grams(p["replacement_pure_grams"]),
            "deltaLabel": "فرق الوزن (نفس العيار)" if same_karat else "فرق الذهب الصافي (عيار 24)",
            "delta": exact_grams(p["valued_delta_grams"], signed=True), "priceText": price or "لا يلزم سعر (لا فرق في الذهب)",
            "goldAmount": money(p["gold_amount"]), "makingLabel": making_label, "makingValue": making_value,
            "makingMissing": p["making_missing"], "makingRequired": p["making_required"],
            "expiredText": p["making_refund_expired_text"],
            "finalAmount": money(p["amount"]), "direction": p["direction"], "directionText": p["direction_text"],
            "moneyDirectionText": ("يستلم المحل" if p["money_direction"] == "IN"
                                   else "يدفع المحل" if p["money_direction"] == "OUT" else ""),
            "pendingText": p["returned_pending_text"],
            "reviewText": "المصنعية خارج نطاق الفئة — ستُرسل لمراجعة المدير" if p["making_review_required"] else "",
            "priceWarning": "، ".join(p.get("price_warnings") or []),
            "needsAck": bool(p.get("requires_price_acknowledgement")),
        }

    @Property("QVariantList", notify=changed)
    def problems(self):
        return [p["message"] for p in self._preview.get("blocking_problems", [])]

    @Property(bool, notify=changed)
    def canFinalize(self):
        p = self._preview
        return bool(p and not p["blocking_problems"] and not self._busy and not self._result
                    and not self._preview_timer.isActive()
                    and (self._ack or not p.get("requires_price_acknowledgement")))

    @Property("QVariantMap", notify=changed)
    def detail(self):
        d = self._detail
        if not d:
            return {}

        def pieces(rows):
            return [{"item": r.get("item_name") or "—", "barcode": r.get("barcode") or "", "karat": str(r["karat"]),
                     "weight": grams(r["weight_grams"]), "pure": exact_grams(r["pure_grams"])} for r in rows]
        making = ""
        if d["making_refund_applies"]:
            making = f"استرداد مصنعية: {money(d['making_refund'])}"
        elif d["making_charge"] not in (None, "0.00"):
            making = f"مصنعية الذهب الزائد: {money(d['making_charge'])}"
        legs = [f"{METHOD_TEXT.get(p['method_code'], p['method_code'])}"
                + (f" — {p['account_name']}" if p.get("account_name") else "")
                + f" ({'مستلم' if p['direction'] == 'IN' else 'مدفوع'}): {money(p['amount'])}" for p in d["payments"]]
        account = d["account_signed"]
        return {"code": d["code"], "sourceNumber": d["source_invoice_number"], "sourceInvoiceId": d["source_invoice_id"],
                "occurredAt": ltr(d["occurred_at"][:16]), "actor": d["actor"] or "—",
                "returned": pieces(d["returned"]), "replacements": pieces(d["replacements"]),
                "returnedPure": exact_grams(d["returned_pure_grams"]), "replacementPure": exact_grams(d["replacement_pure_grams"]),
                "deltaLabel": "فرق الوزن (نفس العيار)" if d["valuation_basis"] == "SAME_KARAT"
                else "فرق الذهب الصافي (عيار 24)",
                "delta": exact_grams(d["valued_delta_grams"], signed=True),
                "priceText": "" if not d["price_basis"] else
                ("سعر بيع المحل" if d["price_basis"] == "SHOP_SELL" else "سعر شراء المحل")
                + f" عيار {d['price_karat']}: {money(d['price_per_gram'])}/جم",
                "goldAmount": money(d["gold_amount"]), "makingText": making,
                "finalAmount": money(d["amount"]), "direction": d["direction"], "directionText": d["direction_text"],
                "payments": legs or ["لا توجد مبالغ فعلية"],
                "accountText": "لا يوجد" if account in (None, "0.00") else
                f"{'على العميل' if not account.startswith('-') else 'للعميل'}: {money(account.lstrip('-'))}",
                "pendingText": d["returned_pending_text"], "notes": d["notes"]}
