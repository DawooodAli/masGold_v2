"""عهدة السوق: custody of cash and Scrap the OWNER takes to the market (issue with an explicit designation, return, melt, close,
reopen, reverse).

Formats what MarketTripService returns; nothing here decides authorization or computes custody or the melting analysis (the service
re-checks the right in the transaction on every call, and every figure is re-read from the database after each change). Shop
balances, master-karat custody and assayed lots are always separate. Ids cross the QML boundary as decimal strings.
"""
from uuid import uuid4

from PySide6.QtCore import QObject, Property, Signal, Slot

from app.formatting import date_text, datetime_text, grams, ltr, money, number

LIMITATION = ("في هذه المرحلة لا توجد عمليات بيع أو تسوية للموردين أو إرجاع للشحنات المعايَرة: تُغلق العهدة فقط بعد إرجاع كل النقدية "
              "وكل الذهب الذي أُخرج منها وإنهاء كل الشحنات. النقدية والذهب في العهدة ملك للمحل لكنهما غير متاحين داخله حتى يُرجَعا.")
STATUS_TEXT = {"OPEN": "مفتوحة", "CLOSED": "مغلقة"}
KIND_TEXT = {"ISSUE": "إخراج عهدة", "RETURN": "رجوع من العهدة", "MELT": "سبك"}
DESIGNATION_TEXT = {"MELT": "للسبك", "LOGO": "لوجو", "UNCLASSIFIED": "غير مصنف (قبل التصنيف)"}
DESIGNATION_CHOICES = [{"id": "MELT", "text": "للسبك"}, {"id": "LOGO", "text": "لوجو"}]
LOT_TEXT = {"MAIN": "الناتج الرئيسي", "RESIDUE": "راسب"}
REVERSED_NOTICE = "عملية معكوسة — الأرقام للتاريخ فقط ولا تؤثر على الرصيد الحالي"
INCOMPLETE_TEXT = "غير مكتمل — عيار بعض الرواسب غير معروف، فلا يُعرض فرق نهائي للذهب الصافي."
LOT_NOTE = "إرجاع الشحنات المعايَرة إلى المحل سيتاح في مرحلة لاحقة؛ ولا يمكن إغلاق العهدة قبل ذلك."


def fineness_text(value):
    return "غير معروف" if value is None else f"{ltr(number(value, 1))} في الألف"


def _gold_label(g):
    return f"عيار {g['karat_value']} ({DESIGNATION_TEXT[g['designation']]})"


