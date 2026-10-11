"""الموردون: supplier list, one supplier's net positions and the two supplier ledgers (read side + master writes).

Formats what SupplierService returns; nothing here decides authorization or computes accounting (the service
re-checks the permission in the transaction on every call). A supplier id is an immutable decimal string.

Positions are never shown as an ambiguous signed number: the Arabic wording carries the direction
(علينا للمورد = the shop owes the supplier, لنا عند المورد = the supplier owes the shop) and gold and making
(مصنعيات) are always separate lines. Weights use the project unit جرام.
"""
from decimal import Decimal
from uuid import uuid4

from PySide6.QtCore import QObject, Property, Signal, Slot

from app.formatting import date_text, datetime_text, grams, money, number
from services import supplier_rules as rules

SHOP_OWES_TEXT, SUPPLIER_OWES_TEXT, SETTLED_TEXT = "علينا للمورد", "لنا عند المورد", "الحساب متوازن"
ACCOUNT_TYPE_TEXT = {"BANK": "حساب بنكي", "MOBILE_WALLET": "محفظة"}
PAYMENT_METHOD_TEXT = {"CASH": "نقدًا من", "BANK": "تحويل من", "CUSTODY": "من عهدة السوق"}
TONES = {rules.SHOP_OWES: "shopOwes", rules.SUPPLIER_OWES: "supplierOwes", rules.SETTLED: "settled"}


def gold_position_text(direction, amount, karat=None):
    """'علينا للمورد 40.050 جرام' / 'لنا عند المورد 9.950 جرام عيار 21' (the magnitude only; the words give the sign)."""
    if direction == rules.SETTLED:
        return SETTLED_TEXT
    lead = SHOP_OWES_TEXT if direction == rules.SHOP_OWES else SUPPLIER_OWES_TEXT
    suffix = f" عيار {karat}" if karat else ""
    return f"{lead} {grams(abs(Decimal(str(amount))))} جرام{suffix}"


def making_position_text(direction, amount):
    """'علينا للمورد 6,500.00 ج مصنعيات' / 'لنا عند المورد 1,500.00 ج مصنعيات'."""
    if direction == rules.SETTLED:
        return SETTLED_TEXT
    lead = SHOP_OWES_TEXT if direction == rules.SHOP_OWES else SUPPLIER_OWES_TEXT
    return f"{lead} {money(abs(Decimal(str(amount))))} مصنعيات"


def position_parts(direction, amount, kind, karat=None):
    """The same sentence as gold_position_text / making_position_text, split so the view can emphasise the figure:
    {lead: direction words, number: the magnitude (money keeps its 'ج'), tail: unit / basis}. Joined with spaces the
    parts equal the full text exactly; a settled position is only a lead."""
    if direction == rules.SETTLED:
        return {"lead": SETTLED_TEXT, "number": "", "tail": ""}
    lead = SHOP_OWES_TEXT if direction == rules.SHOP_OWES else SUPPLIER_OWES_TEXT
    value = abs(Decimal(str(amount)))
    if kind == "making":
        return {"lead": lead, "number": money(value), "tail": "مصنعيات"}
    return {"lead": lead, "number": grams(value), "tail": f"جرام عيار {karat}" if karat else "جرام"}


def karat_note(supplier):
    """Why a supplier's gold account is not a single balance (an unresolved karat), or ''."""
    if supplier["karat_id"] is not None:
        return ""
    values = supplier["history_karats"]
    if len(values) > 1:
        return (f"عيار هذا المورد غير محدد وتاريخه يحتوي أعيرة مختلفة ({'، '.join(str(v) for v in values)}). "
                "تُعرض أرصدته لكل عيار على حدة دون جمعها، ولا يمكن إنشاء فواتير جديدة له.")
    if len(values) == 1:
        return f"عيار هذا المورد غير محدد. يمكن تحديده (عيار {values[0]}) من تعديل المورد قبل إنشاء فواتير جديدة."
    return "عيار هذا المورد غير محدد. حدّده من تعديل المورد قبل إنشاء فواتير جديدة."


