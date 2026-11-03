"""The reports API of the iPad app: READ ONLY, with ONE exception (owner request 2026-11-03): the customers report can write a customer balance off (Dead Cash, «رصيد مشطوب») through the existing
`DeadCashService.write_off`: it needs DEAD_CASH_MANAGE (re-checked in the transaction), the user's CURRENT password (re-authentication with the shared failure cooldown), an exact expected balance and a
client request key (idempotent), exactly as on the desktop. Everything else is `ReportService` reads, as the user who logged in.

* Login uses the normal `AuthenticationService` (Argon2id, failure cooldown, generic errors); the verified result establishes a private `AppSession` per web session, so every right is rechecked
  by the report service in its own read transaction exactly as in the desktop program. The browser never supplies a user, an employee or a permission.
* Filters are whitelisted per report (the same specs as the desktop screen); unknown keys are dropped, every value is a short string.
* Answers are the desktop `result_view` (display strings only) plus the filter specs, so the page shows the same numbers as the program."""
from datetime import date
from decimal import Decimal
import json
import logging
import re

from app.session import AppSession
from app.viewmodels.invoice_history_viewmodel import InvoiceHistoryViewModel
from app.viewmodels.reports_viewmodel import FILTER_SPECS, HIDDEN_ON_SCREEN, NO_PERIOD, _bars, _cell, _lines, default_period, grams_text, result_view
from services.authentication_service import AuthenticationError, AuthenticationService, GENERIC_ERROR
from services.customer_account_service import CustomerAccountService
from services.customer_service import CustomerService
from services.dead_cash_service import DeadCashError, DeadCashService
from services.invoice_history_service import InvoiceHistoryService
from services.report_service import REPORT_TITLES, ReportError, ReportService
from webapp.errors import ApiError
from webapp.sessions import SessionStore

logger = logging.getLogger("webapp")
MAX_VALUE = 100
BALANCE_LIMIT = 300
CUSTOMER_PAGE = 30
SEARCHABLE = frozenset({"alerts", "customers", "deadcash", "sales", "stock", "stockweights", "suppliers", "treasury", "gold"})      # the reports whose service honours the text filter
INVOICE_NUMBER = re.compile(r"^[A-Za-z0-9]{6,20}$")
# the iPad shows two tables the desktop hides: the invoice / return / exchange documents of the sales report and the treasury movements
WEB_HIDDEN = HIDDEN_ON_SCREEN - {("sales", "documents"), ("treasury", "movements")}
GROUPS = (("الذهب", ("goldflow", "stock", "alerts", "stockweights", "indicators", "gold", "scrapcost", "demand", "profit", "market")),
          ("النقدية", ("treasury", "cashflow", "makingprofit")))
# The cards of the home page. A module owns some reports (it is shown only when the user may open at least one of them); the module with `rest` owns every report no other module claims.
# To add a card: add a line here (and its icon / subtitle); the page draws one card per module the user may use.
MODULES = (
    {"key": "reports", "title": "التقارير", "subtitle": "الذهب والمخزون والنقدية والمبيعات", "icon": "📊", "rest": True},
    {"key": "customers", "title": "العملاء", "subtitle": "من عليه فلوس ومن له، وكشف الحساب وشطب الرصيد", "icon": "👥", "reports": ("customers", "deadcash")},
    {"key": "treasury", "title": "حركة الخزنة", "subtitle": "حركة الخزنة والبنوك والمصروفات والإيرادات", "icon": "🏦", "reports": ("treasury", "cashflow"), "period": "today"},
    {"key": "daily", "title": "البيع والشراء اليومي", "subtitle": "فواتير اليوم أو أي فترة تختارها، وفتح الفاتورة", "icon": "🧾", "reports": ("sales",), "period": "today"},
    {"key": "inventory", "title": "المخزون", "subtitle": "البحث عن قطعة بالباركود، والنواقص وجرد الأوزان", "icon": "💎", "reports": ("stock", "alerts", "stockweights")},
    # cards without reports: the page draws them itself (`kind`), shown only to a user holding one of `needs`
    {"key": "transfers", "title": "التحويلات", "subtitle": "بين البنوك والمحافظ والحساب الخاص", "icon": "🔁", "kind": "transfers", "needs": ("FINANCIAL_ACCOUNT_OPERATE",)},
    {"key": "reviews", "title": "مراجعات المدير", "subtitle": "ما ينتظر المراجعة", "icon": "🔔", "kind": "reviews", "needs": ("MANAGEMENT_REVIEW", "INVENTORY_RECONCILE")},
    {"key": "calc", "title": "حاسبة الذهب", "subtitle": "وزن وعيار يطلع قيمة الشراء والبيع بالأسعار الحية", "icon": "🧮", "kind": "calc", "needs": ("APP_LOGIN",)},
    {"key": "activity", "title": "سجل النشاط", "subtitle": "من فعل ماذا ومتى (للمالك)", "icon": "🕵️", "kind": "activity", "needs": ("USERS_MANAGE",)},
)