def outstanding_text(custody):
    """'نقدية 3,000.00 ج • عيار 21 (للسبك): 10.000 جرام' / 'لا شيء متبقٍ' — exactly what is still out."""
    parts = []
    if custody["cash"]["outstanding"] != 0:
        parts.append(f"نقدية {money(custody['cash']['outstanding'])}")
    parts += [f"{_gold_label(g)}: {grams(g['outstanding'])} جرام" for g in custody["gold"] if g["outstanding"] != 0]
    parts += [f"شحنة سبك {l['lot_no']} (الحركة {l['entry_no']}): {grams(l['outstanding'])} جرام" for l in custody["lots"]]
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
        if self._operation in ("issue", "return", "melt"):
            self.entryPosted.emit(result["code"])
        else:
            self.actionDone.emit(self._operation)

    @staticmethod
    def _gold(lines):
        return [{"karat_id": str(line.get("karatId", "")), "weight": str(line.get("weight", "")),
                 "designation": str(line.get("designation", ""))} for line in (lines or [])]

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

    @Slot(str, "QVariantList", str, str, str, str, "QVariantList", str, str, str, str)
    def meltCustody(self, trip_id, input_lines, entry_date, workshop, output_weight, output_fineness, residue_lines,
                    assay_reference, gain_explanation, notes, request_key):
        """Melt designated custody into an assayed main lot and optional residues. No Scrap, cash or supplier moves."""
        inputs = [{"karat_id": str(line.get("karatId", "")), "weight": str(line.get("weight", ""))} for line in (input_lines or [])]
        residues = [{"weight": str(line.get("weight", "")), "fineness": str(line.get("fineness", "")),
                     "label": str(line.get("label", ""))} for line in (residue_lines or [])]
        output = {"weight": output_weight, "fineness": output_fineness}
        self._write("melt", "تم تسجيل السبك", lambda s, a: s.melt(
            trip_id, inputs, entry_date, workshop, output, residues, assay_reference, gain_explanation, notes, request_key,
            _expected_actor=a), False)

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
                "designations": DESIGNATION_CHOICES,
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
        gold = custody["gold"]
        melt_stock = [{"karatId": str(g["karat_id"]), "text": f"عيار {g['karat_value']} — المتاح للسبك {grams(g['outstanding'])}"}
                      for g in gold if g["designation"] == "MELT" and g["outstanding"] > 0]
        return_lines = [{"key": f"{g['karat_id']}:{g['designation']}", "karatId": str(g["karat_id"]), "designation": g["designation"],
                         "text": f"{_gold_label(g)} — المتبقي {grams(g['outstanding'])}"}
                        for g in gold if g["outstanding"] > 0]
        return {
            "id": trip["id"], "code": trip["code"], "open": is_open, "statusText": STATUS_TEXT[trip["status"]],
            "closable": is_open and settled,
            "closeHint": "" if not is_open else ("كل العهدة عادت: يمكن إغلاق العهدة." if settled
                                                  else "لا يمكن الإغلاق قبل إرجاع المتبقي: " + outstanding_text(custody)),
            "cash": {"issued": money(custody["cash"]["issued"]), "returned": money(custody["cash"]["returned"]),
                     "outstanding": money(custody["cash"]["outstanding"]), "zero": custody["cash"]["outstanding"] == 0,
                     "outstandingValue": f"{custody['cash']['outstanding']:.2f}"},
            "gold": [{"karat": g["karat_value"], "karatId": str(g["karat_id"]), "designation": g["designation"],
                      "designationText": DESIGNATION_TEXT[g["designation"]], "label": _gold_label(g), "issued": grams(g["issued"]),
                      "returned": grams(g["returned"]), "melted": grams(g["melted"]), "outstanding": grams(g["outstanding"]),
                      "zero": g["outstanding"] == 0} for g in gold],
            "lots": [{"lotId": l["lot_id"], "title": f"{LOT_TEXT[l['kind']]} — الحركة {l['entry_no']}"
                      + (f" • {l['label']}" if l["label"] else ""),
                      "weight": grams(l["weight"]), "fineness": fineness_text(l["fineness"]),
                      "pure": "غير معروف" if l["pure"] is None else number(l["pure"], 6), "outstanding": grams(l["outstanding"])}
                     for l in custody["lots"]],
            "lotNote": LOT_NOTE if custody["lots"] else "",
            "meltStock": melt_stock, "meltAvailable": is_open and bool(melt_stock), "returnLines": return_lines,
            "outstandingText": outstanding_text(custody),
            "entries": [self._entry(e, is_open) for e in d["entries"]],
            "melts": [self._melt(m) for m in d.get("melts", [])],
            "events": [{"text": {"OPENED": "فتح العهدة", "CLOSED": "إغلاق العهدة", "REOPENED": "إعادة فتح العهدة"}[e["type"]],
                        "reason": e["reason"], "time": datetime_text(e["at"]), "actor": e["actor"]} for e in d["events"]]}

    @staticmethod
    def _entry(e, trip_open):
        parts = []
        if e["cash"] is not None:
            parts.append(f"نقدية {money(e['cash'])}")
        parts += [f"عيار {g['karat_value']} ({DESIGNATION_TEXT[g['designation']]}): {grams(g['weight'])} جرام" for g in e["gold"]]
        return {"id": e["id"], "code": e["code"], "kind": e["kind"], "kindText": KIND_TEXT[e["kind"]],
                "what": " • ".join(parts) if parts else ("انظر تفاصيل السبك أدناه" if e["kind"] == "MELT" else ""),
                "date": date_text(e["date"]), "time": datetime_text(e["at"]), "actor": e["actor"],
                "notes": e["notes"], "reversed": e["reversed"],
                "state": f"معكوسة بالعملية {e['reversal_code']}" if e["reversed"] else "",
                "reason": e["reversal_reason"] or "", "canReverse": trip_open and not e["reversed"]}

    @staticmethod
    def _melt(m):
        """One melt: raw facts, the main output, residues and the permanent analysis. Incomplete assays never show a final loss."""
        a = m["analysis"]
        rows = [("وزن المدخلات", f"{grams(a['input_weight'])} جرام"),
                ("الذهب الصافي الاسمي للمدخلات", f"{number(a['nominal_pure'], 6)} جرام"),
                ("وزن النواتج (مع الرواسب)", f"{grams(a['output_weight'])} جرام"),
                ("الذهب الصافي المعروف في النواتج" + ("" if a["pure_complete"] else " (جزئي)"),
                 f"{number(a['known_output_pure'], 6)} جرام"),
                ("فرق الوزن (النواتج − المدخلات)", f"{grams(a['weight_difference'], signed=True)} جرام")]
        if a["pure_complete"]:
            rows += [("فرق الذهب الصافي (النواتج − المدخلات)", f"{number(a['pure_difference'], 6, signed=True)} جرام"),
                     ("فرق المكافئ بعيار 21", f"{number(a['eq21_difference'], 6, signed=True)} جرام")]
        else:
            rows += [("فرق الذهب الصافي", INCOMPLETE_TEXT), ("فرق المكافئ بعيار 21", "غير مكتمل")]
        return {"id": m["entry_id"], "code": m["code"], "date": date_text(m["date"]), "workshop": m["workshop"],
                "assay": m["assay_reference"], "gainExplanation": m["gain_explanation"], "notes": m["notes"], "actor": m["actor"],
                "inputsText": " • ".join(f"عيار {i['karat_value']}: {grams(i['weight'])} جرام" for i in m["inputs"]),
                "lots": [{"title": LOT_TEXT[l["kind"]] + (f" {l['lot_no'] - 1}" if l["kind"] == "RESIDUE" else "")
                          + (f" — {l['label']}" if l["label"] else ""),
                          "text": f"{grams(l['weight'])} جرام • العيار الفعلي {fineness_text(l['fineness'])}"
                                  + ("" if l["pure"] is None else f" • صافي {number(l['pure'], 6)}")} for l in m["lots"]],
                "analysis": [{"label": label, "value": value} for label, value in rows], "complete": a["pure_complete"],
                "reversed": m["reversed"], "state": f"معكوسة بالعملية {m['reversal_code']}" if m["reversed"] else "",
                "notice": REVERSED_NOTICE if m["reversed"] else "", "reason": m["reversal_reason"] or ""}
