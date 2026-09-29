"""تصحيح / عكس (Reversal / Correction V1): the manager workspace opened from an invoice in سجل الفواتير.

The manager picks the transaction (the invoice, one of its Exchanges or Returns) and ONE mode:
«عكس العملية بالكامل» or «تصحيح طريقة التسوية». Every figure comes from CorrectionService
(preview_reversal / current_settlement / preview_settlement) and is formatted here; nothing is
calculated in QML. The reason is mandatory and a confirmation always precedes execution."""
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from app.formatting import money, number

METHODS = [{"code": "PHYSICAL_CASH", "text": "نقدي"}, {"code": "MOBILE_WALLET", "text": "محفظة"},
           {"code": "INSTAPAY", "text": "InstaPay"}, {"code": "BANK_TRANSFER", "text": "تحويل بنكي"}]
ACCOUNT_TYPE = {"INSTAPAY": "BANK", "BANK_TRANSFER": "BANK", "MOBILE_WALLET": "MOBILE_WALLET"}
STATUS_TEXT = {"IN_STOCK": "المخزون", "SOLD": "مباعة", "RETURNED_PENDING": "بانتظار الفحص"}
REVERSAL, SETTLEMENT = "REVERSAL", "SETTLEMENT"


def _decimal(text):
    try:
        return Decimal(str(text).strip()) if str(text or "").strip() else Decimal("0.00")
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


