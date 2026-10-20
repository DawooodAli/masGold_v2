"""التقارير الأساسية — the view model of the reports page (packet26).

Display only: the numbers come from `ReportService` (exact Decimal, totals over the whole filtered result); this class formats them, keeps the filters, pages through the
service and exports the CSV. QML never computes. A request is identified; the answer of an older request, of another report or of a previous login is dropped. A failed read is
shown as a failure, never as an empty report.

The type registers itself for QML (`import MasGold.Reports 1.0`) when this module is imported (main.py's view-model builder imports `customer_picker_viewmodel`, which imports
this module), so the application entry point needs no change; the page gives it the real `AppSession` exactly like the customer picker."""
from datetime import date, datetime
from decimal import Decimal

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtQml import QmlElement

from app.formatting import date_text, datetime_text, grams, money, number
from services.report_service import REPORT_TITLES, ReportService

QML_IMPORT_NAME = "MasGold.Reports"
QML_IMPORT_MAJOR_VERSION = 1

ALL = ("ALL", "الكل")
FILTER_SPECS = {
    "sales": [{"key": "kind", "label": "النوع", "options": [ALL, ("SALE", "بيع"), ("MIXED", "بيع مع شراء كسر"), ("OLD_GOLD_PURCHASE", "شراء كسر"), ("RETURN", "مرتجع"), ("EXCHANGE", "استبدال")]},
              {"key": "state", "label": "الحالة", "options": [ALL, ("EFFECTIVE", "فعّال"), ("REVERSED", "معكوس"), ("VOIDED", "ملغاة")]}],
    "stock": [{"key": "status", "label": "الحالة", "options": [("IN_STOCK", "متاح"), ("SOLD", "مباع"), ("RETURNED_PENDING", "مرتجع بانتظار الفحص"), ("SCRAPPED", "كسر"), ("REMOVED", "مُخرج"), ("ALL", "كل الحالات")]},
              {"key": "classification", "label": "التصنيف", "options": [("", "الكل"), ("NEW_STOCK", "جديد"), ("OLD_STOCK", "قديم")]},
              {"key": "karat", "label": "العيار", "options": [("", "الكل"), ("18", "18"), ("21", "21"), ("22", "22"), ("24", "24")]}],
    "gold": [{"key": "source", "label": "المصدر", "options": [("SCRAP", "كسر المحل (بالعيار)"), ("PIECES", "المشغولات (قطع)"), ("ASSAYED", "مخزون مرتجع بعيار فعلي"),
                                                              ("BOOK", "عهد السوق الدفترية"), ("MARKETER", "استحقاق المسوقجي")]},
             {"key": "karat", "label": "العيار (للكسر والمشغولات)", "options": [("", "الكل"), ("18", "18"), ("21", "21"), ("22", "22"), ("24", "24")]}],
    "customers": [{"key": "balance", "label": "الرصيد", "options": [ALL, ("OWES", "على العميل"), ("CREDIT", "للعميل"), ("SETTLED", "متوازن")]},
                  {"key": "inactive", "label": "العملاء المتوقفون", "options": [("", "غير معروضين"), ("1", "معروضون")]}],
    "suppliers": [],
    "treasury": [],
    "market": [],
}
KIND_FORMAT = {"money": lambda v: money(v, signed=v < 0), "grams": lambda v: grams(v, signed=v < 0), "eq21": lambda v: grams(v, signed=v < 0)}


def _cell(kind, value):
    if value is None:
        return "—"
    if kind == "date":
        return datetime_text(value) if isinstance(value, datetime) and (value.hour or value.minute) else date_text(value)
    if isinstance(value, str):
        return value                                                           # an unknown legacy value arrives as its own words («غير معلوم»), never as a number
    if kind in KIND_FORMAT:
        return KIND_FORMAT[kind](Decimal(str(value)))
    if kind == "count":
        return str(value)
    return str(value)


def result_view(built):
    """The display form of one `ReportService` result: strings only, plus the open-source references and the paging state."""
    tables = []
    for t in built["tables"]:
        if not t.get("available", True):
            tables.append({"key": t["key"], "title": t["title"], "available": False, "reason": t.get("reason", ""), "columns": [], "rows": [], "paged": False})
            continue
        cols = t["columns"]
        rows = [{"cells": [_cell(c["kind"], r.get(c["key"])) for c in cols], "ref": r.get("ref") or {}, "dim": bool(r.get("state", "") and ("معكوس" in str(r.get("state")) or "ملغاة" in str(r.get("state")))),
                 "refText": {"INVOICE": "فتح الفاتورة", "CUSTOMER": "فتح العميل", "TRIP": "فتح الرحلة"}.get((r.get("ref") or {}).get("type"), "")} for r in t["rows"]]
        tables.append({"key": t["key"], "title": t["title"], "available": True, "reason": "", "columns": [{"title": c["title"], "kind": c["kind"]} for c in cols], "rows": rows,
                       "paged": t["paged"], "rowCount": t.get("row_count", len(rows)), "page": t.get("page", 1), "pageSize": t.get("page_size", len(rows)), "hasMore": t.get("has_more", False),
                       "note": t.get("note", "")})
    return {"report": built["report"], "title": built["title"], "filters": built["filters"], "meaning": built["meaning"], "applied": built.get("applied", []), "tables": tables,
            "totals": [{"label": x["label"], "value": _cell(x["kind"], x["value"]) if x["kind"] != "text" else str(x["value"]), "note": x["note"]} for x in built["totals"]], "error": ""}


