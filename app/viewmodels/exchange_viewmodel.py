"""استبدال (Exchange V1): the workspace opened from an invoice in سجل الفواتير.

QML only forwards selections (returned lines, scanned replacements, typed making, the money
legs); every number shown comes from ExchangeService.preview() and is formatted here. Nothing
is calculated in QML or in this ViewModel. The read-only exchange detail uses the same object."""
from decimal import Decimal

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
    return number(value, max(3, places), signed)


SOURCE_TEXT = {"SCAN": "بالباركود", "SEARCH": "بالبحث", "MANUAL": "إدخال يدوي", "OLD_GOLD_STOCK": "من الكسر"}
ACCOUNT_TYPE = {"INSTAPAY": "BANK", "BANK_TRANSFER": "BANK", "MOBILE_WALLET": "MOBILE_WALLET"}



def _scrap_breakdown(row):
    """Physical / held / available of one karat, only while something is HELD (032); else ""."""
    from services.old_gold_availability import breakdown_text
    held = Decimal(str(row.get("held") or 0))
    if held <= 0:
        return ""
    return breakdown_text(row.get("physical") or 0, held, row.get("grams") or 0)


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
        self._economics = 0                # bumped by every economic change; stale previews are dropped
        self._notes, self._preview, self._result = "", {}, {}
        self._error, self._scan_error, self._ack = "", "", False
        self._search_open, self._search_results, self._search_message = False, [], ""
        self._panel, self._row_seq = "", 0
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

    @Slot()
    def close(self):
        self._preview_timer.stop()
        for operation in ("context", "preview", "scan", "search"):
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
        self._economics_changed()

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
        self._add_replacement(result, "SCAN")

    def _add_replacement(self, item, identified_by):
        """An identified stock piece (SCAN / SEARCH): the same piece can be added once."""
        if any(r.get("inventory_item_id") == item["inventory_item_id"] for r in self._replacements):
            self._scan_error = "القطعة مضافة بالفعل."
            return
        self._append_row(dict(item, making="", identified_by=identified_by))

    def _append_row(self, row):
        self._row_seq += 1
        row["key"] = f"r{self._row_seq}"                   # a manual / F7 row has no inventory id
        self._replacements.append(row)
        self.replacementsChanged.emit()
        self._economics_changed()

    # ------------------------------------------------------------ F5 إدخال يدوي / F7 من الكسر (as in Billing)
    @Slot(str)
    def openPanel(self, name):
        """"manual" (F5) or "stock" (F7); one entry panel at a time."""
        if self._result:
            return
        self._panel = name if name in ("manual", "stock") else ""
        self._search_open = False
        self._scan_error = ""
        self.changed.emit()

    @Slot()
    def closePanel(self):
        self._panel = ""
        self.changed.emit()

    def _karat_value(self, karat_id):
        return next((k["karat"] for k in self._context.get("old_gold_stock", []) if k["karat_id"] == karat_id), None)

    @Slot("QVariantMap")
    def addManual(self, fields):
        """F5: a piece without inventory identity. The service validates it with Billing's own rules
        (reason, «سبب آخر» notes, item type) when the preview runs; nothing is guessed here."""
        self._add_described("MANUAL", fields)

    @Slot("QVariantMap")
    def addFromOldGold(self, fields):
        """F7: weight from the COUNTED old-gold stock of one karat (checked by the service)."""
        self._add_described("OLD_GOLD_STOCK", fields)

    def _add_described(self, source, fields):
        karat_id = int(fields.get("karatId") or 0)
        weight = (fields.get("weight") or "").strip()
        if not karat_id or not weight:
            self._scan_error = "اختر العيار واكتب الوزن."
            self.changed.emit()
            return
        item_type_id = int(fields.get("itemTypeId") or 0) or None
        type_name = next((t["name"] for t in self._context.get("item_types", []) if t["id"] == item_type_id), "")
        description = (fields.get("description") or "").strip()
        self._scan_error = ""
        self._panel = ""
        self._append_row({
            "inventory_item_id": None, "identified_by": source, "making": "", "barcode": "",
            "item_name": " — ".join(v for v in (type_name, description) if v) or ("من الكسر" if source == "OLD_GOLD_STOCK"
                                                                                  else "إدخال يدوي"),
            "karat_id": karat_id, "karat": self._karat_value(karat_id) or "", "weight_grams": weight,
            "item_type_id": item_type_id, "factory_id": int(fields.get("factoryId") or 0) or None,
            "description": description or None, "size": (fields.get("size") or "").strip() or None,
            "notes": (fields.get("notes") or "").strip() or None,
            "reason_code": (fields.get("reasonCode") or None) if source == "MANUAL" else None})

    # ------------------------------------------------------------ بحث عن قطعة (like Billing F3)
    @Slot()
    def openSearch(self):
        if self._result:
            return
        self._search_open, self._search_results, self._search_message = True, [], ""
        self._panel = ""
        self.changed.emit()

    @Slot()
    def closeSearch(self):
        self._search_open, self._search_results, self._search_message = False, [], ""
        self._busy.discard("search")
        self._latest.pop("search", None)
        self.changed.emit()

    @Slot(str, int, str, str)
    def runSearch(self, text, karat, weight, tolerance):
        """IN_STOCK candidates only; the employee then chooses the physical piece."""
        karat_id = {21: 1, 18: 2, 24: 3}.get(int(karat or 0))
        exclude = tuple(r["inventory_item_id"] for r in self._replacements if r.get("inventory_item_id"))
        service = self._service_instance()
        self._search_message = ""
        self._start("search", lambda _expected_actor: service.search_replacements(
            text=text, karat_id=karat_id, weight=weight or None, tolerance=tolerance or "0.05", exclude_ids=exclude,
            _expected_actor=_expected_actor))

    def _on_search(self, result, error):
        if error:
            self._search_results, self._search_message = [], error
            return
        self._search_results = result["items"]
        self._search_message = ("لا توجد قطع مطابقة في المخزون" if not result["items"]
                                else "النتائج كثيرة؛ ضيّق البحث" if result["truncated"] else "")

    @Slot(str)
    def addFromSearch(self, item_id):
        item = next((r for r in self._search_results if r["inventory_item_id"] == item_id), None)
        if item is None:
            return
        self._scan_error = ""
        self._add_replacement(item, "SEARCH")
        self.closeSearch()

    @Slot(str)
    def removeReplacement(self, key):
        if self._result:
            return
        self._replacements = [r for r in self._replacements if r["key"] != key]
        self.replacementsChanged.emit()
        self._economics_changed()

    @Slot(str, str)
    def setMaking(self, key, text):
        for row in self._replacements:
            if row["key"] == key and row["making"] != (text or "").strip():
                row["making"] = (text or "").strip()
                self._economics_changed()
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

    def _economics_changed(self):
        """The ONE recalculation path for anything that changes what the exchange is worth (returned
        selection, replacement added / removed, replacement making). The old result and the money entered
        against it are discarded at once — a previous total can never survive or be settled."""
        self._economics += 1
        self._preview = {}
        self._amount, self._account = "", ""
        self._changed_input()

    def _changed_input(self):
        self._error = ""
        self.changed.emit()
        if self._selected and self._replacements:
            self._preview_timer.start()
        else:
            self._preview_timer.stop()
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
            replacements=tuple(self._replacement_input(r, ReplacementInput) for r in self._replacements),
            payments=payments, account_amount=self._account or None, notes=self._notes.strip() or None)

    @staticmethod
    def _replacement_input(r, ReplacementInput):
        if r.get("identified_by") in ("MANUAL", "OLD_GOLD_STOCK"):
            return ReplacementInput(None, r["making"] or None, r["identified_by"], karat_id=r["karat_id"],
                                    weight_grams=r["weight_grams"], item_type_id=r.get("item_type_id"),
                                    factory_id=r.get("factory_id"), description=r.get("description"),
                                    size=r.get("size"), notes=r.get("notes"), reason_code=r.get("reason_code"))
        return ReplacementInput(r["inventory_item_id"], r["making"] or None, r.get("identified_by", "SCAN"))

    def _run_preview(self):
        if not (self._context and self._selected and self._replacements):
            return
        service, draft, version = self._service_instance(), self._draft(), self._economics
        self._start("preview", lambda _expected_actor: (version, service.preview(
            draft, _expected_actor=_expected_actor)))

    def _on_preview(self, result, error):
        if error:
            self._preview, self._error = {}, error
            return
        version, preview = result
        if version != self._economics or not (self._selected and self._replacements):
            return                       # computed for a previous draft: never shown, never settled
        self._preview, self._error = preview, ""

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
        return "context" in self._busy

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
                "customerId": str(c.get("customer_id") or ""),
                "hasCustomer": c["has_customer"],
                "windowText": (f"داخل مدة استرداد المصنعية ({c['refund_window_hours']} ساعة)"
                               if c["refund_window_open"] else "انتهت مدة استرداد المصنعية")}

    @Property("QVariantList", notify=changed)
    def lines(self):
        return [{"lineId": l["line_id"], "item": l.get("item_name") or "—", "barcode": l.get("barcode") or "",
                 "identity": self._identity(l.get("barcode"), l.get("inventory_item_id")),
                 "karat": str(l["karat"]), "weight": grams(l["weight_grams"]), "pure": exact_grams(l["pure_grams"]),
                 "exchangeable": l["exchangeable"], "reason": l["reason"],
                 "selected": l["line_id"] in self._selected}
                for l in self._context.get("lines", [])]

    @Property("QVariantList", notify=replacementsChanged)
    def replacements(self):
        return [{"itemId": r["key"], "item": r.get("item_name") or "—", "barcode": r.get("barcode") or "",
                 "identity": self._identity(r.get("barcode"), r.get("inventory_item_id"),
                                            r.get("identified_by") in ("MANUAL", "OLD_GOLD_STOCK")),
                 "karat": str(r["karat"]), "weight": grams(r["weight_grams"]),
                 "pure": exact_grams(r["pure_grams"]) if r.get("pure_grams") else "",
                 "making": r["making"], "sourceText": SOURCE_TEXT.get(r.get("identified_by"), ""),
                 "manual": r.get("identified_by") == "MANUAL", "fromOldGold": r.get("identified_by") == "OLD_GOLD_STOCK"}
                for r in self._replacements]

    # ---- F5 / F7 panels (Billing's own lists from the service context)
    @staticmethod
    def _identity(barcode, item_id, described=False):
        """What tells two identical-looking pieces apart: the barcode (or بدون باركود) and the piece #."""
        if described:
            return "بدون هوية مخزون"
        return f"{barcode or 'بدون باركود'}" + (f"  •  #{item_id}" if item_id not in (None, "") else "")

    @Property(str, notify=changed)
    def panel(self):
        return self._panel

    @Property("QVariantList", notify=changed)
    def itemTypes(self):
        return [{"id": 0, "name": "اختر الصنف"}] + list(self._context.get("item_types", []))

    @Property("QVariantList", notify=changed)
    def factories(self):
        return [{"id": 0, "name": "بدون مصنع"}] + list(self._context.get("factories", []))

    @Property("QVariantList", notify=changed)
    def reasons(self):
        return [{"code": "", "text": "اختر السبب"}] + list(self._context.get("reasons", []))

    @Property("QVariantList", notify=changed)
    def karats(self):
        """Active karats with the COUNTED old-gold stock (F7 shows what is available before the exchange)."""
        return [{"id": k["karat_id"], "name": f"عيار {k['karat']}", "stock": f"المتاح {grams(k['grams'])}",
                 "breakdown": _scrap_breakdown(k)}
                for k in sorted(self._context.get("old_gold_stock", []), key=lambda k: -k["karat"])]

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
        """The calculation, every step visible: gold, then making (returned refund / replacement /
        difference), then raw, rounding and the final amount. Nothing final while anything is missing."""
        if self._selected and not self._replacements and not self._result:
            return {"shown": True, "complete": False, "statusText": "الاستبدال غير مكتمل"}
        p = self._preview
        if not p:
            return {}
        same_karat = p["valuation_basis"] == "SAME_KARAT"
        price = ""
        if p["price_basis"]:
            price = f"{p['price_basis_text']} عيار {p['price_karat']}: {money(p['price_per_gram'])}"
        gold_signed = Decimal(p["gold_signed"])
        complete = bool(p.get("complete"))

        def signed_text(value):
            value = Decimal(value)
            return money(abs(value)) + (" على العميل" if value > 0 else " للعميل" if value < 0 else "")
        return {
            "shown": True, "complete": complete,
            "statusText": "" if complete else "الحساب غير مكتمل",
            "returnedPure": exact_grams(p["returned_pure_grams"]), "replacementPure": exact_grams(p["replacement_pure_grams"]),
            "deltaLabel": "فرق الوزن (نفس العيار)" if same_karat else "فرق الذهب الصافي (عيار 24)",
            "delta": exact_grams(p["valued_delta_grams"], signed=True), "priceText": price or "لا يلزم سعر (لا فرق في الذهب)",
            "goldAmount": signed_text(gold_signed) if gold_signed else money("0"),
            "makingRefund": money(p["making_refund"]) if p["making_refund_applies"] else (
                "لا يوجد" if not p["making_refund_expired"] else "انتهت المدة"),
            "makingReplacement": "غير محددة" if p["making_missing"] else money(p["making_charge"]),
            "makingDifference": "غير مكتمل" if p["making_missing"] else signed_text(p["making_difference"]),
            "makingMissing": p["making_missing"], "makingRequired": True,
            "rawAmount": "" if not complete else signed_text(p["raw_amount"]),
            "rounding": "" if not complete else (money(p["rounding_adjustment"]) if Decimal(p["rounding_adjustment"])
                                                  else "لا يوجد"),
            "expiredText": p["making_refund_expired_text"],
            "finalAmount": money(p["amount"]) if complete else "",
            "direction": p["direction"] if complete else "", "directionText": p["direction_text"],
            "moneyDirectionText": ("يستلم المحل" if p["money_direction"] == "IN"
                                   else "يدفع المحل" if p["money_direction"] == "OUT" else ""),
            "pendingText": p["returned_pending_text"],
            "reviewText": "المصنعية خارج نطاق الفئة — ستُرسل لمراجعة المدير" if p["making_review_required"] else "",
            "priceWarning": "، ".join(p.get("price_warnings") or []),
            "needsAck": bool(p.get("requires_price_acknowledgement")),
        }

    @Property("QVariantMap", notify=changed)
    def settlement(self):
        """The settlement bar: the service's result (who owes whom, how much) and what the employee has
        entered so far. Display only — the service checks money + account == the result exactly."""
        p = self._preview
        if not p or not p.get("complete"):
            return {}                        # incomplete: there is no amount to settle
        direction = p["direction"]
        if direction == "NONE":
            return {"direction": "NONE", "resultLabel": "لا يوجد فرق مالي", "due": "", "needsMoney": False,
                    "remainingText": "", "complete": True}
        due = Decimal(p["amount"])
        entered = sum((self._decimal_or_zero(v) for v in (self._amount, self._account)), Decimal("0.00"))
        remaining = due - entered
        owes = direction == "CUSTOMER_OWES"
        return {"direction": direction, "resultLabel": "على العميل" if owes else "للعميل", "due": money(due),
                "needsMoney": not self._result,
                "moneyLabel": "يستلم المحل" if owes else "يدفع المحل للعميل",
                "accountLabel": "على حساب العميل" if owes else "للعميل على حسابه",
                "remainingText": "التسوية مكتملة" if remaining == 0 else
                (f"المتبقي {money(remaining)}" if remaining > 0 else f"أكثر من الفرق بـ {money(-remaining)}"),
                "complete": remaining == 0}

    @staticmethod
    def _decimal_or_zero(text):
        try:
            return Decimal(str(text).strip()) if str(text or "").strip() else Decimal("0.00")
        except Exception:
            return Decimal("0.00")

    @Property("QVariantList", notify=changed)
    def problems(self):
        return [p["message"] for p in self._preview.get("blocking_problems", [])]

    @Property(bool, notify=changed)
    def canFinalize(self):
        p = self._preview
        return bool(p and p.get("complete") and not p["blocking_problems"] and not self._busy and not self._result
                    and bool(self._replacements)
                    and not self._preview_timer.isActive()
                    and (self._ack or not p.get("requires_price_acknowledgement")))

    @Property("QVariantMap", notify=replacementsChanged)
    def replacementTotals(self):
        """Presentation totals of the chosen replacement pieces (count, grams, grams per karat)."""
        by_karat = {}
        for r in self._replacements:
            key = int(r["karat"] or 0)
            by_karat[key] = by_karat.get(key, Decimal("0.000")) + Decimal(str(r["weight_grams"]))
        return {"count": str(len(self._replacements)), "total": grams(sum(by_karat.values(), Decimal("0.000"))),
                "karats": [{"karat": str(k), "weight": grams(v)} for k, v in sorted(by_karat.items(), reverse=True)]}

    @Property(bool, notify=changed)
    def searchOpen(self):
        return self._search_open

    @Property(bool, notify=changed)
    def searching(self):
        return "search" in self._busy

    @Property(str, notify=changed)
    def searchMessage(self):
        return self._search_message

    @Property("QVariantList", notify=changed)
    def searchResults(self):
        return [{"itemId": r["inventory_item_id"], "item": r.get("item_name") or "—", "barcode": r.get("barcode") or "",
                 "karat": str(r["karat"]), "weight": grams(r["weight_grams"]), "factory": r.get("factory_name") or "—",
                 "size": r.get("size") or "—"} for r in self._search_results]
