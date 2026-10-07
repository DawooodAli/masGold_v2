"""الموردون: supplier list, one supplier's net positions and the two supplier ledgers (read side + master writes).

Formats what SupplierService returns; nothing here decides authorization or computes accounting (the service
re-checks the permission in the transaction on every call). A supplier id is an immutable decimal string.

Positions are never shown as an ambiguous signed number: the Arabic wording carries the direction
(علينا للمورد = the shop owes the supplier, لنا عند المورد = the supplier owes the shop) and gold and making
(مصنعيات) are always separate lines. Weights use the project unit جرام.
"""
from decimal import Decimal

from PySide6.QtCore import QObject, Property, Signal, Slot

from app.formatting import date_text, datetime_text, grams, money
from services import supplier_rules as rules

SHOP_OWES_TEXT, SUPPLIER_OWES_TEXT, SETTLED_TEXT = "علينا للمورد", "لنا عند المورد", "الحساب متوازن"
TONES = {rules.SHOP_OWES: "shopOwes", rules.SUPPLIER_OWES: "supplierOwes", rules.SETTLED: "settled"}


def gold_position_text(direction, amount, karat=None):
    """'علينا للمورد 40.050 جرام' / 'لنا عند المورد 9.950 جرام عيار 21' (the magnitude only; the words give the sign)."""
    if direction == rules.SETTLED:
        return SETTLED_TEXT
    lead = SHOP_OWES_TEXT if direction == rules.SHOP_OWES else SUPPLIER_OWES_TEXT
    suffix = f" عيار {karat}" if karat else ""
    return f"{lead} {grams(abs(Decimal(str(amount))))} جرام{suffix}"


def eq21_position_text(direction, amount):
    if direction == rules.SETTLED:
        return SETTLED_TEXT
    lead = SHOP_OWES_TEXT if direction == rules.SHOP_OWES else SUPPLIER_OWES_TEXT
    return f"{lead} {grams(abs(Decimal(str(amount))))} جرام (مكافئ عيار 21)"


def making_position_text(direction, amount):
    """'علينا للمورد 6,500.00 ج مصنعيات' / 'لنا عند المورد 1,500.00 ج مصنعيات'."""
    if direction == rules.SETTLED:
        return SETTLED_TEXT
    lead = SHOP_OWES_TEXT if direction == rules.SHOP_OWES else SUPPLIER_OWES_TEXT
    return f"{lead} {money(abs(Decimal(str(amount))))} مصنعيات"


def movement_side_text(delta):
    delta = Decimal(str(delta))
    return SHOP_OWES_TEXT if delta > 0 else SUPPLIER_OWES_TEXT


class SupplierViewModel(QObject):
    changed = Signal()
    supplierSaved = Signal(str)

    def __init__(self, session, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._workers, self._request, self._latest, self._busy = set(), 0, {}, set()
        self._shutdown = False
        self._suppliers, self._detail = [], {}
        self._selected, self._search, self._active_only = "", "", False
        self._error, self._message, self._done_text = "", "", ""

    # ------------------------------------------------------------ plumbing
    def _service_instance(self):
        if self._service is None:
            from services.supplier_service import SupplierService
            self._service = SupplierService(self._session)
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
    def refresh(self):
        if not self.canView:
            return
        service, search, active = self._service_instance(), self._search, self._active_only
        self._start("list", lambda _expected_actor: service.list_suppliers(search, active,
                                                                           _expected_actor=_expected_actor))

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
    def selectSupplier(self, supplier_id):
        self._selected = str(supplier_id or "")
        self._detail = {}
        self.changed.emit()
        if self._selected:
            self._load_detail()

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

    @Slot(str, str, str)
    def createSupplier(self, name, phone, notes):
        self._write("تم إضافة المورد.", lambda s, a: s.create_supplier(name, phone, notes, _expected_actor=a))

    @Slot(str, str, str, str)
    def updateSupplier(self, supplier_id, name, phone, notes):
        self._write("تم حفظ بيانات المورد.",
                    lambda s, a: s.update_supplier(supplier_id, name, phone, notes, _expected_actor=a) or supplier_id)

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

    # ------------------------------------------------------------ properties
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
                 "goldText": eq21_position_text(s["gold_direction"], s["gold_eq21"]),
                 "goldTone": TONES[s["gold_direction"]],
                 "makingText": making_position_text(s["making_direction"], s["making"]),
                 "makingTone": TONES[s["making_direction"]],
                 "invoiceCount": s["posted_invoice_count"], "selected": s["id"] == self._selected}
                for s in self._suppliers]

    @Property("QVariantMap", notify=changed)
    def detail(self):
        """The selected supplier: identity, the two independent positions and both ledgers (display texts)."""
        if not self._detail:
            return {}
        supplier, balances = self._detail["supplier"], self._detail["balances"]
        return {
            "id": supplier["id"], "code": supplier["code"], "name": supplier["name"], "phone": supplier["phone"],
            "notes": supplier["notes"], "active": supplier["is_active"],
            "goldEq21Text": eq21_position_text(balances["gold_direction"], balances["gold_eq21"]),
            "goldTone": TONES[balances["gold_direction"]],
            "goldLines": [{"karat": k["karat_value"],
                           "text": gold_position_text(k["direction"], k["weight_balance"], k["karat_value"]),
                           "tone": TONES[k["direction"]]} for k in balances["gold_by_karat"]],
            "makingText": making_position_text(balances["making_direction"], balances["making"]),
            "makingTone": TONES[balances["making_direction"]],
            "goldLedger": [{
                "id": r["id"], "time": datetime_text(r["occurred_at"]), "movement": r["movement"],
                "karat": r["karat_value"], "side": movement_side_text(r["weight_delta"]),
                "weight": grams(abs(r["weight_delta"])), "eq21": grams(abs(r["eq21_delta"])),
                "balance": eq21_position_text(rules.direction(r["eq21_balance_after"].quantize(rules.WEIGHT_STEP)),
                                              r["eq21_balance_after"].quantize(rules.WEIGHT_STEP)),
                "reference": r["reference"], "sourceId": r["source_id"], "actor": r["actor"],
                "tone": TONES[rules.direction(r["weight_delta"])]} for r in self._detail["gold_ledger"]],
            "makingLedger": [{
                "id": r["id"], "time": datetime_text(r["occurred_at"]), "movement": r["movement"],
                "side": movement_side_text(r["amount_delta"]), "amount": money(abs(r["amount_delta"])),
                "balance": making_position_text(rules.direction(r["balance_after"]), r["balance_after"]),
                "reference": r["reference"], "sourceId": r["source_id"], "actor": r["actor"],
                "tone": TONES[rules.direction(r["amount_delta"])]} for r in self._detail["making_ledger"]],
        }
