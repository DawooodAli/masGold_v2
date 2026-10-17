"""Marketer (مسوقجي) custody account for the supplier / party page (055): the shop's CASH and 21K GOLD held in trust, apart from the supplier account.

A child object of SupplierViewModel (`supplierVM.marketer`): it follows the selected party, shows the two separate figures (cash and 21K gold, never a total and never
mixed with the supplier ledger), the documents and the settlements, and sends the actions to MarketerCustodyService. Only ids and typed text leave the page: the service
derives every balance, converts, validates, re-checks every right and posts atomically. The previews use the service's own functions on the loaded data (no QML
arithmetic, no database read)."""
from decimal import Decimal
from uuid import uuid4

from PySide6.QtCore import QObject, Property, Signal, Slot

from app.formatting import date_text, datetime_text, grams, money, number
from services import supplier_rules as rules

KIND_TEXT = {"CASH_HANDOVER": "تسليم نقدية", "CASH_RETURN": "استرجاع نقدية", "GOLD_RETURN": "استرجاع ذهب", "PURCHASE": "شراء ذهب نقدي", "SALE": "بيع ذهب"}
WRITE_TEXT = {"cashhandover": "تم تسليم النقدية للمسوقجي", "cashreturn": "تم استرجاع النقدية إلى الخزنة الرئيسية", "goldreturn": "تم استرجاع الذهب إلى كسر المحل",
              "sale": "تم تسجيل بيع الذهب", "purchase": "تم تسجيل شراء الذهب", "settle": "تمت تسوية الذهب للمورد", "reverse": "تم العكس"}
# What each SUCCESSFUL write changed elsewhere (decided here by the operation, never by a hint from the page). Emitted only after the commit:
#   balances  Main Safe (cash) or counted Scrap moved: top-bar / bank / wallet / financial-account / Scrap consumers reload
#   position  shop-owned gold moved or changed place: the consolidated gold position reloads
#   party     this party's marketer figures changed: its page summary (the supplier detail) reloads
#   supplier  a settlement posted / reversed: the supplier ledger and the supplier list balances reload
# A reversal does not know which kind of document it undoes on the client, so it refreshes every target.
EFFECTS = {"cashhandover": ("balances",), "cashreturn": ("balances",), "goldreturn": ("balances", "position", "party"),
           "purchase": ("position", "party"), "sale": ("position", "party"), "settle": ("position", "party", "supplier"),
           "reverse": ("balances", "position", "party", "supplier")}
ISOLATION_NOTE = "أمانة للمحل لدى المسوقجي: تُحسب وزنة عيار 21 فقط، ولا تدخل حساب المورد ولا تُقاصّ به. الجسر الوحيد هو «تسليم ذهب لمورد» وهو مستند صريح."


def _doc_text(d):
    cash = None if d["cash"] is None else money(d["cash"])
    weight = None if d["weight"] is None else grams(d["weight"])
    kind = d["kind"]
    if kind == "CASH_HANDOVER":
        return f"{cash} من الخزنة الرئيسية"
    if kind == "CASH_RETURN":
        return f"{cash} إلى الخزنة الرئيسية"
    if kind == "GOLD_RETURN":
        return f"{weight} جرام وزنة عيار 21 إلى كسر المحل"
    if kind == "PURCHASE":
        text = f"{weight} جرام وزنة عيار 21 من {d['counterparty']} بإجمالي {cash}"
    else:
        text = f"{weight} جرام وزنة عيار 21 إلى {d['counterparty']} بمبلغ {cash}"
    return text + (" • السعر مُقرّ رغم بعده عن المرجع" if d["price_warning_acknowledged"] else "")


