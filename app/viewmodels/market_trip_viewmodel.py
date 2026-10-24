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
from services import settlement_rules, supplier_rules as rules
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
KIND_TEXT = {"ST_MELT": "تسجيل سبك (بعد الرجوع)", "ST_BAR_SUP": "تسليم سبيكة لمورد (بعد الرجوع)", "ST_BAR_MKT": "تسليم سبيكة لمسوقجي (بعد الرجوع)", "ST_LOGO": "اعتبار المتبقي لوجو", "ST_EXPENSE": "مصروف من العهدة", "ST_RET_MKT": "مرتجع سوق للمحل", "ST_LOSS": "خسية", "ST_ADJUST": "تصحيح وزن الإخراج", "ST_BAR_FEE": "أجر سبيكة", "ST_CASH_BANK": "إيداع باقي النقدية في حساب", "ST_CASH_OFF": "إعدام باقي النقدية","ISSUE": "إخراج عهدة", "RETURN": "رجوع من العهدة", "MELT": "سبك", "LOT_RETURN": "رجوع ناتج السبك", "LOT_ISSUE": "إخراج سبكة من المحل",
             "GOLD_BUY": "شراء ذهب نقدي", "GOLD_SALE": "بيع ذهب من العهدة", "HEAT": "حمّية اللوجو", "SUPPLIER_PAY": "دفع مصنعيات من العهدة", "GOLD_SETTLE": "تسوية ذهب من العهدة", "LOT_SETTLE": "تسوية سبكة من العهدة",
             "LOT_HOLD": "تسليم ذهب لمسوقجي", "HOLD_RETURN": "رجوع سبكة من مسوقجي", "LOGO_HOLD": "تسليم لوجو لمسوقجي", "HOLD_BACK": "رجوع لوجو من مسوقجي",
             "BK_ISSUE": "إخراج ذهب (دفتري)", "BK_BUY": "شراء ذهب (دفتري)", "BK_SELL": "بيع ذهب (دفتري)", "BK_RETURN": "رجوع ذهب للمحل (دفتري)", "BK_RECON": "تسوية العهدة الدفترية",
             "BK_ISS_STK": "إخراج مخزون مرتجع إلى العهدة (دفتري)", "BK_DLV_SUP": "تسليم ذهب لمورد (دفتري)", "BK_DLV_MKT": "تسليم ذهب لمسوقجي (دفتري)",
             "BK_RECEIVE": "استلام ذهب من مسوقجي (دفتري)", "BK_GAIN": "زيادة مثبتة (دفتري)"}
BOOK_NOTE = "الرصيد الدفتري ليس مخزونًا فعليًا: هو رصيد محاسبي بمكافئ عيار 21 يُحسب من وقائع كل سطر. فرق التقريب ليس خسارة ولا زيادة."
BOOK_CLOSE_HINT = "لا تُغلق العهدة الدفترية إلا بعد اعتماد تسوية رصيدها الدفتري (مطابقة العهدة)، ونقدية العهدة صفر."
BOOK_CLOSE_READY = "التسوية معتمدة ونقدية العهدة صفر: يمكن الإغلاق، ويُعاد التحقق من سجل التسوية ومطابقته للمستندات عند الضغط."
BOOK_REVERSE_NOTES_PHASE2 = {
    "BK_GAIN": "عكس الزيادة المثبتة يُخرجها من مدخلات الرصيد الدفتري؛ يُمنع إن استُهلك ذهبها بعد تسجيلها أو كانت للعهدة تسوية معتمدة. يبقى المستند وشرحه في السجل.",
    "BK_RECON": "عكس التسوية يعيد للرصيد الدفتري مفتوحًا ما سوّته (الفاقد المرحّل وفرق التقريب) ويسمح بتصحيح المستندات ثم اعتماد تسوية جديدة؛ العهدة المغلقة تُعاد فتحها أولًا. يبقى مستند التسوية الأصلي ظاهرًا كمعكوس.",
    "BK_ISS_STK": "عكس الإخراج يعيد الأوزان والحصة المرحَّلة المخزَّنة إلى بند المخزون المرتجع نفسه ويُنقص الرصيد الدفتري؛ يُمنع إن استُخدم الذهب بعده.",
    "BK_DLV_SUP": "عكس التسليم يعيد للمورد الكمية المحتسبة المخزَّنة بعياره ويعيد المكافئ المرحَّل إلى الرصيد الدفتري؛ لا حركة خزنة أو كسر.",
    "BK_DLV_MKT": "عكس التسليم يُخرج المكافئ المخزَّن من استحقاق المسوقجي ويعيده للرصيد الدفتري؛ يُمنع إن لم يعد المسوقجي يحمله.",
    "BK_RECEIVE": "عكس الاستلام يعيد المكافئ المخزَّن إلى استحقاق المسوقجي ويُنقصه من الرصيد الدفتري؛ يُمنع إن استُخدم الذهب بعده."}
RECON_REASON_TEXT = {"OPERATING_LOSS": "فاقد تشغيل (سبك/تنظيف)", "PURITY_DIFFERENCE": "فرق نقاوة", "TRADING_LOSS": "فاقد تداول", "OTHER": "أخرى", "UNEXPLAINED": "غير مفسَّر"}
BOOK_REVERSE_NOTES = {"BK_ISSUE": "عكس الإخراج يعيد أوزان الكسر المخزَّنة كما خرجت ويُنقص الرصيد الدفتري بالمكافئ المرحَّل المخزَّن؛ يُمنع إن استُخدم الذهب بعده.",
                      "BK_BUY": "عكس الشراء يعيد الثمن المخزَّن إلى نقدية العهدة ويُنقص الرصيد الدفتري بالمكافئ المرحَّل المخزَّن؛ يُمنع إن استُخدم الذهب بعده.",
                      "BK_SELL": "عكس البيع يُخرج الثمن المخزَّن من نقدية العهدة ويعيد المكافئ المرحَّل المخزَّن إلى الرصيد الدفتري؛ يُمنع إن استُخدم الثمن بعده.",
                      "BK_RETURN": "عكس الرجوع يُخرج الأوزان المخزَّنة من كسر المحل أو يُلغي بند المخزون المرتجع بالعيار الفعلي، ويعيد المكافئ المرحَّل إلى الرصيد الدفتري؛ يُمنع إن استُخدم الذهب بعده."}
HOLD_REVERSE_NOTE = ("عكس التسليم يعيد الشحنة الأصلية كاملة إلى هذه العهدة ويسجّل حركة تعويض بالوزن المخزّن في ذهب المحل لدى المسوقجي؛ "
                     "تبقى حركة التسليم الأصلية في السجل. لا نقدية ولا كسر ولا مورد.")
RETURN_REVERSE_NOTE = ("عكس الرجوع يُخرج الشحنة من هذه العهدة ويعيدها إلى ذهب المحل لدى المسوقجي بالوزن المخزّن بحركة تعويض؛ "
                       "تبقى حركة الرجوع الأصلية وتأكيد المالك في السجل.")
DESIGNATION_TEXT = {"MELT": "للسبك", "LOGO": "لوجو", "UNCLASSIFIED": "بدون وجهة", "BAR": "سبيكة (مكافئ عيار 21)"}
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


RECON_LOSS_NOTE = "تقدير مشروط باكتمال تسجيل الحركات واعتماد المالك — لا يُسجَّل شيء الآن."
RECON_ROUNDING_NOTE = "ليس فاقدًا ولا زيادة."
RECON_PREVIEW_NOTE = "معاينة فقط — لم يقع شيء ولا يُطبَّق على الرصيد."
RECON_A_NOTES = {"zero": "لا رصيد تحليلي غير مسوّى.",
                 "positive_below_posting_precision": "رصيد موجب أقل من دقة الترحيل (0.001 جرام): لن يُرحَّل فاقدًا ويُحفظ قيمةً تحليلية.",
                 "positive": "رصيد موجب غير مسوّى: قد يكون ذهبًا لم يُسجَّل رجوعه أو تسليمه بعد.",
                 "negative_integrity": "الرصيد التحليلي سالب: اختلال في السجل. لا يُقترح فاقد ولا تُسجَّل زيادة لتغطيته — راجع المستندات.",
                 "partial": "أرقام جزئية: استُبعد {n} عنصرًا لتعذّر فحصه أو لعدم صلاحية بياناته؛ لا تمثل الرصيد الكامل ولا يُقترح فاقد.",
                 "unavailable": "الرصيد غير متاح: تعذّرت قراءة بيانات المستندات أو الأسطر. هذا ليس رصيدًا صفرًا ولا يُقترح فاقد."}
RECON_UNAVAILABLE_TEXT = "غير متاح"
RECON_SOUND = "لم تُكتشف مشكلة ضمن الفحوص المتاحة — وهذا ليس ضمانًا لسلامة مالية شاملة."
RECON_INCOMPLETE = "المعاينة غير مكتملة لتعذّر قراءة جزء لازم: لا تُعرض تقديرات موثوقة."
RECON_UNTRUSTED = "المعاينة غير موثوقة: توجد ملاحظات تحتاج مراجعة قبل الاعتماد على هذه الأرقام."


