"""التقارير الأساسية — the view model of the reports page (packet26).

Display only: the numbers come from `ReportService` (exact Decimal, totals over the whole filtered result); this class formats them, keeps the filters, pages through the
service and exports the CSV. QML never computes. A request is identified; the answer of an older request, of another report or of a previous login is dropped. A failed read is
shown as a failure, never as an empty report.

The type registers itself for QML (`import MasGold.Reports 1.0`) when this module is imported (main.py's view-model builder imports `customer_picker_viewmodel`, which imports
this module), so the application entry point needs no change; the page gives it the real `AppSession` exactly like the customer picker."""
from datetime import date, datetime, timedelta
from decimal import Decimal

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtQml import QmlElement

from app.formatting import date_text, datetime_text, grams, money, number, rate
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
    "indicators": [],
    "stockweights": [{"key": "karat", "label": "العيار", "options": [("", "الكل"), ("18", "18"), ("21", "21"), ("22", "22"), ("24", "24")]}],
    "alerts": [{"key": "karat", "label": "العيار", "options": [("", "الكل"), ("18", "18"), ("21", "21"), ("22", "22"), ("24", "24")]}],
    "gold": [{"key": "karat", "label": "العيار", "options": [("", "الكل"), ("18", "18"), ("21", "21"), ("22", "22"), ("24", "24")]},
             {"key": "factory", "label": "المصنع", "options": [("", "الكل")], "dynamic": True},
             {"key": "weight_min", "label": "الوزن من (جرام)", "type": "text"},
             {"key": "weight_max", "label": "إلى (جرام)", "type": "text"}],
    "demand": [],
    "scrapcost": [{"key": "karat", "label": "العيار", "options": [("", "الكل"), ("18", "18"), ("21", "21"), ("22", "22"), ("24", "24")]},
                  {"key": "group", "label": "التجميع", "options": [("month", "شهري"), ("week", "أسبوعي"), ("year", "سنوي")]}],
    "makingprofit": [{"key": "category", "label": "الحرف", "options": [("", "الكل")], "dynamic": True},
                     {"key": "karat", "label": "العيار", "options": [("", "الكل"), ("18", "18"), ("21", "21"), ("22", "22"), ("24", "24")]}],
    "employees": [{"key": "employee", "label": "الموظف", "options": [("", "الكل")], "dynamic": True},
                  {"key": "karat", "label": "العيار", "options": [("", "الكل"), ("18", "18"), ("21", "21"), ("22", "22"), ("24", "24")]}],
    "customers": [{"key": "balance", "label": "الرصيد", "options": [ALL, ("OWES", "عليهم (على العميل)"), ("CREDIT", "علينا (للعميل)")]},
                  {"key": "inactive", "label": "العملاء المتوقفون", "options": [("", "غير معروضين"), ("1", "معروضون")]}],
    "deadcash": [{"key": "state", "label": "الحالة", "options": [ALL, ("ACTIVE", "ساري"), ("REVERSED", "معكوس")]},
                 {"key": "inactive", "label": "العملاء المتوقفون", "options": [("", "غير معروضين"), ("1", "معروضون")]}],
    "suppliers": [],
    "treasury": [],
    "market": [],
    "cashflow": [],
    "profit": [],
    "goldflow": [],
}
KIND_FORMAT = {"money": lambda v: money(v, signed=v < 0), "rate": lambda v: rate(v, signed=v < 0), "grams": lambda v: grams(v, signed=v < 0), "eq21": lambda v: grams(v, signed=v < 0)}


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


def _today():
    return date.today()


def month_period(today):
    """The default period of every report: the first day of the CURRENT month up to today (it moves by itself with the calendar)."""
    return today.replace(day=1).isoformat(), today.isoformat()


WEEK_REPORTS = {"scrapcost"}                                                  # owner request 2026-10-31: these reports default to the last 7 days


def default_period(report, today):
    """The automatic period of a report: the current month so far, or (WEEK_REPORTS) the last 7 days ending today."""
    if report in WEEK_REPORTS:
        return (today - timedelta(days=6)).isoformat(), today.isoformat()
    return month_period(today)