class CorrectionViewModel(QObject):
    changed = Signal()
    legsChanged = Signal()
    finalized = Signal(str)            # the original invoice id (its History detail must reload)

    def __init__(self, session, service=None, parent=None, debounce_ms=250):
        super().__init__(parent)
        self._session, self._service = session, service
        self._workers, self._request, self._latest, self._busy = set(), 0, {}, set()
        self._shutdown = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self._run_settlement_preview)
        self._reset()

    def _reset(self):
        self._open, self._targets, self._target = False, {}, None
        self._mode, self._reversal, self._settlement, self._settle_preview = "", {}, {}, {}
        self._legs, self._seq, self._account, self._reason = [], 0, "", ""
        self._error, self._result, self._confirming = "", {}, False
        self.legsChanged.emit()

    # ------------------------------------------------------------ plumbing
    def _service_instance(self):
        if self._service is None:
            from services.correction_service import CorrectionService
            self._service = CorrectionService(self._session)
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
        self._timer.stop()
        for worker in list(self._workers):
            worker.wait(5000)

    # ------------------------------------------------------------ open / target / mode
    @Slot(str)
    def open(self, invoice_id):
        if not invoice_id:
            return
        self._timer.stop()
        self._reset()
        self._open = True
        service = self._service_instance()
        self._start("targets", lambda _expected_actor: service.targets(invoice_id, _expected_actor=_expected_actor))

    def _on_targets(self, result, error):
        if error:
            self._error = error
            return
        self._targets = result
        if result["targets"]:
            self.selectTarget(0)

    @Slot(str)
    def openCustomerSettlement(self, settlement_id):
        if not settlement_id or self.saving:
            return
        self._timer.stop()
        self._reset()
        self._open = True
        service = self._service_instance()
        self._start("targets", lambda _expected_actor: service.settlement_target(
            settlement_id, _expected_actor=_expected_actor))

    @Slot()
    def close(self):
        if "execute" in self._busy:
            return
        self._timer.stop()
        self._reset()
        self.changed.emit()

    @Slot(int)
    def selectTarget(self, index):
        targets = self._targets.get("targets", [])
        if not 0 <= index < len(targets) or self._result:
            return
        self._target = targets[index]
        self._mode, self._reversal, self._settlement, self._settle_preview = "", {}, {}, {}
        self._legs, self._account, self._error, self._confirming = [], "", "", False
        self.legsChanged.emit()
        self.changed.emit()

    @Slot(str)
    def setMode(self, mode):
        """«عكس العملية بالكامل» (REVERSAL) or «تصحيح طريقة التسوية» (SETTLEMENT): an explicit choice."""
        if mode not in (REVERSAL, SETTLEMENT) or not self._target or self._result:
            return
        self._mode, self._error, self._confirming = mode, "", False
        service, t = self._service_instance(), self._target
        if mode == REVERSAL:
            self._start("reversal", lambda _expected_actor: service.preview_reversal(
                t["source_type"], t["source_id"], _expected_actor=_expected_actor))
        else:
            self._start("settlement", lambda _expected_actor: service.current_settlement(
                t["source_type"], t["source_id"], _expected_actor=_expected_actor))
        self.changed.emit()

    def _on_reversal(self, result, error):
        self._reversal, self._error = ({}, error) if error else (result, "")

    def _on_settlement(self, result, error):
        if error:
            self._settlement, self._error = {}, error
            return
        self._settlement, self._error = result, ""
        # Start from the current effective allocation; the manager changes what was wrong.
        self._legs = []
        for leg in result["legs"]:
            self._add_leg(leg["method_code"], leg["bank_account_id"], leg["amount"])
        self._account = result["account"] if Decimal(result["account"]) else ""
        self.legsChanged.emit()
        self._timer.start()

    # ------------------------------------------------------------ settlement legs
    def _add_leg(self, method="PHYSICAL_CASH", bank="", amount=""):
        self._seq += 1
        self._legs.append({"key": f"c{self._seq}", "method": method, "bank": bank or "", "amount": amount})

    @Slot()
    def addLeg(self):
        if self._mode == SETTLEMENT and len(self._legs) < 6 and not self._result:
            self._add_leg()
            self.legsChanged.emit()
            self._changed()

    @Slot(str)
    def removeLeg(self, key):
        self._legs = [l for l in self._legs if l["key"] != key]
        self.legsChanged.emit()
        self._changed()

    def _leg(self, key):
        return next((l for l in self._legs if l["key"] == key), None)

    @Slot(str, str)
    def setLegMethod(self, key, code):
        leg = self._leg(key)
        if leg and code in {m["code"] for m in METHODS} and leg["method"] != code:
            leg["method"], leg["bank"] = code, ""
            self.legsChanged.emit()
            self._changed()

    @Slot(str, str)
    def setLegAccount(self, key, account_id):
        leg = self._leg(key)
        if leg:
            leg["bank"] = account_id or ""
            self.legsChanged.emit()
            self._changed()

    @Slot(str, str)
    def setLegAmount(self, key, text):
        leg = self._leg(key)
        if leg and leg["amount"] != (text or "").strip():
            leg["amount"] = (text or "").strip()
            self._changed()

    @Slot(str)
    def setAccountAmount(self, text):
        self._account = (text or "").strip()
        self._changed()

    @Slot(str)
    def setReason(self, text):
        self._reason = text or ""
        self.changed.emit()

    def _changed(self):
        self._error, self._confirming = "", False
        self.changed.emit()
        if self._mode == SETTLEMENT:
            self._timer.start()

    def _settlement_legs(self):
        from services.correction_service import SettlementLeg
        return [SettlementLeg(l["method"], l["amount"], l["bank"] or None) for l in self._legs
                if l["amount"] not in ("", "0", "0.00")]

    def _run_settlement_preview(self):
        if self._mode != SETTLEMENT or not self._target:
            return
        service, t, legs, account = self._service_instance(), self._target, self._settlement_legs(), self._account or None
        self._start("settle_preview", lambda _expected_actor: service.preview_settlement(
            t["source_type"], t["source_id"], legs, account, _expected_actor=_expected_actor))

    def _on_settle_preview(self, result, error):
        self._settle_preview, self._error = ({}, error) if error else (result, "")

    # ------------------------------------------------------------ confirm / execute
    @Slot()
    def requestConfirm(self):
        if self.canExecute:
            self._confirming = True
            self.changed.emit()

    @Slot()
    def cancelConfirm(self):
        self._confirming = False
        self.changed.emit()

    @Slot()
    def execute(self):
        if not (self._confirming and self.canExecute):
            return
        service, t, reason = self._service_instance(), self._target, self._reason
        self._confirming = False
        if self._mode == REVERSAL:
            self._start("execute", lambda _expected_actor: service.reverse(
                t["source_type"], t["source_id"], reason, _expected_actor=_expected_actor))
        else:
            legs, account = self._settlement_legs(), self._account or None
            sequence = self._settlement.get("next_sequence")
            self._start("execute", lambda _expected_actor: service.correct_settlement(
                t["source_type"], t["source_id"], legs, account, reason, expected_sequence=sequence,
                _expected_actor=_expected_actor))

    def _on_execute(self, result, error):
        if error:
            self._error = error
            return
        self._result = result
        self.finalized.emit(self._targets["invoice_id"])

    # ------------------------------------------------------------ properties
    @Property(bool, notify=changed)
    def isOpen(self):
        return self._open

    @Property(bool, notify=changed)
    def busy(self):
        return bool(self._busy - {"settle_preview"})

    @Property(bool, notify=changed)
    def saving(self):
        return "execute" in self._busy

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property(bool, notify=changed)
    def done(self):
        return bool(self._result)

    @Property(str, notify=changed)
    def doneMessage(self):
        r = self._result
        if not r:
            return ""
        if r["code"].startswith("RV"):
            return f"تم إنشاء الحركة العكسية {r['code']} للعملية {r['source_reference']}؛ العملية الأصلية محفوظة كما هي."
        return f"تم تصحيح التسوية {r['code']} للعملية {r['source_reference']}."

    @Property("QVariantMap", notify=changed)
    def header(self):
        t = self._targets
        if not t:
            return {}
        return {"invoiceId": t["invoice_id"], "number": t["invoice_number"], "customer": t.get("customer_name") or "بدون عميل"}

    @Property("QVariantList", notify=changed)
    def targets(self):
        return [{"index": i, "reference": t["reference"], "text": f"{t['source_text']} {t['reference']}",
                 "state": (f"معكوسة {t['reversed_by']}" if t["reversed_by"] else
                           f"صُححت تسويتها {t['settlement_corrections']} مرة" if t["settlement_corrections"] else ""),
                 "selected": bool(self._target) and t["reference"] == self._target["reference"]}
                for i, t in enumerate(self._targets.get("targets", []))]

    @Property(str, notify=changed)
    def mode(self):
        return self._mode

    @Property("QVariantMap", notify=changed)
    def reversal(self):
        p = self._reversal
        if not p:
            return {}
        money_lines = [f"{m['account']}: {number(m['delta'], 2, signed=True)} ج" for m in p["money"]]
        account_lines = [f"حساب العميل: {number(a['delta'], 2, signed=True)} ج" for a in p["account"]]
        stock = [f"قطعة {i['inventory_item_id']}: {STATUS_TEXT.get(i['from'], i['from'])} إلى "
                 f"{STATUS_TEXT.get(i['to'], i['to'])}" for i in p["inventory"]]
        gold = [f"الكسر عيار {g['karat']}: {number(g['grams'], 3, signed=True)} جرام" for g in p["old_gold"]]
        extra = ([f"إغلاق {p['reviews']} فحص قطعة مرتجعة مفتوح"] if p["reviews"] else []) + \
                ([f"إلغاء {p['cases']} حالة بيع يدوي مفتوحة"] if p["cases"] else [])
        return {"title": f"{p['source_text']} {p['reference']}", "customer": p.get("customer_name") or "بدون عميل",
                "date": p["original_date"][:16], "total": money(p["original_total"]),
                "money": money_lines or ["لا توجد حركة مالية"], "account": account_lines, "stock": stock, "gold": gold,
                "extra": extra, "blockers": [b["message"] for b in p["blockers"]], "canReverse": p["can_reverse"]}

    @Property("QVariantMap", notify=changed)
    def settlement(self):
        s, p = self._settlement, self._settle_preview
        if not s:
            return {}
        current = " + ".join(f"{l['method_text']}{' — ' + l['account_name'] if l['account_name'] else ''} {money(l['amount'])}"
                             for l in s["legs"]) or "—"
        if Decimal(s["account"]):
            current += f" + حساب العميل {money(s['account'])}"
        entered = sum((_decimal(l["amount"]) for l in self._legs), Decimal("0.00")) + _decimal(self._account)
        remaining = Decimal(s["total"]) - entered
        return {"directionText": s["direction_text"], "total": money(s["total"]), "current": current,
                "hasCustomer": s["has_customer"],
                "remainingText": "مطابق للإجمالي" if remaining == 0 else
                (f"المتبقي {money(remaining)}" if remaining > 0 else f"أكثر من الإجمالي بـ {money(-remaining)}"),
                "complete": remaining == 0,
                "deltas": [f"{d['account']}: {number(d['delta'], 2, signed=True)} ج" for d in p.get("money_deltas", [])]
                          + ([f"حساب العميل: {number(p['account_delta'], 2, signed=True)} ج"]
                             if p and Decimal(p.get("account_delta") or "0") else []),
                "problems": [x["message"] for x in p.get("problems", [])]}

    @Property("QVariantList", notify=legsChanged)
    def legs(self):
        accounts = self._targets.get("bank_accounts", [])
        return [{"key": l["key"], "method": l["method"], "amount": l["amount"], "bank": str(l["bank"] or ""),
                 "needsAccount": l["method"] in ACCOUNT_TYPE,
                 "accounts": [{"id": str(a["id"]), "name": a["name"]} for a in accounts
                              if a["account_type"] == ACCOUNT_TYPE.get(l["method"])]} for l in self._legs]

    @Property("QVariantList", notify=changed)
    def methods(self):
        return METHODS

    @Property(str, notify=changed)
    def accountAmount(self):
        return self._account

    @Property(bool, notify=changed)
    def reasonMissing(self):
        return not self._reason.strip()

    @Property(bool, notify=changed)
    def canExecute(self):
        if self._result or self.busy or not self._reason.strip():
            return False
        if self._mode == REVERSAL:
            return bool(self._reversal and self._reversal["can_reverse"])
        if self._mode == SETTLEMENT:
            p = self._settle_preview
            return bool(p and not p["problems"] and not self._timer.isActive() and "settle_preview" not in self._busy)
        return False

    @Property(bool, notify=changed)
    def confirming(self):
        return self._confirming

    @Property(str, notify=changed)
    def confirmText(self):
        t = self._target or {}
        if self._mode == REVERSAL:
            prefix = {"INVOICE": "للفاتورة", "EXCHANGE": "للاستبدال", "RETURN": "للمرتجع"}.get(t.get("source_type"), "للعملية")
            original = {"INVOICE": "الفاتورة الأصلية", "EXCHANGE": "الاستبدال الأصلي", "RETURN": "المرتجع الأصلي"}.get(
                t.get("source_type"), "العملية الأصلية")
            return f"سيتم إنشاء حركة عكسية موثقة {prefix} {t.get('reference', '')} ولن يتم حذف {original}."
        return "سيتم عكس الحركة من الحساب المسجل بالخطأ وتسجيلها على الحساب الصحيح."