def reconciliation_view(r):
    """The display form of the service's reconciliation preview (read only). Nothing here computes anything: every figure is formatted from what the service
    returned; the exact internals (token, exact fraction) never reach the screen."""
    if not r:
        return {}
    if r.get("error"):
        return {"tripId": r["tripId"], "error": r["error"]}
    unit = " جرام مكافئ عيار 21"
    status_of_balance = r.get("balanceStatus", "full")
    has_figures = status_of_balance != "unavailable" and r["a"] is not None
    a = Decimal(r["a"]) if has_figures else None
    b = Decimal(r["b"]) if has_figures else None
    notes = [RECON_A_NOTES[r["aState"]].replace("{n}", str(r.get("excludedCount", 0)))]
    if r["aPositiveBelowDisplay"]:
        notes.append("قيمة موجبة أقل من دقة العرض (ستة منازل): تُحفظ ولا تُعدّ صفرًا.")
    effect = r["effect"]
    if not r["complete"]:
        status = RECON_INCOMPLETE
    elif not r["trustworthy"]:
        status = RECON_UNTRUSTED
    else:
        status = RECON_SOUND
    effect_text = ""
    if effect is not None:
        effect_text = (RECON_PREVIEW_NOTE + f" أثر الاعتماد النظري على المركز المرحّل {grams(Decimal(effect['position']), signed=True)}"
                       f" (الفاقد {grams(Decimal(effect['loss_part']), signed=True)}، فرق التقريب {grams(Decimal(effect['rounding_part']), signed=True)}).")
    delta = "—" if r["delta"] is None else number(Decimal(r["delta"]), 6, signed=True)
    partial = " (جزئي)" if status_of_balance == "partial" else ""
    details = "—" if not has_figures else (f"فرق الترحيل ρ: {number(Decimal(r['rho']), 6, signed=True)} • δ: {delta}"
                                           f" • مجموع فروق التقريب: {number(Decimal(r['sumAbs']), 6)} • الحد: {number(Decimal(r['bound']), 6)}")
    return {
        "tripId": r["tripId"], "aState": r["aState"], "balanceStatus": status_of_balance,
        "aText": RECON_UNAVAILABLE_TEXT if not has_figures else number(a, 6) + unit + partial, "aNote": " ".join(notes),
        "bText": RECON_UNAVAILABLE_TEXT if not has_figures else grams(b, signed=b < 0) + unit + partial,
        "lossAvailable": r["proposedLoss"] is not None,
        "lossText": "—" if r["proposedLoss"] is None else grams(Decimal(r["proposedLoss"])) + unit,
        "lossNote": RECON_LOSS_NOTE if r["proposedLoss"] is not None else "لا يُعرض تقدير للفاقد في هذه الحالة.",
        "roundingText": "—" if r["roundingSettlement"] is None else grams(Decimal(r["roundingSettlement"]), signed=True) + unit,
        "roundingNote": RECON_ROUNDING_NOTE,
        "effectText": effect_text,
        "detailsText": details,
        "countsText": "" if not has_figures else f"{r['entryCount']} مستند ({r['reversedCount']} معكوس) • {r['lineCount']} سطر فعّال",
        "cashText": "" if r["cashOutstanding"] is None else f"نقدية العهدة المتبقية {money(Decimal(r['cashOutstanding']))} (معلومة فقط)",
        "statusText": status, "complete": r["complete"], "trustworthy": r["trustworthy"],
        "diagnostics": [{"code": d["code"], "category": d["category"], "text": d["message"], "entryCode": d["entryCode"]} for d in r["diagnostics"]],
        "coverage": [c["message"] for c in r["coverage"]],
        "closureNote": BOOK_CLOSE_HINT,
    }


def report_view(r):
    """The display form of `MarketTripBookService.book_report`: nothing is computed here, every figure is formatted from the service answer. The totals are PROVISIONAL
    (effective documents only, by effective date): how a reversal is attributed to a period is an open owner decision and is not presented as final."""
    def when(value):
        return datetime_text(value) if value else ""
    reconciliations = [{"code": x["code"], "tripCode": x["trip_code"], "date": date_text(x["effective_date"]), "recorded": when(x["recorded_at"]),
                        "text": (f"تسوية رقم {x['generation']} • الرصيد التحليلي غير المسوّى {number(x['analytical'], 6)} • الرصيد المرحّل {grams(x['posted'], signed=x['posted'] < 0)} • "
                                 f"الفاقد المرحّل {grams(x['loss'])} • فرق تقريب الترحيل {grams(x['rounding'], signed=True)} (ليس فاقدًا ولا زيادة) • السبب: "
                                 f"{x['reason'] or 'بدون سبب'}" + (f" • {x['note']}" if x["note"] else "") + (f" • اعتمدها {x['confirmed_by']}" if x["confirmed_by"] else "")),
                        "reversed": x["reversed"],
                        "state": f"معكوسة • {x['reversal_reason']} • {when(x['reversal_at'])}" if x["reversed"] else ""} for x in r["reconciliations"]]
    gains = [{"code": x["code"], "tripCode": x["trip_code"], "date": date_text(x["effective_date"]), "recorded": when(x["recorded_at"]),
              "text": (f"{grams(x['weight'])} جرام {x['what']} ← مكافئ عيار 21 مرحَّل {grams(x['posted'])} • الشرح: {x['explanation']} • الدليل: {x['evidence_reference']}"
                       + (f" • أكّدها {x['confirmed_by']}" if x["confirmed_by"] else "")),
              "reversed": x["reversed"],
              "state": f"معكوسة • {x['reversal_reason']} • {when(x['reversal_at'])}" if x["reversed"] else ""} for x in r["gains"]]
    t = r["provisional_totals"]
    totals = (f"إجمالي مبدئي (غير نهائي — المستندات الفعّالة فقط حسب التاريخ الفعلي): فاقد مرحّل {grams(t['loss_posted'])} • فرق تقريب الترحيل {grams(t['rounding_settlement'], signed=True)} "
              f"• زيادة مثبتة {grams(t['gain_posted'])} جرام مكافئ عيار 21. الفاقد والتقريب والزيادة أرقام منفصلة لا يُصافَى بعضها ببعض. "
              f"المستندات المعكوسة ({t['reversed_listed']}) تظهر في التفصيل ولا تدخل هذا المجموع؛ وإسناد العكس إلى الفترات قرار لم يُحسم بعد.")
    return {"from": r["from"], "to": r["to"], "reconciliations": reconciliations, "gains": gains, "totalsText": totals,
            "empty": not reconciliations and not gains, "error": ""}


TAB_STATUS = {"ACTIVE": "OPEN", "ARCHIVE": "CLOSED"}      # the active tab shows OPEN trips, the archive tab CLOSED ones (the existing status, nothing else)
TRIP_PAGE_SIZE = 50


