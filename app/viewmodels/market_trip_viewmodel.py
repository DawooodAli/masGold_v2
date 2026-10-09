"""عهدة السوق: custody of cash and Scrap the OWNER takes to the market (issue, return, close, reopen, reverse).

Formats what MarketTripService returns; nothing here decides authorization or computes custody (the service re-checks the right
in the transaction on every call, and every figure is re-read from the database after each change). Shop balances and custody
balances are always separate. Ids cross the QML boundary as decimal strings.
"""
from uuid import uuid4

from PySide6.QtCore import QObject, Property, Signal, Slot

from app.formatting import date_text, datetime_text, grams, money

LIMITATION = ("في هذه المرحلة لا توجد عمليات بيع أو تسوية للموردين: تُغلق العهدة فقط بعد إرجاع كل النقدية وكل الذهب الذي أُخرج منها."
              " النقدية والذهب في العهدة ملك للمحل لكنهما غير متاحين داخله حتى يُرجَعا.")
STATUS_TEXT = {"OPEN": "مفتوحة", "CLOSED": "مغلقة"}
KIND_TEXT = {"ISSUE": "إخراج عهدة", "RETURN": "رجوع من العهدة"}


def outstanding_text(custody):
    """'نقدية 3,000.00 ج • عيار 21: 10.000 جرام' / 'لا شيء متبقٍ' — exactly what is still out."""
    parts = []
    if custody["cash"]["outstanding"] != 0:
        parts.append(f"نقدية {money(custody['cash']['outstanding'])}")
    parts += [f"عيار {g['karat_value']}: {grams(g['outstanding'])} جرام" for g in custody["gold"] if g["outstanding"] != 0]
    return " • ".join(parts) if parts else "لا شيء متبقٍ"


