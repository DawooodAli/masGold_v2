"""عهدة السوق: custody of cash and Scrap the OWNER takes to the market (issue with an explicit designation, return, melt, close,
reopen, reverse).

Formats what MarketTripService returns; nothing here decides authorization or computes custody or the melting analysis (the service
re-checks the right in the transaction on every call, and every figure is re-read from the database after each change). Shop
balances, master-karat custody and assayed lots are always separate. Ids cross the QML boundary as decimal strings.
"""
from decimal import Decimal
from uuid import uuid4

from PySide6.QtCore import QObject, Property, Signal, Slot

from app.formatting import date_text, datetime_text, grams, ltr, money, number
from app.viewmodels.supplier_viewmodel import making_position_text
from services import supplier_rules as rules
from services.intermediary_holding_rules import CLOSURE_NOTE
from services.market_trip_service import TripError

LIMITATION = ("في هذه المرحلة لا توجد عمليات بيع. من العهدة يمكن دفع مصنعيات المورد نقدًا وتسوية ذهب اللوجو للمورد (تُسوّى أيضًا شحنة معايَرة كاملة للمورد): "
              "تُغلق العهدة فقط بعد أن يصبح المتبقي صفرًا، أي بعد إرجاع كل النقدية وكل الذهب الذي أُخرج منها (ما لم يُسلَّم لمورد) وتصفية كل شحنات السبك بالرجوع أو التسوية. "
              "النقدية والذهب في العهدة ملك للمحل لكنهما غير متاحين داخله حتى يُرجَعا.")
STATUS_TEXT = {"OPEN": "مفتوحة", "CLOSED": "مغلقة"}
GOLD_SETTLE_REVERSAL_NOTE = ("عكس تسوية الذهب يسجّل أن الذهب المُسلَّم عاد فعلًا إلى عهدة السوق (بالوزن المخزّن في التسوية) ويعيد رصيد المورد بنفس الكمية المحتسبة المخزَّنة. "
                             "لا يتحرك كسر المحل ولا النقدية؛ أرجع الذهب إلى المحل بـ«تسجيل رجوع» عند إرجاعه فعلًا.")
PAYMENT_REVERSAL_NOTE = ("عكس دفع المصنعيات يسجّل أن المبلغ عاد فعلًا إلى عهدة السوق (بالمبلغ المخزّن في الدفعة) ويعيد مستحقات المورد بنفس المبلغ. "
                         "لا يُعاد المبلغ تلقائيًا إلى الخزنة الرئيسية: سجّل «تسجيل رجوع» من العهدة عند إرجاعه فعلًا.")
KIND_TEXT = {"ISSUE": "إخراج عهدة", "RETURN": "رجوع من العهدة", "MELT": "سبك", "LOT_RETURN": "رجوع ناتج السبك", "LOT_ISSUE": "إخراج سبكة من المحل",
             "HEAT": "حمّية اللوجو", "SUPPLIER_PAY": "دفع مصنعيات من العهدة", "GOLD_SETTLE": "تسوية ذهب من العهدة", "LOT_SETTLE": "تسوية سبكة من العهدة",
             "LOT_HOLD": "تسليم ذهب لمسوقجي", "HOLD_RETURN": "رجوع سبكة من مسوقجي", "LOGO_HOLD": "تسليم لوجو لمسوقجي", "HOLD_BACK": "رجوع لوجو من مسوقجي"}
HOLD_REVERSE_NOTE = ("عكس التسليم يعيد الشحنة الأصلية كاملة إلى هذه العهدة ويسجّل حركة تعويض بالوزن المخزّن في ذهب المحل لدى المسوقجي؛ "
                     "تبقى حركة التسليم الأصلية في السجل. لا نقدية ولا كسر ولا مورد.")
RETURN_REVERSE_NOTE = ("عكس الرجوع يُخرج الشحنة من هذه العهدة ويعيدها إلى ذهب المحل لدى المسوقجي بالوزن المخزّن بحركة تعويض؛ "
                       "تبقى حركة الرجوع الأصلية وتأكيد المالك في السجل.")