class MarketTripViewModel(QObject):
    changed = Signal()
    entryPosted = Signal(str)            # the entry code: the entry dialog can close
    actionDone = Signal(str)             # close / reopen / reversal finished (the dialogs can close)
    vendorRegistered = Signal(str)       # a typed vendor was registered as a new party: the purchase dialog selects it
    supplierPaymentChanged = Signal()    # a supplier payment OR gold settlement from custody was posted or reversed: the supplier views reload from the database
    custodyChanged = Signal()            # cash / Scrap really moved: every balance view reloads from the database

    def __init__(self, session, service=None, parent=None, holdings=None):
        super().__init__(parent)
        self._session, self._service, self._holdings = session, service, holdings
        self._workers, self._request, self._latest, self._busy = set(), 0, {}, set()
        self._shutdown = False
        self._trips, self._detail, self._context, self._shop_lots = [], {}, {}, []
        self._recon = {}                                                 # the read-only reconciliation preview of the SELECTED BOOK trip (R1)
        self._recon_for = ""                                             # the trip the in-flight / shown preview was asked for: an answer for any other trip is dropped
        self._tab, self._page, self._has_more = "ACTIVE", 1, False          # which list is shown, its page and whether a next page exists
        self._reference_prices = {}                                      # SHOP_BUY per gram by karat: loaded when the sale dialog opens
        self._selected, self._error, self._message = "", "", ""
        self._reversing_payment = False
        self._last_code = ""
        self._approval, self._last_reconciliation = {}, {}
        self._report = {}                                                # the read-only gain / reconciliation report (059): replaced by each answer, cleared with the session
        self._session.changed.connect(self._approval_session_changed)

    def _approval_session_changed(self):
        self._cancel("approval")
        self._cancel("report")
        self._approval, self._last_reconciliation, self._report = {}, {}, {}
        self.changed.emit()

    # ------------------------------------------------------------ plumbing
    def _service_instance(self):
        if self._service is None:
            from services.market_trip_book_service import MarketTripBookService          # the legacy service plus the BOOK operations (BOOK creation stays off)
            self._service = MarketTripBookService(self._session)
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
        self._last_code = code or ""                                    # the service's error code (e.g. PRICE_DEVIATION) for the write that just failed
        getattr(self, f"_on_{operation}")(result, error)
        self.changed.emit()

    @Slot()
    def shutdown(self):
        self._shutdown = True
        for worker in list(self._workers):
            worker.wait(5000)

    # ------------------------------------------------------------ reads
    @Slot()
    @Slot(str)
    def refresh(self, _reason=""):
        """The shown page of trips, the selected trip's detail and the entry-dialog context: always from the database."""
        if not self.canManage:
            return
        service = self._service_instance()
        self._load_list()
        self._start("context", lambda _expected_actor: service.context(_expected_actor=_expected_actor))
        self._start("shoplots", lambda _expected_actor: service.shop_lots(_expected_actor=_expected_actor))
        if self._selected:
            self._load_detail()

    def _load_list(self):
        """Ask for the CURRENT tab / page. The request carries both, so an answer that no longer matches (the user switched tab or page meanwhile) is ignored."""
        service, status, page = self._service_instance(), TAB_STATUS[self._tab], self._page
        self._start("list", lambda _expected_actor: service.trip_page(status, page, TRIP_PAGE_SIZE, _expected_actor=_expected_actor))

    def _on_list(self, result, error):
        if error:
            self._error = error
            return
        if result["status"] != TAB_STATUS[self._tab] or result["page"] != self._page:
            return                                                           # a late answer for another tab / page: never shown
        if not result["rows"] and self._page > 1:
            self._page -= 1                                                  # the last row of the last page moved away (closed / reopened): go back one page
            self._load_list()
            return
        self._trips, self._has_more, self._error = result["rows"], bool(result["has_more"]), ""
        # A selected trip that is not in this list is NOT cleared: it may live in the other tab or on another page. Only a missing trip clears it (_on_detail).

    @Slot(str)
    def showTripTab(self, tab):
        """Switch between «النشطة» (OPEN) and «الأرشيف» (CLOSED). The selection belongs to the tab it was made in, so it is cleared; page 1 is shown."""
        tab = "ARCHIVE" if tab == "ARCHIVE" else "ACTIVE"
        if tab == self._tab:
            return
        self._tab, self._page, self._has_more = tab, 1, False
        self._trips, self._selected, self._detail = [], "", {}
        self._drop_recon()
        self._error = self._message = ""
        self.changed.emit()
        if self.canManage:
            self._load_list()

    @Slot()
    def nextTripPage(self):
        if self._has_more and self.canManage:
            self._page += 1
            self._trips, self._has_more = [], False
            self.changed.emit()
            self._load_list()

    @Slot()
    def previousTripPage(self):
        if self._page > 1 and self.canManage:
            self._page -= 1
            self._trips, self._has_more = [], False
            self.changed.emit()
            self._load_list()

    @Slot()
    def loadReferencePrices(self):
        """The sale dialog's price reference (a read-only price-set lookup, loaded when the dialog opens, never with every refresh)."""
        service = self._service_instance()
        self._start("refprices", lambda _expected_actor: service.reference_prices(_expected_actor=_expected_actor))

    def _on_refprices(self, result, error):
        self._reference_prices = {} if error else dict(result or {})

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
        self._last_reconciliation = {}
        self._drop_recon()                                                                      # nothing of the previous trip stays on screen, and its answer can no longer arrive
        self._error = self._message = ""
        self.changed.emit()
        if self._selected:
            self._load_detail()

    def _cancel(self, operation):
        """Invalidate the in-flight request of `operation` NOW: `_completed` ignores its answer (success or failure) and the busy mark is released here, so an ignored
        request can neither be shown nor leave the view model busy."""
        self._latest[operation] = None
        self._busy.discard(operation)

    def _drop_recon(self, error=""):
        """The preview on screen is no longer current (selection, tab or refresh changed): cancel its request and clear it; `error` shows a plain failure state instead."""
        self._cancel("recon")
        self._cancel("approval")
        self._approval = {}
        self._recon_for = ""
        self._recon = {"tripId": self._selected, "error": error} if error and self._selected else {}

    def _load_detail(self):
        self._drop_recon()                                                                      # a refresh starts: the previous preview is not presented as the latest one
        service, trip_id = self._service_instance(), self._selected
        self._start("detail", lambda _expected_actor: service.trip_detail(trip_id, _expected_actor=_expected_actor))

    def _on_detail(self, result, error):
        if error:
            self._error = error
            if self._last_code == "TRIP_NOT_FOUND":                         # only a trip that no longer exists clears the selection
                self._selected, self._detail = "", {}
                self._drop_recon()
            elif self._selected:
                self._drop_recon("تعذّر تحديث تفاصيل العهدة: " + error)      # no old suggestion stays as a trusted result; a later success restores the normal card
            return
        if result["trip"]["id"] == self._selected:
            self._detail, self._error = result, ""
            if result.get("mode") == "BOOK":
                self._load_reconciliation()                                 # every existing refresh path (write, reversal, custodyChanged) reloads the detail, hence the preview
            else:
                self._drop_recon()                                          # LEGACY keeps its screen: no reconciliation card

    def _load_reconciliation(self):
        service, trip_id = self._service_instance(), self._selected
        self._recon_for = trip_id
        self._start("recon", lambda _expected_actor: service.reconciliation_preview(trip_id, _expected_actor=_expected_actor))

    def _on_recon(self, result, error):
        if not self._selected or self._recon_for != self._selected:       # belt and braces: the request identity is the trip it was asked for
            return
        if error:
            self._recon = {"tripId": self._recon_for, "error": "تعذّر تحميل مطابقة العهدة: " + error}
            return
        if result.get("tripId") == self._recon_for:                         # an answer carrying another trip is never shown
            self._recon = result

    @Slot(str, str)
    def loadReport(self, date_from, date_to):
        """The detail report of reconciliations and evidenced gains by effective date (read only). A newer request replaces an older one; a stale answer is dropped."""
        service = self._service_instance()
        self._report = {"loading": True}
        self._start("report", lambda _expected_actor: service.book_report(date_from, date_to, _expected_actor=_expected_actor))

    def _on_report(self, result, error):
        self._report = {"error": "تعذّر تحميل التقرير: " + error} if error else report_view(result)

    @Property("QVariantMap", notify=changed)
    def report(self):
        return {**self._report, "loading": "report" in self._busy}

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
            if self._operation == "reconcile" and self._last_code in ("RECONCILIATION_STALE", "RECONCILIATION_NOT_READY", "RECONCILIATION_EXISTS", "TRIP_CLOSED"):
                self._approval = {"tripId": self._selected, "error": error}
            self._error = error                                        # the dialog stays open with every field kept
            return
        if self._operation == "reconcile":
            self._last_reconciliation = dict(result)
        text = self._done_text
        if isinstance(result, dict) and result.get("code"):
            reference = result.get("payment_code") or result.get("settlement_code") or result.get("document_code")
            text = f"{text} {result['code']}" + (f" ({reference})" if reference else "")
            if result.get("duplicate"):
                text = f"{self._done_text} {result['code']} (مسجلة من قبل)"
        self._message = text
        if isinstance(result, dict) and result.get("trip_id"):
            if self._operation == "create":
                # A NEW custody (createCustody only) is OPEN: show it where it lives - Active, page 1, selected - whatever tab, page or trip was shown.
                # The refresh below issues newer list / detail requests, so a late answer for the previous view is dropped by the request ids.
                self._tab, self._page, self._has_more, self._trips = "ACTIVE", 1, False, []
                self._selected, self._detail = result["trip_id"], {}
            elif not self._selected:
                self._selected = result["trip_id"]                          # an operation on an existing trip never navigates
        self.refresh()
        # Notified only after the transaction succeeded; a failing refresh belongs to the view that failed, never to this write.
        if self._moved:
            self.custodyChanged.emit()
        if self._operation in ("paysupplier", "goldsettle", "lotsettle", "lothold", "logohold", "bookdeliversupplier", "bookdelivermarketer",
                               "bookreceivemarketer", "barmarketer") or (self._operation == "reverse" and self._reversing_payment):
            self.supplierPaymentChanged.emit()
        self._reversing_payment = False
        if self._operation in ("issue", "create", "return", "melt", "settlemelt", "barmarketer", "classifylogo", "cashsettle", "dispose", "lotreturn", "lotissue", "heat", "paysupplier", "goldsettle", "lotsettle", "lothold", "logohold",
                               "goldsale", "goldbuy", "bookissue", "bookbuy", "booksell", "bookreturn",
                               "bookissuestock", "bookdeliversupplier", "bookdelivermarketer", "bookreceivemarketer", "bookgain"):
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
    def settleMelt(self, trip_id, input_lines, output_weight, fineness, workshop, entry_date, notes, request_key):
        """Record a melt AFTER the owner returns (settlement step 2a): gold that left without a destination becomes ONE bar of its 21K equivalent. The bar's weight and
        integer fineness are kept for the reports; the loss is split in weight loss and fineness loss. No cash, Scrap, supplier or lot moves."""
        lines = [{"karat_id": str(line.get("karatId", "")), "weight": str(line.get("weight", ""))} for line in (input_lines or [])]
        self._write("settlemelt", "تم تسجيل السبك", lambda s, a: s.settle_melt(
            trip_id, lines, output_weight, fineness, workshop, entry_date, notes, request_key, _expected_actor=a), False)

    @Slot(str, str, str, str, str, str, str, str)
    def disposeGold(self, trip_id, action, source_line, entry_date, notes, request_key, unused_a, unused_b):
        """Return to the shop or record a loss. `source_line` is "SOURCE|karatId|grams"; `action` is RETURN, CRUCIBLE or MARKET (the loss classification)."""
        source, karat_id, weight = (source_line.split("|") + ["", "", ""])[:3]
        line = {"source": source, "karat_id": karat_id, "weight": weight, "classification": "" if action == "RETURN" else action}
        if action == "RETURN":
            self._write("dispose", "تم إرجاع الذهب للمحل", lambda s, a: s.return_to_shop(trip_id, [line], entry_date, notes, request_key, _expected_actor=a), False)
        else:
            self._write("dispose", "تم تسجيل الخسية", lambda s, a: s.record_loss(trip_id, [line], entry_date, notes, request_key, _expected_actor=a), False)

    @Slot(str, str, str, str, str, str, str)
    def correctIssueWeight(self, trip_id, karat_id, direction, weight, reason, entry_date, request_key):
        """Correct the issued weight of gold issued without a destination, both ways; the reason is mandatory (the service enforces it)."""
        self._write("dispose", "تم تصحيح وزن الإخراج", lambda s, a: s.correct_issue_weight(
            trip_id, karat_id, direction, weight, reason, entry_date, request_key, _expected_actor=a), False)

    @Slot(str, str, str, str, str, str, str, str)
    def settleCash(self, trip_id, kind, amount, text, bank_account_id, entry_date, notes, request_key):
        """Settle custody cash after the trip: EXPENSE (free-text), BANK (deposit into an account) or WRITEOFF (reason). Only ids and typed text are sent."""
        if kind == "BANK":
            self._write("cashsettle", "تم إيداع باقي النقدية", lambda s, a: s.deposit_custody_cash(
                trip_id, amount, bank_account_id, entry_date, text, notes, request_key, _expected_actor=a), False)
        elif kind == "WRITEOFF":
            self._write("cashsettle", "تم إعدام المبلغ من نقدية العهدة", lambda s, a: s.write_off_custody_cash(
                trip_id, amount, text, entry_date, notes, request_key, _expected_actor=a), False)
        else:
            self._write("cashsettle", "تم تسجيل المصروف من العهدة", lambda s, a: s.record_custody_expense(
                trip_id, amount, text, entry_date, "", notes, request_key, _expected_actor=a), False)

    @Slot(str, str, str, str, str, str, str)
    def recordBarFee(self, trip_id, party_id, amount, entry_date, reference, notes, request_key):
        """Record the cash fee a supplier / marketer paid for a bar: it enters the custody cash (a supplier's making account is reduced by it). Only ids and typed text are sent."""
        self._write("barfee", "تم تسجيل أجر السبيكة", lambda s, a: s.bar_fee(
            trip_id, party_id, amount, entry_date, reference, notes, request_key, _expected_actor=a), False)

    @Slot(str, str, str, str)
    def classifyAsLogo(self, trip_id, entry_date, notes, request_key):
        """Classify EVERY remaining unmelted gram as LOGO of its own karat (the owner rule). The grams are derived by the service under the trip lock."""
        self._write("classifylogo", "تم اعتبار المتبقي لوجو", lambda s, a: s.classify_remaining_as_logo(trip_id, entry_date, notes, request_key, _expected_actor=a), False)

    @Slot(str, str, str, str, str, str)
    def settleBarToMarketer(self, trip_id, party_id, weight, entry_date, notes, request_key):
        """Hand part of the melted bar (21K-equivalent grams) to a marketer: his 21K entitlement gains the grams. Only ids and typed text are sent."""
        self._write("barmarketer", "تم تسليم السبيكة للمسوقجي", lambda s, a: s.settle_bar_to_marketer(
            trip_id, party_id, weight, entry_date, notes, request_key, _expected_actor=a), False)

    @Slot(str, str, result="QVariantMap")
    def previewBarToMarketer(self, party_id, weight):
        empty = {"valid": False, "error": "", "facts": ""}
        if not self._detail or not self._context or not str(party_id or "").strip():
            return empty
        party = next((p for p in self._context.get("intermediaries", []) if p["id"] == str(party_id)), None)
        if party is None:
            return {**empty, "error": "اختر المسوقجي."}
        pool = self._detail["custody"].get("bar", Decimal(0))
        facts = f"رصيد السبيكة في العهدة {grams(pool)} جرام (مكافئ عيار 21)"
        if not str(weight or "").strip():
            return {**empty, "facts": facts}
        from services.market_trip_service import parse_weight
        try:
            quantity = parse_weight(weight)
        except TripError as exc:
            return {**empty, "facts": facts, "error": str(exc)}
        if quantity == 0:
            return {**empty, "facts": facts, "error": "أدخل وزنًا موجبًا."}
        if not party["active"]:
            return {**empty, "facts": facts, "error": "هذا المسوقجي غير نشط: لا يُسلَّم له ذهب جديد."}
        if quantity > pool:
            return {**empty, "facts": facts, "error": f"رصيد السبيكة {grams(pool)} جرام فقط."}
        return {**empty, "valid": True, "facts": facts + f" • يُسلَّم {grams(quantity)} جرام ويصير استحقاقًا للمسوقجي من ذهب المحل بعيار 21"}

    @Slot("QVariantList", str, str, result="QVariantMap")
    def previewSettleMelt(self, input_lines, output_weight, fineness):
        """The exact figures of a melt BEFORE it is recorded, from services.settlement_rules (the function the service uses), never from QML arithmetic. The service
        recomputes and re-checks the custody when it posts."""
        empty = {"valid": False, "error": "", "short": False, "inputText": "", "theoreticalText": "", "actualText": "", "lossText": "", "weightLossText": "",
                 "finenessLossText": "", "gainText": ""}
        if not self._detail or not self._context:
            return empty
        values = {str(k["id"]): int(k["karat_value"]) for k in self._context.get("karats", [])}
        outstanding = {str(g["karat_id"]): g["outstanding"] for g in self._detail["custody"]["gold"] if g["designation"] == "UNCLASSIFIED"}
        melted = {}
        try:
            for line in input_lines or []:
                karat_id, weight = str(line.get("karatId", "")), str(line.get("weight", "")).strip()
                if karat_id and weight:
                    melted[karat_id] = melted.get(karat_id, Decimal(0)) + Decimal(weight.replace(",", ""))
            if not melted or not str(output_weight or "").strip() or not str(fineness or "").strip():
                return empty
            if any(w <= 0 or k not in values for k, w in melted.items()):
                return {**empty, "error": "اختر عيارًا ووزنًا موجبًا لكل سطر."}
            output = Decimal(str(output_weight).replace(",", ""))
            figures = settlement_rules.melt_figures([(values[k], w) for k, w in sorted(melted.items())], output, settlement_rules.parse_fineness(fineness))
        except (ArithmeticError, ValueError):
            return {**empty, "error": "اكتب الأوزان بالأرقام."}
        except TripError as exc:
            return {**empty, "error": str(exc)}
        short = any(w > outstanding.get(k, Decimal(0)) for k, w in melted.items())
        loss = figures["loss_eq21"]
        return {**empty, "valid": not short, "short": short, "error": "وزن السبك أكبر من المتاح من هذا العيار في العهدة." if short else "",
                "inputText": f"{grams(figures['input_weight'])} جرام مسبوك", "theoreticalText": f"{grams(figures['theoretical_eq21'])} جرام عيار 21",
                "actualText": f"{grams(figures['actual_eq21'])} جرام عيار 21", "lossText": grams(loss, signed=True),
                "weightLossText": grams(figures["weight_loss_eq21"], signed=True), "finenessLossText": grams(figures["fineness_loss_eq21"], signed=True),
                "gainText": "زيادة: السبيكة أعلى من المتوقع نظريًا" if loss < 0 else ""}

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
        if designation == "BAR":                                                       # 061: the melted bar (21K-equivalent grams), no karat line
            self._write("goldsettle", "تم تسليم السبيكة للمورد", lambda s, a: s.settle_supplier_bar(
                trip_id, supplier_id, weight, settlement_date, reference, notes, request_key, _expected_actor=a), False)
            return
        self._write("goldsettle", "تمت تسوية الذهب من العهدة", lambda s, a: s.settle_supplier_gold(
            trip_id, supplier_id, karat_id, designation, weight, settlement_date, reference, notes, request_key, _expected_actor=a), False)

    @Slot(str, str, str, str, result="QVariantMap")
    def previewGoldSettlement(self, supplier_id, karat_id, designation, weight):
        """What the settlement would credit, BEFORE submitting: figures come from supplier_rules (the function the service uses), never from QML
        arithmetic. The service recomputes and re-checks the effective outstanding when it posts."""
        empty = {"valid": False, "error": "", "short": False, "availableText": "", "supplierKaratText": "", "deliveredText": "", "formulaText": "",
                 "creditedText": "", "analyticalText": "", "differenceText": ""}
        if not self._detail or not self._context or not str(supplier_id or "").strip() or not str(designation or "").strip() \
                or (designation != "BAR" and not str(karat_id or "").strip()):                  # the melted bar has no karat line
            return empty
        if designation == "BAR":                                                       # the melted bar: N grams of the 21K master karat (its equivalent)
            line = {"karat_value": 21, "outstanding": self._detail["custody"].get("bar", Decimal(0))}
        else:
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

    @Slot(str, str, str, str, str, str, str, str, str, str, str, str, str, str, bool)
    def sellGold(self, trip_id, source_kind, karat_id, designation, lot_id, weight, price_mode, price, buyer_party_id, buyer_name, sale_date,
                 reference, notes, request_key, acknowledge_price):
        """Sell custody gold for cash at the agreed price (بيع ذهب العهدة). Only ids and typed text are sent: the service derives the available
        grams, the total, the reference deviation, re-checks every right and posts atomically. The cash stays in custody; no supplier, Scrap or bank
        movement exists. A price far from the reference is refused (PRICE_DEVIATION) until `acknowledge_price` is true."""
        self._write("goldsale", "تم تسجيل بيع الذهب من العهدة", lambda s, a: s.sell_gold(
            trip_id, source_kind, karat_id, designation, lot_id, weight, price_mode, price, buyer_party_id, buyer_name, sale_date, reference, notes,
            request_key, acknowledge_price, _expected_actor=a), False)

    @Slot(str, str, str, str, str, str, str, result="QVariantMap")
    def previewSale(self, source_kind, karat_id, designation, lot_id, weight, price_mode, price):
        """The agreed total, the per-gram price and the reference deviation BEFORE submitting: figures come from the service's own functions on
        the loaded context (never QML arithmetic, no database read). The service recomputes and re-checks everything when it posts."""
        from services.market_trip_service import MarketTripService, TripError, parse_weight, PRICE_WARNING_PERCENT
        empty = {"valid": False, "error": "", "availableText": "", "totalText": "", "perGramText": "", "referenceText": "", "deviationText": "",
                 "warning": False, "warningText": ""}
        if not self._detail:
            return empty
        custody = self._detail["custody"]
        fineness, karat_value = None, None
        try:
            if source_kind == "CUSTODY_LOT":
                lot = next((l for l in custody["lots"] if l["lot_id"] == str(lot_id)), None)
                if lot is None or lot["fineness"] is None or lot["outstanding"] != lot["weight"]:
                    return {**empty, "error": "اختر سبيكة معروفة العيار وكاملة في العهدة."}
                grams_value, fineness = lot["weight"], lot["fineness"]
                available = f"{grams(grams_value)} جرام (عيار فعلي {fineness_text(fineness)})"
            elif source_kind == "TRIP_BAR":
                pool = custody.get("bar", 0)
                if pool <= 0:
                    return {**empty, "error": "لا يوجد رصيد سبيكة للبيع."}
                karat_value = 21
                available = f"المتاح {grams(pool)} جرام (مكافئ عيار 21)"
                if not str(weight or "").strip():
                    return {**empty, "availableText": available}
                grams_value = parse_weight(weight)
                if grams_value == 0:
                    return {**empty, "availableText": available, "error": "أدخل وزنًا موجبًا."}
                if grams_value > pool:
                    return {**empty, "availableText": available, "error": f"المتاح {grams(pool)} جرام فقط."}
            else:
                line = next((g for g in custody["gold"] if str(g["karat_id"]) == str(karat_id) and g["designation"] == designation), None)
                if line is None or line["outstanding"] <= 0:
                    return {**empty, "error": "اختر سطر الذهب المباع من العهدة."}
                karat_value = line["karat_value"]
                available = f"المتاح {grams(line['outstanding'])} جرام عيار {karat_value} ({DESIGNATION_TEXT[designation]})"
                if not str(weight or "").strip():
                    return {**empty, "availableText": available}
                grams_value = parse_weight(weight)
                if grams_value == 0:
                    return {**empty, "availableText": available, "error": "أدخل وزنًا موجبًا."}
                if grams_value > line["outstanding"]:
                    return {**empty, "availableText": available, "error": f"المتاح {grams(line['outstanding'])} جرام فقط."}
            if not str(price or "").strip():
                return {**empty, "availableText": available}
            _, total, per_gram = MarketTripService._sale_price(grams_value, price_mode, price)
        except TripError as exc:
            return {**empty, "error": str(exc)}
        prices = {int(k): Decimal(v) for k, v in self._reference_prices.items()}
        reference_total, reference_gram, deviation = MarketTripService.sale_reference(prices, grams_value, karat_value, fineness, total)
        warned = deviation is not None and abs(deviation) > PRICE_WARNING_PERCENT
        shown_gram = per_gram if per_gram is not None else (total / grams_value).quantize(Decimal("0.0001"))
        return {**empty, "valid": True, "availableText": available, "totalText": money(total), "perGramText": number(shown_gram, 4),
                "referenceText": "" if reference_total is None else f"المرجع (سعر شراء المحل): {money(reference_total)}",
                "deviationText": "" if deviation is None else f"{number(deviation, 2, signed=True)}%", "warning": warned,
                "warningText": ("السعر المتفق عليه بعيد عن سعر شراء المحل المرجعي بنسبة " + number(abs(deviation), 2) + "%: ستُطلب منك الموافقة عليه.") if warned else ""}

    @staticmethod
    def _buy_lines(lines):
        return [{"karat_id": str(line.get("karatId", "")), "designation": str(line.get("designation", "")), "weight": str(line.get("weight", "")),
                 "price": str(line.get("price", ""))} for line in (lines or [])]

    # ------------------------------------------------------------ BOOK core (056): the dialog operations of a BOOK trip
    @staticmethod
    def _book_rows(lines):
        return [{"basis": str(line.get("basis", "")), "karat_id": str(line.get("karatId", "")), "fineness": str(line.get("fineness", "")),
                 "weight": str(line.get("weight", "")), "price": str(line.get("price", ""))} for line in (lines or [])]

    @Slot(str, "QVariantList", str, str, str)
    def bookIssue(self, trip_id, lines, entry_date, notes, request_key):
        """BOOK: existing shop Scrap goes to this trip (master-karat lines only). Only ids and typed text are sent: the service derives availability, the exact
        equivalents and the posted figures, re-checks the right and posts atomically."""
        rows = self._book_rows(lines)
        self._write("bookissue", "تم تسجيل إخراج الذهب (دفتري)", lambda s, a: s.book_issue(trip_id, rows, entry_date, notes, request_key, _expected_actor=a), True)

    @Slot(str, "QVariantList", str, str, str, str, str, str, bool)
    def bookBuy(self, trip_id, lines, vendor_party_id, vendor_name, buy_date, reference, notes, request_key, acknowledge_price):
        rows = self._book_rows(lines)
        self._write("bookbuy", "تم تسجيل شراء الذهب (دفتري)", lambda s, a: s.book_buy(
            trip_id, rows, vendor_party_id, vendor_name, buy_date, reference, notes, request_key, acknowledge_price, _expected_actor=a), False)

    @Slot(str, "QVariantList", str, str, str, str, str, str, bool)
    def bookSell(self, trip_id, lines, buyer_party_id, buyer_name, sale_date, reference, notes, request_key, acknowledge_price):
        rows = self._book_rows(lines)
        self._write("booksell", "تم تسجيل بيع الذهب (دفتري)", lambda s, a: s.book_sell(
            trip_id, rows, buyer_party_id, buyer_name, sale_date, reference, notes, request_key, acknowledge_price, _expected_actor=a), False)

    @Slot(str, "QVariantList", str, str, str)
    def bookReturn(self, trip_id, lines, entry_date, notes, request_key):
        rows = self._book_rows(lines)
        self._write("bookreturn", "تم تسجيل رجوع الذهب للمحل (دفتري)", lambda s, a: s.book_return(trip_id, rows, entry_date, notes, request_key, _expected_actor=a), True)

    # ------------------------------------------------------------ BOOK Phase 2 (058): the market cycle dialogs
    @Slot(str, "QVariantList", str, str, str)
    def bookIssueStock(self, trip_id, items, entry_date, notes, request_key):
        """Assayed-return stock of the shop -> this BOOK trip. Only the item ids and the typed grams are sent; the service locks the items, allocates the posted share
        from their stored figures and posts atomically."""
        rows = [{"entry_id": str(i.get("entryId", "")), "line_no": str(i.get("lineNo", "")), "grams": str(i.get("grams", ""))} for i in (items or [])]
        self._write("bookissuestock", "تم إخراج المخزون المرتجع إلى العهدة (دفتري)",
                    lambda s, a: s.book_issue_stock(trip_id, rows, entry_date, notes, request_key, _expected_actor=a), True)

    @Slot(str, str, "QVariantList", str, str, str, str)
    def bookDeliverSupplier(self, trip_id, supplier_id, lines, entry_date, reference, notes, request_key):
        rows = self._book_rows(lines)
        self._write("bookdeliversupplier", "تم تسليم الذهب للمورد (دفتري)",
                    lambda s, a: s.book_deliver_supplier(trip_id, supplier_id, rows, entry_date, reference, notes, request_key, _expected_actor=a), False)

    @Slot(str, str, "QVariantList", str, str, str)
    def bookDeliverMarketer(self, trip_id, party_id, lines, entry_date, notes, request_key):
        rows = self._book_rows(lines)
        self._write("bookdelivermarketer", "تم تسليم الذهب للمسوقجي (دفتري)",
                    lambda s, a: s.book_deliver_marketer(trip_id, party_id, rows, entry_date, notes, request_key, _expected_actor=a), False)

    @Slot(str, str, "QVariantList", str, str, str)
    def bookReceiveMarketer(self, trip_id, party_id, lines, entry_date, notes, request_key):
        rows = self._book_rows(lines)
        self._write("bookreceivemarketer", "تم استلام الذهب من المسوقجي (دفتري)",
                    lambda s, a: s.book_receive_marketer(trip_id, party_id, rows, entry_date, notes, request_key, _expected_actor=a), False)

    @Slot(str, "QVariantList", str, str, bool, str, str, str)
    def bookGain(self, trip_id, lines, explanation, evidence_reference, confirmed, entry_date, notes, request_key):
        """059: an evidenced gain (the owner's explanation, evidence reference and explicit confirmation; the service re-checks every right and fact)."""
        rows = self._book_rows(lines)
        row = rows[0] if len(rows) == 1 else rows
        self._write("bookgain", "تم تسجيل الزيادة المثبتة (دفتري)",
                    lambda s, a: s.book_gain(trip_id, row, explanation, evidence_reference, confirmed, entry_date, notes, request_key, _expected_actor=a), False)

    @Slot(str, "QVariantList", result=str)
    def previewSupplierCredit(self, supplier_id, lines):
        """What the supplier's account would be credited, in ITS OWN karat, by the same rule as the posting (services.supplier_rules); empty when incomplete."""
        from services import supplier_rules
        from services.market_trip_book_service import MarketTripBookService
        supplier = next((s for s in self._context.get("suppliers", []) if s["id"] == str(supplier_id) and s.get("karat_value")), None)
        if supplier is None:
            return ""
        try:
            parsed = MarketTripBookService.parse_book_lines(self._book_rows(lines), "BK_DLV_SUP")
        except TripError:
            return ""
        if len(parsed) != 1:
            return "سطر واحد فقط لكل تسليم."
        line = parsed[0]
        if line["basis"] == "FINENESS":
            credited, _, _ = supplier_rules.lot_settlement_credit(line["weight"], line["fineness"], supplier["karat_value"])
        else:
            karat = next((int(k["karat_value"]) for k in self._context.get("karats", []) if int(k["id"]) == int(line["karat_id"])), None)
            if karat is None:
                return ""
            credited, _, _ = supplier_rules.settlement_credit(line["weight"], karat, supplier["karat_value"])
        return f"يُحتسب للمورد {grams(credited)} جرام بعيار حسابه {supplier['karat_value']} (حساب المورد بعياره، لا مكافئ 21)"

    @Slot(str, "QVariantList", result="QVariantMap")
    def previewBook(self, kind, lines):
        """The posted equivalent of every line, the document total, the book balance and the first refusal BEFORE submitting: computed by the SAME pure rules as the
        posting (services.book_rules) on the loaded detail - no database read, no QML arithmetic. The service recomputes and re-checks everything when it posts."""
        from services import book_rules
        from services.market_trip_book_service import MarketTripBookService
        from services.market_trip_service import PRICE_WARNING_PERCENT
        empty = {"valid": False, "error": "", "lines": [], "totalPosted": "", "balanceText": "", "totalText": "", "referenceText": "", "deviationText": "",
                 "warning": False, "warningText": ""}
        book = (self._detail or {}).get("book")
        if not book:
            return empty
        view = book["balance"]
        base = {**empty, "balanceText": f"الرصيد الدفتري الحالي: {grams(view['posted'])} جرام (مكافئ عيار 21) • المتاح تحليليًا {grams(view['analytical_available'])}"}
        rows = self._book_rows(lines)
        try:
            parsed = MarketTripBookService.parse_book_lines(rows, kind)
        except TripError as exc:
            started = any(any(v.strip() for k, v in r.items() if k != "basis") for r in rows)
            return {**base, "error": str(exc) if started else ""}
        karats = {int(k["id"]): int(k["karat_value"]) for k in self._context.get("karats", [])}
        if any(p["karat_id"] is not None and p["karat_id"] not in karats for p in parsed):
            return {**base, "error": "العيار غير موجود أو غير نشط."}
        direction = book_rules.IN if kind in book_rules.INCOMING_KINDS else book_rules.OUT
        planned = [book_rules.plan_line(direction, p["weight"], p["basis"], p["karat_id"], karats.get(p["karat_id"]), p["fineness"]) for p in parsed]
        out = {**base, "lines": [f"سطر {i}: {grams(l.weight)} جرام ← مكافئ عيار 21 مرحَّل {grams(l.posted)} (تحليلي {ltr(number(l.analytical_evidence, 6))} • فرق {ltr(number(l.difference, 6, signed=True))})"
                                  for i, l in enumerate(planned, 1)],
               "totalPosted": f"إجمالي المستند: {grams(sum((l.posted for l in planned), Decimal('0.000')))} جرام مكافئ عيار 21"}
        try:
            if direction == book_rules.OUT:
                book_rules.check_outgoing(book["balance_exact"], planned)
        except TripError as exc:
            return {**out, "error": str(exc)}
        if kind in ("BK_BUY", "BK_SELL"):
            total = sum((p["price"] for p in parsed), Decimal("0.00"))
            out["totalText"] = money(total)
            if kind == "BK_BUY":
                available = self._detail["custody"]["cash"]["outstanding"]
                if total > available:
                    return {**out, "error": f"الإجمالي أكبر من المتاح من نقدية العهدة ({money(available)})."}
            prices = {int(k): Decimal(v) for k, v in self._reference_prices.items()}
            reference_total, deviation = MarketTripBookService.book_reference(prices, parsed, karats, total)
            if reference_total is not None:
                warned = abs(deviation) > PRICE_WARNING_PERCENT
                out.update(referenceText=f"المرجع (سعر شراء المحل): {money(reference_total)}", deviationText=f"{ltr(number(deviation, 2, signed=True))}%", warning=warned,
                           warningText=("الإجمالي بعيد عن المرجع بنسبة " + number(abs(deviation), 2) + "%: ستُطلب منك الموافقة عليه.") if warned else "")
        return {**out, "valid": True}

    @Slot(str, "QVariantList", str, str, str, str, str, str, bool)
    def buyGold(self, trip_id, lines, vendor_party_id, vendor_name, buy_date, reference, notes, request_key, acknowledge_price):
        """Buy scrap gold with custody cash (شراء ذهب نقدي). Only ids and typed text are sent: the service derives the available cash, the total,
        the reference deviation, re-checks every right and posts atomically. The vendor is MANDATORY. No supplier, Scrap or bank movement exists."""
        rows = self._buy_lines(lines)
        self._write("goldbuy", "تم تسجيل شراء الذهب النقدي", lambda s, a: s.buy_gold(
            trip_id, rows, vendor_party_id, vendor_name, buy_date, reference, notes, request_key, acknowledge_price, _expected_actor=a), False)

    @Slot(str, str)
    def registerVendor(self, name, karat_id):
        """Register the typed vendor as a NEW ACTIVE party (a supplier with the dedicated karat the owner names); the dialog then selects it."""
        service = self._service_instance()
        self._error = self._message = ""
        self._start("vendor", lambda _expected_actor: service.register_vendor(name, karat_id, _expected_actor=_expected_actor))
        self.changed.emit()

    def _on_vendor(self, result, error):
        if error:
            self._error = error
            return
        self._message = f"تم تسجيل {result['name']} طرفًا جديدًا"
        self.refresh()
        self.vendorRegistered.emit(result["id"])

    @Slot("QVariantList", result="QVariantMap")
    def previewBuy(self, lines):
        """The total, the available cash and the reference deviation BEFORE submitting, from the service's own functions on the loaded context
        (never QML arithmetic, no database read). The service recomputes and re-checks everything when it posts."""
        from services.market_trip_service import MarketTripService, TripError, PRICE_WARNING_PERCENT
        empty = {"valid": False, "error": "", "availableText": "", "totalText": "", "referenceText": "", "deviationText": "", "warning": False,
                 "warningText": ""}
        if not self._detail:
            return empty
        available = self._detail["custody"]["cash"]["outstanding"]
        base = {**empty, "availableText": f"المتاح من نقدية العهدة: {money(available)}"}
        try:
            parsed = MarketTripService.parse_buy_lines(self._buy_lines(lines))
        except TripError as exc:
            started = any(str(l.get("karatId", "")).strip() or str(l.get("weight", "")).strip() or str(l.get("price", "")).strip() for l in (lines or []))
            return {**base, "error": str(exc) if started else ""}
        total = sum((l["price"] for l in parsed), Decimal("0.00"))
        if total > available:
            return {**base, "totalText": money(total), "error": f"الإجمالي أكبر من المتاح من نقدية العهدة ({money(available)})."}
        karat_values = {int(k["id"]): int(str(k["text"]).replace("عيار", "").strip()) for k in self.context.get("karats", [])}
        prices = {int(k): Decimal(v) for k, v in self._reference_prices.items()}
        reference_total, deviation = MarketTripService.buy_reference(prices, parsed, karat_values, total)
        warned = deviation is not None and abs(deviation) > PRICE_WARNING_PERCENT
        return {**base, "valid": True, "totalText": money(total),
                "referenceText": "" if reference_total is None else f"المرجع (سعر شراء المحل لنفس العيارات): {money(reference_total)}",
                "deviationText": "" if deviation is None else f"{number(deviation, 2, signed=True)}%", "warning": warned,
                "warningText": ("إجمالي الشراء بعيد عن المرجع بنسبة " + number(abs(deviation), 2) + "%: ستُطلب منك الموافقة عليه.") if warned else ""}

    @Property(str, notify=changed)
    def errorCode(self):
        """The code of the last failed write ('' when none): lets the dialog tell a price warning from a refusal."""
        return self._last_code if self._error else ""

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

    @Property(bool, notify=changed)
    def reconciliationEnabled(self):
        return self.canManage and bool(getattr(self._service_instance(), "reconciliation_enabled", False))

    @Property("QVariantMap", notify=changed)
    def reconciliationApproval(self):
        return self._approval

    @Property("QVariantMap", notify=changed)
    def lastReconciliation(self):
        return self._last_reconciliation

    @Slot()
    def prepareReconciliation(self):
        if not self.reconciliationEnabled or not self._selected or self.writing:
            return
        trip_id = self._selected
        self._approval = {"tripId": trip_id, "loading": True}
        self._error = ""
        service = self._service_instance()
        self._start("approval", lambda _expected_actor: service.reconciliation_approval_preview(trip_id, _expected_actor=_expected_actor))

    def _on_approval(self, result, error):
        if self._approval.get("tripId") != self._selected:
            return
        if error:
            self._approval = {"tripId": self._selected, "error": error}
        elif result.get("tripId") == self._selected:
            self._approval = result

    @Slot(str, str, str, str, "QVariantMap", str)
    def approveReconciliation(self, trip_id, reason, note, effective_date, confirmations, request_key):
        preview = self._approval
        if not self.reconciliationEnabled or self.writing or trip_id != self._selected or preview.get("tripId") != trip_id or not preview.get("sourceDigest"):
            return
        digest, versions = preview["sourceDigest"], dict(preview["contractVersions"])
        # IDs, amounts, date floor, digest and contract versions are server-derived.
        self._write("reconcile", "تم اعتماد تسوية العهدة", lambda service, actor: service.reconcile(
            trip_id, digest, effective_date.strip() or None, reason or None, note, dict(confirmations), versions,
            request_key, _expected_actor=actor), False)

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
                 "modeText": "دفتري" if t.get("mode") == "BOOK" else "",
                 "outstandingText": (f"رصيد دفتري {grams(t['book']['posted'])} جرام (مكافئ عيار 21)" + (
                     f" • نقدية {money(t['custody']['cash']['outstanding'])}" if t["custody"]["cash"]["outstanding"] != 0 else "")
                                     if t.get("mode") == "BOOK" and t.get("book") else outstanding_text(t["custody"])) + (
                     " • ذهب خارج المحل لدى مسوقجي: " + grams(sum((p["balance"] for p in t.get("external_holdings", [])), Decimal("0"))) + " جرام"
                     if t.get("external_holdings") else ""), "settled": t["custody"]["settled"],
                 "entryCount": t["entry_count"], "date": date_text(t["created_at"]), "closedDate": date_text(t["status_changed_at"]) if t["status"] == "CLOSED" else "",
                 "selected": t["id"] == self._selected}
                for t in self._trips]

    @Property(str, notify=changed)
    def tripTab(self):
        return self._tab

    @Property(int, notify=changed)
    def tripPage(self):
        return self._page

    @Property(bool, notify=changed)
    def tripHasNext(self):
        return self._has_more

    @Property(bool, notify=changed)
    def tripHasPrevious(self):
        return self._page > 1

    @Property(int, constant=True)
    def tripPageSize(self):
        return TRIP_PAGE_SIZE

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
    def reconciliation(self):
        """The READ-ONLY reconciliation preview of the selected BOOK trip (empty for a LEGACY trip and while it loads). It never makes a trip closable."""
        view = reconciliation_view(self._recon) if self._recon and self._recon.get("tripId") == self._selected else {}
        return {**view, "loading": bool({"recon", "detail"} & self._busy)}

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
                "banks": [{"id": b["id"], "text": b["name"]} for b in c.get("banks", [])],
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
                          for g in gold if g["designation"] in ("LOGO", "UNCLASSIFIED") and g["outstanding"] > 0]
        if custody.get("bar", 0) > 0:                                                  # 061: the melted bar, delivered by its 21K equivalent
            settle_sources.append({"key": "BAR", "karatId": "", "designation": "BAR", "text": f"السبيكة (مكافئ عيار 21) — المتاح {grams(custody['bar'])} جرام"})
        return_lines = [{"key": f"{g['karat_id']}:{g['designation']}", "karatId": str(g["karat_id"]), "designation": g["designation"],
                         "text": f"{_gold_label(g)} — المتبقي {grams(g['outstanding'])}"}
                        for g in gold if g["outstanding"] > 0]
        is_book = d.get("mode") == "BOOK"
        book_reconciled = any(e["kind"] == "BK_RECON" and not e["reversed"] for e in d["entries"])        # an effective (not reversed) reconciliation document
        book_cash_zero = custody["cash"]["outstanding"] == 0
        book = d.get("book") or {}
        view = book.get("balance") or {}
        return {
            "id": trip["id"], "code": trip["code"], "open": is_open, "statusText": STATUS_TEXT[trip["status"]],
            "isBook": is_book, "modeText": "دفتري" if is_book else "قديم",
            "book": {} if not is_book else {
                "posted": grams(view["posted"], signed=view["posted"] < 0), "analytical": number(view["analytical"], 6), "available": grams(view["analytical_available"]),
                "drift": ltr(number(view["drift"], 6, signed=True)), "lines": int(view["lines"]), "note": BOOK_NOTE,
                "balanceText": f"{grams(view['posted'])} جرام مكافئ عيار 21",
                "driftText": f"فرق التقريب (للعرض): {ltr(number(view['drift'], 6, signed=True))} — ليس خسارة ولا زيادة",
                "stockItems": [{"text": f"{i['code']} سطر {i['line_no']} • العيار الفعلي {fineness_text(i['fineness'])} • المتبقي {grams(i['grams'])} من {grams(i['original_weight'])} جرام "
                                        f"• المكافئ المرحَّل المتبقي {grams(i['posted'])}"} for i in book.get("stock_items", [])],
                # 058: the Phase 2 dialog sources (ids are strings; the service re-derives every balance and figure under its locks)
                "shopStock": [{"entryId": i["entry_id"], "lineNo": str(i["line_no"]), "grams": f"{i['grams']:.3f}",
                               "text": f"{i['code']} سطر {i['line_no']} • {fineness_text(i['fineness'])} • المتاح {grams(i['grams'])} جرام • مرحَّل {grams(i['posted'])}"}
                              for i in book.get("shop_stock", [])],
                "suppliers": [{"id": s["id"], "text": f"{s['name']} — حسابه بعيار {s['karat_value']}" + ("" if s["active"] else " (متوقف)")}
                              for s in self._context.get("suppliers", []) if s.get("karat_value")],
                "marketers": [{"id": m["id"], "text": m["name"] + ("" if m["active"] else " (متوقف)"), "active": m["active"]}
                              for m in self._context.get("intermediaries", [])]},
            "closable": is_open and ((settled and not is_book) or (is_book and book_reconciled and book_cash_zero)),
            "reconciled": bool(is_book and book_reconciled),
            "closeHint": ("" if not is_open else BOOK_CLOSE_READY if (book_reconciled and book_cash_zero) else
                          BOOK_CLOSE_HINT + (f" نقدية العهدة المتبقية {money(custody['cash']['outstanding'])} ج." if book_reconciled else "")) if is_book else "" if not is_open else (("لا متبقي في العهدة: يمكن إغلاق العهدة." + ((" " + CLOSURE_NOTE) if d.get("external_holdings") else "")) if settled
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
                     "received": money(custody["cash"].get("received", 0)), "hasReceived": custody["cash"].get("received", 0) != 0,
                     "spent": money(custody["cash"].get("spent", 0)), "hasSpent": custody["cash"].get("spent", 0) != 0,
                     "settled": money(custody["cash"].get("settled", 0)), "hasSettled": custody["cash"].get("settled", 0) != 0,
                     "fee": money(custody["cash"].get("fee", 0)), "hasFee": custody["cash"].get("fee", 0) != 0,
                     "outstanding": money(custody["cash"]["outstanding"]), "zero": custody["cash"]["outstanding"] == 0,
                     "outstandingValue": f"{custody['cash']['outstanding']:.2f}"},
            "gold": [{"karat": g["karat_value"], "karatId": str(g["karat_id"]), "designation": g["designation"],
                      "designationText": DESIGNATION_TEXT[g["designation"]], "label": _gold_label(g), "issued": grams(g["issued"]),
                      "returned": grams(g["returned"]), "melted": grams(g["melted"]), "outstanding": grams(g["outstanding"]),
                      "heated": grams(g["heat_delta"], signed=True) if g["designation"] == "LOGO" and g.get("heat_delta") else "",
                      "settled": grams(g["supplier_settled"]) if g.get("supplier_settled") else "",
                      "settleMelted": grams(g["settle_out"]) if g.get("settle_out") else "",
                      "sold": grams(g["sold"]) if g.get("sold") else "",
                      "purchased": grams(g["purchased"]) if g.get("purchased") else "",
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
            "settleMeltLines": [{"karatId": str(g["karat_id"]), "text": f"عيار {g['karat_value']} — المتاح للسبك {grams(g['outstanding'])} جرام"}
                                for g in gold if g["designation"] == "UNCLASSIFIED" and g["outstanding"] > 0],
            "settleMeltAvailable": is_open and not is_book and any(g["designation"] == "UNCLASSIFIED" and g["outstanding"] > 0 for g in gold),
            "bar": grams(custody.get("bar", 0)), "barVisible": custody.get("bar", 0) != 0,
            "classifyLogoLines": [{"text": f"عيار {g['karat_value']}: {grams(g['outstanding'])} جرام"} for g in gold if g["designation"] == "UNCLASSIFIED" and g["outstanding"] > 0],
            "classifyLogoAvailable": is_open and not is_book and any(g["designation"] == "UNCLASSIFIED" and g["outstanding"] > 0 for g in gold),
            "barMarketerVisible": is_open and not is_book and self._holding_service().can_manage(),
            "barMarketerAvailable": is_open and not is_book and custody.get("bar", 0) > 0 and any(p["active"] for p in self._context.get("intermediaries", [])),
            "barText": f"رصيد السبيكة (مكافئ عيار 21): {grams(custody.get('bar', 0))} جرام",
            "heatStock": heat_stock, "heatAvailable": is_open and bool(heat_stock),
            "goldSettleSources": settle_sources, "goldSettleAvailable": is_open and bool(settle_sources),
            "payAvailable": is_open and custody["cash"]["outstanding"] > 0,
            "cashSettleAvailable": is_open and not is_book and custody["cash"]["outstanding"] > 0,
            "disposeSources": [{"key": f"raw:{g['karat_id']}", "source": "RAW", "karatId": str(g["karat_id"]),
                                "text": f"عيار {g['karat_value']} (بدون وجهة) — المتاح {grams(g['outstanding'])} جرام"}
                               for g in gold if g["designation"] == "UNCLASSIFIED" and g["outstanding"] > 0]
                              + [{"key": f"logo:{g['karat_id']}", "source": "LOGO", "karatId": str(g["karat_id"]),
                                  "text": f"عيار {g['karat_value']} (لوجو) — المتاح {grams(g['outstanding'])} جرام"}
                                 for g in gold if g["designation"] == "LOGO" and g["outstanding"] > 0]
                              + ([{"key": "bar", "source": "BAR", "karatId": "", "text": f"سبيكة السبك — المتاح {grams(custody.get('bar', 0))} جرام (مكافئ عيار 21)"}]
                                 if custody.get("bar", 0) > 0 else []),
            "disposeAvailable": is_open and not is_book and (custody.get("bar", 0) > 0 or any(g["outstanding"] > 0 and g["designation"] in ("UNCLASSIFIED", "LOGO") for g in gold)),
            "adjustKarats": [{"id": str(g["karat_id"]), "text": f"عيار {g['karat_value']}"} for g in gold if g["designation"] == "UNCLASSIFIED"],
            "adjustAvailable": is_open and not is_book and any(g["designation"] == "UNCLASSIFIED" for g in gold),
            "buyAvailable": is_open and custody["cash"]["outstanding"] > 0,
            "saleSources": [{"key": f"{g['karat_id']}:{g['designation']}", "kind": "CUSTODY_GOLD", "karatId": str(g["karat_id"]),
                             "designation": g["designation"], "text": f"{_gold_label(g)} — المتاح {grams(g['outstanding'])} جرام"}
                            for g in gold if g["outstanding"] > 0]
                           + ([{"key": "bar", "kind": "TRIP_BAR", "text": f"سبيكة السبك — المتاح {grams(custody.get('bar', 0))} جرام (مكافئ عيار 21)"}]
                              if custody.get("bar", 0) > 0 else [])
                           + [{"key": f"lot:{l['lot_id']}", "kind": "CUSTODY_LOT", "lotId": l["lot_id"],
                               "text": f"{LOT_TEXT[l['kind']]} • {l['code'] or ''} • شحنة {l['lot_no']} • {grams(l['weight'])} جرام • {fineness_text(l['fineness'])}"}
                              for l in custody["lots"] if l["outstanding"] == l["weight"] and l["fineness"] is not None],
            "saleAvailable": is_open and (any(g["outstanding"] > 0 for g in gold) or custody.get("bar", 0) > 0
                                          or any(l["outstanding"] == l["weight"] and l["fineness"] is not None for l in custody["lots"])),
            "saleBuyers": [{"id": b["id"], "text": b["name"] + (" (مورد)" if b["supplier"] else "") + (" (مسوقجي)" if b["intermediary"] else "")}
                           for b in self._context.get("buyers", []) if b["active"]],
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
        buy = e.get("gold_buy")
        if buy:
            parts.append(f"من {buy['vendor_name']} • " + " • ".join(
                f"{grams(Decimal(l['weight']))} جرام عيار {l['karat_value']} ({DESIGNATION_TEXT[l['designation']]}) بـ {money(Decimal(l['price']))}" for l in buy["lines"])
                         + f" • الإجمالي {money(Decimal(buy['total_price']))}"
                         + (" • السعر مُقرّ رغم بعده عن المرجع" if buy["price_warning_acknowledged"] else ""))
        for l in e.get("book_lines", []):
            what = (f"عيار {l['karat_value']}" if l["basis"] == "MASTER_KARAT" else f"عيار فعلي {fineness_text(l['fineness'])}")
            where = ("كسر المحل" if l["scrap"] else "مخزون مرتجع بعيار فعلي") if e["kind"] in ("BK_ISSUE", "BK_RETURN") else ""
            parts.append(f"سطر {l['line_no']}: {grams(l['weight'])} جرام {what}" + (f" ({where})" if where else "")
                         + f" ← مكافئ عيار 21 مرحَّل {grams(l['posted'])} (تحليلي {ltr(number(l['analytical'], 6))} • فرق {ltr(number(l['difference'], 6, signed=True))})"
                         + ("" if l["price"] is None else f" • {money(l['price'])}"))
        gain = e.get("gain")
        if gain:
            parts.append(f"زيادة مثبتة — الشرح: {gain['explanation']} • الدليل: {gain['evidence_reference']}" + (f" • أكّدها {gain['confirmed_by']}" if gain["confirmed_by"] else ""))
        recon = e.get("recon")
        if recon:
            reason = RECON_REASON_TEXT.get(recon["reason_code"], "بدون سبب") if recon["reason_code"] else "بدون سبب"
            parts.append(f"تسوية رقم {recon['generation']} • التاريخ الفعلي {date_text(recon['effective_date'])} • الرصيد التحليلي غير المسوّى {number(recon['a'], 6)} • "
                         f"الرصيد المرحّل {grams(recon['b'], signed=recon['b'] < 0)} • الفاقد المرحّل {grams(recon['loss'])} • فرق تقريب الترحيل "
                         f"{grams(recon['rounding'], signed=True)} (ليس فاقدًا ولا زيادة) • السبب: {reason}" + (f" • {recon['note']}" if recon["note"] else "")
                         + (f" • اعتمدها {recon['confirmed_by']}" if recon["confirmed_by"] else ""))
        for line in e.get("settle_lines") or []:
            what = "سبيكة (مكافئ عيار 21)" if line["source"] == "BAR" else f"عيار {line['karat_value']} ({'بدون وجهة' if line['source'] == 'RAW' else 'لوجو'})"
            parts.append(f"{what}: {grams(line['weight'])} جرام" + {"CRUCIBLE": " • خسية بوتقة", "MARKET": " • خسية سوق"}.get(line.get("classification") or "", ""))
        adjust = e.get("settle_adjust")
        if adjust:
            source = "من كسر المحل" if adjust.get("source_kind", "SHOP_SCRAP") == "SHOP_SCRAP" else "زيادة فعلية دون حركة كسر"
            parts.append(f"عيار {adjust['karat_value']}: {'زيادة' if adjust['direction'] == 'INCREASE' else 'إنقاص'} "
                         f"{grams(adjust['weight'])} جرام • {source} • {adjust['reason']}")
        cash_doc = e.get("settle_cash")
        if cash_doc:
            parts.append(f"{money(cash_doc['amount'])}" + (f" ← {cash_doc['bank_account']}" if cash_doc["bank_account"] else f" • {cash_doc['description']}")
                         + (f" • مرجع {cash_doc['reference']}" if cash_doc["reference"] else ""))
        if e.get("settle_logo"):
            parts.append("اعتُبر لوجو: " + " + ".join(f"عيار {g['karat_value']}: {grams(g['weight'])} جرام" for g in e["settle_logo"]))
        mkt = e.get("bar_marketer")
        if mkt:
            parts.append(f"{mkt['party_name']}: {grams(mkt['grams'])} جرام (مكافئ عيار 21) ← استحقاقه من ذهب المحل (أمانة)")
        bar = e.get("bar_supplier")
        if bar:
            parts.append(f"{bar['code']} — المورد {bar['supplier_name']}: سُلِّم {grams(bar['delivered_weight'])} جرام (مكافئ عيار 21) ← احتُسب "
                         f"{grams(bar['credited_weight'])} جرام عيار {bar['target_karat']} • القيمة التحليلية {number(bar['analytical'], 6)} • فرق التقريب "
                         f"{number(bar['rounding_difference'], 6, signed=True)}")
        settle = e.get("settle_melt")
        if settle:
            parts.append(" + ".join(f"عيار {i['karat_value']}: {grams(i['weight'])} جرام" for i in settle["inputs"])
                         + f" ← سبيكة {grams(settle['output_weight'])} جرام عيار {settle['fineness']} بالألف • مكافئ عيار 21: نظري {grams(settle['theoretical'])} ← فعلي "
                         f"{grams(settle['actual'])} • خسية الوزن {grams(settle['weight_loss'], signed=True)} • خسية العيار {grams(settle['fineness_loss'], signed=True)}"
                         f" (الإجمالي {grams(settle['loss'], signed=True)}) • {settle['workshop']}")
        trade = e.get("book_trade")
        if trade:
            parts.append(f"{'من' if trade['kind'] == 'BK_BUY' else 'إلى'} {trade['name']} • الإجمالي {money(trade['total'])}"
                         + (" • السعر مُقرّ رغم بعده عن المرجع" if trade["acknowledged"] else ""))
        sale = e.get("gold_sale")
        if sale:
            source = (f"سبيكة {grams(sale['weight'])} جرام" if sale["is_lot"]
                      else f"سبيكة السبك {grams(sale['weight'])} جرام (مكافئ عيار 21)" if sale.get("source_kind") == "TRIP_BAR"
                      else f"عيار {sale['karat_value']} ({DESIGNATION_TEXT[sale['designation']]}): {grams(sale['weight'])} جرام")
            parts.append(f"{source} ← {sale['buyer_name']} • {money(Decimal(sale['total_price']))}"
                         + (" • السعر مُقرّ رغم بعده عن المرجع" if sale["price_warning_acknowledged"] else ""))
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
                "reverseNote": BOOK_REVERSE_NOTES_PHASE2[e["kind"]] if e["kind"] in BOOK_REVERSE_NOTES_PHASE2 else BOOK_REVERSE_NOTES[e["kind"]] if e["kind"] in BOOK_REVERSE_NOTES else HOLD_REVERSE_NOTE if e["kind"] == "LOT_HOLD" else RETURN_REVERSE_NOTE if e["kind"] == "HOLD_RETURN" else "عكس التسوية يعيد الشحنة الأصلية كاملة إلى نفس العهدة ويعيد رصيد المورد بالكمية المخزّنة؛ لا حركة بالمحل." if e["kind"] == "LOT_SETTLE" else PAYMENT_REVERSAL_NOTE if e["kind"] == "SUPPLIER_PAY" else GOLD_SETTLE_REVERSAL_NOTE if e["kind"] == "GOLD_SETTLE" else "",
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