class MarketTripViewModel(QObject):
    changed = Signal()
    entryPosted = Signal(str)            # the entry code: the entry dialog can close
    actionDone = Signal(str)             # close / reopen / reversal finished (the dialogs can close)
    custodyChanged = Signal()            # cash / Scrap really moved: every balance view reloads from the database

    def __init__(self, session, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._workers, self._request, self._latest, self._busy = set(), 0, {}, set()
        self._shutdown = False
        self._trips, self._detail, self._context = [], {}, {}
        self._selected, self._error, self._message = "", "", ""

    # ------------------------------------------------------------ plumbing
    def _service_instance(self):
        if self._service is None:
            from services.market_trip_service import MarketTripService
            self._service = MarketTripService(self._session)
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

    # ------------------------------------------------------------ reads
    @Slot()
    def refresh(self, _reason=""):
        """Trips, the open trip's detail and the entry-dialog context: always from the database."""
        if not self.canManage:
            return
        service = self._service_instance()
        self._start("list", lambda _expected_actor: service.trips(_expected_actor=_expected_actor))
        self._start("context", lambda _expected_actor: service.context(_expected_actor=_expected_actor))
        if self._selected:
            self._load_detail()

    def _on_list(self, result, error):
        if error:
            self._error = error
            return
        self._trips, self._error = result, ""
        if self._selected and not any(t["id"] == self._selected for t in result):
            self._selected, self._detail = "", {}

    def _on_context(self, result, error):
        if error:
            self._error = error
            return
        self._context = result

    @Slot(str)
    def selectTrip(self, trip_id):
        self._selected, self._detail = str(trip_id or ""), {}
        self._error = self._message = ""
        self.changed.emit()
        if self._selected:
            self._load_detail()

    def _load_detail(self):
        service, trip_id = self._service_instance(), self._selected
        self._start("detail", lambda _expected_actor: service.trip_detail(trip_id, _expected_actor=_expected_actor))

    def _on_detail(self, result, error):
        if error:
            self._error = error
            return
        if result["trip"]["id"] == self._selected:
            self._detail, self._error = result, ""

    @Slot()
    def clearMessage(self):
        if self._message or self._error:
            self._message = self._error = ""
            self.changed.emit()

    @Slot(result=str)
    def newRequestKey(self):
        """One key per opened dialog: a double click re-sends the SAME key, so it stays idempotent."""
        return str(uuid4())

    # ------------------------------------------------------------ writes (each re-checked by the service)
    def _write(self, operation, text, call, moved):
        if "write" in self._busy:
            return                                                     # a double click never submits twice
        self._message = self._error = ""
        self._done_text, self._moved, self._operation = text, moved, operation
        service = self._service_instance()
        self._start("write", lambda _expected_actor: call(service, _expected_actor))
        self.changed.emit()

    def _on_write(self, result, error):
        if error:
            self._error = error                                        # the dialog stays open with every field kept
            return
        text = self._done_text
        if isinstance(result, dict) and result.get("code"):
            text = f"{text} {result['code']}"
            if result.get("duplicate"):
                text = f"{self._done_text} {result['code']} (مسجلة من قبل)"
        self._message = text
        if isinstance(result, dict) and result.get("trip_id") and not self._selected:
            self._selected = result["trip_id"]
        self.refresh()
        # Notified only after the transaction succeeded; a failing refresh belongs to the view that failed, never to this write.
        if self._moved:
            self.custodyChanged.emit()
        if self._operation in ("issue", "return"):
            self.entryPosted.emit(result["code"])
        else:
            self.actionDone.emit(self._operation)

    @staticmethod
    def _gold(lines):
        return [{"karat_id": str(line.get("karatId", "")), "weight": str(line.get("weight", ""))} for line in (lines or [])]

    @Slot(str, str, "QVariantList", str, str, str)
    def issue(self, trip_id, cash, gold_lines, entry_date, notes, request_key):
        lines = self._gold(gold_lines)
        self._write("issue", "تم إخراج العهدة", lambda s, a: s.issue(
            trip_id, cash, lines, entry_date, notes, request_key, _expected_actor=a), True)

    @Slot(str, str, "QVariantList", str, str, str)
    def returnCustody(self, trip_id, cash, gold_lines, entry_date, notes, request_key):
        lines = self._gold(gold_lines)
        self._write("return", "تم تسجيل الرجوع", lambda s, a: s.return_custody(
            trip_id, cash, lines, entry_date, notes, request_key, _expected_actor=a), True)

    @Slot(str)
    def closeTrip(self, trip_id):
        self._write("close", "تم إغلاق العهدة", lambda s, a: s.close(trip_id, _expected_actor=a), False)

    @Slot(str, str)
    def reopenTrip(self, trip_id, reason):
        self._write("reopen", "تمت إعادة فتح العهدة", lambda s, a: s.reopen(trip_id, reason, _expected_actor=a), False)

    @Slot(str, str)
    def reverseEntry(self, entry_id, reason):
        self._write("reverse", "تم عكس الحركة", lambda s, a: s.reverse(entry_id, reason, _expected_actor=a), True)

    # ------------------------------------------------------------ properties
    @Property(bool, notify=changed)
    def canManage(self):
        actor = self._session.current_actor()
        return bool(actor and "MARKET_TRIP_MANAGE" in actor.permissions)

    @Property(bool, notify=changed)
    def busy(self):
        return bool(self._busy)

    @Property(bool, notify=changed)
    def writing(self):
        return "write" in self._busy

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(str, constant=True)
    def limitation(self):
        return LIMITATION

    @Property(str, notify=changed)
    def selectedTripId(self):
        return self._selected

    @Property("QVariantList", notify=changed)
    def trips(self):
        return [{"id": t["id"], "code": t["code"], "status": t["status"], "statusText": STATUS_TEXT[t["status"]],
                 "outstandingText": outstanding_text(t["custody"]), "settled": t["custody"]["settled"],
                 "entryCount": t["entry_count"], "date": date_text(t["created_at"]), "selected": t["id"] == self._selected}
                for t in self._trips]

    @Property("QVariantMap", notify=changed)
    def context(self):
        """Entry-dialog data and the SHOP balances (shown apart from custody)."""
        c = self._context
        if not c:
            return {}
        safe = c["safe"]
        return {"safeName": "" if safe is None else safe["name"], "safeReady": bool(safe and safe["active"]),
                "safeBalanceText": "" if safe is None else money(safe["balance"]),
                "karats": [{"id": str(k["id"]), "text": f"عيار {k['karat_value']}"} for k in c["karats"]],
                "shop": [{"karat": s["karat_value"], "text": f"عيار {s['karat_value']}: المتاح {grams(s['available'])} جرام"
                          + (f" • محجوز لمراجعة المدير {grams(s['held'])}" if s["held"] > 0 else "")}
                         for s in c["shop_scrap"] if s["physical"] != 0 or s["available"] != 0]}

    @Property("QVariantMap", notify=changed)
    def detail(self):
        d = self._detail
        if not d:
            return {}
        trip, custody = d["trip"], d["custody"]
        is_open = trip["status"] == "OPEN"
        settled = custody["settled"]
        return {
            "id": trip["id"], "code": trip["code"], "open": is_open, "statusText": STATUS_TEXT[trip["status"]],
            "closable": is_open and settled,
            "closeHint": "" if not is_open else ("كل العهدة عادت: يمكن إغلاق العهدة." if settled
                                                  else "لا يمكن الإغلاق قبل إرجاع المتبقي: " + outstanding_text(custody)),
            "cash": {"issued": money(custody["cash"]["issued"]), "returned": money(custody["cash"]["returned"]),
                     "outstanding": money(custody["cash"]["outstanding"]), "zero": custody["cash"]["outstanding"] == 0,
                     "outstandingValue": f"{custody['cash']['outstanding']:.2f}"},
            "gold": [{"karat": g["karat_value"], "karatId": str(g["karat_id"]), "issued": grams(g["issued"]),
                      "returned": grams(g["returned"]), "outstanding": grams(g["outstanding"]), "zero": g["outstanding"] == 0}
                     for g in custody["gold"]],
            "outstandingText": outstanding_text(custody),
            "entries": [self._entry(e, is_open) for e in d["entries"]],
            "events": [{"text": {"OPENED": "فتح العهدة", "CLOSED": "إغلاق العهدة", "REOPENED": "إعادة فتح العهدة"}[e["type"]],
                        "reason": e["reason"], "time": datetime_text(e["at"]), "actor": e["actor"]} for e in d["events"]]}

    @staticmethod
    def _entry(e, trip_open):
        parts = []
        if e["cash"] is not None:
            parts.append(f"نقدية {money(e['cash'])}")
        parts += [f"عيار {g['karat_value']}: {grams(g['weight'])} جرام" for g in e["gold"]]
        return {"id": e["id"], "code": e["code"], "kind": e["kind"], "kindText": KIND_TEXT[e["kind"]],
                "what": " • ".join(parts), "date": date_text(e["date"]), "time": datetime_text(e["at"]), "actor": e["actor"],
                "notes": e["notes"], "reversed": e["reversed"],
                "state": f"معكوسة بالعملية {e['reversal_code']}" if e["reversed"] else "",
                "reason": e["reversal_reason"] or "", "canReverse": trip_open and not e["reversed"]}