class MarketerCustodyViewModel(QObject):
    changed = Signal()
    posted = Signal(str)                 # a document / settlement was posted (its code): the dialog can close
    reversedDone = Signal()              # a reversal finished: the reason dialog can close
    balancesChanged = Signal()           # the Main Safe or the counted Scrap moved (after commit): the balance views reload
    positionChanged = Signal()           # shop-owned gold moved or changed place (after commit): the consolidated gold position reloads
    partyChanged = Signal()              # this party's marketer figures changed (after commit): the party page summary (supplier detail) reloads
    supplierChanged = Signal()           # a settlement posted / reversed: the supplier ledger of the recipient and the supplier list balances changed

    def __init__(self, session, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._workers, self._request, self._latest, self._busy = set(), 0, {}, set()
        self._shutdown = False
        self._party, self._account = None, None
        self._error, self._message, self._last_code, self._done_text, self._operation = "", "", "", "", ""
        self._reference_prices = {}
        self._effects = ()

    # ------------------------------------------------------------ plumbing
    def _service_instance(self):
        if self._service is None:
            from services.marketer_custody_service import MarketerCustodyService
            self._service = MarketerCustodyService(self._session)
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
        self._last_code = code or ""
        getattr(self, f"_on_{operation}")(result, error)
        self.changed.emit()

    @Slot()
    def shutdown(self):
        self._shutdown = True
        for worker in list(self._workers):
            worker.wait(5000)

    # ------------------------------------------------------------ selection and reads
    def consider(self, supplier):
        """The page selected a party (its detail loaded): a marketer is loaded, anything else clears the section."""
        actor = self._session.current_actor()
        if not supplier or not supplier.get("is_intermediary") or actor is None or "INTERMEDIARY_HOLDING_VIEW" not in actor.permissions:
            self.clear()
            return
        if self._party is None or self._party != str(supplier["id"]):
            self._account, self._error, self._message = None, "", ""
        self._party = str(supplier["id"])
        self.load()

    @Slot()
    def clear(self):
        if self._party is not None or self._account is not None:
            self._party, self._account, self._error, self._message = None, None, "", ""
            self.changed.emit()

    @Slot()
    def load(self):
        if self._party is None:
            return
        service, party = self._service_instance(), self._party
        self._start("account", lambda _expected_actor: service.account(party, _expected_actor=_expected_actor))

    def _on_account(self, result, error):
        if error:
            if self._last_code == "PARTY_NOT_FOUND":
                self._party, self._account = None, None
            else:
                self._error = error
            return
        if result["party"]["id"] == self._party:
            self._account, self._error = result, ""

    @Slot()
    def loadReferencePrices(self):
        service = self._service_instance()
        self._start("refprices", lambda _expected_actor: service.reference_prices(_expected_actor=_expected_actor))

    def _on_refprices(self, result, error):
        self._reference_prices = {} if error else dict(result or {})

    @Slot(result=str)
    def newRequestKey(self):
        return str(uuid4())

    @Slot()
    def clearMessage(self):
        if self._message or self._error:
            self._message = self._error = ""
            self.changed.emit()

    # ------------------------------------------------------------ writes (each re-checked by the service)
    def _write(self, operation, call):
        if "write" in self._busy or self._party is None:
            return                                                     # a double click never submits twice
        self._message = self._error = ""
        self._operation, self._effects = operation, EFFECTS.get(operation, ())
        service = self._service_instance()
        self._start("write", lambda _expected_actor: call(service, _expected_actor))
        self.changed.emit()

    def _on_write(self, result, error):
        if error:
            self._error = error                                        # the dialog stays open with every field kept
            return
        code = (result or {}).get("code", "") if isinstance(result, dict) else ""
        text = WRITE_TEXT.get(self._operation, "تم")
        self._message = f"{text} {code}".strip() + ((" (مسجلة من قبل)") if isinstance(result, dict) and result.get("duplicate") else "")
        self.load()
        if self._operation == "reverse":
            self.reversedDone.emit()
        else:
            self.posted.emit(code)
        for effect, signal in (("balances", self.balancesChanged), ("position", self.positionChanged), ("party", self.partyChanged),
                               ("supplier", self.supplierChanged)):
            if effect in self._effects:
                signal.emit()

    @Slot(str, str, str, str, str)
    def cashHandover(self, amount, document_date, reference, notes, request_key):
        party = self._party
        self._write("cashhandover", lambda s, a: s.cash_handover(party, amount, document_date, reference, notes, request_key, _expected_actor=a))

    @Slot(str, str, str, str, str)
    def cashReturn(self, amount, document_date, reference, notes, request_key):
        party = self._party
        self._write("cashreturn", lambda s, a: s.cash_return(party, amount, document_date, reference, notes, request_key, _expected_actor=a))

    @Slot(str, str, str, str, str)
    def goldReturn(self, weight, document_date, reference, notes, request_key):
        party = self._party
        self._write("goldreturn", lambda s, a: s.gold_return(party, weight, document_date, reference, notes, request_key, _expected_actor=a))

    @Slot(str, str, str, str, str, str, str, str, str, bool)
    def sell(self, weight, price_mode, price, buyer_party_id, buyer_name, document_date, reference, notes, request_key, acknowledge_price):
        party = self._party
        self._write("sale", lambda s, a: s.sale(party, weight, price_mode, price, buyer_party_id, buyer_name, document_date, reference, notes, request_key,
                                                acknowledge_price, _expected_actor=a))

    @Slot("QVariantList", str, str, str, str, str, str, bool)
    def purchase(self, lines, vendor_party_id, vendor_name, document_date, reference, notes, request_key, acknowledge_price):
        party = self._party
        rows = [{"weight": str(l.get("weight", "")), "price": str(l.get("price", ""))} for l in (lines or [])]
        self._write("purchase", lambda s, a: s.purchase(party, rows, vendor_party_id, vendor_name, document_date, reference, notes, request_key,
                                                        acknowledge_price, _expected_actor=a))

    @Slot(str, str, bool, str, str, str, str)
    def settle(self, supplier_id, weight, self_confirmed, settlement_date, reference, notes, request_key):
        party = self._party
        self._write("settle", lambda s, a: s.settle_supplier(party, supplier_id, weight, self_confirmed, settlement_date, reference, notes, request_key,
                                                             _expected_actor=a))

    @Slot(str, str)
    def reverseDocument(self, document_id, reason):
        self._write("reverse", lambda s, a: s.reverse(document_id, reason, _expected_actor=a))

    @Slot(str, str)
    def reverseSettlement(self, settlement_id, reason):
        self._write("reverse", lambda s, a: s.reverse_settlement(settlement_id, reason, _expected_actor=a))

    # ------------------------------------------------------------ previews (service functions on loaded data; no database read)
    @Slot(str, str, str, result="QVariantMap")
    def previewSale(self, weight, price_mode, price):
        from services.market_trip_service import MarketTripService, PRICE_WARNING_PERCENT, TripError, parse_weight
        empty = {"valid": False, "error": "", "availableText": "", "totalText": "", "perGramText": "", "referenceText": "", "deviationText": "", "warning": False,
                 "warningText": ""}
        if not self._account:
            return empty
        held = self._account["gold"]
        base = {**empty, "availableText": f"ذهب المسوقجي (عيار 21): {grams(held)} جرام"}
        if not str(weight or "").strip() or not str(price or "").strip():
            return base
        try:
            amount = parse_weight(weight)
            if amount == 0:
                return {**base, "error": "أدخل وزنًا موجبًا."}
            if amount > held:
                return {**base, "error": f"المتاح {grams(held)} جرام فقط."}
            _, total, per_gram = MarketTripService._sale_price(amount, price_mode, price)
        except TripError as exc:
            return {**base, "error": str(exc)}
        prices = {int(k): Decimal(v) for k, v in self._reference_prices.items()}
        reference_total, _, deviation = MarketTripService.sale_reference(prices, amount, 21, None, total)
        warned = deviation is not None and abs(deviation) > PRICE_WARNING_PERCENT
        shown = per_gram if per_gram is not None else (total / amount).quantize(Decimal("0.0001"))
        return {**base, "valid": True, "totalText": money(total), "perGramText": number(shown, 4),
                "referenceText": "" if reference_total is None else f"المرجع (سعر شراء المحل): {money(reference_total)}",
                "deviationText": "" if deviation is None else f"{number(deviation, 2, signed=True)}%", "warning": warned,
                "warningText": ("السعر المتفق عليه بعيد عن سعر شراء المحل بنسبة " + number(abs(deviation), 2) + "%: ستُطلب منك الموافقة عليه.") if warned else ""}

    @Slot("QVariantList", result="QVariantMap")
    def previewPurchase(self, lines):
        from services.market_trip_service import MarketTripService, PRICE_WARNING_PERCENT, TripError
        from services.marketer_custody_service import parse_purchase_lines
        empty = {"valid": False, "error": "", "availableText": "", "totalText": "", "referenceText": "", "deviationText": "", "warning": False, "warningText": ""}
        if not self._account:
            return empty
        held = self._account["cash"]
        base = {**empty, "availableText": f"نقدية المسوقجي: {money(held)}"}
        rows = [{"weight": str(l.get("weight", "")), "price": str(l.get("price", ""))} for l in (lines or [])]
        try:
            parsed = parse_purchase_lines(rows)
        except TripError as exc:
            started = any(r["weight"].strip() or r["price"].strip() for r in rows)
            return {**base, "error": str(exc) if started else ""}
        total = sum((l["price"] for l in parsed), Decimal("0.00"))
        if total > held:
            return {**base, "totalText": money(total), "error": f"الإجمالي أكبر من نقدية المسوقجي ({money(held)})."}
        prices = {int(k): Decimal(v) for k, v in self._reference_prices.items()}
        reference_total, deviation = MarketTripService.buy_reference(prices, [{"karat_id": 21, "weight": l["weight"]} for l in parsed], {21: 21}, total)
        warned = deviation is not None and abs(deviation) > PRICE_WARNING_PERCENT
        return {**base, "valid": True, "totalText": money(total),
                "referenceText": "" if reference_total is None else f"المرجع (سعر شراء المحل): {money(reference_total)}",
                "deviationText": "" if deviation is None else f"{number(deviation, 2, signed=True)}%", "warning": warned,
                "warningText": ("إجمالي الشراء بعيد عن المرجع بنسبة " + number(abs(deviation), 2) + "%: ستُطلب منك الموافقة عليه.") if warned else ""}

    @Slot(str, str, result="QVariantMap")
    def previewSettlement(self, supplier_id, weight):
        empty = {"valid": False, "error": "", "availableText": "", "creditedText": "", "formulaText": "", "needsConfirmation": False}
        if not self._account:
            return empty
        held = self._account["gold"]
        base = {**empty, "availableText": f"ذهب المسوقجي (عيار 21): {grams(held)} جرام"}
        recipient = next((r for r in self._account["recipients"] if r["id"] == str(supplier_id)), None)
        if recipient is None:
            return base
        base["needsConfirmation"] = bool(recipient["self"])
        if not str(weight or "").strip():
            return base
        try:
            amount = rules.parse_delivered_weight(weight)
            credited, analytical, difference = rules.settlement_credit(amount, 21, recipient["karat_value"])
        except rules.SupplierRuleError as exc:
            return {**base, "error": str(exc)}
        if amount > held:
            return {**base, "error": f"المتاح {grams(held)} جرام فقط."}
        return {**base, "valid": True, "creditedText": f"{grams(credited)} جرام عيار {recipient['karat_value']}",
                "formulaText": f"{grams(amount)} × 21 ÷ {recipient['karat_value']}"}

    # ------------------------------------------------------------ properties
    def _actor_has(self, *codes):
        actor = self._session.current_actor()
        return bool(actor and set(codes) <= set(actor.permissions))

    @Property(bool, notify=changed)
    def visible(self):
        return self._account is not None and self._actor_has("INTERMEDIARY_HOLDING_VIEW")

    @Property(bool, notify=changed)
    def canManage(self):
        return bool(self._account and self._account["can_manage"])

    @Property(bool, notify=changed)
    def canSettle(self):
        return bool(self._account and self._account["can_settle"])

    @Property(bool, notify=changed)
    def canReverse(self):
        return bool(self._account and self._account["can_reverse"])

    @Property(bool, notify=changed)
    def writing(self):
        return "write" in self._busy

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property(str, notify=changed)
    def errorCode(self):
        return self._last_code if self._error else ""

    @Property("QVariantMap", notify=changed)
    def account(self):
        a = self._account
        if not a:
            return {}
        party = a["party"]
        documents = [{"id": d["id"], "code": d["code"], "kindText": KIND_TEXT[d["kind"]], "kind": d["kind"], "text": _doc_text(d),
                      "date": date_text(d["date"]), "time": datetime_text(d["at"]), "reference": d["reference"], "reversed": d["reversed"],
                      "state": ("معكوس • " + d["reversal_reason"]) if d["reversed"] else "",
                      "canReverse": bool(a["can_reverse"] and not d["reversed"])} for d in a["documents"]]
        settlements = [{"id": s["id"], "code": s["code"], "text": f"{s['supplier_name']}: سُلِّم {grams(s['delivered'])} جرام وزنة عيار 21 ← احتُسب "
                                                                   f"{grams(s['credited'])} جرام عيار {s['target_karat']}"
                                                                   + (" • تسوية للطرف نفسه (بتأكيد المالك)" if s["self_settlement"] else ""),
                        "date": date_text(s["date"]), "reversed": s["reversed"], "state": ("معكوسة • " + s["reversal_reason"]) if s["reversed"] else "",
                        "canReverse": bool(a["can_reverse"] and not s["reversed"])} for s in a["settlements"]]
        parties = [{"id": c["id"], "text": c["name"] + (" (مورد)" if c["supplier"] else "") + (" (مسوقجي)" if c["intermediary"] else "")}
                   for c in a["counterparties"] if c["active"] and c["id"] != party["id"]]
        recipients = [{"id": r["id"], "text": r["name"] + ("" if r["active"] else " (متوقف)") + f" — عيار حساب الذهب {r['karat_value']}"
                       + (" — الطرف نفسه" if r["self"] else ""), "self": r["self"], "karatValue": r["karat_value"]} for r in a["recipients"]]
        return {"partyId": party["id"], "partyName": party["name"], "partyActive": party["active"], "isDual": party["is_supplier"],
                "cashText": money(a["cash"]), "goldText": f"{grams(a['gold'])} جرام", "cashZero": a["cash"] == 0, "goldZero": a["gold"] == 0,
                "cashValue": f"{a['cash']:.2f}", "goldValue": f"{a['gold']:.3f}",
                "isolationNote": ISOLATION_NOTE, "documents": documents, "settlements": settlements, "parties": parties, "recipients": recipients}