HIDDEN_ON_SCREEN = {("sales", "documents"), ("stock", "items"), ("stock", "summary"), ("suppliers", "movements"), ("treasury", "movements"), ("market", "summary")}
NO_PERIOD = {"stock", "alerts", "stockweights", "indicators", "demand", "customers", "deadcash", "suppliers"}                      # reports of the CURRENT state: the period fields are not offered


def grams_text(value):
    return f"{Decimal(str(value)):,.2f} جرام"


def clean_label(label):
    """The owner does not want the word «فعّال» on a total (effective is the default meaning; only the reversed documents are named apart)."""
    return str(label).replace(" (فعّال)", "").replace("(فعّال)", "").replace("  ", " ").strip()


def _bars(groups):
    """Bar-chart data: each bar's length is its share of the group's largest value (strings for the money, a ratio for the length)."""
    out = []
    for g in groups or []:
        top = max((abs(r["value"]) for r in g["rows"]), default=0)
        counts, grams = g.get("kind") == "count", g.get("kind") == "grams"
        out.append({"key": g["key"], "title": g["title"], "tone": g.get("tone", ""), "karat": int(g.get("karat") or 0),
                    "rows": [{"label": r["label"], "text": f"{int(r['value'])} قطعة" if counts else grams_text(r["value"]) if grams else _cell("money", r["value"]), "ratio": float(abs(r["value"]) / top) if top else 0.0} for r in g["rows"]]})
    return out


def _lines(groups):
    """Line-chart data for the Canvas: plain floats (the picture only), the exact figures stay in the tables."""
    out = []
    for g in groups or []:
        values = [float(v) for s in g["series"] for v in s["values"] if v is not None]
        out.append({"key": g["key"], "title": g["title"], "labels": list(g["labels"]), "low": min(values) if values else 0.0, "high": max(values) if values else 0.0,
                    "series": [{"name": s["name"], "tone": s.get("tone", ""), "values": [None if v is None else float(v) for v in s["values"]]} for s in g["series"]]})
    return out