class WebReports:
    def __init__(self, authentication=None, store=None, session_factory=AppSession, service_factory=ReportService, today=date.today, deadcash_factory=None):
        self._authentication = authentication or AuthenticationService()          # ONE instance: the failure cooldown spans attempts
        self._store = store or SessionStore()
        self._session_factory, self._service_factory, self._today = session_factory, service_factory, today
        from webapp.extras import Extras
        self.extras = Extras(self)
        self._deadcash_factory = deadcash_factory or (lambda session: DeadCashService(session, authentication=self._authentication))

    # ---- session
    def login(self, username, password):
        if not isinstance(username, str) or not isinstance(password, str):
            raise ApiError(400, GENERIC_ERROR)
        try:
            result = self._authentication.authenticate(username, password)
        except AuthenticationError as exc:
            raise ApiError(401, str(exc) or GENERIC_ERROR) from None
        except Exception:
            logger.exception("login failed")
            raise ApiError(401, GENERIC_ERROR) from None
        session = self._session_factory()
        if not session._establish(result, int(session.generation)):
            raise ApiError(401, GENERIC_ERROR)
        service = self._service_factory(session)
        if not service.available_reports():
            session.logout()
            raise ApiError(403, "هذا المستخدم لا يملك صلاحية عرض أي تقرير.")
        return self._store.add(session, service)

    def logout(self, token):
        self._store.remove(token)

    def _entry(self, token):
        entry = self._store.get(token)
        if entry is None:
            raise ApiError(401, "انتهت الجلسة، سجّل الدخول من جديد.")
        return entry

    # ---- reads
    def overview(self, token):
        entry = self._entry(token)
        actor = entry.session.current_actor()
        if actor is None:
            self._store.remove(token)
            raise ApiError(401, "انتهت الجلسة، سجّل الدخول من جديد.")
        available = {r["key"]: r["title"] for r in entry.service.available_reports()}
        groups = [{"name": name, "reports": [{"key": k, "title": available[k]} for k in keys if k in available]} for name, keys in GROUPS]
        groups = [g for g in groups if g["reports"]]
        listed = {r["key"] for g in groups for r in g["reports"]}
        general = [{"key": k, "title": t} for k, t in available.items() if k not in listed]
        if general:
            groups.append({"name": "عام", "reports": general})
        return {"user": actor.display_name, "today": self._today().isoformat(), "groups": groups, "modules": self._modules(available, groups, actor.permissions),
                "canWriteOff": "DEAD_CASH_MANAGE" in actor.permissions, "canLegacy": "CUSTOMER_BALANCE_ADJUST" in actor.permissions}

    @staticmethod
    def _modules(available, groups, permissions=frozenset()):
        """One entry per card the user may use: {key, title, subtitle, icon, count, groups}. A module with reports shows only those; the `rest` module shows every other group."""
        claimed = {k for m in MODULES for k in m.get("reports", ())}
        out = []
        for module in MODULES:
            if module.get("kind"):
                if set(module["needs"]) & set(permissions):
                    out.append({"key": module["key"], "title": module["title"], "subtitle": module["subtitle"], "icon": module["icon"], "period": "", "kind": module["kind"], "count": 0, "groups": []})
                continue
            if module.get("rest"):
                shown = [{"name": g["name"], "reports": [r for r in g["reports"] if r["key"] not in claimed]} for g in groups]
            else:
                shown = [{"name": "", "reports": [{"key": k, "title": available[k]} for k in module["reports"] if k in available]}]
            shown = [g for g in shown if g["reports"]]
            if shown:
                out.append({"key": module["key"], "title": module["title"], "subtitle": module["subtitle"], "icon": module["icon"], "period": module.get("period", ""), "kind": "reports",
                            "count": sum(len(g["reports"]) for g in shown), "groups": shown})
        return out

    def report(self, token, key, params):
        entry = self._entry(token)
        if key not in REPORT_TITLES:
            raise ApiError(404, "التقرير غير معروف.")
        specs = FILTER_SPECS.get(key, [])
        filters = self._filters(key, params)
        actor = entry.session.current_actor()
        try:
            built = entry.service.run(key, filters, _expected_actor=actor)
        except PermissionError as exc:
            raise ApiError(403, str(exc) or "ليس لديك صلاحية عرض هذا التقرير.") from None
        except ReportError as exc:
            raise ApiError(400, str(exc)) from None
        except Exception:
            logger.exception("report %s failed", key)
            raise ApiError(500, "تعذر تحميل التقرير؛ أعد المحاولة.") from None
        view = result_view(built, WEB_HIDDEN)
        options = view.get("options") or {}
        view["filterSpecs"] = [self._spec(s, options) for s in specs]
        view["periodUsed"] = key not in NO_PERIOD
        view["searchable"] = key in SEARCHABLE
        if key == "customers":
            view["balances"] = self._balances(built.get("balances") or [])
        return view

    @staticmethod
    def _balances(items):
        """The customers of the report with their balance (largest first, at most BALANCE_LIMIT): `owes` = على العميل, `credit` = للعميل, display strings plus the exact balance for the write-off."""
        ordered = sorted(items, key=lambda i: abs(i["balance"]), reverse=True)
        out = [{"id": i["customer_id"], "name": i["name"], "code": i["code"] or "", "balance": str(i["balance"]),
                "owes": _cell("money", i["balance"]) if i["balance"] > 0 else "", "credit": _cell("money", -i["balance"]) if i["balance"] < 0 else ""} for i in ordered[:BALANCE_LIMIT]]
        return {"rows": out, "total": len(items), "limited": len(items) > BALANCE_LIMIT}

    # ---- Dead Cash write-off (the only write): the desktop service, its re-authentication and its idempotent request key
    def deadcash_context(self, token, params):
        entry = self._entry(token)
        service = self._deadcash(entry)
        try:
            return service.context(str(params.get("customer") or "")[:30], _expected_actor=entry.session.current_actor())
        except PermissionError as exc:
            raise ApiError(403, str(exc)) from None
        except DeadCashError as exc:
            raise ApiError(400, str(exc)) from None
        except ValueError as exc:
            raise ApiError(400, str(exc) or "عميل غير صحيح.") from None
        except Exception:
            logger.exception("dead cash context failed")
            raise ApiError(500, "تعذر تحميل بيانات الشطب؛ أعد المحاولة.") from None

    def write_off(self, token, body):
        entry = self._entry(token)
        service = self._deadcash(entry)
        text = lambda name: str(body.get(name) if body.get(name) is not None else "")
        password = body.get("password")
        try:
            return service.write_off(text("customer"), text("amount"), text("reason"), password if isinstance(password, str) else "", text("expected_balance"), text("request_key"),
                                     silent=True, _expected_actor=entry.session.current_actor())
        except PermissionError as exc:
            raise ApiError(403, str(exc)) from None
        except DeadCashError as exc:
            raise ApiError(409 if exc.code in ("BALANCE_CHANGED", "REQUEST_CONFLICT") else 400, str(exc)) from None
        except ValueError as exc:
            raise ApiError(400, str(exc) or "بيانات الشطب غير صحيحة.") from None
        except Exception:
            logger.exception("dead cash write-off failed")
            raise ApiError(500, "تعذر تنفيذ الشطب؛ راجع الرصيد ثم أعد المحاولة.") from None
        finally:
            password = None

    def _deadcash(self, entry):
        if getattr(entry, "deadcash", None) is None:
            entry.deadcash = self._deadcash_factory(entry.session)
        return entry.deadcash

    # ---- invoices (read only): one invoice in full, and the invoices of one customer
    def invoice(self, token, params):
        """The invoice page of the desktop history (the SAME service and the same display rules as `InvoiceHistoryViewModel`), read only."""
        entry = self._entry(token)
        number = str(params.get("id") or "")[:30]
        try:
            detail = InvoiceHistoryService(entry.session).detail(number, _expected_actor=entry.session.current_actor())
        except PermissionError as exc:
            raise ApiError(403, str(exc) or "ليس لديك صلاحية عرض الفاتورة.") from None
        except ValueError as exc:
            raise ApiError(404, str(exc) or "الفاتورة غير موجودة.") from None
        except Exception:
            logger.exception("invoice detail failed")
            raise ApiError(500, "تعذر تحميل الفاتورة؛ أعد المحاولة.") from None
        vm = InvoiceHistoryViewModel(entry.session)
        vm._detail = detail
        view = {}
        for name in ("detail", "saleLines", "oldGoldLines", "payments", "timeline", "returns", "exchanges", "corrections"):
            try:
                view[name] = getattr(vm, name)
            except Exception:
                logger.exception("invoice view part %s failed", name)
                view[name] = {} if name == "detail" else []
        return json.loads(json.dumps(view, default=str))

    def customer_invoices(self, token, params):
        """The finalized invoices of ONE customer (newest first, 50 per page), as the desktop Customers screen lists them."""
        entry = self._entry(token)
        text = lambda name: str(params.get(name) or "")[:MAX_VALUE]
        page = text("page")
        try:
            data = CustomerAccountService(entry.session).customer_invoices(text("customer"), text("from"), text("to"), "", text("text"), int(page) if page.isdigit() and int(page) > 0 else 1,
                                                                           _expected_actor=entry.session.current_actor())
        except PermissionError as exc:
            raise ApiError(403, str(exc) or "ليس لديك صلاحية عرض الفواتير.") from None
        except ValueError as exc:
            raise ApiError(400, str(exc) or "بيانات غير صحيحة.") from None
        except Exception:
            logger.exception("customer invoices failed")
            raise ApiError(500, "تعذر تحميل الفواتير؛ أعد المحاولة.") from None
        from app.viewmodels.invoice_history_viewmodel import KIND_TYPE_TEXT, SETTLEMENT_TEXT, STATUS_TEXT
        from app.formatting import money
        rows = [{"id": str(r["invoice_id"]), "number": r["invoice_number"], "date": str(r["finalized_at"]), "kind": KIND_TYPE_TEXT.get(r.get("transaction_kind"), ""),
                 "total": money(r["invoice_total"]), "status": STATUS_TEXT.get(r["invoice_status"], "") + " · " + SETTLEMENT_TEXT.get(r["settlement_status"], "")} for r in data.get("items", [])]
        return {"rows": rows, "page": int(data.get("page") or 1), "hasMore": bool(data.get("has_more"))}

    def customer_search(self, token, params):
        """Find a customer (name, mobile, code) or an invoice by its exact number, read only: {invoice: {id, number} | None, customers: [...], page, hasMore}. The invoice lookup is the exact
        printed number (never a partial match) and is silently left out for a user without the invoice right; the customers come from the desktop Customers search with their balances."""
        entry = self._entry(token)
        text = str(params.get("text") or "").strip()[:MAX_VALUE]
        page = str(params.get("page") or "1")
        page = int(page) if page.isdigit() and 0 < int(page) < 1000 else 1
        if not text:
            return {"invoice": None, "customers": [], "page": 1, "hasMore": False}
        actor, invoice = entry.session.current_actor(), None
        if INVOICE_NUMBER.match(text) and page == 1:
            try:
                found = InvoiceHistoryService(entry.session).find_by_number(text, _expected_actor=actor)
                invoice = {"id": str(found), "number": text.upper()} if found else None
            except PermissionError:
                invoice = None
            except Exception:
                logger.exception("invoice lookup failed")
                invoice = None
        try:
            data = CustomerService(entry.session).search_customers(search=text, status="ALL", page=page, page_size=CUSTOMER_PAGE)
        except PermissionError as exc:
            if invoice is None:
                raise ApiError(403, str(exc) or "ليس لديك صلاحية عرض العملاء.") from None
            data = {"items": [], "has_more": False}
        except ValueError as exc:
            raise ApiError(400, str(exc) or "نص البحث غير صحيح.") from None
        except Exception:
            logger.exception("customer search failed")
            raise ApiError(500, "تعذر البحث؛ أعد المحاولة.") from None
        rows = []
        for r in data.get("items", []):
            balance = Decimal(str(r.get("balance") or 0))
            rows.append({"id": str(r["customer_id"]), "name": r["name"], "code": r.get("customer_code") or "", "mobile": r.get("mobile") or "", "place": r.get("place") or "",
                         "active": bool(r.get("is_active")), "balance": str(balance), "owes": _cell("money", balance) if balance > 0 else "", "credit": _cell("money", -balance) if balance < 0 else ""})
        return {"invoice": invoice, "customers": rows, "page": page, "hasMore": bool(data.get("has_more"))}

    def _filters(self, key, params):
        """The whitelisted filters of one report (unknown keys dropped, short strings) with the automatic period when none was chosen."""
        filters = {}
        for name in ("from", "to", "text") + tuple(s["key"] for s in FILTER_SPECS.get(key, [])):
            value = params.get(name)
            if value is not None:
                filters[name] = str(value)[:MAX_VALUE]
        page = str(params.get("page") or "1")
        filters["page"] = int(page) if page.isdigit() and 0 < int(page) < 10000 else 1
        if key not in NO_PERIOD:
            first, last = default_period(key, self._today())
            filters["from"] = filters.get("from") or first                      # an automatic period unless the viewer chose one
            filters["to"] = filters.get("to") or last
        return filters

    def chart(self, token, params):
        """The pop-up charts of the desktop reports, read only: monthly sales of an item type (CHART), the daily price of a karat (PRICE_CHART), the yearly sales lines (DEMAND_CHART) and the
        pieces behind a row (PIECES). The same service methods as the desktop screen; every right is rechecked by the service."""
        entry = self._entry(token)
        kind, actor = str(params.get("kind") or ""), entry.session.current_actor()
        service = entry.service

        def text(name):
            return str(params.get(name) or "")[:MAX_VALUE]
        try:
            if kind == "PRICE_CHART":
                result = service.price_chart(text("id") or text("karat"), int(text("days") or 90), _expected_actor=actor)
                multi = len(result["points"]) > 200
                labels = [p["date"].strftime("%Y-%m-%d" if multi else "%m-%d") for p in result["points"]]
                series = [{"name": "شراء المحل", "tone": "buy", "values": [p["buy"] for p in result["points"]]},
                          {"name": "بيع المحل", "tone": "sell", "values": [p["sell"] for p in result["points"]]},
                          {"name": "متوسط 30 يوم", "tone": "avg", "values": [p["average"] for p in result["points"]]}]
                return {"kind": kind, "title": f"أسعار عيار {result['karat']} اليومية", "days": result["days"], "karat": str(result["karat"]),
                        "lines": _lines([{"key": "price", "title": "", "labels": labels, "series": series}]) if labels else [], "bars": [], "pieces": None}
            if kind == "DEMAND_CHART":
                result = service.demand_chart(text("id") or text("granularity") or "M", text("years"), _expected_actor=actor)
                series = [{"name": str(line["year"]), "tone": f"y{i}", "values": line["values"]} for i, line in enumerate(result["lines"])]
                return {"kind": kind, "title": ("المبيعات الأسبوعية" if result["granularity"] == "W" else "المبيعات الشهرية") + " على مدار السنة (جرام، كل الأعيار)",
                        "granularity": result["granularity"], "selected": [str(y) for y in result["selected"]], "available": [str(y) for y in result["available"]],
                        "hasData": bool(result["has_data"]),
                        "lines": _lines([{"key": "demand", "title": "", "labels": list(result["labels"]), "series": series}]) if result["has_data"] else [], "bars": [], "pieces": None}
            if kind == "CHART":
                result = service.gold_chart(self._filters("gold", params), text("id"), text("extra"), _expected_actor=actor)
                rows = [{"label": p["month"], "value": p["weight"]} for p in result["points"]]
                bars = _bars([{"key": "monthly", "title": f"{result['type']} — المبيعات الشهرية (الوزن)", "tone": "receivable", "kind": "grams", "rows": rows}])
                for bar, point in zip(bars[0]["rows"], result["points"]):
                    bar["text"] = f"{point['count']} قطعة • {grams_text(point['weight'])}"
                return {"kind": kind, "title": result["type"], "applied": result["applied"], "count": str(result["count"]), "weight": grams_text(result["weight"]),
                        "lines": [], "bars": bars, "pieces": None}
            if kind == "PIECES":
                if text("report") == "alerts":
                    result = service.alert_pieces(text("id"), text("extra"), _expected_actor=actor)
                else:
                    karat, _, band = text("extra").partition("|")
                    result = service.stock_pieces(self._filters("stockweights", params), text("id"), karat, band, _expected_actor=actor)
                rows = [[p.get("barcode") or "بدون باركود", grams_text(p["weight"]), p.get("size") or "—", p.get("category") or "—", p.get("factory") or "—"] for p in result["pieces"]]
                title = f"{result['type']} — عيار {result.get('karat', '')} {result.get('band', '')}".strip()
                return {"kind": kind, "title": title, "lines": [], "bars": [],
                        "pieces": {"columns": ["الباركود", "الوزن", "المقاس", "الفئة", "المصنع"], "rows": rows, "count": str(len(rows)), "weight": grams_text(result["weight"])}}
        except PermissionError as exc:
            raise ApiError(403, str(exc) or "ليس لديك صلاحية عرض هذا الرسم.") from None
        except ReportError as exc:
            raise ApiError(400, str(exc)) from None
        except Exception:
            logger.exception("chart %s failed", kind)
            raise ApiError(500, "تعذر تحميل الرسم؛ أعد المحاولة.") from None
        raise ApiError(404, "الرسم غير معروف.")

    @staticmethod
    def _spec(spec, options):
        if spec.get("type") == "text":
            return {"key": spec["key"], "label": spec["label"], "type": "text", "options": []}
        choices = [{"value": v, "text": t} for v, t in spec["options"]]
        if spec.get("dynamic"):
            choices = options.get(spec["key"]) or choices                         # e.g. the factories, from the last answer
        return {"key": spec["key"], "label": spec["label"], "type": "choice", "options": choices}