GOLD_SOURCE_TEXT = {"LOGO": "لوجو", "MELT": "للسبك"}      # the custody designation of a custody-sourced gold settlement (046)


def movement_side_text(delta):
    delta = Decimal(str(delta))
    return SHOP_OWES_TEXT if delta > 0 else SUPPLIER_OWES_TEXT


def making_row(r):
    """One making-ledger row for the view. A payment row names its method and account and, once reversed, the RV that
    reversed it; a reversal row names the payment it restores. The original payment row is never altered or hidden."""
    is_payment = r.get("movement_code") == "PAYMENT" and bool(r.get("payment_id"))
    reversed_by = r.get("reversed_by") if is_payment else None
    detail = f"{PAYMENT_METHOD_TEXT[r['payment_method']]} {r['payment_account'] or ''}".strip() if is_payment else ""
    if is_payment and reversed_by:
        state = f"معكوسة بالعملية {reversed_by}"
    elif r.get("movement_code") == "PAYMENT_REVERSAL" and r.get("reverses_reference"):
        state = f"عكس الدفعة {r['reverses_reference']}"
    else:
        state = ""
    return {"id": r["id"], "time": datetime_text(r["occurred_at"]), "movement": r["movement"],
            "side": movement_side_text(r["amount_delta"]), "amount": money(abs(r["amount_delta"])),
            "balance": making_position_text(rules.direction(r["balance_after"]), r["balance_after"]),
            "reference": r["reference"], "sourceId": r["source_id"], "actor": r["actor"],
            "tone": TONES[rules.direction(r["amount_delta"])],
            "paymentId": r.get("payment_id") or "", "detail": detail, "state": state,
            "reversed": bool(reversed_by),
            "canReverse": is_payment and not reversed_by and r.get("payment_method") != "CUSTODY"}