def result_view(built, hidden=HIDDEN_ON_SCREEN):
    """The display form of one `ReportService` result: strings only, plus the open-source references and the paging state."""
    tables = []
    for t in built["tables"]:
        if (built["report"], t["key"]) in hidden:
            continue                                                               # owner review: the sales documents table is not shown (the totals are; the CSV still has every row)
        if not t.get("available", True):
            tables.append({"key": t["key"], "title": t["title"], "available": False, "reason": t.get("reason", ""), "columns": [], "rows": [], "paged": False})
            continue
        cols = t["columns"]
        rows = [{"cells": [_cell(c["kind"], r.get(c["key"])) for c in cols], "ref": r.get("ref") or {}, "dim": bool(r.get("state", "") and ("معكوس" in str(r.get("state")) or "ملغاة" in str(r.get("state")))),
                 "refText": {"INVOICE": "فتح الفاتورة", "CUSTOMER": "فتح العميل", "TRIP": "فتح الرحلة", "CHART": "رسم بياني", "PRICE_CHART": "رسم بياني", "DEMAND_CHART": "رسم بياني", "PIECES": "عرض القطع"}.get((r.get("ref") or {}).get("type"), "")} for r in t["rows"]]
        tables.append({"key": t["key"], "title": t["title"], "available": True, "reason": "", "columns": [{"title": c["title"], "kind": c["kind"]} for c in cols], "rows": rows,
                       "paged": t["paged"], "rowCount": t.get("row_count", len(rows)), "page": t.get("page", 1), "pageSize": t.get("page_size", len(rows)), "hasMore": t.get("has_more", False),
                       "note": t.get("note", ""), "karat": int(t.get("karat") or 0)})
    return {"report": built["report"], "title": built["title"], "filters": built["filters"], "meaning": built["meaning"], "applied": built.get("applied", []), "tables": tables,
            "totals": [{"label": clean_label(x["label"]), "value": _cell(x["kind"], x["value"]) if x["kind"] != "text" else str(x["value"]), "note": x["note"], "group": x.get("group", ""), "karat": int(x.get("karat") or 0), "tone": x.get("tone") or ""}
                       for x in built["totals"]], "options": built.get("options", {}), "error": "", "bars": _bars(built.get("bars")), "lines": _lines(built.get("lines"))}


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
        self._chart, self._chart_request = {}, 0
        self._pieces, self._pieces_request = {}, 0
        self._price_chart, self._price_request = {}, 0
        self._demand_chart, self._demand_request = {}, 0
        self._period_edited = False                                           # False: the period is the automatic one (month start .. today)

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
        out = []
        for s in FILTER_SPECS.get(self._report, []):
            if s.get("type") == "text":
                out.append({"key": s["key"], "label": s["label"], "type": "text", "options": []})
                continue
            options = [{"value": v, "text": t} for v, t in s["options"]]
            if s.get("dynamic"):                                                # the options come from the last answer (e.g. the factories), never typed by the client
                options = (self._result.get("options") or {}).get(s["key"]) or options
            out.append({"key": s["key"], "label": s["label"], "type": "choice", "options": options})
        return out

    @Property("QVariantMap", notify=changed)
    def result(self):
        return self._result

    @Property("QVariantMap", notify=changed)
    def priceChart(self):
        return self._price_chart

    @Slot()
    def closePriceChart(self):
        self._price_request += 1
        self._price_chart = {}
        self.changed.emit()

    @Slot(str, int)
    def showPriceChart(self, karat, days):
        """The daily price chart of one karat (days = 0 means everything stored)."""
        if self._report != "indicators" or self._service is None or self._session is None:
            return
        self._price_request += 1
        request, report = self._price_request, self._report
        self._price_chart = {"loading": True, "karat": str(karat), "days": int(days), "error": "", "points": []}
        actor = self._session.current_actor()
        if actor is None:
            return
        from app.workers.invoice_worker import InvoiceWorker
        worker = InvoiceWorker(lambda _expected_actor: self._service.price_chart(karat, days, _expected_actor=_expected_actor), self._session, actor, request, "report", self)
        worker.completed.connect(lambda r, generation, operation, result, error, code, kind: self._price_chart_completed(r, generation, report, result, error, karat, days))
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        worker.start()
        self.changed.emit()

    def _price_chart_completed(self, request, generation, report, result, error, karat, days):
        actor = self._session.current_actor() if self._session is not None else None
        if actor is None or str(actor.generation) != generation or request != self._price_request or report != self._report:
            return
        if error:
            self._price_chart = {"loading": False, "karat": str(karat), "days": int(days), "error": error, "points": []}
        else:
            points = [{"date": p["date"].isoformat(), "buy": float(p["buy"]), "sell": float(p["sell"]), "average": None if p["average"] is None else float(p["average"])} for p in result["points"]]
            self._price_chart = {"loading": False, "error": "", "karat": str(result["karat"]), "days": result["days"], "points": points,
                                 "low": 0.0 if result["low"] is None else float(result["low"]), "high": 0.0 if result["high"] is None else float(result["high"]),
                                 "lowText": "" if result["low"] is None else f"{result['low']:,.0f}", "highText": "" if result["high"] is None else f"{result['high']:,.0f}",
                                 "first": points[0]["date"] if points else "", "last": points[-1]["date"] if points else ""}
        self.changed.emit()

    @Property("QVariantMap", notify=changed)
    def demandChart(self):
        return self._demand_chart

    @Slot()
    def closeDemandChart(self):
        self._demand_request += 1
        self._demand_chart = {}
        self.changed.emit()

    @Slot(str, str)
    def showDemandChart(self, granularity, years):
        """The sales line chart over the year (granularity M = months, W = weeks); `years` = the years to compare as "2026,2025" (empty = the latest year with data and the one before it)."""
        if self._report != "demand" or self._service is None or self._session is None:
            return
        self._demand_request += 1
        request, report = self._demand_request, self._report
        self._demand_chart = {"loading": True, "granularity": str(granularity), "selected": [], "error": "", "labels": [], "lines": [], "available": []}
        actor = self._session.current_actor()
        if actor is None:
            return
        from app.workers.invoice_worker import InvoiceWorker
        worker = InvoiceWorker(lambda _expected_actor: self._service.demand_chart(granularity, years, _expected_actor=_expected_actor), self._session, actor, request, "report", self)
        worker.completed.connect(lambda r, generation, operation, result, error, code, kind: self._demand_chart_completed(r, generation, report, result, error, granularity))
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        worker.start()
        self.changed.emit()

    def _demand_chart_completed(self, request, generation, report, result, error, granularity):
        actor = self._session.current_actor() if self._session is not None else None
        if actor is None or str(actor.generation) != generation or request != self._demand_request or report != self._report:
            return
        if error:
            self._demand_chart = {"loading": False, "granularity": str(granularity), "error": error, "labels": [], "lines": [], "selected": [], "available": []}
        else:
            self._demand_chart = {"loading": False, "error": "", "granularity": result["granularity"], "selected": [str(y) for y in result["selected"]], "available": [str(y) for y in result["available"]],
                                  "labels": result["labels"], "lines": [{"year": str(line["year"]), "values": line["values"]} for line in result["lines"]], "high": result["high"],
                                  "hasData": result["has_data"], "highText": f"{result['high']:,.0f}"}
        self.changed.emit()

    @Property(bool, notify=changed)
    def periodUsed(self):
        return self._report not in NO_PERIOD

    @Property("QVariantMap", notify=changed)
    def chart(self):
        return self._chart

    @Property("QVariantMap", notify=changed)
    def pieces(self):
        return self._pieces

    @Slot()
    def closePieces(self):
        self._pieces_request += 1                                             # a late answer for a closed list is never shown
        self._pieces = {}
        self.changed.emit()

    @Slot(str, str)
    def showPieces(self, item_type, extra):
        """The pieces (barcodes) of one weight band of the weight stocktake: `extra` is «karat|band»."""
        if self._report not in ("stockweights", "alerts") or self._service is None or self._session is None:
            return
        karat, _, band = str(extra).partition("|")
        actor = self._session.current_actor()
        if actor is None:
            return
        self._pieces_request += 1
        request, filters, report = self._pieces_request, dict(self._filters), self._report
        self._pieces = {"loading": True, "open": True, "title": f"{item_type} — عيار {karat}", "error": "", "rows": []}
        from app.workers.invoice_worker import InvoiceWorker
        worker = InvoiceWorker(lambda _expected_actor: (self._service.alert_pieces(item_type, extra, _expected_actor=_expected_actor) if report == "alerts"
                                                          else self._service.stock_pieces(filters, item_type, karat, band, _expected_actor=_expected_actor)), self._session, actor, request, "report", self)
        worker.completed.connect(lambda r, generation, operation, result, error, code, kind: self._pieces_completed(r, generation, report, result, error, item_type, karat))
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        worker.start()
        self.changed.emit()

    def _pieces_completed(self, request, generation, report, result, error, item_type, karat):
        actor = self._session.current_actor() if self._session is not None else None
        if actor is None or str(actor.generation) != generation or request != self._pieces_request or report != self._report:
            return
        title = f"{item_type} — عيار {karat}"
        if error:
            self._pieces = {"loading": False, "open": True, "title": title, "error": error, "rows": []}
        else:
            self._pieces = {"loading": False, "open": True, "error": "", "title": f"{result['type']} — عيار {result['karat']} — {result['band']}", "count": str(len(result["pieces"])),
                            "weight": grams_text(result["weight"]),
                            "rows": [{"barcode": p["barcode"] or "بدون باركود", "weight": grams_text(p["weight"]), "size": p["size"] or "—", "category": p["category"] or "—", "factory": p["factory"] or "—"} for p in result["pieces"]]}
        self.changed.emit()

    @Slot()
    def closeChart(self):
        self._chart_request += 1                                              # a late answer for a closed chart is never shown
        self._chart = {}
        self.changed.emit()

    @Slot(str, str)
    def showChart(self, item_type, karat=""):
        """The monthly sales of one item type under the CURRENT filters of the report (the same words as the table)."""
        if self._report != "gold" or self._service is None or self._session is None:
            return
        self._chart_request += 1
        request, filters, report = self._chart_request, dict(self._filters), self._report
        self._chart = {"loading": True, "type": item_type + (f" — عيار {karat}" if karat else ""), "error": "", "points": []}
        from app.workers.invoice_worker import InvoiceWorker
        actor = self._session.current_actor()
        if actor is None:
            return
        worker = InvoiceWorker(lambda _expected_actor: self._service.gold_chart(filters, item_type, karat, _expected_actor=_expected_actor), self._session, actor, request, "report", self)
        worker.completed.connect(lambda r, generation, operation, result, error, code, kind: self._chart_completed(r, generation, report, result, error, karat))
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        worker.start()
        self.changed.emit()

    def _chart_completed(self, request, generation, report, result, error, karat_value=""):
        actor = self._session.current_actor() if self._session is not None else None
        if actor is None or str(actor.generation) != generation or request != self._chart_request or report != self._report:
            return
        if error:
            self._chart = {"loading": False, "type": self._chart.get("type", ""), "error": error, "points": []}
        else:
            peak = max([p["weight"] for p in result["points"]] or [Decimal("0")]) or Decimal("1")
            self._chart = {"loading": False, "error": "", "type": result["type"] + (f" — عيار {karat_value}" if karat_value else ""), "applied": result["applied"], "count": str(result["count"]), "weight": grams_text(result["weight"]),
                           "points": [{"month": p["month"], "count": str(p["count"]), "weight": grams_text(p["weight"]), "ratio": float(p["weight"] / peak)} for p in result["points"]]}
        self.changed.emit()

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
        self._period_edited = False
        self._filters = {"from": default_period(report, _today())[0], "to": default_period(report, _today())[1], "text": ""}
        for spec in FILTER_SPECS.get(report, []):
            self._filters[spec["key"]] = "" if spec.get("type") == "text" else spec["options"][0][0]
        self.changed.emit()
        self.run(1)

    @Slot(str, str)
    def setFilter(self, key, value):
        if key in ("from", "to", "text") or any(s["key"] == key for s in FILTER_SPECS.get(self._report, [])):
            if key in ("from", "to"):
                self._period_edited = True                                    # the employee chose a period: it is never moved automatically
            self._filters[key] = value
            self.changed.emit()

    @Slot()
    def refreshPeriod(self):
        """Called when the page is shown again: an automatic period (never edited) follows the calendar - the first day of the current month up to today - and the report is
        re-read when it moved (a new day or a new month). An edited period is left alone."""
        if not self._report or self._period_edited:
            return
        first, last = default_period(self._report, _today())
        if (self._filters.get("from"), self._filters.get("to")) != (first, last):
            self._filters["from"], self._filters["to"] = first, last
            self.changed.emit()
            self.run(1)

    @Slot()
    def apply(self):
        self.run(1)

    @Slot(int)
    def run(self, page):
        if not self._report or self._service is None or self._session is None:
            return
        if not self._period_edited:                                           # an automatic period is re-evaluated at every read (a session kept open past midnight / month end)
            self._filters["from"], self._filters["to"] = default_period(self._report, _today())
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
        if kind == "CHART":
            self.showChart(str(identifier), str(extra or ""))
        elif kind == "PIECES":
            self.showPieces(str(identifier), str(extra or ""))
        elif kind == "PRICE_CHART":
            self.showPriceChart(str(identifier), 90)
        elif kind == "DEMAND_CHART":
            self.showDemandChart(str(identifier), "")
        elif kind in ("INVOICE", "CUSTOMER", "TRIP") and identifier:
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
