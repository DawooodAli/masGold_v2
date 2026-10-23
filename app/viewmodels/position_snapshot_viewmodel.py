"""Settings → الرصيد الافتتاحي: the owner's initial gold / cash balance (entered ONCE) and the stocktaking sheets behind the gold profit report.

The view model never decides a right or a number: the service rechecks the owner rights inside its transaction, validates the figures and takes the sheet. The daily sheet is requested once
when an authorised user opens the application (`ensureDaily`); a manual sheet is a button. Every call runs off the GUI thread and its answer is ignored when the session changed meanwhile."""
from PySide6.QtCore import Property, QObject, Signal, Slot

from app.workers.invoice_worker import InvoiceWorker


def _grams(value):
    return f"{value:,.3f} جرام"


class PositionSnapshotViewModel(QObject):
    changed = Signal()
    snapshotTaken = Signal()

    def __init__(self, session=None, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._opening, self._loaded, self._busy = None, False, False
        self._ledgers_posted, self._accounts, self._posted = False, [], {}
        self._message, self._is_error = "", False
        self._request, self._workers = 0, set()

    # ------------------------------------------------------------------ state for the Settings card
    @Property(bool, notify=changed)
    def canManage(self):
        return self._service is not None and self._service.can_snapshot()

    @Property(bool, notify=changed)
    def loaded(self):
        return self._loaded

    @Property(bool, notify=changed)
    def hasOpening(self):
        return self._opening is not None

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(bool, notify=changed)
    def messageIsError(self):
        return self._is_error

    @Property(bool, notify=changed)
    def ledgersPosted(self):
        return self._ledgers_posted

    @Property("QVariantList", notify=changed)
    def accounts(self):
        """The main safe and the bank / wallet accounts that can take an opening balance: {id, ledger, name, typeText, free}."""
        return self._accounts

    @Property(str, notify=changed)
    def postedText(self):
        """What stage 1 posted (read-only once recorded)."""
        if not self._ledgers_posted:
            return ""
        scrap = self._posted.get("scrap", {})
        parts = [f"كسر {karat}: {scrap[karat]:,.3f} جرام" for karat in (21, 18, 24) if scrap.get(karat)]
        return "سُجّل: " + " • ".join(parts + [f"الكاش {self._posted.get('cash', 0):,.2f} ج", f"البنوك والمحافظ {self._posted.get('bank', 0):,.2f} ج"])

    @Property(str, notify=changed)
    def openingText(self):
        o = self._opening
        if o is None:
            return ""
        return (f"سُجّل في {o['taken_at']:%Y-%m-%d %H:%M} — ذهب المحل {_grams(o['gold_shop_eq21'])} • ذهب الموردين (صافي) {_grams(o['gold_suppliers_eq21'])} • "
                f"الكاش {o['cash_egp']:,.2f} ج • البنوك {o['bank_egp']:,.2f} ج")

    # ------------------------------------------------------------------ actions
    @Slot()
    def refresh(self):
        self._run("opening", lambda _expected_actor: self._service.opening_state(_expected_actor=_expected_actor), self._opening_loaded)

    @Slot("QVariantMap", str, "QVariantMap")
    def saveLedgers(self, scrap, cash, banks):
        """Stage 1: scrap per karat ({"21": "...", ...}), the cash and the bank / wallet openings ({account id: amount})."""
        scrap, banks = dict(scrap or {}), dict(banks or {})
        self._run("opening-ledgers", lambda _expected_actor: self._service.post_opening_ledgers(scrap, cash, banks, _expected_actor=_expected_actor), self._ledgers_saved)

    @Slot("QVariantMap", str)
    def saveOpening(self, new_gold, suppliers):
        """Stage 2: the new gold per karat ({"21": "...", ...}) and the optional net supplier gold; it records the OPENING sheet and closes the card."""
        new_gold = dict(new_gold or {})
        self._run("opening-save", lambda _expected_actor: self._service.set_opening(new_gold, suppliers, _expected_actor=_expected_actor), self._opening_saved)

    @Slot("QVariantMap", result=str)
    def previewEq21(self, grams):
        """The 21K-equivalent grams of what is typed per karat (display only; the service computes the stored figure)."""
        from decimal import Decimal, InvalidOperation
        from services import position_snapshot_rules as rules
        parsed = {}
        for karat, value in dict(grams or {}).items():
            try:
                parsed[int(karat)] = Decimal(str(value).strip().replace(",", "") or "0")
            except (InvalidOperation, ValueError):
                return "—"
        return f"{rules.eq21(parsed):,.3f}"

    @Slot()
    def takeNow(self):
        self._run("snapshot", lambda _expected_actor: self._service.take("MANUAL", _expected_actor=_expected_actor), self._manual_taken)

    @Slot()
    def ensureDaily(self):
        """The first authorised opening of the application each day; silent when not applicable."""
        self._run("daily", lambda _expected_actor: self._service.ensure_daily(_expected_actor=_expected_actor), self._daily_done, silent=True)

    # ------------------------------------------------------------------ plumbing
    def _run(self, operation, call, done, silent=False):
        if self._service is None or self._session is None:
            return
        actor = self._session.current_actor()
        if actor is None:
            return
        self._request += 1
        request = self._request
        if not silent:
            self._busy, self._message, self._is_error = True, "", False
            self.changed.emit()
        worker = InvoiceWorker(call, self._session, actor, request, operation, self)
        worker.completed.connect(lambda r, generation, op, result, error, code, kind: self._completed(r, generation, result, error, done, silent))
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        worker.start()

    def _completed(self, request, generation, result, error, done, silent):
        actor = self._session.current_actor() if self._session is not None else None
        if actor is None or str(actor.generation) != generation:
            return
        if not silent:
            self._busy = False
        if error:
            if not silent:
                self._message, self._is_error = error, True
        else:
            done(result)
        self.changed.emit()

    def _opening_loaded(self, result):
        self._opening, self._loaded = result["opening"], True
        self._ledgers_posted, self._posted = result["ledgers_posted"], result["posted"]
        names = {"CASH": "نقدية", "BANK": "بنك", "MOBILE_WALLET": "محفظة"}
        self._accounts = [{"id": a["id"], "ledger": a["ledger"], "name": a["name"], "typeText": names.get(a["type"], a["type"] or ""), "free": a["free"]} for a in result["accounts"]]

    def _ledgers_saved(self, result):
        self._message, self._is_error = "تم تسجيل الكسر والكاش والبنوك. الآن سجّل الذهب الجديد.", False
        self.refresh()

    def _opening_saved(self, result):
        self._message, self._is_error = "تم تسجيل الرصيد الافتتاحي ولا يمكن تعديله.", False
        self.refresh()
        self.snapshotTaken.emit()

    def _manual_taken(self, result):
        self._message, self._is_error = "تم حفظ لقطة الرصيد الآن.", False
        self.snapshotTaken.emit()

    def _daily_done(self, result):
        if result.get("taken"):
            self.snapshotTaken.emit()

    def shutdown(self):
        for worker in list(self._workers):
            worker.wait(2000)


def create_position_snapshot_viewmodel(session):
    from services.position_snapshot_service import PositionSnapshotService
    return PositionSnapshotViewModel(session, PositionSnapshotService(session))