class SupplierViewModel(QObject):
    changed = Signal()
    supplierSaved = Signal(str)
    selectionChanged = Signal(str)       # the selected supplier id: whoever selects (click, save), dependents follow
    paymentPosted = Signal(str)          # a making payment was posted (its code): the payment dialog can close
    reversalDone = Signal(str)           # a making payment was reversed (the RV code): the reversal dialog can close
    settlementPosted = Signal(str)       # a gold settlement was posted (its SG code): the dialog can close and the Scrap views reload
    settlementReversed = Signal(str)     # a gold settlement was reversed (the RV code): the dialog can close and the Scrap views reload

    def __init__(self, session, service=None, parent=None, payments=None):
        super().__init__(parent)
        self._session, self._service, self._payments = session, service, payments
        self._pay_context = {}
        self._settlements, self._set_context = None, {}
        self._workers, self._request, self._latest, self._busy = set(), 0, {}, set()
        self._shutdown = False
        self._suppliers, self._detail = [], {}
        self._selected, self._search, self._active_only = "", "", False
        self._role = ""                                  # the list filter: "" (all) / SUPPLIER / INTERMEDIARY
        self._error, self._message, self._done_text = "", "", ""

    # ------------------------------------------------------------ plumbing
    def _service_instance(self):
        if self._service is None:
            from services.supplier_service import SupplierService
            self._service = SupplierService(self._session)
        return self._service

    def _payment_service(self):
        if self._payments is None:
            from services.supplier_payment_service import SupplierPaymentService
            self._payments = SupplierPaymentService(self._session)
        return self._payments

    def _settlement_service(self):
        if self._settlements is None:
            from services.supplier_gold_settlement_service import SupplierGoldSettlementService
            self._settlements = SupplierGoldSettlementService(self._session)
        return self._settlements

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

    # ------------------------------------------------------------ reads
    @Slot()
    def refresh(self):
        if not self.canView:
            return
        service, search, active, role = self._service_instance(), self._search, self._active_only, self._role
        extra = {"role": role} if role else {}           # the role filter is passed only when chosen
        self._start("list", lambda _expected_actor: service.list_suppliers(search, active, _expected_actor=_expected_actor, **extra))

    def _on_list(self, result, error):
        if error:
            self._error = error
            return
        self._suppliers, self._error = result, ""
        if self._selected and not any(s["id"] == self._selected for s in result):
            self._selected, self._detail = "", {}
        if self._selected:
            self._load_detail()

    @Slot(str)
    def search(self, text):
        self._search = str(text or "").strip()
        self.refresh()

    @Slot(bool)
    def setActiveOnly(self, value):
        self._active_only = bool(value)
        self.refresh()

    @Slot(str)
    def setRoleFilter(self, role):
        """All parties, suppliers (alone or also intermediaries) or market intermediaries (idem)."""
        role = str(role or "").strip().upper()
        self._role = role if role in ("SUPPLIER", "INTERMEDIARY") else ""
        self.refresh()

    @Slot(str)
    def selectSupplier(self, supplier_id):
        self._selected = str(supplier_id or "")
        self._detail = {}
        self.changed.emit()
        if self._selected:
            self._load_detail()
        self.selectionChanged.emit(self._selected)

    def _load_detail(self):
        service, supplier_id = self._service_instance(), self._selected
        self._start("detail", lambda _expected_actor: service.supplier_detail(supplier_id,
                                                                            _expected_actor=_expected_actor))

    def _on_detail(self, result, error):
        if error:
            self._error = error
            return
        if result["supplier"]["id"] == self._selected:
            self._detail, self._error = result, ""

    @Slot()
    def clearMessage(self):
        if self._message or self._error:
            self._message = self._error = ""
            self.changed.emit()

    # ------------------------------------------------------------ master writes (each re-checked by the service)
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
        saved = str(result) if isinstance(result, str) else self._selected
        self.refresh()
        self.supplierSaved.emit(saved)

    @Slot(str, str, str, int)
    def createSupplier(self, name, phone, notes, karat_id):
        self._write("تم إضافة المورد.", lambda s, a: s.create_supplier(name, phone, notes, karat_id, _expected_actor=a))

    @Slot(str, str, str, str, int)
    def updateSupplier(self, supplier_id, name, phone, notes, karat_id):
        self._write("تم حفظ بيانات المورد.",
                    lambda s, a: s.update_supplier(supplier_id, name, phone, notes, karat_id, _expected_actor=a) or supplier_id)

    @Slot(str, str, str, int, bool, bool)
    def createParty(self, name, phone, notes, karat_id, is_supplier, is_intermediary):
        """Create a party with its roles (a supplier needs its karat; an intermediary-only party has none)."""
        self._write("تم إضافة الطرف.", lambda s, a: s.create_supplier(name, phone, notes, karat_id, is_supplier=is_supplier,
                                                                      is_intermediary=is_intermediary, _expected_actor=a))

    @Slot(str, str, str, str, int, bool, bool)
    def updateParty(self, supplier_id, name, phone, notes, karat_id, is_supplier, is_intermediary):
        self._write("تم حفظ بيانات الطرف.",
                    lambda s, a: s.update_supplier(supplier_id, name, phone, notes, karat_id, is_supplier=is_supplier,
                                                   is_intermediary=is_intermediary, _expected_actor=a) or supplier_id)

    @Slot(str, bool)
    def setSupplierActive(self, supplier_id, active):
        self._write("تم تفعيل المورد." if active else "تم إيقاف المورد.",
                    lambda s, a: s.set_active(supplier_id, active, _expected_actor=a) or supplier_id)

    @Slot(str)
    def deleteSupplier(self, supplier_id):
        def call(service, expected):
            service.delete_unused_supplier(supplier_id, _expected_actor=expected)
            return ""
        self._selected = "" if self._selected == supplier_id else self._selected
        self._write("تم حذف المورد.", call)

    # ------------------------------------------------------------ making payment (دفع مصنعيات) and its reversal
    @Slot(result=str)
    def newRequestKey(self):
        """One key per opened payment dialog: a double click re-sends the SAME key, so it stays idempotent."""
        return str(uuid4())

    @Slot(str)
    def loadPaymentContext(self, supplier_id):
        """The CURRENT making position, MAIN_SAFE and the active bank accounts for the payment dialog."""
        self._pay_context = {}
        self._error = self._message = ""
        service = self._payment_service()
        self._start("paycontext", lambda _expected_actor: service.payment_context(supplier_id,
                                                                                _expected_actor=_expected_actor))

    def _on_paycontext(self, result, error):
        if error:
            self._error = error
            return
        self._pay_context = result

    @Slot(str, result="QVariantMap")
    def previewPayment(self, amount):
        """The making position after paying `amount` (exact; may cross zero into supplier credit). Uses the position the
        dialog loaded; the figures come from supplier_rules, never from QML arithmetic."""
        context = self._pay_context
        text = str(amount or "").strip()
        empty = {"valid": False, "error": "", "afterText": "", "afterParts": {}, "afterTone": "settled", "note": ""}
        if not context or not text:
            return empty
        try:
            value = rules.parse_amount(text)
        except rules.SupplierRuleError as exc:
            return {**empty, "error": str(exc)}
        after = rules.making_after_payment(context["making"], value)
        direction = rules.direction(after)
        return {"valid": True, "error": "", "afterText": making_position_text(direction, after),
                "afterParts": position_parts(direction, after, "making"), "afterTone": TONES[direction],
                "note": ("المبلغ أكبر من المستحق: يصبح رصيد المصنعيات «لنا عند المورد» (رصيد دائن للمحل)."
                         if after < 0 else "")}

    @Slot(str, str, str, str, str, str, str, str)
    def payMaking(self, supplier_id, method, bank_account_id, amount, payment_date, reference, notes, request_key):
        if "pay" in self._busy:
            return                                                     # a double click never submits twice
        self._message = self._error = ""
        service = self._payment_service()
        self._start("pay", lambda _expected_actor: service.pay(
            supplier_id, method, bank_account_id, amount, payment_date, reference, notes, request_key,
            _expected_actor=_expected_actor))
        self.changed.emit()

    def _on_pay(self, result, error):
        if error:
            self._error = error                                        # the dialog stays open with every field kept
            return
        self._message = (f"دفعة المصنعيات {result['code']} مسجلة من قبل (لم تُسجل مرة ثانية)." if result.get("duplicate")
                         else f"تم تسجيل دفعة المصنعيات {result['code']}.")
        self.refresh()
        self.paymentPosted.emit(result["code"])

    @Slot(str, str)
    def reversePayment(self, payment_id, reason):
        if "reverse" in self._busy:
            return
        self._message = self._error = ""
        service = self._payment_service()
        self._start("reverse", lambda _expected_actor: service.reverse(payment_id, reason, _expected_actor=_expected_actor))
        self.changed.emit()

    def _on_reverse(self, result, error):
        if error:
            self._error = error
            return
        self._message = f"تم عكس دفعة المصنعيات {result['source_reference']} ({result['code']})."
        self.refresh()
        self.reversalDone.emit(result["code"])

    # ------------------------------------------------------------ gold settlement (تسوية ذهب) and its reversal (045)
    @Slot(str)
    def loadSettlementContext(self, supplier_id):
        """The recipient and the SHOP Scrap available per karat for the settlement dialog."""
        self._set_context = {}
        self._error = self._message = ""
        service = self._settlement_service()
        self._start("setctx", lambda _expected_actor: service.settlement_context(supplier_id, _expected_actor=_expected_actor))

    def _on_setctx(self, result, error):
        if error:
            self._error = error
            return
        self._set_context = result

    @Slot(str, str, result="QVariantMap")
    def previewSettlement(self, karat_id, weight):
        """What the settlement would credit, before submitting: the figures come from supplier_rules (the same function the service uses), never
        from QML arithmetic. The service recomputes and re-checks the available Scrap when it posts."""
        context = self._set_context
        empty = {"valid": False, "error": "", "deliveredText": "", "creditedText": "", "analyticalText": "", "differenceText": "",
                 "formulaText": "", "availableText": "", "short": False}
        if not context or not str(weight or "").strip() or not str(karat_id or "").strip():
            return empty
        supplier = context["supplier"]
        if supplier["karat_value"] is None:
            return {**empty, "error": "عيار هذا المورد غير محدد: حدّده من تعديل المورد أولًا."}
        try:
            grams_in = rules.parse_delivered_weight(weight)
            row = next(k for k in context["scrap"] if str(k["karat_id"]) == str(karat_id))
            credited, analytical, difference = rules.settlement_credit(grams_in, row["karat_value"], supplier["karat_value"])
        except StopIteration:
            return {**empty, "error": "اختر عيار الذهب المُسلَّم."}
        except rules.SupplierRuleError as exc:
            return {**empty, "error": str(exc)}
        short = grams_in > row["available"]
        return {"valid": not short, "error": "", "short": short,
                "deliveredText": f"{grams(grams_in)} جرام عيار {row['karat_value']}",
                "creditedText": f"{grams(credited)} جرام عيار {supplier['karat_value']}",
                "analyticalText": number(analytical, 6), "differenceText": number(difference, 6, signed=True),
                "formulaText": f"{grams(grams_in)} × {row['karat_value']} ÷ {supplier['karat_value']}",
                "availableText": f"{grams(row['available'])} جرام عيار {row['karat_value']}"}

    @Slot(str, str, str, str, str, str, str)
    def postSettlement(self, supplier_id, karat_id, weight, settlement_date, reference, notes, request_key):
        if "settle" in self._busy:
            return                                                     # a double click never submits twice
        self._message = self._error = ""
        service = self._settlement_service()
        self._start("settle", lambda _expected_actor: service.settle(
            supplier_id, karat_id, weight, settlement_date, reference, notes, request_key, _expected_actor=_expected_actor))
        self.changed.emit()

    def _on_settle(self, result, error):
        if error:
            self._error = error                                        # the dialog stays open with every field kept
            return
        self._message = (f"تسوية الذهب {result['code']} مسجلة من قبل (لم تُسجل مرة ثانية)." if result.get("duplicate")
                         else f"تم تسجيل تسوية الذهب {result['code']}: سُلِّم {grams(Decimal(result['delivered_weight']))} جرام عيار "
                              f"{result['delivered_karat']} واحتُسب للمورد {grams(Decimal(result['credited_weight']))} جرام عيار {result['target_karat']}.")
        self.refresh()
        self.settlementPosted.emit(result["code"])

    @Slot(str, str)
    def reverseSettlement(self, settlement_id, reason):
        if "setrev" in self._busy:
            return
        self._message = self._error = ""
        service = self._settlement_service()
        self._start("setrev", lambda _expected_actor: service.reverse(settlement_id, reason, _expected_actor=_expected_actor))
        self.changed.emit()

    def _on_setrev(self, result, error):
        if error:
            self._error = error
            return
        self._message = f"تم عكس تسوية الذهب {result['source_reference']} ({result['code']}): عاد الذهب المُسلَّم فعليًا إلى كسر المحل وعاد رصيد المورد."
        self.refresh()
        self.settlementReversed.emit(result["code"])

    # ------------------------------------------------------------ properties
    @Property(bool, notify=changed)
    def canSettleGold(self):
        actor = self._session.current_actor()
        return bool(actor and "SUPPLIER_GOLD_SETTLE" in actor.permissions)

    @Property(bool, notify=changed)
    def canReverseSettlement(self):
        actor = self._session.current_actor()
        return bool(actor and "SUPPLIER_GOLD_SETTLE_REVERSE" in actor.permissions)

    @Property(bool, notify=changed)
    def settling(self):
        return "settle" in self._busy

    @Property(bool, notify=changed)
    def settlementReversing(self):
        return "setrev" in self._busy

    @Property("QVariantMap", notify=changed)
    def settlementContext(self):
        """The settlement dialog's data (empty while it loads): recipient, its karat and the available Scrap per karat."""
        context = self._set_context
        if not context:
            return {}
        supplier = context["supplier"]
        return {"supplierId": supplier["id"], "supplierName": supplier["name"], "supplierCode": supplier["code"], "supplierActive": supplier["is_active"],
                "isSupplier": supplier["is_supplier"], "karatResolved": supplier["karat_value"] is not None,
                "karatText": f"عيار {supplier['karat_value']}" if supplier["karat_value"] else "العيار غير محدد",
                "scrap": [{"karatId": str(k["karat_id"]), "text": f"عيار {k['karat_value']} — المتاح {grams(k['available'])} جرام"
                           + (f" • محجوز لمراجعة المدير {grams(k['held'])}" if k["held"] > 0 else "")} for k in context["scrap"]]}

    @Property(bool, notify=changed)
    def canPayMaking(self):
        actor = self._session.current_actor()
        return bool(actor and "SUPPLIER_MAKING_PAY" in actor.permissions)

    @Property(bool, notify=changed)
    def canReversePayment(self):
        actor = self._session.current_actor()
        return bool(actor and "SUPPLIER_MAKING_PAY_REVERSE" in actor.permissions)

    @Property(bool, notify=changed)
    def paying(self):
        return "pay" in self._busy

    @Property(bool, notify=changed)
    def reversing(self):
        return "reverse" in self._busy

    @Property("QVariantMap", notify=changed)
    def payContext(self):
        """The payment dialog's data (empty while it loads): supplier, current making position, Main Safe, bank accounts."""
        context = self._pay_context
        if not context:
            return {}
        direction = context["making_direction"]
        safe = context["safe"]
        return {"supplierId": context["supplier"]["id"], "supplierName": context["supplier"]["name"],
                "supplierCode": context["supplier"]["code"], "supplierActive": context["supplier"]["active"],
                "currentParts": position_parts(direction, context["making"], "making"),
                "currentText": making_position_text(direction, context["making"]),
                "currentTone": TONES[direction],
                "safeName": "" if safe is None else safe["name"], "safeReady": bool(safe and safe["active"]),
                "banks": [{"id": b["id"],
                           "text": f"{b['name']} — {ACCOUNT_TYPE_TEXT.get(b['account_type'], b['account_type'])}"
                                   + (f" ({b['label']})" if b["label"] else "")} for b in context["banks"]]}

    @Property(bool, notify=changed)
    def canView(self):
        actor = self._session.current_actor()
        return bool(actor and "SUPPLIER_VIEW" in actor.permissions)

    @Property(bool, notify=changed)
    def canManage(self):
        actor = self._session.current_actor()
        return bool(actor and "SUPPLIER_MANAGE" in actor.permissions)

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
    def selectedSupplierId(self):
        return self._selected

    @Property("QVariantList", notify=changed)
    def suppliers(self):
        return [{"id": s["id"], "code": s["code"], "name": s["name"], "phone": s["phone"], "notes": s["notes"],
                 "active": s["is_active"], "stateText": "نشط" if s["is_active"] else "موقوف",
                 "isSupplier": s.get("is_supplier", True), "isIntermediary": s.get("is_intermediary", False),
                 "roleText": s.get("role_text", "مورد"),
                 "karatId": s["karat_id"] or 0, "karatText": f"عيار {s['karat_value']}" if s["karat_value"] else "العيار غير محدد",
                 "goldText": ("" if s["gold_status"] == "NOT_APPLICABLE"
                              else gold_position_text(s["gold_direction"], s["gold_balance"], s["karat_value"])
                              if s["gold_status"] == "RESOLVED" else "العيار غير محدد"),
                 "goldTone": TONES.get(s["gold_direction"], "settled"),
                 "makingText": "" if s["gold_status"] == "NOT_APPLICABLE" else making_position_text(s["making_direction"], s["making"]),
                 "makingTone": TONES[s["making_direction"]],
                 "invoiceCount": s["posted_invoice_count"], "selected": s["id"] == self._selected}
                for s in self._suppliers]

    @Property("QVariantMap", notify=changed)
    def detail(self):
        """The selected supplier: identity, the two independent positions and both ledgers (display texts)."""
        if not self._detail:
            return {}
        supplier, balances = self._detail["supplier"], self._detail["balances"]
        resolved = balances["gold_status"] == "RESOLVED"
        karat = balances["gold_karat_value"]
        applicable = balances["gold_status"] != "NOT_APPLICABLE"         # an intermediary-only party has no supplier account
        gold_lines = [{"karat": k["karat_value"],
                       "text": gold_position_text(k["direction"], k["weight_balance"], k["karat_value"]),
                       "tone": TONES[k["direction"]]} for k in balances["gold_by_karat"]]
        direction = balances["gold_direction"]
        return {
            "id": supplier["id"], "code": supplier["code"], "name": supplier["name"], "phone": supplier["phone"],
            "notes": supplier["notes"], "active": supplier["is_active"],
            "isSupplier": supplier.get("is_supplier", True), "isIntermediary": supplier.get("is_intermediary", False),
            "roleText": supplier.get("role_text", "مورد"),
            # the supplier's dedicated karat (invoices inherit it) and whether it can still be changed
            "karatId": supplier["karat_id"] or 0, "karatValue": supplier["karat_value"] or 0,
            "karatText": f"عيار {supplier['karat_value']}" if supplier["karat_value"] else "العيار غير محدد",
            "karatResolved": supplier["karat_id"] is not None, "karatLocked": supplier["karat_locked"],
            "hasAccount": applicable,
            "goldResolved": resolved, "goldNote": karat_note(supplier) if applicable else "",
            "goldText": gold_position_text(direction, balances["gold_balance"], karat) if resolved else "العيار غير محدد",
            "goldTone": TONES.get(direction, "settled"),
            "goldParts": (position_parts(direction, balances["gold_balance"], "gold", karat) if resolved
                          else {"lead": "العيار غير محدد", "number": "", "tail": ""}),
            "makingParts": position_parts(balances["making_direction"], balances["making"], "making"),
            "goldLines": gold_lines,
            # read-only per-karat history, only for a supplier whose karat is unresolved (never added into one balance)
            "goldBreakdown": [] if resolved else gold_lines,
            "makingText": making_position_text(balances["making_direction"], balances["making"]),
            "makingTone": TONES[balances["making_direction"]],
            "goldLedger": [{
                "id": r["id"], "time": datetime_text(r["occurred_at"]), "movement": r["movement"],
                "karat": r["karat_value"], "side": movement_side_text(r["weight_delta"]),
                "weight": grams(abs(r["weight_delta"])),
                "balance": gold_position_text(rules.direction(r["weight_balance_after"].quantize(rules.WEIGHT_STEP)),
                                              r["weight_balance_after"].quantize(rules.WEIGHT_STEP), r["karat_value"]),
                "reference": r["reference"], "sourceId": r["source_id"], "actor": r["actor"],
                "tone": TONES[rules.direction(r["weight_delta"])],
                # a gold settlement shows what was delivered beside what was credited; a reversal row names the settlement it restores
                # (046) a settlement delivered from market custody names its LOGO source and its custody entry, where its reversal belongs
                "detail": (((f"سُلِّم {grams(r['delivered_weight'])} جرام • {'رواسب' if r.get('lot_kind') == 'RESIDUE' else 'الناتج الرئيسي'} • {r.get('lot_origin')} • شحنة {r.get('lot_no')} • العيار الفعلي {r.get('lot_fineness')} بالألف" if r.get("lot_id") else f"سُلِّم {grams(r['delivered_weight'])} جرام عيار {r['delivered_karat']}")
                            + (f" ({GOLD_SOURCE_TEXT[r['custody_designation']]})" if r.get("custody_designation") in GOLD_SOURCE_TEXT else "")
                            + f" ← احتُسب {grams(abs(r['weight_delta']))} جرام عيار {r['karat_value']}"
                            + (f" • من عهدة السوق {r['custody_reference']} (تُعكس من صفحة العهدة)" if r.get("custody_reference") else ""))
                           if r.get("settlement_id") else ""),
                "custodyReference": r.get("custody_reference") or "",
                "state": (f"معكوسة بالعملية {r['reversed_by']}" if r.get("reversed_by")
                          else f"عكس التسوية {r['reverses_reference']}" if r.get("reverses_reference") else ""),
                "settlementId": r.get("settlement_id") or "", "reversed": bool(r.get("reversed_by")),
                "canReverse": bool(r.get("settlement_id")) and not r.get("reversed_by") and not r.get("custody_reference")}
                           for r in self._detail["gold_ledger"]],
            "makingLedger": [making_row(r) for r in self._detail["making_ledger"]],
        }