@QmlElement
class ReportsViewModel(QObject):
    changed = Signal()
    openRequested = Signal(str, str, str)               # type (INVOICE / CUSTOMER / TRIP), id, extra

    def __init__(self, parent=None, service=None):
        super().__init__(parent)
        self._default_service = service is None
        self._service = service
        self._session = None
        self._report, self._filters, self._result, self._error, self._message = "", {}, {}, "", ""
        self._request, self._latest, self._busy = 0, 0, False
        self._workers = set()

    # ---- session
    def _get_session(self):
        return self._session

    def _set_session(self, session):
        from app.session import AppSession
        if isinstance(session, AppSession) and session is not self._session:
            self._session = session
            if self._default_service:
                self._service = ReportService(session)
            session.changed.connect(self._session_changed)
            self.changed.emit()

    session = Property(QObject, _get_session, _set_session)

    def _session_changed(self):
        self._latest = 0                                                      # no answer of the previous login is ever shown
        self._report, self._filters, self._result, self._error, self._message, self._busy = "", {}, {}, "", "", False
        self.changed.emit()

    # ---- state
    @Property("QVariantList", notify=changed)
    def reports(self):
        return self._service.available_reports() if self._service is not None and self._session is not None else []

    @Property(bool, notify=changed)
    def canView(self):
        return bool(self.reports)

    @Property(str, notify=changed)
    def report(self):
        return self._report

    @Property("QVariantMap", notify=changed)
    def filters(self):
        return {"from": self._filters.get("from", ""), "to": self._filters.get("to", ""), "text": self._filters.get("text", ""), **{k: v for k, v in self._filters.items() if k not in ("from", "to", "text")}}

    @Property("QVariantList", notify=changed)
    def filterSpecs(self):
        specs = FILTER_SPECS.get(self._report, [])
        if self._report == "gold" and self._filters.get("source", "SCRAP") not in ("SCRAP", "PIECES"):
            specs = [s for s in specs if s["key"] != "karat"]                  # a filter that would have no effect is not offered
        return [{"key": s["key"], "label": s["label"], "options": [{"value": v, "text": t} for v, t in s["options"]]} for s in specs]

    @Property("QVariantMap", notify=changed)
    def result(self):
        return self._result

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property(str, notify=changed)
    def message(self):
        return self._message

    # ---- actions
    @Slot(str)
    def select(self, report):
        if report not in REPORT_TITLES or report == self._report:
            return
        self._report, self._result, self._error, self._message = report, {}, "", ""
        self._filters = {"from": date.today().replace(day=1).isoformat(), "to": date.today().isoformat(), "text": ""}
        for spec in FILTER_SPECS.get(report, []):
            self._filters[spec["key"]] = spec["options"][0][0]
        self.changed.emit()
        self.run(1)

    @Slot(str, str)
    def setFilter(self, key, value):
        if key in ("from", "to", "text") or any(s["key"] == key for s in FILTER_SPECS.get(self._report, [])):
            self._filters[key] = value
            self.changed.emit()

    @Slot()
    def apply(self):
        self.run(1)

    @Slot(int)
    def run(self, page):
        if not self._report or self._service is None or self._session is None:
            return
        report, filters = self._report, {**self._filters, "page": max(1, int(page))}
        self._error, self._message, self._busy = "", "", True
        self._request += 1
        self._latest = request = self._request
        self._start(request, report, lambda actor: self._service.run(report, filters, _expected_actor=actor), export=False)
        self.changed.emit()

    @Slot()
    def exportCsv(self):
        if not self._report or self._service is None or self._busy:
            return
        report, filters = self._report, dict(self._filters)
        self._error, self._message, self._busy = "", "", True
        self._request += 1
        self._latest = request = self._request
        self._start(request, report, lambda actor: self._service.export_csv(report, filters, _expected_actor=actor), export=True)
        self.changed.emit()

    @Slot(str, str, str)
    def open(self, kind, identifier, extra):
        if kind in ("INVOICE", "CUSTOMER", "TRIP") and identifier:
            self.openRequested.emit(kind, str(identifier), str(extra or ""))

    # ---- workers
    def _start(self, request, report, call, *, export):
        from app.workers.invoice_worker import InvoiceWorker
        actor = self._session.current_actor()
        if actor is None:
            self._busy = False
            return
        worker = InvoiceWorker(lambda _expected_actor: call(_expected_actor), self._session, actor, request, "report", self)
        worker.completed.connect(lambda r, generation, operation, result, error, code, kind, report=report, export=export: self._completed(r, generation, report, result, error, export))
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        worker.start()

    def _completed(self, request, generation, report, result, error, export):
        actor = self._session.current_actor() if self._session is not None else None
        if actor is None or str(actor.generation) != generation or request != self._latest or report != self._report:
            return                                                              # a stale answer (older request, another report, another login) is never shown
        self._busy = False
        if error:
            self._error = error
            if not export:
                self._result = {}                                               # a failed read is a failure, never an old or empty report
        elif export:
            self._message = f"تم حفظ الملف: {result}"
        else:
            self._result = result_view(result)
        self.changed.emit()

    @Slot()
    def shutdown(self):
        self._latest = 0
        for worker in list(self._workers):
            worker.wait(5000)
