"""Basic reports (packet26): sales and returns, available stock, gold movements, customers, suppliers, treasury. READ ONLY.

Principles (every number below follows them):
- The sources are the existing ledgers and document tables (through `ReportRepository`, plain SELECTs, one snapshot, no locks, a fixed number of statements per report).
  Nothing is computed from another report's summary and no JOIN multiplies a movement: payments, ledger rows and reversals are read as separate sets or as per-row aggregates.
- Every total is computed here, in Decimal, over the WHOLE filtered result; the page only slices the detail rows. A result larger than `MAX_ROWS` is refused (never truncated).
- A section that cannot be read (a missing table at this schema level, a missing right) says so (`available=False` + reason); it is never shown as zero or as an empty successful report.
- Reversed documents stay visible with their reversal reference and date; the totals of EFFECTIVE documents are separate from the reversed ones. Documents are dated by their own
  effective date (invoice: finalization; return / exchange: finalization / occurrence). Nothing here says that the effective documents add up to the cash movement of the period
  (a sale on account, a payment on a later day, an old-gold purchase and a return all move money differently): the treasury report is the money report.
- Sales value is never «profit»; no cost, margin or stock valuation exists in this phase.
- Weights of different karats are never added; supplier gold stays in the supplier's own karat; the BOOK balance is a 21K-equivalent posted figure and is not a weight."""
from contextlib import contextmanager
import csv
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from repositories.invoice_lifecycle_repository import exchange_code
from repositories.invoice_return_repository import return_code
from repositories.market_trip_repository import entry_code, trip_code
from repositories.report_repository import ReportRepository
from services.authorization_service import AuthorizationService
from services.correction_rules import correction_code
from services.gold_position_service import GoldPositionError, merge_reconciliations, read_transaction

PAGE_SIZE = 50
MAX_ROWS = 50000
ZERO = Decimal("0")
ZERO3, ZERO2 = Decimal("0.000"), Decimal("0.00")
BASE_PERMISSION = "REPORTS_VIEW"
REPORT_PERMISSIONS = {"sales": ("INVOICE_VIEW",), "stock": ("INVENTORY_VIEW",), "gold": ("OLD_GOLD_VIEW",), "customers": ("CUSTOMERS_VIEW",),
                      "suppliers": ("SUPPLIER_VIEW",), "treasury": ()}                      # treasury: CASH_VIEW and / or BANK_VIEW per section
REPORT_TITLES = {"sales": "المبيعات والمرتجعات", "stock": "المخزون المتاح", "gold": "حركة المخزون والذهب", "customers": "العملاء", "suppliers": "الموردون", "treasury": "الخزنة والبنوك"}
KIND_TEXT = {"SALE": "بيع", "MIXED": "بيع مع شراء كسر", "OLD_GOLD_PURCHASE": "شراء كسر", "RETURN": "مرتجع", "EXCHANGE": "استبدال"}
STATUS_TEXT = {"IN_STOCK": "متاح", "SOLD": "مباع", "SCRAPPED": "كسر", "REMOVED": "مُخرج", "RETURNED_PENDING": "مرتجع بانتظار الفحص", "CONVERTED_TO_OLD_GOLD": "حُوّل إلى كسر"}
CLASS_TEXT = {"NEW_STOCK": "جديد", "OLD_STOCK": "قديم"}
LINE_TEXT = {"SELL_INVENTORY": "بيع قطعة", "SELL_OLD_GOLD": "بيع من الكسر", "BUY_OLD_GOLD": "شراء كسر", "SELL_MANUAL": "بيع يدوي"}
REF_TEXT = {"INVOICE": "فاتورة", "RETURN": "مرتجع", "EXCHANGE": "استبدال", "CUSTOMER_ACCOUNT_SETTLEMENT": "تحصيل / تسوية حساب", "CUSTOMER_DEPOSIT": "عربون",
            "CUSTOMER_DEPOSIT_REFUND": "رد عربون", "FULL_REVERSAL": "عكس", "SETTLEMENT_CORRECTION": "تصحيح تسوية", "DEAD_CASH_WRITE_OFF": "شطب رصيد",
            "DEAD_CASH_REVERSAL": "عكس شطب", "UNKNOWN": "غير محدد"}
GOLD_SOURCES = {"SCRAP": "كسر المحل (بالعيار)", "PIECES": "المشغولات (قطع)", "ASSAYED": "مخزون مرتجع بعيار فعلي", "BOOK": "عهد السوق الدفترية (مكافئ عيار 21)",
                "MARKETER": "استحقاق المسوقجي (عيار 21)"}
MARKETER_TYPE_TEXT = {"PURCHASE_IN": "شراء", "SALE_OUT": "بيع", "GOLD_RETURN": "رجوع ذهب للمحل", "SETTLEMENT": "تسوية لمورد", "TRIP_BOOK_IN": "تسليم من عهدة رحلة",
                      "TRIP_BOOK_OUT": "استلام إلى عهدة رحلة"}


UNKNOWN_TEXT = "غير معلوم"
FILTER_LABELS = {"kind": "النوع", "state": "الحالة", "status": "الحالة", "classification": "التصنيف", "karat": "العيار", "source": "المصدر", "balance": "الرصيد",
                 "inactive": "العملاء المتوقفون", "supplier": "المورد", "account": "الحساب"}


class _Gate:
    """The CURRENT right of the actor, re-read from the database inside the report's own read transaction (`require_permission_read`, no lock). The ActorContext of the
    session may be older than a revoked grant; every section decides from this gate, never from `actor.permissions`."""

    def __init__(self, authorization, connection, actor):
        self._authorization, self._connection, self._actor, self._memo = authorization, connection, actor, {}

    def __call__(self, code):
        if code not in self._memo:
            try:
                self._authorization.require_permission_read(self._connection, self._actor, code)
                self._memo[code] = True
            except PermissionError:
                self._memo[code] = False
        return self._memo[code]


class ReportError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _dec(value):
    return Decimal(str(value if value is not None else 0))


def _parse_day(value, label):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ReportError("DATE_INVALID", f"{label} غير صحيح (yyyy-MM-dd).") from None


def _range(filters, today):
    first = _parse_day(filters.get("from"), "تاريخ البداية") or today.replace(day=1)
    last = _parse_day(filters.get("to"), "تاريخ النهاية") or today
    if first > last:
        raise ReportError("RANGE_INVALID", "تاريخ البداية بعد تاريخ النهاية.")
    return first, last, datetime.combine(first, time.min), datetime.combine(last + timedelta(days=1), time.min)


def _text_filter(filters):
    from utils.search_normalization import normalize_search_text
    text = str(filters.get("text") or "").strip()
    if len(text) > 100:
        raise ReportError("TEXT_LONG", "نص البحث طويل.")
    return text, normalize_search_text(text)


def _matches(folded, raw, *values):
    if not raw:
        return True
    from utils.search_normalization import normalize_search_text
    return any(folded in normalize_search_text(str(v or "")) or raw in str(v or "") for v in values)