DESIGNATION_TEXT = {"MELT": "للسبك", "LOGO": "لوجو", "UNCLASSIFIED": "غير مصنف (قبل التصنيف)"}
DESIGNATION_CHOICES = [{"id": "MELT", "text": "للسبك"}, {"id": "LOGO", "text": "لوجو"}]
LOT_TEXT = {"MAIN": "الناتج الرئيسي", "RESIDUE": "رواسب"}
REVERSED_NOTICE = "عملية معكوسة — الأرقام للتاريخ فقط ولا تؤثر على الرصيد الحالي"
INCOMPLETE_TEXT = "غير مكتمل — عيار بعض الرواسب غير معروف، فلا يُعرض فرق نهائي للذهب الصافي."
LOT_NOTE = "صفِّ السبائك المتبقية قبل إغلاق العهدة: برجوع ناتج السبك كاملًا إلى المحل أو بتسوية سبكة معلومة العيار للمورد؛ لا يمكن التصرف في جزء من السبكة."
WHOLE_LOT_RULE = "ترجع السبكة كاملة ولا يمكن إرجاع جزء منها"
UNKNOWN_ASSAY = "غير معلومة"


def fineness_text(value):
    return UNKNOWN_ASSAY if value is None else f"{ltr(number(value, 1))} في الألف"


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
    supplierPaymentChanged = Signal()    # a supplier payment OR gold settlement from custody was posted or reversed: the supplier views reload from the database
    custodyChanged = Signal()            # cash / Scrap really moved: every balance view reloads from the database

    def __init__(self, session, service=None, parent=None, holdings=None):
        super().__init__(parent)
        self._session, self._service, self._holdings = session, service, holdings
        self._workers, self._request, self._latest, self._busy = set(), 0, {}, set()
        self._shutdown = False
        self._trips, self._detail, self._context, self._shop_lots = [], {}, {}, []
        self._selected, self._error, self._message = "", "", ""
        self._reversing_payment = False

    # ------------------------------------------------------------ plumbing
    def _service_instance(self):
        if self._service is None:
            from services.market_trip_service import MarketTripService
            self._service = MarketTripService(self._session)
        return self._service

    def _holding_service(self):
        if self._holdings is None:
            from services.intermediary_holding_service import IntermediaryHoldingService
            self._holdings = IntermediaryHoldingService(self._session)
        return self._holdings

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
        self._start("shoplots", lambda _expected_actor: service.shop_lots(_expected_actor=_expected_actor))
        if self._selected:
            self._load_detail()

    def _on_list(self, result, error):
        if error:
            self._error = error
            return
        self._trips, self._error = result, ""
        if self._selected and not any(t["id"] == self._selected for t in result):
            self._selected, self._detail = "", {}

    def _on_shoplots(self, result, error):
        if error:
            self._error = error
            return
        self._shop_lots = result

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
            reference = result.get("payment_code") or result.get("settlement_code") or result.get("document_code")
            text = f"{text} {result['code']}" + (f" ({reference})" if reference else "")
            if result.get("duplicate"):
                text = f"{self._done_text} {result['code']} (مسجلة من قبل)"
        self._message = text
        if isinstance(result, dict) and result.get("trip_id") and not self._selected:
            self._selected = result["trip_id"]
        self.refresh()
        # Notified only after the transaction succeeded; a failing refresh belongs to the view that failed, never to this write.
        if self._moved:
            self.custodyChanged.emit()
        if self._operation in ("paysupplier", "goldsettle", "lotsettle", "lothold", "logohold") or (self._operation == "reverse" and self._reversing_payment):
            self.supplierPaymentChanged.emit()
        self._reversing_payment = False
        if self._operation in ("issue", "create", "return", "melt", "lotreturn", "lotissue", "heat", "paysupplier", "goldsettle", "lotsettle", "lothold", "logohold"):
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

    @Slot(str, "QVariantList", "QVariantList", str, str, str)
    def createCustody(self, cash, gold_lines, lot_lines, entry_date, notes, request_key):
        """ONE new custody from any combination of cash, Scrap lines and whole shop bars, posted in one service transaction."""
        lines = self._gold(gold_lines)
        lots = [{"lot_id": str(line.get("lotId", ""))} for line in (lot_lines or [])]
        self._write("create", "تم إخراج العهدة", lambda s, a: s.create_trip(
            cash, lines, lots, entry_date, notes, request_key, _expected_actor=a), True)

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

    @Slot(str, "QVariantList", str, str, str, str, str, str)
    def heatCustody(self, trip_id, lines, entry_date, workshop, reference, gain_explanation, notes, request_key):
        """Heat LOGO custody (حمّية اللوجو): processed grams out, the actual resulting grams back, same karat and designation.
        Weights only: no cash, Scrap, supplier, inventory or lot movement, and no purity figure."""
        heat_lines = [{"karat_id": str(line.get("karatId", "")), "input_weight": str(line.get("inputWeight", "")),
                       "output_weight": str(line.get("outputWeight", ""))} for line in (lines or [])]
        self._write("heat", "تم تسجيل حمّية اللوجو", lambda s, a: s.heat(
            trip_id, heat_lines, entry_date, workshop, reference, gain_explanation, notes, request_key, _expected_actor=a), False)

    @Slot(str, str, result=str)
    def heatDifference(self, input_weight, output_weight):
        """A display-only preview of output - input in grams (the service recomputes and stores the authoritative value)."""
        from services.market_trip_service import TripError, parse_weight      # the service's own parser: one definition of a valid weight
        try:
            before, after = parse_weight(input_weight), parse_weight(output_weight)
        except TripError:
            return ""
        if before == 0 or after == 0:
            return ""
        return f"{grams(after - before, signed=True)} جرام"

    @Slot(str, "QVariantList", str, str, str)
    def issueLots(self, trip_id, lot_lines, entry_date, notes, request_key):
        """Send whole shop bars out into an OPEN custody. Only the lot ids are sent: the service derives the full weight."""
        lines = [{"lot_id": str(line.get("lotId", ""))} for line in (lot_lines or [])]
        self._write("lotissue", "تم إخراج السبكة من المحل", lambda s, a: s.issue_lots(
            trip_id, lines, entry_date, notes, request_key, _expected_actor=a), False)

    @Slot(str, "QVariantList", str, str, str)
    def returnLots(self, trip_id, lot_lines, entry_date, notes, request_key):
        """Return whole assayed lots from custody to the shop's lot stock. Only the lot ids are sent: the service derives the full weight."""
        lines = [{"lot_id": str(line.get("lotId", ""))} for line in (lot_lines or [])]
        self._write("lotreturn", "تم رجوع ناتج السبك", lambda s, a: s.return_lots(
            trip_id, lines, entry_date, notes, request_key, _expected_actor=a), False)

    @Slot(str)
    def closeTrip(self, trip_id):
        self._write("close", "تم إغلاق العهدة", lambda s, a: s.close(trip_id, _expected_actor=a), False)

    @Slot(str, str)
    def reopenTrip(self, trip_id, reason):
        self._write("reopen", "تمت إعادة فتح العهدة", lambda s, a: s.reopen(trip_id, reason, _expected_actor=a), False)

    @Slot(str, str, str, str, str, str, str)
    def paySupplier(self, trip_id, supplier_id, amount, payment_date, reference, notes, request_key):
        """Pay a supplier's making with cash already in this custody (no Main Safe / bank movement; the service re-checks every right)."""
        self._write("paysupplier", "تم دفع مصنعيات من العهدة", lambda s, a: s.pay_supplier(
            trip_id, supplier_id, amount, payment_date, reference, notes, request_key, _expected_actor=a), False)

    @Slot(str, str, str, str, str, str, str, str, str)
    def settleGold(self, trip_id, supplier_id, karat_id, designation, weight, settlement_date, reference, notes, request_key):
        """Deliver LOGO custody gold to a supplier against its gold account (تسوية ذهب من العهدة). Only ids and the typed text are sent: the
        service derives the source balance, the credit and the rounding, re-checks every right and posts atomically. No Scrap, cash or lot moves."""
        self._write("goldsettle", "تمت تسوية الذهب من العهدة", lambda s, a: s.settle_supplier_gold(
            trip_id, supplier_id, karat_id, designation, weight, settlement_date, reference, notes, request_key, _expected_actor=a), False)

    @Slot(str, str, str, str, result="QVariantMap")
    def previewGoldSettlement(self, supplier_id, karat_id, designation, weight):
        """What the settlement would credit, BEFORE submitting: figures come from supplier_rules (the function the service uses), never from QML
        arithmetic. The service recomputes and re-checks the effective outstanding when it posts."""
        empty = {"valid": False, "error": "", "short": False, "availableText": "", "supplierKaratText": "", "deliveredText": "", "formulaText": "",
                 "creditedText": "", "analyticalText": "", "differenceText": ""}
        if not self._detail or not self._context or not str(supplier_id or "").strip() or not str(karat_id or "").strip() \
                or not str(designation or "").strip():
            return empty
        line = next((g for g in self._detail["custody"]["gold"] if str(g["karat_id"]) == str(karat_id) and g["designation"] == designation), None)
        supplier = next((s for s in self._context.get("suppliers", []) if s["id"] == str(supplier_id)), None)
        if line is None or supplier is None:
            return {**empty, "error": "اختر المورد ومصدر الذهب من العهدة."}
        available = f"{grams(line['outstanding'])} جرام عيار {line['karat_value']}"
        if supplier["karat_value"] is None:
            return {**empty, "availableText": available,
                    "error": "عيار هذا المورد غير محدد: حدّده من تعديل المورد أولًا."}
        base = {**empty, "availableText": available, "supplierKaratText": f"عيار {supplier['karat_value']}"}
        if not str(weight or "").strip():
            return base
        try:
            delivered = rules.parse_delivered_weight(weight)
            credited, analytical, difference = rules.settlement_credit(delivered, line["karat_value"], supplier["karat_value"])
        except rules.SupplierRuleError as exc:
            return {**base, "error": str(exc)}
        short = delivered > line["outstanding"]
        return {**base, "valid": not short, "short": short,
                "deliveredText": f"{grams(delivered)} جرام عيار {line['karat_value']} ({DESIGNATION_TEXT[designation]})",
                "formulaText": f"{grams(delivered)} × {line['karat_value']} ÷ {supplier['karat_value']}",
                "creditedText": f"{grams(credited)} جرام عيار {supplier['karat_value']}",
                "analyticalText": number(analytical, 6), "differenceText": number(difference, 6, signed=True)}

    @Slot(str, str)
    def reverseEntry(self, entry_id, reason):
        self._reversing_payment = any(e["id"] == str(entry_id) and e["kind"] in ("SUPPLIER_PAY", "GOLD_SETTLE", "LOT_SETTLE", "LOT_HOLD", "HOLD_RETURN", "LOGO_HOLD", "HOLD_BACK")
                                      for e in (self._detail or {}).get("entries", []))
        self._write("reverse", "تم عكس الحركة", lambda s, a: s.reverse(entry_id, reason, _expected_actor=a), True)

    @Slot(str, str, str, str, str, str, str)
    def settleLot(self, trip_id, supplier_id, lot_id, settlement_date, reference, notes, request_key):
        self._write("lotsettle", "تمت تسوية السبكة من العهدة", lambda s, a: s.settle_supplier_lot(
            trip_id, supplier_id, lot_id, settlement_date, reference, notes, request_key, _expected_actor=a), False)

    @Slot(str, str, str, str, str, str, str)
    def handOverLot(self, trip_id, party_id, lot_id, handover_date, reference, notes, request_key):
        """Hand one WHOLE known-assay lot of this custody to an intermediary (تسليم ذهب لمسوقجي). Only ids and typed text are sent: the service
        derives the full weight under the locks, re-checks every right and posts atomically. No cash, Scrap, making, supplier or shop-lot movement."""
        holding = self._holding_service()
        self._write("lothold", "تم تسليم الذهب للمسوقجي", lambda s, a: holding.hand_over_lot(
            trip_id, party_id, lot_id, handover_date, reference, notes, request_key, _expected_actor=a), False)

    @Slot(str, str, str, str, str, str, str, str)
    def handOverLogo(self, trip_id, party_id, karat_id, grams_text, handover_date, reference, notes, request_key):
        """Hand pooled LOGO grams of one master karat to an intermediary. Only ids and typed text are sent: the service derives the effective
        custody availability, re-checks every right and posts atomically."""
        holding = self._holding_service()
        self._write("logohold", "تم تسليم اللوجو للمسوقجي", lambda s, a: holding.hand_over_logo(
            trip_id, party_id, karat_id, grams_text, handover_date, reference, notes, request_key, _expected_actor=a), False)

    @Slot(str, str, str, result="QVariantMap")
    def previewLogoHandover(self, party_id, karat_id, grams_text):
        empty = {"valid": False, "error": "", "facts": ""}
        if not self._detail or not party_id or not karat_id:
            return empty
        line = next((g for g in self._detail["custody"]["gold"] if str(g["karat_id"]) == str(karat_id) and g["designation"] == "LOGO"), None)
        party = next((p for p in self._context.get("intermediaries", []) if p["id"] == str(party_id)), None)
        if line is None or party is None:
            return {**empty, "error": "اختر المسوقجي وعيار اللوجو."}
        facts = f"لوجو عيار {line['karat_value']} • المتاح في العهدة {grams(line['outstanding'])} جرام"
        if not str(grams_text or "").strip():
            return {**empty, "facts": facts}
        from services.market_trip_service import parse_weight
        try:
            quantity = parse_weight(grams_text)
            self._holding_service().logo_handover_check(line["outstanding"], quantity, {"is_intermediary": True, "is_active": party["active"]})
        except TripError as exc:
            return {**empty, "facts": facts, "error": str(exc)}
        return {**empty, "valid": True, "facts": facts + f" • يُسلَّم {grams(quantity)} جرام"}

    @Slot(str, str, result="QVariantMap")
    def previewHandover(self, party_id, lot_id):
        empty = {"valid": False, "error": "", "facts": "", "note": ""}
        if not self._detail or not party_id or not lot_id:
            return empty
        lot = next((l for l in self._detail["custody"]["lots"] if l["lot_id"] == str(lot_id)), None)
        party = next((p for p in self._context.get("intermediaries", []) if p["id"] == str(party_id)), None)
        if lot is None or party is None:
            return {**empty, "error": "اختر الشحنة والمسوقجي."}
        facts = (f"{LOT_TEXT[lot['kind']]} • {lot['code']} • شحنة {lot['lot_no']} • {grams(lot['weight'])} جرام • العيار الفعلي {fineness_text(lot['fineness'])}")
        try:
            self._holding_service().handover_check(lot, {"is_intermediary": True, "is_active": party["active"]})
        except TripError as exc:
            return {**empty, "facts": facts, "error": str(exc)}
        return {**empty, "valid": True, "facts": facts,
                "note": "تخرج السبكة كاملة من العهدة وتُسجَّل ذهبًا للمحل لدى المسوقجي (ليس دينًا للمورد ولا بيعًا)، وتعود إلى هذه العهدة بالرجوع."}

    @Slot(str, str, result="QVariantMap")
    def previewLotSettlement(self, supplier_id, lot_id):
        empty = {"valid": False, "error": "", "facts": "", "credit": "", "analysis": ""}
        if not self._detail or not supplier_id or not lot_id:
            return empty
        lot = next((l for l in self._detail["custody"]["lots"] if l["lot_id"] == str(lot_id)), None)
        supplier = next((s for s in self._context.get("suppliers", []) if s["id"] == str(supplier_id)), None)
        if lot is None or supplier is None:
            return {**empty, "error": "اختر الشحنة والمورد."}
        facts = f"{LOT_TEXT[lot['kind']]} • {lot['code']} • شحنة {lot['lot_no']} • {grams(lot['weight'])} جرام • العيار الفعلي {fineness_text(lot['fineness'])}"
        try:
            posted, analytical, difference = self._service_instance().lot_settlement_preview(lot, {**supplier, "is_supplier": True})
        except (TripError, rules.SupplierRuleError) as exc:
            return {**empty, "facts": facts, "error": str(exc)}
        return {**empty, "valid": True, "facts": facts,
                "credit": f"يُحتسب للمورد {grams(posted)} جرام عيار {supplier['karat_value']}",
                "analysis": f"القيمة التحليلية {number(analytical, 6)} • فرق التقريب {number(difference, 6, signed=True)}"}

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
                 "outstandingText": outstanding_text(t["custody"]) + (
                     " • ذهب خارج المحل لدى مسوقجي: " + grams(sum((p["balance"] for p in t.get("external_holdings", [])), Decimal("0"))) + " جرام"
                     if t.get("external_holdings") else ""), "settled": t["custody"]["settled"],
                 "entryCount": t["entry_count"], "date": date_text(t["created_at"]), "selected": t["id"] == self._selected}
                for t in self._trips]

    @Property("QVariantList", notify=changed)
    def shopLots(self):
        """The shop-side lot stock (ناتج السبك بالمحل): physical grams and exact assay per lot; pure grams only when the assay is known."""
        return [{"lotId": l["lot_id"], "ref": f"{l['code']} • شحنة {l['lot_no']}", "tripCode": l["trip_code"],
                 "kindText": LOT_TEXT[l["kind"]] + (f" — {l['label']}" if l["label"] else ""), "weight": grams(l["stock"]),
                 "fineness": fineness_text(l["fineness"]), "pure": UNKNOWN_ASSAY if l["pure"] is None else number(l["pure"], 6),
                 "fullWeight": grams(l["original_weight"]), "issuable": l["stock"] == l["original_weight"],
                 "reason": "" if l["stock"] == l["original_weight"] else
                 f"رصيدها بالمحل جزئي ({grams(l['stock'])} من {grams(l['original_weight'])} جرام): اعكس الرجوع الجزئي أولًا."}
                for l in self._shop_lots]

    @Property(str, notify=changed)
    def shopLotSummary(self):
        if not self._shop_lots:
            return ""
        total = sum((l["stock"] for l in self._shop_lots), Decimal("0"))
        known = sum((l["pure"] for l in self._shop_lots if l["pure"] is not None), Decimal("0"))
        unknown = sum(1 for l in self._shop_lots if l["pure"] is None)
        return (f"الإجمالي الفعلي {grams(total)} جرام • الذهب الصافي المعروف {number(known, 6)} جرام"
                + (f" • {unknown} شحنة بلا عيار معلوم" if unknown else ""))

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
                "holdParties": [{"id": r["id"], "text": r["name"] + ("" if r["active"] else " (متوقف)")} for r in c.get("intermediaries", [])],
                "suppliers": [{"id": r["id"], "text": r["name"] + ("" if r["active"] else " (متوقف)") + " — "
                               + making_position_text(rules.direction(r["making_balance"]), r["making_balance"])} for r in c.get("suppliers", [])],
                # the gold-settlement dialog: the supplier's DEDICATED karat is what the credit is converted into
                "goldSuppliers": [{"id": r["id"], "text": r["name"] + ("" if r["active"] else " (متوقف)") + " — "
                                   + (f"عيار حساب الذهب {r['karat_value']}" if r.get("karat_value") else "العيار غير محدد"),
                                   "karatResolved": bool(r.get("karat_value"))} for r in c.get("suppliers", [])],
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
        heat_stock = [{"karatId": str(g["karat_id"]), "text": f"عيار {g['karat_value']} — لوجو متاح للحمّية {grams(g['outstanding'])}"}
                      for g in gold if g["designation"] == "LOGO" and g["outstanding"] > 0]
        settle_sources = [{"key": f"{g['karat_id']}:{g['designation']}", "karatId": str(g["karat_id"]), "designation": g["designation"],
                           "text": f"{_gold_label(g)} — المتاح {grams(g['outstanding'])} جرام"}
                          for g in gold if g["designation"] == "LOGO" and g["outstanding"] > 0]
        return_lines = [{"key": f"{g['karat_id']}:{g['designation']}", "karatId": str(g["karat_id"]), "designation": g["designation"],
                         "text": f"{_gold_label(g)} — المتبقي {grams(g['outstanding'])}"}
                        for g in gold if g["outstanding"] > 0]
        return {
            "id": trip["id"], "code": trip["code"], "open": is_open, "statusText": STATUS_TEXT[trip["status"]],
            "closable": is_open and settled,
            "closeHint": "" if not is_open else (("لا متبقي في العهدة: يمكن إغلاق العهدة." + ((" " + CLOSURE_NOTE) if d.get("external_holdings") else "")) if settled
                                                  else "لا يمكن الإغلاق قبل تصفية المتبقي: " + outstanding_text(custody)),
            "lotSettleSources": [{"id": l["lot_id"], "text": f"{LOT_TEXT[l['kind']]} • {l['code']} • شحنة {l['lot_no']} • {grams(l['weight'])} جرام",
                                  "known": l["fineness"] is not None} for l in custody["lots"] if l["outstanding"] > 0],
            "lotSettleAvailable": is_open and any(l["outstanding"] == l["weight"] and l["fineness"] is not None for l in custody["lots"]),
            "holdSources": [{"id": l["lot_id"], "text": f"{LOT_TEXT[l['kind']]} • {l['code']} • شحنة {l['lot_no']} • {grams(l['weight'])} جرام • {fineness_text(l['fineness'])}"}
                            for l in custody["lots"] if l["outstanding"] == l["weight"] and l["fineness"] is not None],
            "holdVisible": is_open and self._holding_service().can_manage(),
            "holdAvailable": is_open and any(p["active"] for p in self._context.get("intermediaries", [])) and any(
                l["outstanding"] == l["weight"] and l["fineness"] is not None for l in custody["lots"]),
            "externalHoldings": [{"poolId": p["pool_id"], "party": p["party_name"] + ("" if p["party_active"] else " (متوقف)"),
                                  "source": f"لوجو عيار {p['karat_value']}" if p.get("logo") else p["source"],
                                  "text": (f"{p['party_name']} • لوجو عيار {p['karat_value']} (جرام فعلي) • المتبقي {grams(p['balance'])} من {grams(p['weight'])} جرام • {p['document']}"
                                           if p.get("logo") else
                                           f"{p['party_name']} • {p['source']} • {grams(p['balance'])} جرام • العيار الفعلي {fineness_text(p['fineness'])} • {p['document']}")}
                                 for p in d.get("external_holdings", [])],
            "logoHoldSources": [{"karatId": str(g["karat_id"]), "text": f"عيار {g['karat_value']} — المتاح للتسليم {grams(g['outstanding'])} جرام"}
                                for g in gold if g["designation"] == "LOGO" and g["outstanding"] > 0],
            "logoHoldAvailable": is_open and any(p["active"] for p in self._context.get("intermediaries", []))
                                 and any(g["designation"] == "LOGO" and g["outstanding"] > 0 for g in gold),
            "externalNote": CLOSURE_NOTE if d.get("external_holdings") else "",
            "cash": {"issued": money(custody["cash"]["issued"]), "returned": money(custody["cash"]["returned"]),
                     "paid": money(custody["cash"]["paid"]), "hasPaid": custody["cash"]["paid"] != 0,
                     "outstanding": money(custody["cash"]["outstanding"]), "zero": custody["cash"]["outstanding"] == 0,
                     "outstandingValue": f"{custody['cash']['outstanding']:.2f}"},
            "gold": [{"karat": g["karat_value"], "karatId": str(g["karat_id"]), "designation": g["designation"],
                      "designationText": DESIGNATION_TEXT[g["designation"]], "label": _gold_label(g), "issued": grams(g["issued"]),
                      "returned": grams(g["returned"]), "melted": grams(g["melted"]), "outstanding": grams(g["outstanding"]),
                      "heated": grams(g["heat_delta"], signed=True) if g["designation"] == "LOGO" and g.get("heat_delta") else "",
                      "settled": grams(g["supplier_settled"]) if g.get("supplier_settled") else "",
                      "held": grams(g["held_out"] - g["held_back"]) if g.get("held_out") or g.get("held_back") else "",
                      "zero": g["outstanding"] == 0} for g in gold],
            "lots": [{"lotId": l["lot_id"], "title": f"{LOT_TEXT[l['kind']]} — الحركة {l['entry_no']}"
                      + (f" • {l['label']}" if l["label"] else "")
                      + (f" • أُعيد إخراجها من المحل (سبكة {l['code']})" if str(l.get("origin_trip_id") or "") not in ("", trip["id"]) else ""),
                      "weight": grams(l["weight"]), "fineness": fineness_text(l["fineness"]),
                      "pure": UNKNOWN_ASSAY if l["pure"] is None else number(l["pure"], 6), "outstanding": grams(l["outstanding"])}
                     for l in custody["lots"]],
            "returnableLots": [{"lotId": l["lot_id"], "ref": f"{l['code'] or ''} • شحنة {l['lot_no']}",
                                "kindText": LOT_TEXT[l["kind"]] + (f" — {l['label']}" if l["label"] else ""),
                                "fineness": fineness_text(l["fineness"]), "weight": grams(l["weight"]), "available": grams(l["outstanding"]),
                                "returnable": l["outstanding"] == l["weight"],
                                "reason": "" if l["outstanding"] == l["weight"] else
                                f"رُجع منها جزء سابقًا (المتبقي {grams(l['outstanding'])} من {grams(l['weight'])} جرام): اعكس الرجوع الجزئي أولًا."}
                               for l in custody["lots"]],
            "lotReturnAvailable": is_open and bool(custody["lots"]),
            "lotNote": LOT_NOTE if custody["lots"] else "",
            "meltStock": melt_stock, "meltAvailable": is_open and bool(melt_stock), "returnLines": return_lines,
            "heatStock": heat_stock, "heatAvailable": is_open and bool(heat_stock),
            "goldSettleSources": settle_sources, "goldSettleAvailable": is_open and bool(settle_sources),
            "payAvailable": is_open and custody["cash"]["outstanding"] > 0,
            "outstandingText": outstanding_text(custody),
            "entries": [self._entry(e, is_open) for e in d["entries"]],
            "melts": [self._melt(m) for m in d.get("melts", [])],
            "heats": [self._heat(h) for h in d.get("heats", [])],
            "heatTotals": [{"text": f"عيار {t['karat_value']}: قبل {grams(t['input'])} • بعد {grams(t['output'])} • الفرق {grams(t['difference'], signed=True)} جرام"}
                           for t in d.get("heat_totals", [])],
            "events": [{"text": {"OPENED": "فتح العهدة", "CLOSED": "إغلاق العهدة", "REOPENED": "إعادة فتح العهدة"}[e["type"]],
                        "reason": e["reason"], "time": datetime_text(e["at"]), "actor": e["actor"]} for e in d["events"]]}

    @staticmethod
    def _entry(e, trip_open):
        parts = []
        if e["cash"] is not None:
            parts.append(f"نقدية {money(e['cash'])}")
        parts += [f"عيار {g['karat_value']} ({DESIGNATION_TEXT[g['designation']]}): {grams(g['weight'])} جرام" for g in e["gold"]]
        parts += [f"شحنة {l['lot_no']} من {l['code']}: {grams(l['weight'])} جرام (العيار {fineness_text(l['fineness'])})"
                  for l in e.get("lot_returns", []) + e.get("lot_issues", [])]
        settlement = e.get("gold_settlement")
        if settlement:
            settlement_text = (f"{settlement['code']} — المورد {settlement['supplier_name']}: سُلِّم {grams(settlement['delivered_weight'])} جرام عيار "
                               f"{settlement['karat_value']} ({DESIGNATION_TEXT[settlement['designation']]}) ← احتُسب {grams(settlement['credited_weight'])} جرام "
                               f"عيار {settlement['target_karat']} • القيمة التحليلية {number(settlement['analytical'], 6)} • فرق التقريب "
                               f"{number(settlement['rounding_difference'], 6, signed=True)}")
        logo_holding = e.get("logo_holding")
        if logo_holding:
            parts.append(f"{logo_holding['code']} — {logo_holding['party_name']} • لوجو عيار {logo_holding['karat_value']}: {grams(logo_holding['weight'])} جرام")
        holding = e.get("holding")
        if holding:
            parts.append(f"{holding['code']} — {holding['party_name']} • {LOT_TEXT[holding['lot_kind']]} • {holding['origin']} • شحنة {holding['lot_no']}: "
                         f"{grams(holding['weight'])} جرام • العيار الفعلي {fineness_text(holding['fineness'])}")
        lot_settlement = e.get("lot_settlement")
        if lot_settlement:
            parts.append(f"{lot_settlement['settlement_code']} — المورد {lot_settlement['supplier_name']} • "
                         f"{LOT_TEXT[lot_settlement['lot_kind']]} • {lot_settlement['origin']} • شحنة {lot_settlement['lot_no']}: "
                         f"{lot_settlement['delivered_weight']} جرام • العيار الفعلي {lot_settlement['fineness']} بالألف "
                         f"← احتُسب {lot_settlement['credited_weight']} جرام عيار {lot_settlement['target_karat']} • "
                         f"القيمة التحليلية {lot_settlement['analytical']} • فرق التقريب {lot_settlement['rounding_difference']}")
        return {"id": e["id"], "code": e["code"], "kind": e["kind"], "kindText": KIND_TEXT[e["kind"]],
                "what": (settlement_text if settlement else
                         f"{e['supplier_payment']['code']} — المورد {e['supplier_payment']['supplier_name']}: {money(e['supplier_payment']['amount'])}"
                         if e.get("supplier_payment") else " • ".join(parts)) if settlement or parts or e.get("supplier_payment") else ("انظر تفاصيل السبك أدناه" if e["kind"] == "MELT"
                                                     else "انظر تفاصيل الحمّية أدناه" if e["kind"] == "HEAT" else ""),
                "settlementCode": settlement["code"] if settlement else lot_settlement["settlement_code"] if lot_settlement else "",
                "date": date_text(e["date"]), "time": datetime_text(e["at"]), "actor": e["actor"],
                "notes": e["notes"], "reversed": e["reversed"],
                "state": f"معكوسة بالعملية {e['reversal_code']}" if e["reversed"] else "",
                "reverseNote": HOLD_REVERSE_NOTE if e["kind"] == "LOT_HOLD" else RETURN_REVERSE_NOTE if e["kind"] == "HOLD_RETURN" else "عكس التسوية يعيد الشحنة الأصلية كاملة إلى نفس العهدة ويعيد رصيد المورد بالكمية المخزّنة؛ لا حركة بالمحل." if e["kind"] == "LOT_SETTLE" else PAYMENT_REVERSAL_NOTE if e["kind"] == "SUPPLIER_PAY" else GOLD_SETTLE_REVERSAL_NOTE if e["kind"] == "GOLD_SETTLE" else "",
                "reason": e["reversal_reason"] or "", "canReverse": trip_open and not e["reversed"]}

    @staticmethod
    def _heat(h):
        """One heating document: the raw measurements per karat and their permanent difference (kept after reversal)."""
        return {"id": h["entry_id"], "code": h["code"], "date": date_text(h["date"]), "workshop": h["workshop"], "reference": h["reference"],
                "gainExplanation": h["gain_explanation"], "notes": h["notes"], "actor": h["actor"],
                "lines": [{"text": f"عيار {l['karat_value']}: قبل الحمّية {grams(l['input'])} • بعدها {grams(l['output'])} • "
                                   f"الفرق {grams(l['difference'], signed=True)} جرام"} for l in h["lines"]],
                "total": f"الفرق الإجمالي {grams(h['total_difference'], signed=True)} جرام (قبل {grams(h['total_input'])} • بعد {grams(h['total_output'])})",
                "reversed": h["reversed"], "state": f"معكوسة بالعملية {h['reversal_code']}" if h["reversed"] else "",
                "notice": REVERSED_NOTICE if h["reversed"] else "", "reason": h["reversal_reason"] or ""}

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