def _dateish(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    return value


def col(key, title, kind="text"):
    return {"key": key, "title": title, "kind": kind}


def table(key, title, columns, rows, *, paged=False, note=""):
    return {"key": key, "title": title, "columns": columns, "rows": rows, "paged": paged, "note": note}


def total(label, value, kind, note=""):
    return {"label": label, "value": value, "kind": kind, "note": note}


class ReportService:
    def __init__(self, session, *, transaction_factory=read_transaction, repository=ReportRepository, authorization=None, today=None):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        self._authorization = authorization or AuthorizationService(session)
        self._today = today or date.today

    # ------------------------------------------------------------------ public
    def available_reports(self):
        actor = self._session.current_actor()
        if actor is None or BASE_PERMISSION not in actor.permissions:
            return []
        out = []
        for key, needed in REPORT_PERMISSIONS.items():
            ok = set(needed) <= set(actor.permissions) if key != "treasury" else bool({"CASH_VIEW", "BANK_VIEW"} & set(actor.permissions))
            if ok:
                out.append({"key": key, "title": REPORT_TITLES[key]})
        return out

    def run(self, report, filters=None, *, _expected_actor=None):
        """The report with its WHOLE-result totals and ONE page of its paged detail table."""
        built = self._build(report, dict(filters or {}), _expected_actor)
        page = max(1, int(built["filters"].get("page") or 1))
        for t in built["tables"]:
            if t["paged"]:
                count = len(t["rows"])
                t["row_count"], t["page"], t["page_size"] = count, page, PAGE_SIZE
                t["rows"] = t["rows"][(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
                t["has_more"] = count > page * PAGE_SIZE
        return built

    def export_csv(self, report, filters=None, directory=None, *, _expected_actor=None):
        """Every row of every table of the report for the current filters (not the page), UTF-8 with BOM for Excel. Returns the file path."""
        built = self._build(report, dict(filters or {}), _expected_actor)
        folder = Path(directory) if directory else Path.home() / "Documents" / "masGold-reports"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{report}-{built['filters']['from']}-{built['filters']['to']}-{datetime.now():%H%M%S}.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow([built["title"]])
            for line in built["applied"]:
                writer.writerow([line])
            for line in built["meaning"]:
                writer.writerow([line])
            for t in built["tables"]:
                writer.writerow([])
                writer.writerow([t["title"]])
                if not t.get("available", True):
                    writer.writerow([t.get("reason", "غير متاح")])
                    continue
                writer.writerow([c["title"] for c in t["columns"]])
                for row in t["rows"]:
                    writer.writerow([self._cell(row.get(c["key"])) for c in t["columns"]])
            writer.writerow([])
            writer.writerow(["الإجماليات (كما هي معرَّفة في عمود الملاحظة؛ المبالغ كاملة للفلتر المعروض، وأرصدة الحسابات لا يغيّرها البحث)"])
            for item in built["totals"]:
                writer.writerow([item["label"], self._cell(item["value"]), item["note"]])
        return str(path)

    @staticmethod
    def _cell(value):
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, (datetime, date)):
            return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
        return "" if value is None else value

    # ------------------------------------------------------------------ plumbing
    def _build(self, report, filters, expected_actor):
        if report not in REPORT_TITLES:
            raise ReportError("REPORT_INVALID", "التقرير غير معروف.")
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        first, last, start, end = _range(filters, self._today())
        filters = {**filters, "from": first.isoformat(), "to": last.isoformat()}
        with self._transaction() as connection:
            gate = _Gate(self._authorization, connection, actor)
            denied = PermissionError("ليس لديك صلاحية عرض هذا التقرير.")
            if not gate(BASE_PERMISSION):
                raise denied
            if report == "treasury":
                if not (gate("CASH_VIEW") or gate("BANK_VIEW")):                  # the last right needed is gone: a clear refusal, nothing is read
                    raise denied
            elif not all(gate(code) for code in REPORT_PERMISSIONS[report]):
                raise denied
            repo = self._repository(connection)
            built = getattr(self, f"_{report}")(repo, gate, filters, start, end)
            if not self._session.is_current(actor):
                raise PermissionError("انتهت جلسة الدخول.")
        built.update(report=report, title=REPORT_TITLES[report], filters=filters, applied=self._applied(filters))
        for t in built["tables"]:
            if len(t["rows"]) > MAX_ROWS:
                raise ReportError("TOO_MANY_ROWS", "نتيجة التقرير أكبر من الحد المسموح: ضيّق الفترة أو الفلتر.")
        return built

    @staticmethod
    def _applied(filters):
        """The filters that were really applied (the same words on the screen and in the CSV)."""
        out = [f"الفترة: من {filters['from']} إلى {filters['to']}"]
        if str(filters.get("text") or "").strip():
            out.append(f"البحث: {str(filters['text']).strip()}")
        for key, label in FILTER_LABELS.items():
            if str(filters.get(key) or "") not in ("", "ALL"):
                out.append(f"{label}: {filters[key]}")
        return out

    @staticmethod
    def _unavailable(key, title, reason):
        return {"key": key, "title": title, "columns": [], "rows": [], "paged": False, "note": "", "available": False, "reason": reason}

    # ------------------------------------------------------------------ 1. sales and returns
    def _sales(self, repo, gate, filters, start, end):
        kind_filter = str(filters.get("kind") or "ALL")
        state_filter = str(filters.get("state") or "ALL")
        if kind_filter not in ("ALL", "SALE", "MIXED", "OLD_GOLD_PURCHASE", "RETURN", "EXCHANGE") or state_filter not in ("ALL", "EFFECTIVE", "REVERSED", "VOIDED"):
            raise ReportError("FILTER_INVALID", "فلتر غير صحيح.")
        raw, folded = _text_filter(filters)
        docs = []
        for r in repo.sales_invoices(start, end):
            kind = r["kind"]
            legacy = r["origin"] == "LEGACY"
            sell = None if r["sell_total"] is None else _dec(r["sell_total"])
            buy = None if r["buy_total"] is None else _dec(r["buy_total"])
            payments = int(r.get("payment_count") or 0)
            ledger = int(r.get("account_count") or 0)
            docs.append({"doc": "INVOICE", "kind": kind, "id": str(r["id"]), "number": r["number"], "date": _dateish(r["doc_date"]), "customer": r["customer_name"] or "", "legacy": legacy,
                         # NULL stays NULL. For a LEGACY invoice the kind is only INFERRED from its saved lines (no purchase line => «SALE»), and a missing historical line proves
                         # nothing: both stored amounts are taken as stored (NULL = unknown, a stored 0 = proven zero), whatever the inferred kind. For a NATIVE invoice the
                         # kind is authoritative: the side that does not belong to it is a proven zero.
                         "sales": sell if legacy else (sell if kind in ("SALE", "MIXED") else ZERO2),
                         "oldgold": buy if legacy else (buy if kind in ("MIXED", "OLD_GOLD_PURCHASE") else ZERO2),
                         "returns": ZERO2, "exchange": ZERO2,
                         # a legacy invoice with no payment row / no ledger row carries no EVIDENCE of how it was paid: unknown, not zero (a native one with none is a proven zero)
                         "in": None if legacy and payments == 0 else _dec(r["money_in"]), "out": None if legacy and payments == 0 else _dec(r["money_out"]),
                         "account": None if legacy and ledger == 0 else _dec(r["account_delta"]),
                         "voided": r["status"] == "VOIDED", "reversal_id": r["reversal_id"], "reversal_at": r["reversal_at"], "source": None})
        for r in repo.sales_returns(start, end):
            docs.append({"doc": "RETURN", "kind": "RETURN", "id": str(r["id"]), "number": return_code(r["id"]), "date": _dateish(r["doc_date"]), "customer": r["customer_name"] or "", "legacy": False,
                         "sales": ZERO2, "oldgold": ZERO2, "returns": _dec(r["total_amount"]), "exchange": ZERO2, "in": ZERO2, "out": _dec(r["money_paid"]),
                         "account": -_dec(r["account_amount"]), "voided": False, "reversal_id": r["reversal_id"], "reversal_at": r["reversal_at"],
                         "source": {"type": "INVOICE", "id": str(r["source_invoice_id"])}, "source_number": r["source_number"]})
        for r in repo.sales_exchanges(start, end):
            docs.append({"doc": "EXCHANGE", "kind": "EXCHANGE", "id": str(r["id"]), "number": exchange_code(r["id"]), "date": _dateish(r["doc_date"]), "customer": r["customer_name"] or "", "legacy": False,
                         "sales": ZERO2, "oldgold": ZERO2, "returns": ZERO2, "exchange": _dec(r["signed_amount"]), "in": _dec(r["money_received"]), "out": _dec(r["money_paid"]),
                         "account": _dec(r["account_signed"]), "voided": False, "reversal_id": r["reversal_id"], "reversal_at": r["reversal_at"],
                         "source": {"type": "INVOICE", "id": str(r["source_invoice_id"])}, "source_number": r["source_number"]})
        for d in docs:
            d["reversed"] = d["reversal_id"] is not None
            d["effective"] = not d["reversed"] and not d["voided"]
        kept = [d for d in docs if (kind_filter in ("ALL", d["kind"])) and _matches(folded, raw, d["number"], d["customer"], d.get("source_number"))
                and (state_filter == "ALL" or (state_filter == "EFFECTIVE" and d["effective"]) or (state_filter == "REVERSED" and d["reversed"])
                     or (state_filter == "VOIDED" and d["voided"] and not d["reversed"]))]
        kept.sort(key=lambda d: (d["date"], int(d["id"])), reverse=True)
        eff = [d for d in kept if d["effective"]]
        rev = [d for d in kept if d["reversed"]]
        void = [d for d in kept if d["voided"] and not d["reversed"]]

        def known(items, key):
            return sum((d[key] for d in items if d[key] is not None), ZERO2)

        def unknown(items, key):
            return sum(1 for d in items if d[key] is None)

        def show(value):
            return UNKNOWN_TEXT if value is None else value
        rows = []
        for d in kept:
            state = ("معكوس " + correction_code("FULL_REVERSAL", d["reversal_id"]) + " • " + (d["reversal_at"].strftime("%Y-%m-%d") if d["reversal_at"] else "")) if d["reversed"] \
                else "ملغاة" if d["voided"] else "فعّال"
            if d["legacy"] and any(d[k] is None for k in ("sales", "oldgold", "in", "out", "account")):
                state += " • قديمة: بعض القيم غير معلومة"
            ref = {"type": "INVOICE", "id": d["id"]} if d["doc"] == "INVOICE" else d["source"]
            rows.append({"date": d["date"], "kind": KIND_TEXT.get(d["kind"], d["kind"]), "number": d["number"] + (f" (على فاتورة {d['source_number']})" if d.get("source_number") else ""),
                         "customer": d["customer"], "sales": show(d["sales"]), "oldgold": show(d["oldgold"]), "returns": d["returns"], "exchange": d["exchange"], "in": show(d["in"]),
                         "out": show(d["out"]), "account": show(d["account"]), "state": state, "ref": ref})
        cols = [col("date", "التاريخ الفعلي", "date"), col("kind", "النوع"), col("number", "الرقم"), col("customer", "العميل"), col("sales", "قيمة البيع", "money"),
                col("oldgold", "قيمة شراء الكسر", "money"), col("returns", "قيمة المرتجع", "money"), col("exchange", "فرق الاستبدال", "money"), col("in", "المحصَّل", "money"),
                col("out", "المدفوع للعميل", "money"), col("account", "أثره على حساب العميل", "money"), col("state", "الحالة")]
        sales_total, returns_total = known(eff, "sales"), known(eff, "returns")
        totals = [total("عدد المستندات الفعّالة", len(eff), "count")]
        for label, key, note in (("قيمة البيع (فعّال)", "sales", "مجموع قيمة فواتير البيع والبيع مع شراء كسر، بغض النظر عن التحصيل"), ("قيمة شراء الكسر (فعّال)", "oldgold", ""),
                                 ("قيمة المرتجع (فعّال)", "returns", ""), ("فرق الاستبدال الصافي (فعّال)", "exchange", "موجب: على العميل، سالب: للعميل — لا يُجمع مع البيع")):
            missing = unknown(eff, key)
            totals.append(total(label + (" — جزئي" if missing else ""), known(eff, key), "money", (note + " • " if note else "") + (f"يستثني {missing} مستندًا قديمًا غير معلوم القيمة" if missing else "")))
        totals.append(total("فرق قيمة البيع والمرتجع" + (" — جزئي" if unknown(eff, "sales") else ""), sales_total - returns_total, "money",
                            "ليس ربحًا: لا تكلفة ولا هامش في هذه المرحلة" + (" • يستثني بيعًا قديمًا غير معلوم" if unknown(eff, "sales") else "")))
        for label, key, note in (("المحصَّل (فعّال)", "in", "حركة نقدية/بنكية مرتبطة بالمستندات؛ لا تساوي بالضرورة قيمة البيع"), ("المدفوع للعملاء (فعّال)", "out", ""),
                                 ("أثر المستندات على حسابات العملاء (فعّال)", "account", "موجب: زاد المستحق على العملاء")):
            missing = unknown(eff, key)
            totals.append(total(label + (" — جزئي" if missing else ""), known(eff, key), "money", (note + " • " if note else "") + (f"يستثني {missing} فاتورة قديمة بلا دليل" if missing else "")))
        incomplete = [d for d in eff if d["legacy"] and any(d[k] is None for k in ("sales", "oldgold", "in", "out", "account"))]
        totals += [total("مستندات قديمة ناقصة المعرفة (عددها)", len(incomplete), "count", "قيمها غير معلومة لا صفر؛ لا يُستنتج بيعها من الصافي ولا تُنشأ لها دفعات"),
                   total("المستندات المعكوسة (عددها)", len(rev), "count", "لا تدخل الإجماليات أعلاه"), total("قيمة البيع المعكوسة", known(rev, "sales"), "money"),
                   total("قيمة المرتجع المعكوسة", known(rev, "returns"), "money"), total("الفواتير الملغاة (عددها)", len(void), "count")]
        reversed_unknown = unknown(rev, "sales")
        if reversed_unknown:                                                    # the reversed total excludes unknown values too: it is partial as well
            for item in totals:
                if item["label"] == "قيمة البيع المعكوسة":
                    item["label"] += " — جزئي"
                    item["note"] = f"يستثني {reversed_unknown} مستندًا قديمًا معكوسًا غير معلوم القيمة"
        meaning = ["التاريخ: تاريخ إتمام كل مستند (الفاتورة: وقت الإتمام، المرتجع والاستبدال: وقت إتمامهما).",
                   "قيمة البيع غير المحصَّل: البيع الآجل أو على الحساب أو المقابل بكسر؛ المحصَّل مأخوذ من دفعات المستند الفعلية كما في سجل الفواتير.",
                   "المرتجع: قيمته كما سُجّلت في مستند المرتجع، وأثره على الحساب يظهر بإشارة حساب العميل (سالب = للعميل).",
                   "المستند المعكوس يظهر برقم العكس وتاريخه ولا يدخل إجماليات الفعّال.",
                   "«غير معلوم»: فاتورة قديمة لم يثبت مصدرها هذا التقسيم أو لا دليل على دفعها؛ يُعرض غير معلوم ولا يُحسب صفرًا، والإجمالي المتأثر موسوم «جزئي»."]
        return {"meaning": meaning, "tables": [table("documents", "المستندات", cols, rows, paged=True)], "totals": totals}

    # ------------------------------------------------------------------ 2. available stock
    def _stock(self, repo, gate, filters, start, end):
        status = str(filters.get("status") or "IN_STOCK")
        statuses = ["IN_STOCK", "SOLD", "SCRAPPED", "REMOVED", "RETURNED_PENDING", "CONVERTED_TO_OLD_GOLD"] if status == "ALL" else [status]
        if status != "ALL" and status not in STATUS_TEXT:
            raise ReportError("FILTER_INVALID", "حالة غير صحيحة.")
        classification = str(filters.get("classification") or "")
        if classification and classification not in CLASS_TEXT:
            raise ReportError("FILTER_INVALID", "تصنيف غير صحيح.")
        karat = str(filters.get("karat") or "")
        raw, folded = _text_filter(filters)
        items = [r for r in repo.stock_items(statuses)
                 if (not classification or r["stock_classification"] == classification) and (not karat or str(r["karat_value"]) == karat)
                 and _matches(folded, raw, r["barcode"], r["item_type"], r["category"], r["factory"], r["invoice_number"])]
        groups = {}
        for r in items:
            g = groups.setdefault((int(r["karat_value"]), r["stock_classification"], r["inventory_status"]), {"count": 0, "weight": ZERO3})
            g["count"] += 1
            g["weight"] += _dec(r["weight_grams"])
        summary = [{"karat": f"عيار {k}", "classification": CLASS_TEXT.get(c, c), "status": STATUS_TEXT.get(s, s), "count": g["count"], "weight": g["weight"]}
                   for (k, c, s), g in sorted(groups.items(), key=lambda kv: (-kv[0][0], kv[0][1], kv[0][2]))]
        per_karat = {}
        for (k, _, _), g in groups.items():
            p = per_karat.setdefault(k, {"count": 0, "weight": ZERO3})
            p["count"] += g["count"]
            p["weight"] += g["weight"]
        rows = [{"barcode": r["barcode"] or "—", "karat": f"عيار {int(r['karat_value'])}", "classification": CLASS_TEXT.get(r["stock_classification"], r["stock_classification"]),
                 "status": STATUS_TEXT.get(r["inventory_status"], r["inventory_status"]), "type": r["item_type"] or "", "category": r["category"] or "", "factory": r["factory"] or "",
                 "weight": _dec(r["weight_grams"]), "size": r["size"] or "",
                 "invoice": (r["invoice_number"] + (f" ({LINE_TEXT.get(r['invoice_line_type'], r['invoice_line_type'] or '')})" if r["invoice_line_type"] else "")) if r["invoice_number"] else "غير مرتبطة بفاتورة",
                 "ref": {"type": "INVOICE", "id": str(r["invoice_id"])} if r["invoice_id"] else None} for r in items]
        rows.sort(key=lambda x: (x["karat"], x["barcode"]))
        scols = [col("karat", "العيار"), col("classification", "التصنيف"), col("status", "الحالة"), col("count", "عدد القطع", "count"), col("weight", "الوزن (جرام)", "grams")]
        dcols = [col("barcode", "الباركود"), col("karat", "العيار"), col("classification", "التصنيف"), col("status", "الحالة"), col("type", "الصنف"), col("category", "الفئة"),
                 col("factory", "المصنع"), col("weight", "الوزن (جرام)", "grams"), col("size", "المقاس"), col("invoice", "الفاتورة المرتبطة")]
        totals = [total("عدد القطع", len(items), "count")] + [total(f"عيار {k}: عدد القطع", v["count"], "count") for k, v in sorted(per_karat.items(), reverse=True)] \
                 + [total(f"عيار {k}: الوزن (جرام)", v["weight"], "grams", "وزن هذا العيار وحده") for k, v in sorted(per_karat.items(), reverse=True)]
        meaning = ["المخزون المتاح = القطع بحالة «متاح» (الافتراضي)؛ يمكن عرض الحالات الأخرى لمراجعة القطع المباعة أو المرتجعة.",
                   "الأوزان تُجمع داخل كل عيار فقط؛ لا يوجد إجمالي وزن لعيارات مختلفة. هذا التقرير للقطع المشغولة، وكسر المحل في تقرير «حركة المخزون والذهب».",
                   "الفاتورة المرتبطة: آخر فاتورة غير مسودة تحمل هذه القطعة في أحد أسطرها (الرابط `invoice_lines.inventory_item_id`)؛ ليست بالضرورة فاتورة أصل اقتناء القطعة، وتفتح من شاشة الفواتير."]
        return {"meaning": meaning, "tables": [table("summary", "الملخص حسب العيار والتصنيف والحالة", scols, summary), table("items", "القطع", dcols, rows, paged=True)], "totals": totals}

    # ------------------------------------------------------------------ 3. gold movements (a ledger per source, never added together)
    def _gold(self, repo, gate, filters, start, end):
        source = str(filters.get("source") or "SCRAP")
        if source not in GOLD_SOURCES:
            raise ReportError("FILTER_INVALID", "مصدر غير صحيح.")
        if source not in ("SCRAP", "PIECES"):
            filters.pop("karat", None)                                           # the karat filter exists only for the sources that are kept per master karat
        needs = {"BOOK": ("MARKET_TRIP_MANAGE",), "MARKETER": ("INTERMEDIARY_HOLDING_VIEW",), "ASSAYED": ("MARKET_TRIP_MANAGE",), "PIECES": ("INVENTORY_VIEW",)}.get(source, ())
        meaning = ["لكل مصدر دفتره ووحدته؛ لا يوجد إجمالي واحد للمصادر لأن ذلك يضاعف الذهب بين المحل والعهدة والمخزون والمسوقجي.",
                   "البحث يصفّي الحركات المعروضة وعددها فقط؛ الأرصدة والملخصات تبقى كاملة للفترة."]
        if not all(gate(code) for code in needs):                                # checked BEFORE any read of this source's data
            return {"meaning": meaning, "tables": [self._unavailable("denied", GOLD_SOURCES[source], "تحتاج صلاحية إضافية لعرض هذا المصدر.")], "totals": []}
        return getattr(self, f"_gold_{source.lower()}")(repo, filters, start, end, meaning)

    def _gold_scrap(self, repo, filters, start, end, meaning):
        opening_rows = repo.scrap_opening(start)
        opening = {int(r["karat_id"]): _dec(r["grams"]) for r in opening_rows}
        opening_karat = {int(r["karat_id"]): int(r["karat_value"]) for r in opening_rows}
        moves = repo.scrap_movements(start, end)
        karat = str(filters.get("karat") or "")
        per = {}
        for m in moves:
            p = per.setdefault(int(m["karat_id"]), {"karat": int(m["karat_value"]), "in": ZERO3, "out": ZERO3, "ref_only": 0})
            g = _dec(m["grams"])
            if m["balance_effect"] == "COUNTED" and m["grams"] is not None:
                p["in" if g > 0 else "out"] += abs(g) if g > 0 else abs(g)
            else:
                p["ref_only"] += 1
        for kid in opening:
            per.setdefault(kid, {"karat": opening_karat[kid], "in": ZERO3, "out": ZERO3, "ref_only": 0})
        running = dict(opening)
        rows = []
        for m in moves:
            kid, g = int(m["karat_id"]), _dec(m["grams"])
            counted = m["balance_effect"] == "COUNTED" and m["grams"] is not None
            if counted:
                running[kid] = running.get(kid, ZERO3) + g
            rows.append({"date": _dateish(m["occurred_at"]), "karat": f"عيار {int(m['karat_value'])}", "code": m["movement_code"] or "", "in": g if counted and g > 0 else ZERO3,
                         "out": -g if counted and g < 0 else ZERO3, "balance": running.get(kid, ZERO3) if counted else None,
                         "effect": "يدخل الرصيد" if counted else "مرجعي فقط (لا يدخل الرصيد)", "reference": m["reference_code"] or "",
                         "ref": {"type": "INVOICE", "id": str(m["invoice_id"])} if m["invoice_id"] else None, "_karat_value": int(m["karat_value"])})
        if karat:
            rows = [r for r in rows if str(r["_karat_value"]) == karat]
        raw, folded = _text_filter(filters)
        rows = [r for r in rows if _matches(folded, raw, r["code"], r["reference"], r["karat"], r["effect"])]
        rows.sort(key=lambda r: r["date"], reverse=True)
        karat_values = {**opening_karat, **{int(m["karat_id"]): int(m["karat_value"]) for m in moves}}
        summary = []
        for kid, p in sorted(per.items(), key=lambda kv: -(kv[1]["karat"] or 0)):
            kv = p["karat"] or karat_values.get(kid)
            if karat and str(kv) != karat:
                continue
            o = opening.get(kid, ZERO3)
            summary.append({"karat": f"عيار {kv}" if kv else f"عيار #{kid}", "opening": o, "in": p["in"], "out": p["out"], "closing": o + p["in"] - p["out"], "ref_only": p["ref_only"]})
        cols = [col("date", "التاريخ", "date"), col("karat", "العيار"), col("code", "نوع الحركة"), col("in", "داخل (جرام)", "grams"), col("out", "خارج (جرام)", "grams"),
                col("balance", "رصيد العيار بعدها", "grams"), col("effect", "أثرها"), col("reference", "المرجع")]
        scol = [col("karat", "العيار"), col("opening", "رصيد أول الفترة", "grams"), col("in", "داخل", "grams"), col("out", "خارج", "grams"), col("closing", "رصيد آخر الفترة", "grams"),
                col("ref_only", "حركات مرجعية (لا تدخل الرصيد)", "count")]
        meaning.append("كسر المحل بالجرام الفعلي لكل عيار على حدة؛ الرصيد = حركات «COUNTED» فقط، والحركات المرجعية تظهر ولا تدخل الرصيد.")
        return {"meaning": meaning, "tables": [table("summary", "رصيد الكسر حسب العيار", scol, summary), table("movements", "الحركات", cols, rows, paged=True)],
                "totals": [total("عدد الحركات المعروضة", len(rows), "count")]}

    def _gold_pieces(self, repo, filters, start, end, meaning):
        moves = repo.piece_movements(start, end)
        karat = str(filters.get("karat") or "")
        rows, groups = [], {}
        for m in moves:
            if karat and str(m["karat_value"]) != karat:
                continue
            enter = m["to_inventory_status"] == "IN_STOCK" and m["from_inventory_status"] != "IN_STOCK"
            leave = m["from_inventory_status"] == "IN_STOCK" and m["to_inventory_status"] != "IN_STOCK"
            weight = _dec(m["to_weight_grams"] if m["to_weight_grams"] is not None else m["from_weight_grams"])
            g = groups.setdefault((int(m["karat_value"]), m["type_name"]), {"enter": 0, "leave": 0, "enter_w": ZERO3, "leave_w": ZERO3})
            if enter:
                g["enter"] += 1
                g["enter_w"] += weight
            if leave:
                g["leave"] += 1
                g["leave_w"] += weight
            rows.append({"date": _dateish(m["occurred_at"]), "karat": f"عيار {int(m['karat_value'])}", "type": m["type_name"], "from": STATUS_TEXT.get(m["from_inventory_status"], m["from_inventory_status"] or "—"),
                         "to": STATUS_TEXT.get(m["to_inventory_status"], m["to_inventory_status"] or "—"), "weight": weight, "reference": m["reference_code"] or "", "item": f"#{m['item_id']}"})
        raw, folded = _text_filter(filters)
        rows = [r for r in rows if _matches(folded, raw, r["type"], r["reference"], r["item"], r["from"], r["to"], r["karat"])]
        rows.sort(key=lambda r: r["date"], reverse=True)
        summary = [{"karat": f"عيار {k}", "type": t, "enter": g["enter"], "enter_w": g["enter_w"], "leave": g["leave"], "leave_w": g["leave_w"]}
                   for (k, t), g in sorted(groups.items(), key=lambda kv: (-kv[0][0], kv[0][1]))]
        meaning.append("المشغولات: قطع بهوية وباركود؛ الداخل = دخول حالة «متاح»، الخارج = خروج منها. الرصيد الحالي للقطع في تقرير «المخزون المتاح» وليس هنا.")
        scols = [col("karat", "العيار"), col("type", "نوع الحركة"), col("enter", "قطع داخلة", "count"), col("enter_w", "وزنها (جرام)", "grams"), col("leave", "قطع خارجة", "count"), col("leave_w", "وزنها (جرام)", "grams")]
        cols = [col("date", "التاريخ", "date"), col("karat", "العيار"), col("type", "نوع الحركة"), col("from", "من حالة"), col("to", "إلى حالة"), col("weight", "الوزن (جرام)", "grams"),
                col("item", "القطعة"), col("reference", "المرجع")]
        return {"meaning": meaning, "tables": [table("summary", "ملخص حركة القطع", scols, summary), table("movements", "الحركات", cols, rows, paged=True)],
                "totals": [total("عدد الحركات", len(rows), "count")]}

    def _gold_assayed(self, repo, filters, start, end, meaning):
        if not repo.has_table("shop_assayed_stock_movements"):
            return {"meaning": meaning, "tables": [self._unavailable("assayed", GOLD_SOURCES["ASSAYED"], "هذا الجدول غير موجود في مستوى قاعدة البيانات الحالي.")], "totals": []}
        opening = repo.assayed_opening(start)
        moves = repo.assayed_movements(start, end)
        g_in = sum((_dec(m["grams_delta"]) for m in moves if _dec(m["grams_delta"]) > 0), ZERO3)
        g_out = -sum((_dec(m["grams_delta"]) for m in moves if _dec(m["grams_delta"]) < 0), ZERO3)
        p_in = sum((_dec(m["eq21_posted_delta"]) for m in moves if _dec(m["eq21_posted_delta"]) > 0), ZERO3)
        p_out = -sum((_dec(m["eq21_posted_delta"]) for m in moves if _dec(m["eq21_posted_delta"]) < 0), ZERO3)
        og, op = _dec(opening["grams"]), _dec(opening["posted"])
        summary = [{"label": "رصيد أول الفترة", "grams": og, "posted": op}, {"label": "داخل", "grams": g_in, "posted": p_in}, {"label": "خارج", "grams": g_out, "posted": p_out},
                   {"label": "رصيد آخر الفترة", "grams": og + g_in - g_out, "posted": op + p_in - p_out}]
        names = {"RECEIVE": "استلام من رحلة", "ISSUE": "إخراج إلى رحلة", "RECEIVE_REVERSAL": "عكس استلام", "ISSUE_REVERSAL": "عكس إخراج"}
        rows = [{"date": _dateish(m["created_at"]), "type": names.get(m["movement_type"], m["movement_type"]), "item": f"بند {entry_code(m['trip_id'], m['entry_no'])} — عيار {m['fineness_permille']}‰",
                 "grams": _dec(m["grams_delta"]), "posted": _dec(m["eq21_posted_delta"]), "reference": entry_code(m["trip_id"], m["entry_no"]),
                 "ref": {"type": "TRIP", "id": str(m["trip_id"])}} for m in reversed(moves)]
        raw, folded = _text_filter(filters)
        rows = [r for r in rows if _matches(folded, raw, r["type"], r["item"], r["reference"])]
        meaning.append("مخزون الذهب المرتجع بعيار فعلي (غير معياري): بالجرام الفعلي وبمكافئ عيار 21 المرحَّل المخزَّن معه؛ كل حركة معكوسة لها حركة تعويض مستقلة تُحتسب بجانب الأصل.")
        return {"meaning": meaning, "tables": [table("summary", "الرصيد", [col("label", "البند"), col("grams", "جرام فعلي", "grams"), col("posted", "مكافئ عيار 21 مرحَّل", "eq21")], summary),
                                               table("movements", "الحركات", [col("date", "التاريخ", "date"), col("type", "النوع"), col("item", "البند"), col("grams", "جرام", "grams"),
                                                                              col("posted", "مكافئ 21 مرحَّل", "eq21"), col("reference", "مستند الرحلة")], rows, paged=True)],
                "totals": [total("عدد الحركات", len(rows), "count")]}

    def _gold_book(self, repo, filters, start, end, meaning):
        """BOOK trips. TWO different things are shown and never mixed:
        1. the CURRENT balance after the effective reconciliations (source balance B, the posted loss L, the rounding settlement r, B - b_star), merged and VALIDATED by the same
           `gold_position_service.merge_reconciliations` the position uses (a record problem is an error, never a zero);
        2. the DOCUMENTS of the selected period with their CURRENT reversal state (not a historical snapshot: the reversal date is not used to rebuild an old balance)."""
        if not repo.has_table("market_trip_book_lines"):
            return {"meaning": meaning, "tables": [self._unavailable("book", GOLD_SOURCES["BOOK"], "هذا الجدول غير موجود في مستوى قاعدة البيانات الحالي.")], "totals": []}
        try:
            merged = merge_reconciliations(repo.book_groups_current(), repo.book_effective_reconciliations())
        except GoldPositionError as exc:
            raise ReportError(exc.code, str(exc)) from None
        current, tb, tl, tr_, ta = [], ZERO3, ZERO3, ZERO3, ZERO3
        for t in merged:
            balance = t["balance"]
            rc = t["reconciliation"]
            b_star = rc["b_star"] if rc else ZERO3
            after = balance.posted - b_star
            tb += balance.posted
            tl += rc["loss"] if rc else ZERO3
            tr_ += rc["rounding"] if rc else ZERO3
            ta += after
            if balance.posted or rc:
                current.append({"trip": t["code"], "source": balance.posted, "loss": -(rc["loss"]) if rc else ZERO3, "rounding": -(rc["rounding"]) if rc else ZERO3, "after": after,
                                "recon": (f"{rc['code']} • جيل {rc['generation']}") if rc else "بلا تسوية فعّالة", "ref": {"type": "TRIP", "id": str(t["id"])}})
        raw, folded = _text_filter(filters)
        lines = repo.book_lines(end)
        docs, per = [], {}
        for l in lines:
            when = _dateish(l["entry_date"])
            if not (start <= when < end):
                continue
            signed, reversed_ = _dec(l["signed_eq21_posted"]), l["reversal_id"] is not None
            p = per.setdefault(int(l["trip_id"]), {"in": ZERO3, "out": ZERO3, "reversed": 0, "recon": ZERO3})
            if reversed_:
                p["reversed"] += 1
            else:
                p["in" if signed > 0 else "out"] += abs(signed)
            what = f"عيار {l['karat_value']}" if l["basis"] == "MASTER_KARAT" else f"عيار فعلي {l['fineness_permille']}‰"
            docs.append({"date": when, "trip": trip_code(l["trip_id"]), "entry": entry_code(l["trip_id"], l["entry_no"]), "kind": l["entry_kind"], "physical": f"{_dec(l['weight_grams'])} جرام {what}",
                         "posted": signed, "state": "معكوس (حالة اليوم)" if reversed_ else "فعّال", "ref": {"type": "TRIP", "id": str(l["trip_id"])}})
        for r in repo.book_recon_documents(start, end):
            when = _dateish(r["entry_date"])
            reversed_ = r["reversal_id"] is not None
            effect = -_dec(r["b_star"]) if r["b_star"] is not None else None
            p = per.setdefault(int(r["trip_id"]), {"in": ZERO3, "out": ZERO3, "reversed": 0, "recon": ZERO3})
            if reversed_:
                p["reversed"] += 1
            elif effect is not None:
                p["recon"] += effect
            docs.append({"date": when, "trip": trip_code(r["trip_id"]), "entry": entry_code(r["trip_id"], r["entry_no"]), "kind": "BK_RECON",
                         "physical": "تسوية الرصيد الدفتري" + (f" • الفاقد المرحّل {_dec(r['loss_posted'])} • فرق التقريب {_dec(r['rounding_settlement'])}" if r["loss_posted"] is not None else " • بلا سجل مكتمل"),
                         "posted": effect if effect is not None else UNKNOWN_TEXT, "state": "معكوس (حالة اليوم)" if reversed_ else "فعّال", "ref": {"type": "TRIP", "id": str(r["trip_id"])}})
        docs = [d for d in docs if _matches(folded, raw, d["trip"], d["entry"], d["kind"], d["physical"])]
        docs.sort(key=lambda r: (r["date"], r["entry"]), reverse=True)
        summary = [{"trip": trip_code(t), "in": p["in"], "out": p["out"], "recon": p["recon"], "reversed": p["reversed"], "ref": {"type": "TRIP", "id": str(t)}}
                   for t, p in sorted(per.items(), reverse=True) if p["in"] or p["out"] or p["recon"] or p["reversed"]]
        meaning.append("العهد الدفترية: الرصيد رقم واحد بمكافئ عيار 21 المرحَّل (ليس وزنًا فعليًا). «الرصيد الحالي بعد التسويات» = رصيد المصدر B − الرصيد المرحّل المسوّى b_star من التسوية الفعّالة "
                       "(= الفاقد المرحّل L + فرق التقريب r، وهما منفصلان: التقريب ليس فاقدًا ولا ربحًا). عكس التسوية يعيد الرصيد، والجيل الجديد وحده هو الفعّال، والإغلاق لا يُسقط الذهب.")
        meaning.append("«مستندات الفترة» تُعرض بحالة العكس الحالية اليوم وليست لقطة تاريخية لرصيد نهاية الفترة؛ إسناد العكس إلى الفترات قرار مفتوح لعلي، فلا يُقدَّم رصيد تاريخي.")
        return {"meaning": meaning,
                "tables": [table("current", "الرصيد الحالي بعد التسويات (اليوم، مكافئ عيار 21 مرحَّل)",
                                 [col("trip", "الرحلة"), col("source", "رصيد المصدر B", "eq21"), col("loss", "أثر الفاقد المرحّل (−L)", "eq21"), col("rounding", "أثر فرق التقريب (−r)", "eq21"),
                                  col("after", "الرصيد بعد التسوية", "eq21"), col("recon", "التسوية الفعّالة")], current),
                           table("summary", "مستندات الفترة وحالتها الحالية (ليست رصيدًا تاريخيًا)",
                                 [col("trip", "الرحلة"), col("in", "داخل (فعّال)", "eq21"), col("out", "خارج (فعّال)", "eq21"), col("recon", "أثر التسويات الفعّالة", "eq21"),
                                  col("reversed", "مستندات معكوسة", "count")], summary),
                           table("movements", "المستندات", [col("date", "التاريخ", "date"), col("trip", "الرحلة"), col("entry", "المستند"), col("kind", "النوع"), col("physical", "الحقائق الفعلية"),
                                                           col("posted", "المكافئ المرحَّل", "eq21"), col("state", "الحالة")], docs, paged=True)],
                "totals": [total("رصيد المصدر B (كل الرحلات، اليوم)", tb, "eq21"), total("أثر الفاقد المرحّل المسوَّى (−L)", -tl, "eq21"),
                           total("أثر فرق التقريب المسوَّى (−r)", -tr_, "eq21", "ليس فاقدًا ولا ربحًا"), total("الرصيد الحالي بعد التسويات", ta, "eq21", "اليوم، بالتسويات الفعّالة فقط"),
                           total("عدد المستندات المعروضة", len(docs), "count")]}

    def _gold_marketer(self, repo, filters, start, end, meaning):
        if not repo.has_table("marketer_gold_movements"):
            return {"meaning": meaning, "tables": [self._unavailable("marketer", GOLD_SOURCES["MARKETER"], "هذا الجدول غير موجود في مستوى قاعدة البيانات الحالي.")], "totals": []}
        moves = repo.marketer_gold(end)
        per, rows = {}, []
        for m in moves:
            when = _dateish(m["created_at"])
            delta = _dec(m["weight_delta"])
            p = per.setdefault(int(m["party_id"]), {"name": m["party_name"], "opening": ZERO3, "in": ZERO3, "out": ZERO3})
            if when < start:
                p["opening"] += delta
            else:
                p["in" if delta > 0 else "out"] += abs(delta)
                src = (f"{m['doc_kind']}" if m["document_id"] else "تسوية لمورد" if m["settlement_id"] else entry_code(m["trip_id"], m["trip_entry_no"]) if m["trip_entry_id"] else "")
                rows.append({"date": when, "party": m["party_name"], "type": MARKETER_TYPE_TEXT.get(m["movement_type"].replace("_REVERSAL", ""), m["movement_type"]) + (" (عكس)" if m["movement_type"].endswith("_REVERSAL") else ""),
                             "delta": delta, "source": src, "ref": {"type": "TRIP", "id": str(m["trip_id"])} if m["trip_entry_id"] else None})
        raw, folded = _text_filter(filters)
        rows = [r for r in rows if _matches(folded, raw, r["party"], r["type"], r["source"])]
        rows.sort(key=lambda r: r["date"], reverse=True)
        summary = [{"party": p["name"], "opening": p["opening"], "in": p["in"], "out": p["out"], "closing": p["opening"] + p["in"] - p["out"]} for _, p in sorted(per.items(), key=lambda kv: kv[1]["name"])]
        meaning.append("استحقاق المسوقجي من ذهب المحل بعيار 21 (جرام): رصيد = مجموع كل حركات دفتره بما فيها حركات التعويض عن العكس؛ يخص المحل ولا يدخل حساب مورد.")
        return {"meaning": meaning, "tables": [table("summary", "استحقاق كل مسوقجي", [col("party", "المسوقجي"), col("opening", "أول الفترة", "grams"), col("in", "داخل", "grams"), col("out", "خارج", "grams"),
                                                                                         col("closing", "آخر الفترة", "grams")], summary),
                                               table("movements", "الحركات", [col("date", "التاريخ", "date"), col("party", "المسوقجي"), col("type", "النوع"), col("delta", "الأثر (جرام)", "grams"),
                                                                              col("source", "المصدر")], rows, paged=True)],
                "totals": [total("عدد الحركات", len(rows), "count")]}

    # ------------------------------------------------------------------ 4. customers
    def _customers(self, repo, gate, filters, start, end):
        state = str(filters.get("balance") or "ALL")
        if state not in ("ALL", "OWES", "CREDIT", "SETTLED"):
            raise ReportError("FILTER_INVALID", "فلتر الرصيد غير صحيح.")
        include_inactive = str(filters.get("inactive") or "") in ("1", "true", "True")
        raw, folded = _text_filter(filters)
        before = {int(r["customer_id"]): _dec(r["total"]) for r in repo.customer_before(start)}
        period = {}
        for r in repo.customer_period(start, end):
            p = period.setdefault(int(r["customer_id"]), {"up": ZERO2, "collections": ZERO2, "returns": ZERO2, "other_down": ZERO2})
            up, down, kind = _dec(r["owed_up"]), _dec(r["owed_down"]), r["reference_type"]
            p["up"] += up
            if kind in ("CUSTOMER_ACCOUNT_SETTLEMENT", "CUSTOMER_DEPOSIT"):
                p["collections"] += -down
            elif kind in ("RETURN", "EXCHANGE"):
                p["returns"] += -down
            else:
                p["other_down"] += -down
        rows, receivable, payable, counts = [], ZERO2, ZERO2, {"OWES": 0, "CREDIT": 0, "SETTLED": 0}
        up_all = col_all = ret_all = other_all = ZERO2
        for c in repo.customer_rows():
            if not c["is_active"] and not include_inactive:
                continue
            if not _matches(folded, raw, c["name"], c["mobile"], c["customer_code"]):
                continue
            balance = _dec(c["balance"])
            st = "OWES" if balance > 0 else "CREDIT" if balance < 0 else "SETTLED"
            if state != "ALL" and st != state:
                continue
            cid = int(c["id"])
            p = period.get(cid, {"up": ZERO2, "collections": ZERO2, "returns": ZERO2, "other_down": ZERO2})
            opening = before.get(cid, ZERO2)
            closing = opening + p["up"] - p["collections"] - p["returns"] - p["other_down"]
            counts[st] += 1
            receivable += balance if balance > 0 else ZERO2
            payable += -balance if balance < 0 else ZERO2
            up_all += p["up"]
            col_all += p["collections"]
            ret_all += p["returns"]
            other_all += p["other_down"]
            rows.append({"code": c["customer_code"], "name": c["name"], "mobile": c["mobile"] or "", "opening": opening, "up": p["up"], "collections": p["collections"], "returns": p["returns"],
                         "other": p["other_down"], "closing": closing, "balance": balance, "state": {"OWES": "على العميل", "CREDIT": "للعميل", "SETTLED": "الحساب متوازن"}[st],
                         "ref": {"type": "CUSTOMER", "id": str(cid)}})
        cols = [col("code", "الكود"), col("name", "العميل"), col("mobile", "الجوال"), col("opening", "رصيد أول الفترة", "money"), col("up", "مستحق جديد على العميل", "money"),
                col("collections", "تحصيل وعربون", "money"), col("returns", "مرتجع واستبدال", "money"), col("other", "تخفيضات أخرى", "money"), col("closing", "رصيد آخر الفترة", "money"),
                col("balance", "الرصيد الحالي", "money"), col("state", "الحالة")]
        totals = [total("عدد العملاء المعروضين", len(rows), "count"), total("إجمالي المستحق على العملاء (الأرصدة الحالية الموجبة)", receivable, "money"),
                  total("إجمالي المستحق للعملاء (الأرصدة الحالية السالبة)", payable, "money"), total("عدد العملاء على الحساب / للعميل / متوازن", f"{counts['OWES']} / {counts['CREDIT']} / {counts['SETTLED']}", "text"),
                  total("مستحق جديد في الفترة", up_all, "money"), total("تحصيل وعربون في الفترة", col_all, "money"), total("مرتجع واستبدال في الفترة", ret_all, "money"),
                  total("تخفيضات أخرى في الفترة", other_all, "money", "عكس وتصحيح وشطب وغيرها")]
        meaning = ["الإشارة: موجب = على العميل (مستحق للمحل)، سالب = للعميل. المصدر دفتر حساب العميل نفسه الذي يقرأه كشف الحساب.",
                   "الأعمدة تفصل المستحق الجديد عن التحصيل والمرتجع؛ التحصيل هنا حركة على الحساب وليس بالضرورة نقدية الفترة. الرصيد الحالي هو رصيد اليوم لا رصيد نهاية الفترة المختارة.",
                   "يفتح العميل من الجدول إلى شاشة العملاء وكشف حسابه."]
        return {"meaning": meaning, "tables": [table("customers", "أرصدة العملاء", cols, rows, paged=True)], "totals": totals}

    # ------------------------------------------------------------------ 5. suppliers
    def _suppliers(self, repo, gate, filters, start, end):
        raw, folded = _text_filter(filters)
        supplier_filter = str(filters.get("supplier") or "")
        suppliers = {int(s["id"]): s for s in repo.supplier_rows() if _matches(folded, raw, s["name"], s["phone"]) and (not supplier_filter or str(s["id"]) == supplier_filter)}
        gold_before = {(int(r["supplier_id"]), int(r["karat_id"])): _dec(r["grams"]) for r in repo.supplier_gold_before(start)}
        making_before = {int(r["supplier_id"]): _dec(r["amount"]) for r in repo.supplier_making_before(start)}
        gold = [m for m in repo.supplier_gold_movements(start, end) if int(m["supplier_id"]) in suppliers]
        making = [m for m in repo.supplier_making_movements(start, end) if int(m["supplier_id"]) in suppliers]
        g_sum, karat_value = {}, {}
        for m in gold:
            k = (int(m["supplier_id"]), int(m["karat_id"]))
            karat_value[k] = int(m["karat_value"])
            p = g_sum.setdefault(k, {"up": ZERO3, "down": ZERO3})
            g = _dec(m["grams"])
            p["up" if g > 0 else "down"] += abs(g)
        for s in suppliers.values():
            if s["karat_id"] is not None:
                karat_value[(int(s["id"]), int(s["karat_id"]))] = int(s["karat_value"])
        m_sum = {}
        for m in making:
            p = m_sum.setdefault(int(m["supplier_id"]), {"up": ZERO2, "down": ZERO2})
            a = _dec(m["amount"])
            p["up" if a > 0 else "down"] += abs(a)
        keys = set(g_sum) | set(gold_before) | {(sid, int(s["karat_id"])) for sid, s in suppliers.items() if s["karat_id"] is not None}
        gold_rows = []
        for (sid, kid) in sorted(k for k in keys if k[0] in suppliers):
            o, p = gold_before.get((sid, kid), ZERO3), g_sum.get((sid, kid), {"up": ZERO3, "down": ZERO3})
            if not (o or p["up"] or p["down"]):
                continue
            gold_rows.append({"supplier": suppliers[sid]["name"], "karat": f"عيار {karat_value.get((sid, kid), '؟')}", "opening": o, "up": p["up"], "down": p["down"], "closing": o + p["up"] - p["down"]})
        making_rows = []
        for sid, s in sorted(suppliers.items(), key=lambda kv: kv[1]["name"]):
            o, p = making_before.get(sid, ZERO2), m_sum.get(sid, {"up": ZERO2, "down": ZERO2})
            if o or p["up"] or p["down"]:
                making_rows.append({"supplier": s["name"], "opening": o, "up": p["up"], "down": p["down"], "closing": o + p["up"] - p["down"]})
        names = {sid: s["name"] for sid, s in suppliers.items()}
        det = [{"date": _dateish(m["occurred_at"]), "supplier": names[int(m["supplier_id"])], "account": f"ذهب عيار {m['karat_value']}", "type": m["type_name"],
                "gold": _dec(m["grams"]), "amount": None, "source": m["source_reference"] or "", "description": m["description"] or ""} for m in gold]
        det += [{"date": _dateish(m["occurred_at"]), "supplier": names[int(m["supplier_id"])], "account": "مصنعيات", "type": m["type_name"], "gold": None, "amount": _dec(m["amount"]),
                 "source": m["source_reference"] or "", "description": m["description"] or ""} for m in making]
        det.sort(key=lambda r: r["date"], reverse=True)
        gcols = [col("supplier", "المورد"), col("karat", "عيار حساب الذهب"), col("opening", "أول الفترة (جرام)", "grams"), col("up", "زيادة علينا", "grams"), col("down", "نقص علينا", "grams"),
                 col("closing", "آخر الفترة (جرام)", "grams")]
        mcols = [col("supplier", "المورد"), col("opening", "أول الفترة (ج)", "money"), col("up", "زيادة علينا", "money"), col("down", "نقص علينا", "money"), col("closing", "آخر الفترة (ج)", "money")]
        dcols = [col("date", "التاريخ", "date"), col("supplier", "المورد"), col("account", "الحساب"), col("type", "نوع الحركة (يشمل العكس)"), col("gold", "ذهب (جرام)", "grams"),
                 col("amount", "مصنعيات (ج)", "money"), col("source", "المرجع"), col("description", "البيان")]
        totals = [total("عدد الحركات", len(det), "count"), total("إجمالي مصنعيات علينا للموردين (أرصدة آخر الفترة الموجبة)", sum((r["closing"] for r in making_rows if r["closing"] > 0), ZERO2), "money"),
                  total("إجمالي مصنعيات لنا عند الموردين (السالبة)", -sum((r["closing"] for r in making_rows if r["closing"] < 0), ZERO2), "money")]
        for k in sorted({r["karat"] for r in gold_rows}):
            totals.append(total(f"{k}: صافي حساب الذهب (جرام، علينا +)", sum((r["closing"] for r in gold_rows if r["karat"] == k), ZERO3), "grams", "عيار حساب هذا المورد نفسه؛ لا تحويل إلى عيار 21"))
        meaning = ["الإشارة: موجب = علينا للمورد، سالب = لنا عند المورد (اصطلاح الدفتر). الذهب بجرام فعلي بعيار حساب كل مورد؛ لا يُحوَّل إلى عيار 21 ولا يُجمع بين عيارين.",
                   "المصنعيات بالجنيه حساب مستقل عن الذهب؛ لا يُجمع الذهب والمصنعيات.",
                   "الفواتير والمدفوعات والتسويات وعكسها تظهر كحركات في دفتر الحساب بمرجعها ونوعها؛ العكس حركة مستقلة بنوعها ولا يمحو الأصل."]
        return {"meaning": meaning, "tables": [table("gold", "أرصدة الذهب (بعيار حساب كل مورد)", gcols, gold_rows), table("making", "أرصدة المصنعيات", mcols, making_rows),
                                               table("movements", "الحركات", dcols, det, paged=True)], "totals": totals}

    # ------------------------------------------------------------------ 6. treasury
    def _treasury(self, repo, gate, filters, start, end):
        account_filter = str(filters.get("account") or "")
        raw, folded = _text_filter(filters)
        sections, missing = [], []
        for right, kind, label, accounts, before, moves in (("CASH_VIEW", "CASH", "نقدي", "cash_accounts", "cash_before", "cash_movements"),
                                                            ("BANK_VIEW", "BANK", "بنكي / محفظة", "bank_accounts", "bank_before", "bank_movements")):
            if gate(right):                                                      # the CURRENT right, decided before the section's data is read
                sections.append((kind, label, getattr(repo, accounts)(), getattr(repo, before)(start), getattr(repo, moves)(start, end)))
            else:
                missing.append(label)
        summary, det = [], []
        net_transfers = ZERO2
        tot = {"CASH": ZERO2, "BANK": ZERO2}
        for kind, label, accounts, before, moves in sections:
            opening = {int(r["account_id"]): _dec(r["total"]) for r in before}
            by = {}
            for m in moves:
                by.setdefault(int(m["account_id"]), []).append(m)
            for a in accounts:
                aid = int(a["id"])
                key = f"{kind}:{aid}"
                if account_filter and account_filter != key:
                    continue
                inn = out = t_in = t_out = ZERO2
                for m in by.get(aid, []):                                        # the account's balances use EVERY movement of the period: a search never changes them
                    amount = _dec(m["amount"])
                    transfer = m["transfer_group_id"] is not None
                    if transfer:
                        t_in += amount if amount > 0 else ZERO2
                        t_out += -amount if amount < 0 else ZERO2
                    else:
                        inn += amount if amount > 0 else ZERO2
                        out += -amount if amount < 0 else ZERO2
                    if _matches(folded, raw, m["reference_code"], m["description"], m["type_name"]):
                        det.append({"date": _dateish(m["occurred_at"]), "account": f"{label}: {a['name']}", "type": m["type_name"] + (" (تحويل داخلي)" if transfer else ""), "amount": amount,
                                    "reference": m["reference_code"] or "", "description": m["description"] or "", "_transfer": transfer,
                                    "ref": {"type": "INVOICE", "id": str(m["reference_id"])} if m["reference_type"] == "INVOICE" and m["reference_id"] else None})
                o = opening.get(aid, ZERO2)
                closing = o + inn - out + t_in - t_out
                net_transfers += t_in - t_out
                tot[kind] += closing
                if o or inn or out or t_in or t_out or a["is_active"]:
                    summary.append({"account": f"{label}: {a['name']}", "opening": o, "in": inn, "out": out, "t_in": t_in, "t_out": t_out, "closing": closing})
        det.sort(key=lambda r: r["date"], reverse=True)
        scols = [col("account", "الحساب"), col("opening", "رصيد أول الفترة", "money"), col("in", "داخل", "money"), col("out", "خارج", "money"), col("t_in", "تحويل داخلي وارد", "money"),
                 col("t_out", "تحويل داخلي صادر", "money"), col("closing", "رصيد آخر الفترة", "money")]
        dcols = [col("date", "التاريخ", "date"), col("account", "الحساب"), col("type", "نوع الحركة"), col("amount", "المبلغ (+ داخل / − خارج)", "money"), col("reference", "المرجع"), col("description", "البيان")]
        plain = [d["amount"] for d in det if not d["_transfer"]]
        shown = "الحركات المطابقة للبحث" if raw else "الحركات المعروضة"
        totals = [total("عدد " + shown, len(det), "count"),
                  total(shown + " — داخل (بلا التحويلات)", sum((a for a in plain if a > 0), ZERO2), "money", "إجمالي ما يطابق البحث والحساب المختارين؛ لا يغيّر الأرصدة"),
                  total(shown + " — خارج (بلا التحويلات)", -sum((a for a in plain if a < 0), ZERO2), "money"),
                  total(shown + " — صافي التحويلات الداخلية", sum((d["amount"] for d in det if d["_transfer"]), ZERO2), "money", "التحويل الداخلي ليس إيرادًا ولا مصروفًا")]
        if any(k == "CASH" for k, *_ in sections):
            totals.append(total("رصيد آخر الفترة — النقدي (كل حركات الفترة)", tot["CASH"], "money", "لا يغيّره البحث"))
        if any(k == "BANK" for k, *_ in sections):
            totals.append(total("رصيد آخر الفترة — البنكي والمحافظ (كل حركات الفترة)", tot["BANK"], "money", "لا يغيّره البحث"))
        if not missing and not account_filter:
            totals.append(total("صافي التحويلات الداخلية لكل الحسابات (يجب أن يساوي صفرًا)", net_transfers, "money", "كل الحسابات، بلا بحث"))
        meaning = ["الرصيد = مجموع حركات حساب الخزنة أو الحساب البنكي نفسه (دفتر الحركات)؛ رصيد أول الفترة = كل الحركات قبل تاريخ البداية. البحث يصفّي جدول الحركات وإجماليات «المطابقة» فقط، وأرصدة الحسابات كاملة دائمًا.",
                   "«داخل/خارج» للحركات العادية فقط؛ التحويل بين حسابين يظهر في عموده الخاص ولا يُحتسب إيرادًا ولا مصروفًا.",
                   "حركة الفترة لا تساوي بالضرورة مجموع المستندات الفعّالة في تقرير المبيعات (آجل، تحصيل لاحق، شراء كسر، مرتجع...)."]
        tables = [table("accounts", "الحسابات (أرصدة كاملة للفترة — لا يغيّرها البحث)", scols, summary), table("movements", "الحركات", dcols, det, paged=True)]
        for label in missing:
            tables.append(self._unavailable("missing_" + label, f"الحسابات ({label})", "ليست لديك صلاحية عرض هذا القسم."))
        return {"meaning": meaning, "tables": tables, "totals": totals}
