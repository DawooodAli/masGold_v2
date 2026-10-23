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
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from repositories.invoice_lifecycle_repository import exchange_code
from repositories.invoice_return_repository import return_code
from repositories.market_trip_repository import entry_code, trip_code
from repositories.report_repository import ReportRepository
from services import book_rules
from services import position_snapshot_rules as snapshot_math
from services import price_indicators as indicators_math
from services.authorization_service import AuthorizationService
from services.correction_rules import correction_code
from services.gold_position_service import read_transaction

PAGE_SIZE = 50
MAX_ROWS = 50000
ZERO = Decimal("0")
ZERO3, ZERO2 = Decimal("0.000"), Decimal("0.00")
BASE_PERMISSION = "REPORTS_VIEW"
REPORT_PERMISSIONS = {"sales": ("INVOICE_VIEW",), "stock": ("INVENTORY_VIEW",), "alerts": ("INVENTORY_VIEW",), "indicators": (), "gold": (), "employees": ("INVOICE_VIEW",), "makingprofit": ("INVOICE_VIEW", "INVENTORY_VIEW"), "scrapcost": ("INVOICE_VIEW",), "demand": ("INVOICE_VIEW",), "customers": ("CUSTOMERS_VIEW",), "deadcash": ("CUSTOMERS_VIEW",),
                      "suppliers": ("SUPPLIER_VIEW",), "treasury": (), "cashflow": (), "market": ("MARKET_TRIP_MANAGE",),
                      "profit": ("MANAGEMENT_REVIEW", "MARKET_TRIP_MANAGE", "INTERMEDIARY_HOLDING_VIEW")}                      # treasury: CASH_VIEW and / or BANK_VIEW per section
REPORT_TITLES = {"sales": "المبيعات والمرتجعات", "stock": "المخزون المتاح", "alerts": "تنبيهات المخزون", "indicators": "مؤشرات الذهب", "gold": "حركة الأصناف", "employees": "أداء الموظفين", "makingprofit": "ربح المصنعية", "scrapcost": "تكلفة الكسر", "demand": "الموسمية والطلب", "customers": "العملاء", "deadcash": "الديون المعدومة (Dead Cash)", "suppliers": "الموردون", "treasury": "الخزنة والبنوك", "cashflow": "المصروفات والإيرادات", "market": "عهدة السوق والخسائر", "profit": "ربح الذهب (مكافئ 21)"}
KIND_TEXT = {"SALE": "بيع", "MIXED": "بيع مع شراء كسر", "OLD_GOLD_PURCHASE": "شراء كسر", "RETURN": "مرتجع", "EXCHANGE": "استبدال"}
STATUS_TEXT = {"IN_STOCK": "متاح", "SOLD": "مباع", "SCRAPPED": "كسر", "REMOVED": "مُخرج", "RETURNED_PENDING": "مرتجع بانتظار الفحص", "CONVERTED_TO_OLD_GOLD": "حُوّل إلى كسر"}
CLASS_TEXT = {"NEW_STOCK": "جديد", "OLD_STOCK": "قديم"}
LINE_TEXT = {"SELL_INVENTORY": "بيع قطعة", "SELL_OLD_GOLD": "بيع من الكسر", "BUY_OLD_GOLD": "شراء كسر", "SELL_MANUAL": "بيع يدوي"}
REF_TEXT = {"INVOICE": "فاتورة", "RETURN": "مرتجع", "EXCHANGE": "استبدال", "CUSTOMER_ACCOUNT_SETTLEMENT": "تحصيل / تسوية حساب", "CUSTOMER_DEPOSIT": "عربون",
            "CUSTOMER_DEPOSIT_REFUND": "رد عربون", "FULL_REVERSAL": "عكس", "SETTLEMENT_CORRECTION": "تصحيح تسوية", "DEAD_CASH_WRITE_OFF": "شطب رصيد",
            "DEAD_CASH_REVERSAL": "عكس شطب", "UNKNOWN": "غير محدد"}
NO_PERIOD = frozenset({"stock", "alerts", "indicators", "demand", "customers", "deadcash", "suppliers"})          # reports of the CURRENT state: the period is neither offered nor printed
PRICE_KARATS = (24, 21, 18)                                                                    # the karats the price indicators are shown for (the provider's three)
HINT_TEXT = {indicators_math.BUY_FAVOURABLE: "الشراء مناسب نسبيًا", indicators_math.SELL_FAVOURABLE: "البيع مناسب نسبيًا", indicators_math.NEUTRAL: "محايد",
             indicators_math.UNKNOWN: "بيانات غير كافية"}
HINT_DISCLAIMER = "إرشاد مبني على حركة أسعار المحل المخزنة وليس توصية مالية"
NO_TYPE = "بدون صنف"
NO_EMPLOYEE = "غير محدد"
NO_CATEGORY = "بدون حرف"
# cash-flow report: how a ledger movement is named (by what posted it) and what is NOT income / expense (money that only changes place)
FLOW_LABELS = {"INVOICE": "المبيعات وشراء الكسر", "INVOICE_LIFECYCLE": "الاستبدال", "INVOICE_RETURN": "المرتجعات", "CUSTOMER_ACCOUNT_SETTLEMENT": "سداد العملاء والدفع لهم",
               "CUSTOMER_DEPOSIT": "العربون", "SUPPLIER_MAKING_PAYMENT": "مصنعيات الموردين"}
CORRECTION_FLOW = {"INVOICE": "INVOICE", "EXCHANGE": "INVOICE_LIFECYCLE", "RETURN": "INVOICE_RETURN", "CUSTOMER_ACCOUNT_SETTLEMENT": "CUSTOMER_ACCOUNT_SETTLEMENT",
                   "CUSTOMER_DEPOSIT": "CUSTOMER_DEPOSIT", "SUPPLIER_MAKING_PAYMENT": "SUPPLIER_MAKING_PAYMENT"}
INTERNAL_FLOW = {"MARKET_TRIP_ENTRY": "عهدة السوق (إخراج / رجوع / إيداع)", "MARKETER_DOCUMENT": "عهدة المسوقجي", "ACCOUNT_TRANSFER": "تحويلات بين الحسابات"}
DEAD_STOCK_DAYS = 180                  # a piece in stock for six months without a sale is dead stock (owner decision)
VELOCITY_DAYS, RECENT_DAYS = 90, 30    # the window the sales rate is measured on, and the "recent" figure shown beside it
WEIGHT_BAND_TOP = 10                  # weight bands are one gram wide (1 – 2, 2 – 3 ...); every piece of WEIGHT_BAND_TOP grams or more is in the last band
LOW_COVER_DAYS, MIN_SALES = 14, 3      # an item sold at least MIN_SALES times in the window whose stock covers fewer than LOW_COVER_DAYS days of sales is about to run out
UNKNOWN_TEXT = "غير معلوم"
FILTER_LABELS = {"category_name": "الحرف", "employee_name": "الموظف", "factory_name": "المصنع", "weight_min": "الوزن من", "weight_max": "الوزن إلى", "kind": "النوع", "state": "الحالة", "status": "الحالة", "classification": "التصنيف", "karat": "العيار", "source": "المصدر", "balance": "الرصيد",
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


def table(key, title, columns, rows, *, paged=False, note="", karat=None):
    return {"key": key, "title": title, "columns": columns, "rows": rows, "paged": paged, "note": note, "karat": karat}


def total(label, value, kind, note="", group="", karat=None, tone=None):
    return {"label": label, "value": value, "kind": kind, "note": note, "group": group, "karat": karat, "tone": tone}


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
            ok = self._allowed(key, needed, set(actor.permissions))
            if ok:
                out.append({"key": key, "title": REPORT_TITLES[key]})
        return out

    @staticmethod
    def _allowed(key, needed, has):
        """`has` is a set of codes or a gate callable. treasury: cash and / or bank; gold: (invoices and inventory) and / or old gold; every other report: all its rights."""
        have = has if callable(has) else (lambda code: code in has)
        if key in ("treasury", "cashflow"):
            return bool(have("CASH_VIEW") or have("BANK_VIEW"))
        if key == "gold":
            return bool((have("INVOICE_VIEW") and have("INVENTORY_VIEW")) or have("OLD_GOLD_VIEW"))
        return all(have(code) for code in needed)

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
                writer.writerow([item["label"].replace(" (فعّال)", "").replace("(فعّال)", "").strip(), self._cell(item["value"]), item["note"]])
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
            if not self._allowed(report, REPORT_PERMISSIONS[report], gate):                  # the last right needed is gone: a clear refusal, nothing is read
                raise denied
            repo = self._repository(connection)
            built = getattr(self, f"_{report}")(repo, gate, filters, start, end)
            if not self._session.is_current(actor):
                raise PermissionError("انتهت جلسة الدخول.")
        built.update(report=report, title=REPORT_TITLES[report], filters=filters, applied=self._applied(filters, report not in NO_PERIOD))
        for t in built["tables"]:
            if len(t["rows"]) > MAX_ROWS:
                raise ReportError("TOO_MANY_ROWS", "نتيجة التقرير أكبر من الحد المسموح: ضيّق الفترة أو الفلتر.")
        return built

    @staticmethod
    def _applied(filters, with_period=True):
        """The filters that were really applied (the same words on the screen and in the CSV)."""
        out = [f"الفترة: من {filters['from']} إلى {filters['to']}"] if with_period else []
        if str(filters.get("text") or "").strip():
            out.append(f"البحث: {str(filters['text']).strip()}")
        for key, label in FILTER_LABELS.items():
            if str(filters.get(key) or "") not in ("", "ALL"):
                out.append(f"{label}: {filters[key]}")
        return out

    @staticmethod
    def _unavailable(key, title, reason):
        return {"key": key, "title": title, "columns": [], "rows": [], "paged": False, "note": "", "available": False, "reason": reason}

    # ------------------------------------------------------------------ 9. income and expenses (owner request): the real money that came in and went out, named by what posted it
    def _cashflow(self, repo, gate, filters, start, end):
        """Every cash / bank movement of the period, named by what posted it, netted per name (a reversal / correction lands on the SAME name as its original, so it nets). Money that only
        changes place (transfers between accounts, market custody issue / return / bank deposit, the marketer's cash) is shown apart and is neither income nor expense. Opening balances
        are not movements of the period. «مصروفات مسجلة» and «إيرادات مسجلة» are the categorised manual entries of the treasury (إيجار، مرتبات، كهرباء...)."""
        from services.financial_account_service import CASH_LABELS as FIXED_LABELS
        reader = getattr(repo, "cash_categories", None)
        named = reader() if reader else None
        CASH_LABELS = {**FIXED_LABELS, **{r["code"]: r["name"] for r in (named or [])}}                       # a category's CURRENT name (renamed in Settings), otherwise the fixed names
        ledgers = tuple(k for right, k in (("CASH_VIEW", "CASH"), ("BANK_VIEW", "BANK")) if gate(right))
        operating, internal = {}, {}
        recorded_out = recorded_in = ZERO2
        for m in repo.cashflow_movements(start, end, ledgers):
            amount = _dec(m["amount"])
            if m["type_code"] == "OPENING_BALANCE":
                continue
            reference = m["reference_type"]
            source, category, label, bucket = reference, m["category"], None, operating
            if reference == "TRANSACTION_CORRECTION":
                original = m["correction_source"] or ""
                source = CORRECTION_FLOW.get(original, original)
                if original == "FINANCIAL_OPERATION":
                    source, category = "FINANCIAL_OPERATION", m["corrected_category"]
                reference = original if original in INTERNAL_FLOW else reference
                if original == "FINANCIAL_OPERATION" and m.get("corrected_operation_type") == "ACCOUNT_TRANSFER":
                    reference = "ACCOUNT_TRANSFER"                                          # the reversal of a manual transfer between accounts is still only a change of place
            if m["transfer_group_id"] is not None:
                bucket, label = internal, INTERNAL_FLOW["ACCOUNT_TRANSFER"]
            elif reference in INTERNAL_FLOW:
                bucket, label = internal, INTERNAL_FLOW[reference]
            elif m["type_code"] == "TRIP_CUSTODY_BANK_IN":
                bucket, label = internal, INTERNAL_FLOW["MARKET_TRIP_ENTRY"]
            elif source == "FINANCIAL_OPERATION" and category in ("STAFF_ADVANCE", "CUSTOMER_CUSTODY", "ADVANCE_REPAY", "BORROWED_CASH", "BORROW_REPAY"):
                bucket, label = internal, "سلف وعهد مؤقتة (تعود للمحل)" if category in ("STAFF_ADVANCE", "CUSTOMER_CUSTODY", "ADVANCE_REPAY") else "سلف مستلفة (تُسدد لأصحابها)"                  # money lent and taken back: neither income nor expense
            elif source == "FINANCIAL_OPERATION":
                label = CASH_LABELS.get(category or "", "إيداع يدوي" if amount > 0 else "سحب يدوي")
                if category:                                                              # a categorised entry counts on the side of its ORIGINAL direction; its reversal reduces that side
                    direct = m["reference_type"] == "FINANCIAL_OPERATION"
                    if (amount < 0) == direct:
                        recorded_out += -amount
                    else:
                        recorded_in += amount
            else:
                label = FLOW_LABELS.get(source) or m["type_name"] or "حركة أخرى"
            row = bucket.setdefault(label, {"in": ZERO2, "out": ZERO2})
            if amount > 0:
                row["in"] += amount
            else:
                row["out"] += -amount
        cols = [col("label", "البند"), col("in", "داخل", "money"), col("out", "خارج", "money"), col("net", "الصافي", "money")]

        def rows_of(bucket):
            return [{"label": k, "in": v["in"], "out": v["out"], "net": v["in"] - v["out"]} for k, v in sorted(bucket.items(), key=lambda kv: (-(kv[1]["in"] + kv[1]["out"]), kv[0]))]
        total_in, total_out = sum((v["in"] for v in operating.values()), ZERO2), sum((v["out"] for v in operating.values()), ZERO2)
        internal_net = sum((v["in"] - v["out"] for v in internal.values()), ZERO2)
        tables = [table("operating", "الإيرادات والمصروفات حسب البند", cols, rows_of(operating)), table("internal", "حركات لا تُحسب إيرادًا ولا مصروفًا (تغيير مكان الفلوس)", cols, rows_of(internal))]
        missing = [name for right, name in (("CASH_VIEW", "النقدية"), ("BANK_VIEW", "البنوك والمحافظ")) if not gate(right)]
        for name in missing:
            tables.append(self._unavailable("missing_" + name, name, "ليست لديك صلاحية عرض هذا القسم: الأرقام لا تشملها."))
        note = (" • لا تشمل: " + "، ".join(missing)) if missing else ""
        totals = [total("مصروفات مسجلة", recorded_out, "money", "إيجار، مرتبات، كهرباء، نقل، صيانة، ضيافة، سلف... من حركة الخزنة" + note, "recorded", tone="payable"),
                  total("إيرادات مسجلة", recorded_in, "money", "إيرادات مسجلة يدويًا من حركة الخزنة" + note, "recorded", tone="receivable"),
                  total("إجمالي الداخل", total_in, "money", "كل الفلوس الداخلة بلا تغيير المكان" + note, "flow", tone="receivable"),
                  total("إجمالي الخارج", total_out, "money", "كل الفلوس الخارجة بلا تغيير المكان" + note, "flow", tone="payable"),
                  total("الصافي", total_in - total_out, "money", "الداخل − الخارج", "flow"),
                  total("صافي حركات تغيير المكان", internal_net, "money", "عهدة السوق والمسوقجي والتحويلات (للمعلومية)", "internal")]
        meaning = ["الفلوس الحقيقية فقط (خزنة وبنوك ومحافظ) مسمّاة بما سجّلها. الإلغاء والتصحيح يُحسبان على نفس بند الأصل فيُخصمان منه.",
                   "عهدة السوق والمسوقجي وتحويلات الحسابات تغيّر مكان الفلوس فقط، فتظهر في جدول منفصل ولا تدخل في الإيراد أو المصروف.",
                   "الداخل يشمل دفعات العملاء على الفواتير والسداد، وليس هو الربح: لا توجد تكلفة في هذا التقرير."]
        return {"meaning": meaning, "tables": tables, "totals": totals}

    # ------------------------------------------------------------------ 8. gold profit (owner definition): the gold at the end of the period minus the gold at its start, in 21K equivalent
    def _profit(self, repo, gate, filters, start, end):
        """The owner's definition: profit = the gold the shop holds at the END of the period minus what it held at its START, in 21K-equivalent grams. The gold is the consolidated position (stock,
        Scrap, bars, market custody, marketer and intermediary holdings) plus the NET supplier gold (what the suppliers hold for the shop counts as shop gold, what the shop owes is subtracted).
        The two figures come from stored stocktaking sheets (position snapshots), never from a recomputed history. Cash and bank are shown beside it, NOT inside the profit."""
        if not repo.has_table("position_snapshots"):
            return {"meaning": [], "tables": [self._unavailable("missing", "بيانات غير متاحة", "جدول لقطات الرصيد غير موجود في مستوى قاعدة البيانات الحالي: طبّق التحديثات.")], "totals": []}
        sheets = [{**dict(r), "taken_at": _dateish(r["taken_at"])} for r in repo.position_snapshots()]
        opening, closing, problem = snapshot_math.choose(sheets, start, end)
        meaning = ["ربح الذهب = ذهب آخر الفترة − ذهب أول الفترة، بجرام مكافئ عيار 21: مخزون جديد + كسر + سبائك + عهدة السوق + ذهب المسوقجي + ذهب الموردين (لنا موجب وعلينا سالب).",
                   "الأرقام من لقطات الرصيد (جرد يُحفظ أول فتح للبرنامج كل يوم)، وليست إعادة حساب لتاريخ قديم. الكاش والبنوك للمعلومية فقط ولا تدخل في الربح.",
                   "الربح هنا يشمل أي تغيير في الذهب (شراء بكاش أو سحب أو إيداع)، فراجعه مع حركة الكاش."]
        notes = {"NO_SNAPSHOT": "لا توجد لقطات رصيد: سجّل الرصيد الافتتاحي من الإعدادات.", "NO_CLOSING": "لا توجد لقطة تغطي نهاية هذه الفترة.",
                 "SAME_SNAPSHOT": "يوجد جرد واحد فقط لهذه الفترة، فلا يمكن قياس الفرق: اختر فترة أطول أو خذ لقطة جديدة."}
        if problem:
            return {"meaning": meaning, "tables": [self._unavailable("profit", "ربح الذهب", notes[problem])],
                    "totals": [total("ربح الذهب (مكافئ 21)", "غير متاح", "text", notes[problem], "profit", tone="deadcash")]}
        diff = snapshot_math.profit(opening, closing)

        def day(row):
            return _dateish(row["taken_at"]).strftime("%Y-%m-%d %H:%M")
        early = " • تنبيه: الفترة تبدأ قبل أول جرد فاستُخدم أول جرد كمرجع" if opening["before_first"] else ""
        grams_text = lambda v: f"{v:+,.3f} جرام" if v else "0.000 جرام"
        money_text = lambda v: f"{v:+,.2f} ج" if v else "0.00 ج"
        totals = [total("ربح الذهب (مكافئ 21)", grams_text(diff["gold_total_eq21"]), "text", f"من جرد {day(opening)} إلى جرد {day(closing)}{early}", "profit", tone="receivable" if diff["gold_total_eq21"] >= 0 else "payable"),
                  total("ذهب أول الفترة", f"{_dec(opening['gold_total_eq21']):,.3f} جرام", "text", f"جرد {day(opening)}", "ends"),
                  total("ذهب آخر الفترة", f"{_dec(closing['gold_total_eq21']):,.3f} جرام", "text", f"جرد {day(closing)}", "ends"),
                  total("الكاش (الخزنة) — التغير", money_text(diff["cash_egp"]), "text", f"أول {_dec(opening['cash_egp']):,.2f} → آخر {_dec(closing['cash_egp']):,.2f} ج (للمعلومية)", "cash"),
                  total("البنوك والمحافظ — التغير", money_text(diff["bank_egp"]), "text", f"أول {_dec(opening['bank_egp']):,.2f} → آخر {_dec(closing['bank_egp']):,.2f} ج (للمعلومية)", "cash")]
        labels = (("gold_shop_eq21", "مخزون وكسر وسبائك المحل"), ("gold_custody_eq21", "عهدة السوق"), ("gold_intermediary_eq21", "ذهب المسوقجية"), ("gold_book_eq21", "رحلات الدفتر"),
                  ("gold_suppliers_eq21", "ذهب الموردين (صافي)"), ("gold_total_eq21", "الإجمالي"))
        rows = [{"part": label, "opening": _dec(opening[key]), "closing": _dec(closing[key]), "change": diff[key]} for key, label in labels]
        unknown = _dec(closing.get("unknown_grams") or 0)
        if unknown:
            rows.append({"part": "جرامات بعيار غير معلوم (خارج الإجمالي)", "opening": _dec(opening.get("unknown_grams") or 0), "closing": unknown, "change": unknown - _dec(opening.get("unknown_grams") or 0)})
        tables = [table("parts", "مكونات الذهب (جرام مكافئ عيار 21)", [col("part", "البند"), col("opening", "أول الفترة", "eq21"), col("closing", "آخر الفترة", "eq21"), col("change", "التغير", "eq21")], rows)]
        return {"meaning": meaning, "tables": tables, "totals": totals}

    # ------------------------------------------------------------------ 7. market custody: ONE total of loss (owner: totals only, no detail tables)
    def _market(self, repo, gate, filters, start, end):
        """Read only. The owner wants only the TOTAL of the loss: the melt loss (weight loss + fineness loss), the crucible loss and the market loss, all in 21K-equivalent grams so the
        karats can be added (the bar is already in 21K-equivalent grams; unmelted gold and logo are converted grams x karat / 21, posted once per line at 0.001 g). Documents sit in the
        period by their OWN date; reversed documents are not counted (their number is shown apart). A missing table is «غير متاح», never a zero."""
        first, last = start.date(), (end - timedelta(days=1)).date()
        until = last + timedelta(days=1)
        meaning = ["هذا التقرير أرقام إجمالية فقط للفقد والخسية، كلها بمكافئ عيار 21 حتى يمكن جمع الأعيار.",
                   "المستندات المعكوسة لا تدخل الإجماليات (عددها يظهر منفصلًا). كل مستند يُحسب في الفترة بتاريخه الفعلي. الفقد السالب = زيادة.",
                   "المصروفات وإعدام النقدية ليست فقد ذهب فلا تظهر هنا."]
        rows, totals = [], []
        melt_weight = melt_fineness = crucible = market = ZERO3
        reversed_count = 0
        unavailable = []
        if repo.has_table("market_trip_settle_melts"):
            for m in repo.trip_melts(first, until):
                if m["reversal_id"]:
                    reversed_count += 1
                    continue
                melt_weight += _dec(m["weight_loss_eq21"])
                melt_fineness += _dec(m["fineness_loss_eq21"])
        else:
            unavailable.append("السبك")
        if repo.has_table("market_trip_settle_lines"):
            for l in repo.trip_settle_lines(first, until):
                if l["entry_kind"] != "ST_LOSS":
                    continue
                if l["reversal_id"]:
                    reversed_count += 1
                    continue
                grams_ = _dec(l["weight_grams"])
                if l["source"] != "BAR":
                    grams_ = book_rules.post(book_rules.analytical(grams_, book_rules.MASTER_KARAT, int(l["karat_value"])))
                if l["classification"] == "CRUCIBLE":
                    crucible += grams_
                else:
                    market += grams_
        else:
            unavailable.append("الخسية")
        melt_total = melt_weight + melt_fineness
        grand = melt_total + crucible + market
        for label, value in (("فاقد السبك — وزن", melt_weight), ("فاقد السبك — عيار", melt_fineness), ("إجمالي فاقد السبك", melt_total), ("خسية البوتقة", crucible), ("خسية السوق", market),
                             ("إجمالي الفقد والخسية", grand)):
            rows.append({"label": label, "grams": value})
        tables = [table("summary", "الفقد والخسية (مكافئ عيار 21)", [col("label", "البند"), col("grams", "جرام مكافئ عيار 21", "eq21")], rows)]
        if unavailable:
            tables.append(self._unavailable("missing", "بيانات غير متاحة", "جداول غير موجودة في مستوى قاعدة البيانات الحالي: " + "، ".join(unavailable) + " — الإجمالي ناقص."))
        incomplete = (" • ناقص: " + "، ".join(unavailable)) if unavailable else ""

        def gram_card(label, value, note, group):
            return total(label, f"{value:,.3f} جرام", "text", ("زيادة (سالب = زيادة)" if value < 0 else note) + incomplete, group)
        totals = [gram_card("فاقد السبك", melt_total, f"وزن {melt_weight:,.3f} + عيار {melt_fineness:,.3f} (مكافئ عيار 21)", "losses"),
                  gram_card("خسية البوتقة", crucible, "مكافئ عيار 21", "losses"),
                  gram_card("خسية السوق", market, "مكافئ عيار 21", "losses"),
                  total("إجمالي الفقد والخسية", f"{grand:,.3f} جرام", "text", "فاقد السبك + خسية البوتقة + خسية السوق" + (f" • {reversed_count} مستند معكوس غير محسوب" if reversed_count else "") + incomplete, "grand", tone="deadcash")]
        return {"meaning": meaning, "tables": tables, "totals": totals}

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
        # Owner review: ONE card per karat with the piece count and, under it, that karat's weight (karats are never added); then one card per karat of the shop Scrap (weight only, its
        # own karat). The Scrap cards need the old-gold right and are left out (never shown as zero) without it.
        totals = [total(f"عيار {k}", f"{v['weight']:,.3f} جرام", "text", f"{v['count']} قطعة", "pieces", karat=k) for k, v in sorted(per_karat.items(), reverse=True)]
        if gate("OLD_GOLD_VIEW"):
            scrap = {}
            for r in repo.scrap_opening(datetime(9999, 12, 31)):
                if not karat or str(r["karat_value"]) == karat:
                    scrap[int(r["karat_value"])] = scrap.get(int(r["karat_value"]), ZERO3) + _dec(r["grams"])
            totals += [total(f"كسر المحل — عيار {k}", f"{g:,.3f} جرام", "text", "الرصيد الحالي (الكسر المحسوب فقط)", "scrap", karat=k) for k, g in sorted(scrap.items(), reverse=True)]
        meaning = ["المخزون المتاح = القطع بحالة «متاح» (الافتراضي)؛ يمكن عرض الحالات الأخرى لمراجعة القطع المباعة أو المرتجعة.",
                   "الأوزان تُجمع داخل كل عيار فقط؛ لا يوجد إجمالي وزن لعيارات مختلفة. هذا التقرير للقطع المشغولة، وكسر المحل في تقرير «حركة المخزون والذهب».",
                   "الفاتورة المرتبطة: آخر فاتورة غير مسودة تحمل هذه القطعة في أحد أسطرها (الرابط `invoice_lines.inventory_item_id`)؛ ليست بالضرورة فاتورة أصل اقتناء القطعة، وتفتح من شاشة الفواتير."]
        return {"meaning": meaning, "tables": [table("summary", "الملخص حسب العيار والتصنيف والحالة", scols, summary), table("items", "القطع", dcols, rows, paged=True)], "totals": totals}

    # ------------------------------------------------------------------ 2b. inventory alerts (dead stock and items about to run out) - plain rules over our own data, no forecast
    def _alerts(self, repo, gate, filters, start, end):
        """Owner request (2026-10-21), rule based and explainable. (1) DEAD STOCK: a piece IN_STOCK acquired DEAD_STOCK_DAYS ago or earlier that was never on an invoice (a piece that came back
        from a sale is not dead, it is recent). Grouped per KARAT (never across karats) by type and factory with count, weight and the oldest age. (2) ABOUT TO RUN OUT: for every (karat, type, factory,
        size) sold at least MIN_SALES times in the last VELOCITY_DAYS, stock / (sales per day) = the days the stock covers; fewer than LOW_COVER_DAYS (or no stock left) is an alert; needs the invoice
        right. It reads the CURRENT state (no period) and suggests nothing by itself: the owner decides."""
        karat = str(filters.get("karat") or "")
        if karat not in ("", "18", "21", "22", "24"):
            raise ReportError("FILTER_INVALID", "عيار غير صحيح.")
        raw, folded = _text_filter(filters)
        today = self._today()
        cutoff = today - timedelta(days=DEAD_STOCK_DAYS)
        all_stock = list(repo.stock_items(("IN_STOCK",)))
        stock = [r for r in all_stock if (not karat or str(r["karat_value"]) == karat) and _matches(folded, raw, r["item_type"], r["category"], r["factory"])]
        tables, cards = [], []
        dead = {}
        for r in stock:
            acquired = _dateish(r["acquired_at"])
            if r["invoice_id"] is not None or acquired is None or acquired.date() > cutoff:
                continue
            g = dead.setdefault((int(r["karat_value"]), r["item_type"] or NO_TYPE, r["factory"] or "بدون مصنع"), {"count": 0, "weight": ZERO3, "oldest": 0})
            g["count"] += 1
            g["weight"] += _dec(r["weight_grams"])
            g["oldest"] = max(g["oldest"], (today - acquired.date()).days)
        per_karat = {}
        for (k, _, _), g in dead.items():
            p = per_karat.setdefault(k, {"count": 0, "weight": ZERO3, "oldest": 0})
            p["count"] += g["count"]
            p["weight"] += g["weight"]
            p["oldest"] = max(p["oldest"], g["oldest"])
        dead_cols = [col("type", "الصنف"), col("factory", "المصنع"), col("count", "عدد القطع", "count"), col("weight", "الوزن (جرام)", "grams"), col("oldest", "أقدم قطعة (يوم)", "count")]
        for k in sorted(per_karat, reverse=True):
            rows = [{"type": t, "factory": f, "count": g["count"], "weight": g["weight"], "oldest": g["oldest"]}
                    for (kk, t, f), g in sorted(dead.items(), key=lambda kv: (-kv[1]["weight"], kv[0][1])) if kk == k]
            tables.append(table(f"dead_{k}", f"قطع راكدة (أكثر من {DEAD_STOCK_DAYS} يوم) — عيار {k}", dead_cols, rows, karat=k))
            cards.append(total(f"راكد عيار {k}", f"{per_karat[k]['weight']:,.3f} جرام", "text", f"{per_karat[k]['count']} قطعة • أقدمها {per_karat[k]['oldest']} يوم", "dead", karat=k))
        if not per_karat:
            tables.append(table("dead", f"قطع راكدة (أكثر من {DEAD_STOCK_DAYS} يوم)", dead_cols, []))
        # ---- items about to run out
        sold_weights = []
        if gate("INVOICE_VIEW"):
            since = datetime.combine(today - timedelta(days=VELOCITY_DAYS), time.min)
            recent_from = today - timedelta(days=RECENT_DAYS)
            sold90, sold30 = {}, {}
            for r in repo.gold_sold_pieces(since, datetime.combine(today + timedelta(days=1), time.min)):
                if r["reversal_id"] or (karat and str(r["karat_value"]) != karat) or not _matches(folded, raw, r["item_type"], r["category"], r["factory"]):
                    continue
                key = (int(r["karat_value"]), r["item_type"] or NO_TYPE, r["factory"] or "بدون مصنع", str(r.get("size") or "").strip())
                sold90[key] = sold90.get(key, 0) + 1
                recent = _dateish(r["doc_date"]).date() >= recent_from
                if recent:
                    sold30[key] = sold30.get(key, 0) + 1
                sold_weights.append((int(r["karat_value"]), r["item_type"] or NO_TYPE, _dec(r["weight_grams"]), recent))
            in_stock = {}
            for r in stock:
                key = (int(r["karat_value"]), r["item_type"] or NO_TYPE, r["factory"] or "بدون مصنع", str(r["size"] or "").strip())
                in_stock[key] = in_stock.get(key, 0) + 1
            low = {}
            for key, n90 in sold90.items():
                have = in_stock.get(key, 0)
                if n90 < MIN_SALES:
                    continue
                cover = Decimal(have) * VELOCITY_DAYS / Decimal(n90)
                if have == 0 or cover < LOW_COVER_DAYS:
                    low[key] = {"sold30": sold30.get(key, 0), "sold90": n90, "stock": have, "cover": cover}
            low_cols = [col("type", "الصنف"), col("factory", "المصنع"), col("size", "المقاس"), col("sold30", "مباع آخر 30 يوم", "count"), col("sold90", f"مباع آخر {VELOCITY_DAYS} يوم", "count"),
                        col("stock", "المتاح", "count"), col("cover", "يكفي (يوم)")]
            low_karats = sorted({k for k, *_ in low}, reverse=True)
            for k in low_karats:
                rows = [{"type": t, "factory": f, "size": sz or "—", "sold30": v["sold30"], "sold90": v["sold90"], "stock": v["stock"], "cover": "نفد" if v["stock"] == 0 else f"{int(v['cover'])}"}
                        for (kk, t, f, sz), v in sorted(low.items(), key=lambda kv: (kv[1]["cover"], kv[0][1])) if kk == k]
                tables.append(table(f"low_{k}", f"قاربت على النفاد — عيار {k}", low_cols, rows, karat=k))
                cards.append(total(f"قارب النفاد عيار {k}", f"{len(rows)} صنف", "text", f"من أصناف بيعت {MIN_SALES} مرات فأكثر وتكفي أقل من {LOW_COVER_DAYS} يوم", "low", karat=k))
            if not low_karats:
                tables.append(table("low", "قاربت على النفاد", low_cols, []))
        else:
            tables.append(self._unavailable("low", "قاربت على النفاد", "تحتاج صلاحية عرض الفواتير لحساب معدل البيع."))
        # ---- the minimum levels the owner set in Settings: a type of a karat below its minimum pieces / grams is a reorder alert (it only alerts)
        levels = getattr(repo, "stock_min_levels", lambda: None)()
        if levels:
            have = {}
            for r in all_stock:
                h = have.setdefault((r["item_type"] or NO_TYPE, int(r["karat_value"])), [0, ZERO3])
                h[0] += 1
                h[1] += _dec(r["weight_grams"])
            below = []
            for lv in levels:
                k = int(lv["karat_value"])
                if karat and str(k) != karat:
                    continue
                pieces, grams = have.get((lv["item_type"], k), [0, ZERO3])
                short_p = max(0, int(lv["min_pieces"]) - pieces) if lv["min_pieces"] else 0
                short_g = max(ZERO3, _dec(lv["min_grams"]) - grams) if lv["min_grams"] else ZERO3
                if short_p or short_g:
                    below.append({"type": lv["item_type"], "karat": f"عيار {k}", "have": f"{pieces} قطعة • {grams:,.3f} جرام",
                                  "minimum": " و ".join(x for x in (f"{int(lv['min_pieces'])} قطعة" if lv["min_pieces"] else "", f"{_dec(lv['min_grams']):,.3f} جرام" if lv["min_grams"] else "") if x),
                                  "short": " و ".join(x for x in (f"{short_p} قطعة" if short_p else "", f"{short_g:,.3f} جرام" if short_g else "") if x), "_k": k})
            below.sort(key=lambda r: (-r["_k"], r["type"]))
            tables.append(table("below_min", "تحت الحد الأدنى للمخزون", [col("type", "الصنف"), col("karat", "العيار"), col("have", "الموجود"), col("minimum", "الحد الأدنى"), col("short", "العجز")], below))
            cards.append(total("تحت الحد الأدنى", len(below), "count", f"من {len(levels)} حدًّا حدّدته في الإعدادات", "minimum"))
        # ---- the weights: stock and sales per ONE-GRAM weight band, per KARAT and per item TYPE (information for the owner to decide with; no alert)
        def band(weight):
            return min(int(weight), WEIGHT_BAND_TOP)

        def band_label(index):
            return f"{WEIGHT_BAND_TOP} جرام فأكثر" if index >= WEIGHT_BAND_TOP else "0.01 – 1 جرام" if index == 0 else f"{index} – {index + 1} جرام"      # the first band starts at 0.01 g (owner)
        bands = {}
        for r in stock:
            b = bands.setdefault((int(r["karat_value"]), r["item_type"] or NO_TYPE, band(_dec(r["weight_grams"]))), {"stock": 0, "weight": ZERO3, "sold30": 0, "sold90": 0})
            b["stock"] += 1
            b["weight"] += _dec(r["weight_grams"])
        for k, kind, weight, recent in sold_weights:
            b = bands.setdefault((k, kind, band(weight)), {"stock": 0, "weight": ZERO3, "sold30": 0, "sold90": 0})
            b["sold90"] += 1
            b["sold30"] += 1 if recent else 0
        weight_cols = [col("type", "الصنف"), col("band", "شريحة الوزن"), col("stock", "المتاح (عدد)", "count"), col("weight", "الوزن المتاح (جرام)", "grams"), col("sold30", "مباع آخر 30 يوم", "count"),
                       col("sold90", f"مباع آخر {VELOCITY_DAYS} يوم", "count"), col("cover", "يكفي (يوم)")]
        has_sales = gate("INVOICE_VIEW")
        for k in sorted({k for k, _, _ in bands}, reverse=True):
            rows = []
            for (kk, kind, index), b in sorted(bands.items(), key=lambda kv: (kv[0][1], kv[0][2])):
                if kk != k:
                    continue
                cover = "—" if not has_sales or not b["sold90"] else "نفد" if b["stock"] == 0 else f"{int(Decimal(b['stock']) * VELOCITY_DAYS / Decimal(b['sold90']))}"
                rows.append({"type": kind, "band": band_label(index), "stock": b["stock"], "weight": b["weight"], "sold30": b["sold30"] if has_sales else None,
                             "sold90": b["sold90"] if has_sales else None, "cover": cover})
            tables.append(table(f"weights_{k}", f"الأوزان حسب الصنف — عيار {k}", weight_cols, rows, karat=k))
        meaning = [f"راكد = قطعة متاحة منذ {DEAD_STOCK_DAYS} يومًا أو أكثر ولم تظهر في أي فاتورة. قارب النفاد = صنف بيع {MIN_SALES} مرات فأكثر في آخر {VELOCITY_DAYS} يومًا والمتاح منه يكفي أقل من {LOW_COVER_DAYS} يومًا بمعدل البيع.",
                   "جداول الأوزان لكل عيار وكل صنف: شرائح وزنها جرام واحد (الحد الأدنى داخل الشريحة والأعلى خارجها) معلومات للمقارنة ولا تنبّه ولا تقرّر. التقرير تنبيه وليس قرارًا: لا يقترح سعرًا ولا يغيّر شيئًا. الأعيار منفصلة دائمًا."]
        return {"meaning": meaning, "tables": tables, "totals": cards}

    # ------------------------------------------------------------------ 2c. gold price indicators (owner request): standard formulas over the shop's own stored daily prices
    @staticmethod
    def _price_series(repo):
        """{karat: [(date, buy, sell)]} = the imported history of the old shop (karat 21) merged with the live daily prices (every karat): legacy days stop the day before the first live day."""
        legacy, live = {}, {}
        if repo.has_table("gold_price_history_legacy"):
            for r in repo.gold_price_history_legacy():
                legacy.setdefault(int(r["karat_value"]), []).append((_dateish(r["day"]).date(), _dec(r["buy"]), _dec(r["sell"])))
        for r in repo.gold_price_daily():
            live.setdefault(int(r["karat_value"]), []).append((_dateish(r["day"]).date(), _dec(r["buy"]), _dec(r["sell"])))
        return {k: indicators_math.merge_series(legacy.get(k, []), live.get(k, [])) for k in set(legacy) | set(live)}

    def _indicators(self, repo, gate, filters, start, end):
        """Indicative figures, NOT a forecast and NOT advice: per karat the latest SHOP_BUY / SHOP_SELL with their change, the 30-day average, the position inside the last 90 days, the daily
        volatility and a plain hint (the SHOP_BUY price in the lower quarter of its 90-day range = buying is relatively favourable; the SHOP_SELL price in the upper quarter = selling is). A figure
        that needs more history than stored is shown as unavailable. Current state: no period."""
        karat = str(filters.get("karat") or "")
        if karat not in ("", "18", "21", "24"):
            raise ReportError("FILTER_INVALID", "عيار غير صحيح.")
        series_by_karat = self._price_series(repo)
        cards, coverage = [], []
        signed = lambda v, unit="%": "—" if v is None else f"{'+' if v > 0 else ''}{v:,.2f}{unit}"
        for k in PRICE_KARATS:
            series = series_by_karat.get(k) or []
            if len(series) < 1 or (karat and str(k) != karat):
                continue
            latest = series[-1]
            group = f"price{k}"
            c1, c7 = indicators_math.change(series, 1, 1), indicators_math.change(series, 1, 7)
            cards.append(total(f"عيار {k} — سعر شراء المحل", latest[1], "money", f"عن اليوم السابق {signed(c1 and c1[1])} • عن أسبوع {signed(c7 and c7[1])} • آخر سعر {latest[0].isoformat()}", group, karat=k))
            spread = indicators_math.spread_percent(series)
            cards.append(total(f"عيار {k} — سعر بيع المحل", latest[2], "money", f"الفرق بين الشراء والبيع {spread:,.2f}%", group, karat=k))
            average = indicators_math.moving_average(series, 1, 30)
            cards.append(total(f"عيار {k} — متوسط 30 يوم", average[0] if average else "بيانات غير كافية", "money" if average else "text",
                               (f"سعر الشراء الحالي {'أعلى' if average[1] > 0 else 'أقل' if average[1] < 0 else 'مساوٍ'} منه بنسبة {abs(average[1]):,.2f}%" if average else "تحتاج 10 أيام على الأقل"), group, karat=k))
            position = indicators_math.range_position(series, 1, indicators_math.HINT_DAYS)
            if position:
                where = "الربع الأدنى" if position[0] <= indicators_math.QUARTER else "الربع الأعلى" if position[0] >= 1 - indicators_math.QUARTER else "في المنتصف"
                cards.append(total(f"عيار {k} — موقع سعر الشراء (آخر {indicators_math.HINT_DAYS} يوم)", f"{where} ({position[0] * 100:,.0f}%)", "text", f"أدنى {position[1]:,.0f} • أعلى {position[2]:,.0f}", group, karat=k))
            else:
                cards.append(total(f"عيار {k} — موقع سعر الشراء (آخر {indicators_math.HINT_DAYS} يوم)", "بيانات غير كافية", "text", "تحتاج 5 أيام على الأقل بأسعار مختلفة", group, karat=k))
            volatility = indicators_math.volatility(series, 1)
            cards.append(total(f"عيار {k} — التذبذب اليومي (30 يوم)", "بيانات غير كافية" if volatility is None else f"{volatility:,.2f}%", "text",
                               "متوسط تحرك السعر في اليوم" if volatility is not None else "تحتاج 8 أيام على الأقل", group, karat=k))
            code, buy_position, sell_position = indicators_math.hint(series)
            basis = ("سعر الشراء في الربع الأدنى من نطاقه" if code == indicators_math.BUY_FAVOURABLE else "سعر البيع في الربع الأعلى من نطاقه" if code == indicators_math.SELL_FAVOURABLE
                     else "السعران داخل النطاق المعتاد" if code == indicators_math.NEUTRAL else "")
            cards.append(total(f"عيار {k} — إرشاد", HINT_TEXT[code], "text", (basis + " • " if basis else "") + HINT_DISCLAIMER, group, karat=k))
            coverage.append({"karat": f"عيار {k}", "days": len(series), "first": series[0][0], "last": latest[0], "ref": {"type": "PRICE_CHART", "id": str(k)}})
        cols = [col("karat", "العيار"), col("days", "أيام الأسعار المتاحة", "count"), col("first", "أقدم يوم", "date"), col("last", "آخر يوم", "date")]
        tables = [table("coverage", "الأسعار المخزنة (للرسم البياني)", cols, coverage)]
        meaning = ["مؤشرات إرشادية من أسعار المحل المخزنة بمعادلات قياسية (متوسط، موقع بين الأدنى والأعلى، تذبذب). ليست توقعًا ولا توصية مالية.",
                   "عيار 21 له تاريخ طويل من بيانات المحل القديمة؛ عيارا 18 و24 من الأسعار الحية فقط، فتظهر مؤشراتهما الأطول عند تراكم البيانات."]
        return {"meaning": meaning, "tables": tables, "totals": cards}

    def price_chart(self, karat, days, *, _expected_actor=None):
        """The daily SHOP_BUY / SHOP_SELL prices of one karat for the chart (the last `days` days, 0 = everything stored) with the 30-day average of every point."""
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        try:
            number, span = int(karat), int(days)
        except (TypeError, ValueError):
            raise ReportError("FILTER_INVALID", "عيار أو مدة غير صحيحة.") from None
        if number not in PRICE_KARATS or span < 0:
            raise ReportError("FILTER_INVALID", "عيار أو مدة غير صحيحة.")
        with self._transaction() as connection:
            gate = _Gate(self._authorization, connection, actor)
            if not gate(BASE_PERMISSION):
                raise PermissionError("ليس لديك صلاحية عرض هذا الرسم.")
            series = self._price_series(self._repository(connection)).get(number, [])
            if not self._session.is_current(actor):
                raise PermissionError("انتهت جلسة الدخول.")
        shown = indicators_math.window(series, span) if span else list(series)
        points, low, running, total = [], 0, Decimal(0), 0
        for day, buy, sell in shown:
            while low < len(series) and series[low][0] < day - timedelta(days=29):          # a sliding 30-day window over the (date ordered) series: one pass instead of a rescan per point
                low += 1
            window = [r for r in series[low:] if r[0] <= day]
            points.append({"date": day, "buy": buy, "sell": sell, "average": (sum((r[1] for r in window), Decimal(0)) / len(window)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if len(window) >= 3 else None})
        return {"karat": number, "days": span, "points": points,
                "low": min((p["buy"] for p in points), default=None), "high": max((p["sell"] for p in points), default=None)}

    # ------------------------------------------------------------------ 3. gold movement: the pieces SOLD by type and the scrap BOUGHT by karat (owner redesign)
    def _gold(self, repo, gate, filters, start, end):
        """The PHYSICAL gold movement of the period. Table 1: the pieces sold (effective finalized invoices) grouped by TYPE (دبلة، خاتم، غويشة…) and karat, filterable by factory and a weight range;
        one card per karat (weight on top, piece count under it). Table 2: the scrap bought from customers, per karat. Karats are never added together. A section whose right is missing is
        marked unavailable (never a zero). Reversed / voided invoices are not counted; returns are NOT netted (this is the SALES movement)."""
        karat, factory, weight_min, weight_max, folded, raw = self._gold_filter_values(filters)
        can_sales, can_scrap = gate("INVOICE_VIEW") and gate("INVENTORY_VIEW"), gate("OLD_GOLD_VIEW")
        tables, cards, options = [], [], {}
        title_sales, title_scrap = "حركة البيع حسب الصنف", "شراء الكسر بالعيار"
        if can_sales:
            options["factory"] = [{"value": "", "text": "الكل"}] + [{"value": str(f["id"]), "text": f["name"]} for f in repo.factories()]
            if factory:
                filters["factory_name"] = next((o["text"] for o in options["factory"] if o["value"] == factory), factory)
            groups, per_karat = {}, {}
            for r in repo.gold_sold_pieces(start, end):
                if not self._gold_keep(r, karat, factory, weight_min, weight_max, folded, raw):
                    continue
                weight = _dec(r["weight_grams"])
                k = int(r["karat_value"])
                g = groups.setdefault((k, r["item_type"] or NO_TYPE), {"count": 0, "weight": ZERO3})
                g["count"] += 1
                g["weight"] += weight
                p = per_karat.setdefault(k, {"count": 0, "weight": ZERO3})
                p["count"] += 1
                p["weight"] += weight
            columns = [col("type", "الصنف"), col("count", "عدد القطع", "count"), col("weight", "الوزن (جرام)", "grams")]
            for k in sorted(per_karat, reverse=True):                                                  # a SEPARATE table per karat: pieces of different karats are never grouped together
                rows = [{"type": t, "count": g["count"], "weight": g["weight"], "ref": {"type": "CHART", "id": t, "extra": str(k)}}
                        for (kk, t), g in sorted(groups.items(), key=lambda kv: (-kv[1]["weight"], kv[0][1])) if kk == k]
                tables.append(table(f"sales_{k}", f"{title_sales} — عيار {k}", columns, rows, karat=k))
            if not per_karat:
                tables.append(table("sales", title_sales, columns, []))
            cards += [total(f"مبيع عيار {k}", f"{v['weight']:,.3f} جرام", "text", f"{v['count']} قطعة", "pieces", karat=k) for k, v in sorted(per_karat.items(), reverse=True)]
        else:
            tables.append(self._unavailable("sales", title_sales, "تحتاج صلاحية عرض الفواتير والمخزون."))
        if can_scrap:
            per = {}
            for r in repo.scrap_purchases(start, end):
                if karat and str(r["karat_value"]) != karat:
                    continue
                p = per.setdefault(int(r["karat_value"]), {"count": 0, "weight": ZERO3})
                p["weight"] += _dec(r["grams"])                                                       # purchases plus their (negative) reversals
                if r["movement_code"] == "CUSTOMER_BUY_IN":
                    p["count"] += int(r["n"])
            rows = [{"karat": f"عيار {k}", "count": v["count"], "weight": v["weight"]} for k, v in sorted(per.items(), reverse=True)]
            tables.append(table("scrap", title_scrap, [col("karat", "العيار"), col("count", "عدد عمليات الشراء", "count"), col("weight", "الوزن المشترى (جرام)", "grams")], rows))
        else:
            tables.append(self._unavailable("scrap", title_scrap, "تحتاج صلاحية عرض الكسر."))
        return {"meaning": [], "tables": tables, "totals": cards, "options": options}

    # ------------------------------------------------------------------ 3b. employee KPI (owner request): sold grams, average making and scrap bought per employee and karat
    SELL_LINES = ("SELL_INVENTORY", "SELL_OLD_GOLD", "SELL_UNIDENTIFIED")

    def _employees(self, repo, gate, filters, start, end):
        """Per employee (the invoice's salesperson) and per KARAT (never added across karats): grams sold, the average making per gram (making total / weight over the lines whose making is
        KNOWN; a line without a stored making is counted apart and never turned into zero), the grams of scrap bought and the average price paid per gram. Only FINALIZED invoices dated in the
        period; fully reversed invoices are not counted (their number is shown). Returns are not netted (this is the employee's SALES activity)."""
        karat = str(filters.get("karat") or "")
        if karat not in ("", "18", "21", "22", "24"):
            raise ReportError("FILTER_INVALID", "عيار غير صحيح.")
        wanted = str(filters.get("employee") or "")
        if wanted and not wanted.isdigit():
            raise ReportError("FILTER_INVALID", "موظف غير صحيح.")
        options = {"employee": [{"value": "", "text": "الكل"}, {"value": "0", "text": NO_EMPLOYEE}] + [{"value": str(e["id"]), "text": e["name"]} for e in repo.employees()]}
        if wanted:
            filters["employee_name"] = next((o["text"] for o in options["employee"] if o["value"] == wanted), wanted)
        stats, names, reversed_invoices, people = {}, {}, set(), set()
        for r in repo.employee_lines(start, end):
            if r["reversal_id"]:
                reversed_invoices.add(int(r["invoice_id"]))
                continue
            key = str(r["employee_id"] or 0)
            if (wanted and key != wanted) or (karat and str(r["karat_value"]) != karat):
                continue
            names[key] = r["employee"] or NO_EMPLOYEE
            people.add(key)
            weight = _dec(r["weight_grams"])
            s = stats.setdefault((key, int(r["karat_value"])), {"sold": ZERO3, "making": ZERO2, "making_weight": ZERO3, "unknown": ZERO3, "bought": ZERO3, "paid": ZERO2})
            if r["line_type"] in self.SELL_LINES:
                s["sold"] += weight
                if r["making_total"] is None:
                    s["unknown"] += weight
                else:
                    s["making"] += _dec(r["making_total"])
                    s["making_weight"] += weight
            else:
                s["bought"] += weight
                s["paid"] += _dec(r["line_total"])
        columns = [col("karat", "العيار"), col("sold", "جرامات البيع", "grams"), col("making", "متوسط المصنعية للجرام", "money"),
                   col("bought", "جرامات الكسر المشتراة", "grams"), col("paid", "متوسط سعر شراء الكسر للجرام", "money")]
        tables = []
        for key in sorted(people, key=lambda k: (names[k], k)):
            rows, unknown = [], ZERO3
            for k in sorted({kk for (ek, kk) in stats if ek == key}, reverse=True):
                s = stats[(key, k)]
                unknown += s["unknown"]
                rows.append({"karat": f"عيار {k}", "sold": s["sold"],
                             "making": (s["making"] / s["making_weight"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if s["making_weight"] else "غير معلوم" if s["sold"] else "—",
                             "bought": s["bought"], "paid": (s["paid"] / s["bought"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if s["bought"] else "—"})
            note = f"المصنعية غير معروفة في {unknown:,.3f} جرام مباع: لا تدخل في المتوسط." if unknown else ""
            tables.append(table(f"employee_{key}", names[key], columns, rows, note=note))
        if not tables:
            tables.append(table("employees", "أداء الموظفين", columns, []))
        meaning = ["لكل موظف وكل عيار على حدة (لا يُجمع بين الأعيار). المتوسطان موزونان بالوزن: المصنعية ÷ الجرامات المباعة المعروفة المصنعية، وسعر الشراء ÷ جرامات الكسر المشتراة.",
                   "الفواتير النهائية فقط بتاريخها، والمعكوسة بالكامل لا تُحسب. المرتجعات لا تُخصم (هذا نشاط البيع)."]
        cards = [total("موظفون لهم حركة", len(people), "count", "", "summary")]
        if reversed_invoices:
            cards.append(total("فواتير معكوسة غير محسوبة", len(reversed_invoices), "count", "", "summary"))
        return {"meaning": meaning, "tables": tables, "totals": cards, "options": options}

    # ------------------------------------------------------------------ 3c. making profit (owner request): the making charged on a sale against the making COST of the piece
    def _makingprofit(self, repo, gate, filters, start, end):
        """For every identified piece SOLD in the period (SELL_INVENTORY lines of FINALIZED, not fully reversed invoices): the making charged on the line minus the making COST of the piece =
        its profit or loss on the making. The cost is the piece's stored acquisition making per gram (the real one of a supplier intake, or the category cost frozen at stock entry); a piece
        without one is ESTIMATED from the category's CURRENT cost and its margin is shown apart as an estimate; a piece with neither, or a sale line whose making is unknown (legacy), is counted
        and left out of the margin, never turned into zero. Karats stay in separate tables (the cost is the same number for every karat). Sales activity only: returns are not netted."""
        karat = str(filters.get("karat") or "")
        if karat not in ("", "18", "21", "22", "24"):
            raise ReportError("FILTER_INVALID", "عيار غير صحيح.")
        wanted = str(filters.get("category") or "")
        if wanted and not wanted.isdigit():
            raise ReportError("FILTER_INVALID", "حرف غير صحيح.")
        costs = repo.category_costs()
        current = {int(r["category_id"]): _dec(r["cost"]) for r in (costs or [])}
        options = {"category": [{"value": "", "text": "الكل"}, {"value": "0", "text": NO_CATEGORY}] + [{"value": str(c["id"]), "text": c["code"]} for c in repo.item_categories()]}
        if wanted:
            filters["category_name"] = next((o["text"] for o in options["category"] if o["value"] == wanted), wanted)
        stats, reversed_invoices = {}, set()
        for r in repo.making_profit_lines(start, end):
            if r["reversal_id"]:
                reversed_invoices.add(int(r["invoice_id"]))
                continue
            key = str(r["category_id"] or 0)
            if (wanted and key != wanted) or (karat and str(r["karat_value"]) != karat):
                continue
            weight = _dec(r["weight_grams"])
            s = stats.setdefault((int(r["karat_value"]), r["category_code"] or NO_CATEGORY), {"pieces": 0, "grams": ZERO3, "sold": ZERO2, "cost": ZERO2, "margin": ZERO2, "estimated": ZERO2,
                                                                                          "estimated_pieces": 0, "left_out": 0})
            s["pieces"] += 1
            s["grams"] += weight
            if r["making_total"] is None:
                s["left_out"] += 1                                                                          # legacy sale line: the making charged is unknown
                continue
            sold = _dec(r["making_total"])
            if r["piece_cost"] is not None:
                per_gram, estimated = _dec(r["piece_cost"]), False
            elif r["category_id"] and int(r["category_id"]) in current:
                per_gram, estimated = current[int(r["category_id"])], True
            else:
                s["left_out"] += 1                                                                          # no cost anywhere: left out of the margin
                continue
            cost = (per_gram * weight).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            s["sold"] += sold
            s["cost"] += cost
            s["margin"] += sold - cost
            if estimated:
                s["estimated"] += sold - cost
                s["estimated_pieces"] += 1
        columns = [col("category", "الحرف"), col("pieces", "القطع المباعة", "count"), col("grams", "الجرامات", "grams"), col("sold", "مصنعية البيع", "money"), col("cost", "تكلفة المصنعية", "money"),
                   col("margin", "الفرق (ربح / خسارة)", "money"), col("estimated", "منه تقديري", "money"), col("left_out", "قطع بدون حساب", "count")]
        tables, tot_margin, tot_estimated, tot_sold, tot_left = [], ZERO2, ZERO2, ZERO2, 0
        for k in sorted({kk for kk, _ in stats}, reverse=True):
            rows = [{"category": code, **{n: v for n, v in s.items() if n != "estimated_pieces"}} for (kk, code), s in sorted(stats.items(), key=lambda kv: kv[0][1]) if kk == k]
            for s in (s for (kk, _), s in stats.items() if kk == k):
                tot_margin += s["margin"]
                tot_estimated += s["estimated"]
                tot_sold += s["sold"]
                tot_left += s["left_out"]
            tables.append(table(f"making_{k}", f"ربح المصنعية — عيار {k}", columns, rows, karat=k))
        if not tables:
            tables.append(table("making", "ربح المصنعية", columns, []))
        meaning = ["الفرق = مصنعية البيع − تكلفة المصنعية للقطعة (جرامات القطعة × تكلفة الجرام المحفوظة فيها). التكلفة الحقيقية لقطع الموردين، وتكلفة الحرف وقت إدخال القطعة لباقي القطع.",
                   "القطعة بلا تكلفة محفوظة تُقدَّر بتكلفة حرفها الحالية ويظهر فرقها منفصلًا في «منه تقديري». قطعة بلا تكلفة أصلًا أو بيع مصنعيته مجهولة (قديم) تُعدّ في «قطع بدون حساب» ولا تدخل الفرق.",
                   "نشاط بيع فقط: المرتجعات لا تُخصم. القطع المباعة بدون هوية (F5 / F7) ليست في هذا التقرير."]
        if costs is None:
            meaning.append("جدول تكلفة الحروف غير موجود في مستوى قاعدة البيانات الحالي: طبّق التحديثات.")
        cards = [total("الفرق على المصنعية", tot_margin, "money", "مصنعية البيع − تكلفتها للقطع المحسوبة" + (f" • منه تقديري {tot_estimated:,.2f}" if tot_estimated else ""), "margin",
                       tone="receivable" if tot_margin >= 0 else "payable"),
                 total("مصنعية البيع المحسوبة", tot_sold, "money", "للقطع التي لها تكلفة", "margin"),
                 total("قطع بدون حساب", tot_left, "count", "بلا تكلفة أو مصنعية بيعها مجهولة", "margin")]
        if reversed_invoices:
            cards.append(total("فواتير معكوسة غير محسوبة", len(reversed_invoices), "count", "", "margin"))
        return {"meaning": meaning, "tables": tables, "totals": cards, "options": options}

    # ------------------------------------------------------------------ 3d. scrap cost (owner request): what the shop really paid per gram for the scrap it bought
    def _scrapcost(self, repo, gate, filters, start, end):
        """Per KARAT: the grams of scrap bought from customers and the value paid for them, as the weighted average price per gram, for the PERIOD and cumulatively from the first stored purchase up
        to the end of the period; and, for information, the latest stored SHOP_BUY price of that karat with the difference (average of the period minus that price: positive = the shop paid more than
        the price it would pay today). Only FINALIZED invoices; fully reversed ones are not counted. The value is the line total before the invoice's final EGP-5 rounding. It is a price comparison,
        NOT a valuation of the stock."""
        karat = str(filters.get("karat") or "")
        if karat not in ("", "18", "21", "22", "24"):
            raise ReportError("FILTER_INVALID", "عيار غير صحيح.")
        period, cumulative, reversed_invoices = {}, {}, set()
        for r in repo.scrap_cost_lines(end):
            if r["reversal_id"]:
                if _dateish(r["doc_date"]) >= start:
                    reversed_invoices.add(int(r["invoice_id"]))
                continue
            k = int(r["karat_value"])
            if karat and str(k) != karat:
                continue
            grams, paid = _dec(r["weight_grams"]), _dec(r["line_total"])
            for bucket in ((cumulative, period) if _dateish(r["doc_date"]) >= start else (cumulative,)):
                b = bucket.setdefault(k, {"grams": ZERO3, "paid": ZERO2})
                b["grams"] += grams
                b["paid"] += paid
        latest = {}
        if repo.has_table("gold_price_quotes"):
            for r in repo.gold_price_daily():                                      # ascending by day: the last one per karat is the latest stored price
                latest[int(r["karat_value"])] = (_dateish(r["day"]).date(), _dec(r["buy"]))

        def average(b):
            return (b["paid"] / b["grams"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if b and b["grams"] else None
        columns = [col("karat", "العيار"), col("p_grams", "جرامات الفترة", "grams"), col("p_paid", "المدفوع في الفترة", "money"), col("p_avg", "متوسط سعر الجرام (الفترة)", "money"),
                   col("c_grams", "الجرامات من البداية", "grams"), col("c_avg", "متوسط سعر الجرام (من البداية)", "money"), col("price", "سعر شراء المحل الحالي", "money"),
                   col("diff", "الفرق عن السعر الحالي", "money")]
        rows, cards = [], []
        for k in sorted(set(period) | set(cumulative), reverse=True):
            p, c = period.get(k), cumulative.get(k)
            p_avg, c_avg = average(p), average(c)
            price = latest.get(k)
            rows.append({"karat": f"عيار {k}", "p_grams": p["grams"] if p else ZERO3, "p_paid": p["paid"] if p else ZERO2, "p_avg": p_avg if p_avg is not None else "—",
                         "c_grams": c["grams"] if c else ZERO3, "c_avg": c_avg if c_avg is not None else "—", "price": price[1] if price else "—",
                         "diff": (p_avg - price[1]) if p_avg is not None and price else "—"})
            if p_avg is not None:
                note = f"{p['grams']:,.3f} جرام بقيمة {p['paid']:,.2f} ج" + (f" • من البداية {c_avg:,.2f}" if c_avg is not None else "")
                cards.append(total(f"متوسط تكلفة كسر عيار {k}", f"{p_avg:,.2f} ج/جرام", "text", note, f"scrap{k}", karat=k))
        meaning = ["متوسط مرجح: كل ما دفعناه ÷ كل جرامات الكسر المشتراة من العملاء، لكل عيار على حدة (لا يُجمع بين الأعيار). «من البداية» من أول فاتورة شراء مخزنة حتى نهاية الفترة.",
                   "القيمة هي إجمالي بند شراء الكسر (بعد خصم الكسر) قبل تقريب الفاتورة النهائي. الفواتير المعكوسة بالكامل لا تُحسب.",
                   "الفرق عن السعر الحالي = متوسط الفترة − آخر سعر شراء مخزن: الموجب يعني أن الشراء كان بأعلى من سعر اليوم. هو مقارنة أسعار وليس تقييمًا للمخزون."]
        if reversed_invoices:
            cards.append(total("فواتير معكوسة غير محسوبة", len(reversed_invoices), "count", "", "summary"))
        return {"meaning": meaning, "tables": [table("scrapcost", "تكلفة الكسر المشترى", columns, rows)], "totals": cards}

    # ------------------------------------------------------------------ 3e. demand and seasonality (owner request): sold grams per month, a simple seasonal forecast and the price association
    def _demand(self, repo, gate, filters, start, end):
        """The shop's SOLD GRAMS per month (the old shop's imported history up to the day before the owner's initial balance, then the new system), the seasonal index of every calendar month that was
        seen at least twice, a forecast of the next three months and the association between the monthly 21K selling price and the grams. A small explainable statistic (services/demand_forecast.py),
        not a promise: every number says how many observations stand behind it and a figure that the history cannot support is not produced. The old history has no karat (all karats together)
        and its weights exist only from February 2025."""
        from services import demand_forecast as forecast_math
        today = self._today()
        daily, legacy, opening = self._demand_daily(repo)
        series = forecast_math.monthly(daily, today)
        prices = {}
        sell_prices = {}
        for day, _buy, sell in self._price_series(repo).get(21, []):
            sell_prices.setdefault((day.year, day.month), []).append(sell)
        for key, values in sell_prices.items():
            prices[key] = (sum(values, Decimal(0)) / len(values)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        grams_by_month = {(m["year"], m["month"]): m["grams"] for m in series}
        months_ar = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")
        rows = []
        for m in series:
            before = grams_by_month.get((m["year"] - 1, m["month"]))
            change = None
            if m["complete"] and m["grams"] is not None and before:
                change = ((m["grams"] - before) / before * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            rows.append({"month": f"{months_ar[m['month'] - 1]} {m['year']}" + ("" if m["complete"] else " (لم ينتهِ)"), "bills": m["bills"],
                         "grams": m["grams"].quantize(Decimal("0.001")) if m["grams"] is not None else "غير معلوم", "price": prices.get((m["year"], m["month"]), "—"),
                         "yoy": f"{change:+}%" if change is not None else "—"})
        indices = forecast_math.seasonal_indices(series)
        season_rows = [{"month": months_ar[i - 1], "index": f"{indices[i][0]:.2f}" if i in indices else "—", "observations": indices[i][1] if i in indices else 0} for i in range(1, 13)]
        predicted = forecast_math.forecast(series, today)
        forecast_rows = [{"month": f"{months_ar[f['month'] - 1]} {f['year']}", "grams": f["grams"], "confidence": f["confidence"], "basis": f"معامل موسمي {f['index']:.2f} ({f['observations']} سنة)" if f["index"] is not None else "متوسط آخر 6 أشهر فقط"}
                         for f in predicted]
        link = forecast_math.elasticity(series, prices)
        complete = forecast_math.usable(series)
        cards = []
        if complete:
            last_six = complete[-forecast_math.TREND_MONTHS:]
            average = sum((m["grams"] for m in last_six), Decimal(0)) / len(last_six)
            cards.append(total("متوسط المبيعات الشهري", f"{average:,.1f} جرام", "text", f"متوسط آخر {len(last_six)} أشهر كاملة بأوزان معروفة", "demand"))
            cards.append(total("آخر شهر كامل", f"{complete[-1]['grams']:,.1f} جرام", "text", f"{months_ar[complete[-1]['month'] - 1]} {complete[-1]['year']}", "demand"))
        if predicted:
            first = predicted[0]
            cards.append(total("المتوقع للشهر القادم", f"{first['grams']:,.1f} جرام", "text", f"ثقة {first['confidence']} • {forecast_rows[0]['basis']}", "demand"))
        if link is not None:
            cards.append(total("علاقة السعر بالطلب", f"{link['slope']:+.2f}", "text", f"عند ارتفاع سعر بيع 21 بنسبة 10% يتغير البيع بحوالي {link['slope'] * 10:+.1f}% • علاقة {forecast_math.strength(link['r'])} (ن = {link['n']} شهرًا)", "demand"))
        notes = []
        if legacy is None:
            notes.append("جدول تاريخ مبيعات المحل القديم غير موجود في مستوى قاعدة البيانات الحالي: طبّق التحديثات.")
        if opening is None:
            notes.append("الرصيد الافتتاحي غير مسجل: المبيعات من النظام الجديد لا تدخل التقرير بعد (تاريخ المحل القديم فقط).")
        meaning = ["إحصاء بسيط من أرقام مبيعاتك، وليس نموذج ذكاء اصطناعي ولا وعدًا بالمستقبل: كل رقم معه عدد المشاهدات خلفه، وما لا يدعمه التاريخ لا يُحسب.",
                   "تاريخ المحل القديم بجرامات كل الأعيار معًا (بدون تفصيل عيار) وأوزانه معروفة من فبراير 2025 فقط؛ الشهر الجاري لا يدخل الحساب لأنه لم ينتهِ. المعامل الموسمي يحتاج مشاهدتين على الأقل لنفس الشهر.",
                   "علاقة السعر بالطلب ارتباط إحصائي على أشهر قليلة، ولا تثبت أن السعر هو السبب (قد يكون نمو المحل أو موسم بعينه)."] + notes
        cols = [col("month", "الشهر"), col("bills", "عدد الفواتير", "count"), col("grams", "الجرامات المباعة", "grams"), col("price", "متوسط سعر بيع 21", "money"), col("yoy", "عن نفس الشهر العام الماضي")]
        charts = [{"chart": "المبيعات على مدار السنة — شهريًا (خط)", "ref": {"type": "DEMAND_CHART", "id": "M"}}, {"chart": "المبيعات على مدار السنة — أسبوعيًا (خط)", "ref": {"type": "DEMAND_CHART", "id": "W"}}]
        tables = [table("charts", "الرسوم البيانية", [col("chart", "الرسم")], charts),
                  table("monthly", "المبيعات الشهرية (جرامات كل الأعيار)", cols, rows),
                  table("seasonal", "معامل الموسمية لكل شهر (1.00 = المتوسط العام)", [col("month", "الشهر"), col("index", "المعامل"), col("observations", "عدد السنوات", "count")], season_rows),
                  table("forecast", "التوقع للأشهر القادمة", [col("month", "الشهر"), col("grams", "الجرامات المتوقعة", "grams"), col("confidence", "الثقة"), col("basis", "الأساس")], forecast_rows)]
        if not predicted:
            tables[-1]["note"] = "التاريخ أقصر من " + str(forecast_math.TREND_MONTHS) + " أشهر كاملة بأوزان معروفة: لا يوجد توقع."
        # ---- seasons that move with the Islamic calendar, the weekday and the part of the month (daily grams; services/season_patterns.py)
        from services import season_patterns as season_math
        values, first_day, last_day = season_math.daily_values(daily, today)
        uplifts = season_math.season_uplifts(values, first_day, last_day, today)
        upcoming = season_math.next_season(today)

        def next_start(key):
            for year in (today.year, today.year + 1):
                for w in season_math.season_windows(year):
                    if w["key"] == key and w["end"] >= today:
                        return w
            return None
        season_rows = []
        for key, name in season_math.SEASONS:
            entry, window = uplifts[key], next_start(key)
            season_rows.append({"season": name, "years": len(entry["years"]), "uplift": f"×{entry['mean']:.2f}" if entry["mean"] is not None else "غير مقاس",
                                "detail": " • ".join(f"{y['year']}: ×{y['uplift']:.2f}" for y in entry["years"]) or "التاريخ لا يغطي الموسم وما حوله",
                                "next": f"{window['start']:%Y-%m-%d} → {window['end']:%Y-%m-%d}" if window else "—"})
        weekday = season_math.weekday_pattern(values, first_day, last_day)
        parts = season_math.month_pattern(values, first_day, last_day)
        tables.append(table("seasons", "مواسم الأعياد والمناسبات (متوسط البيع اليومي مقابل الأيام المحيطة)",
                            [col("season", "الموسم"), col("years", "سنوات مقاسة", "count"), col("uplift", "ارتفاع الطلب المعتاد"), col("detail", "كل سنة"), col("next", "الموعد القادم")], season_rows))
        tables.append(table("weekdays", "نمط أيام الأسبوع (1.00 = متوسط اليوم)", [col("name", "اليوم"), col("index", "المعامل"), col("days", "عدد الأيام", "count")],
                            [{"name": p["name"], "index": f"{p['index']:.2f}", "days": p["days"]} for p in weekday]))
        tables.append(table("monthparts", "نمط أجزاء الشهر (1.00 = متوسط اليوم)", [col("label", "الجزء"), col("index", "المعامل"), col("days", "عدد الأيام", "count")],
                            [{"label": p["label"], "index": f"{p['index']:.2f}", "days": p["days"]} for p in parts]))
        if not weekday:
            tables[-2]["note"] = "التاريخ لا يكفي لنمط الأسبوع (يلزم " + str(season_math.MIN_WEEKDAY_DAYS) + " أيام على الأقل لكل يوم)."
        if not parts:
            tables[-1]["note"] = "التاريخ لا يكفي لنمط الشهر (يلزم " + str(season_math.MIN_MONTH_PART_DAYS) + " يومًا على الأقل لكل جزء)."
        if upcoming is not None:
            measured = uplifts[upcoming["key"]]
            typical = f"الارتفاع المعتاد ×{measured['mean']:.2f} ({len(measured['years'])} سنة)" if measured["mean"] is not None else "لم يُقَس ارتفاعه بعد"
            cards.append(total("أقرب موسم", upcoming["name"], "text", ("جارٍ الآن" if upcoming["days_left"] == 0 else f"بعد {upcoming['days_left']} يوم") + f" • من {upcoming['start']:%Y-%m-%d} إلى {upcoming['end']:%Y-%m-%d} • {typical}", "demand"))
        meaning.append("مواسم الأعياد وعيد الأم تُحسب على أيام كل سنة الفعلية (التقويم الهجري الجدولي، قد يختلف يومًا أو يومين عن الرؤية، ونوافذها أسبوع أو أكثر): متوسط البيع اليومي داخل النافذة ÷ متوسط الأيام المحيطة بها (28 يومًا قبلها وبعدها دون أي نافذة موسم أخرى). "
                       "لكل موسم سنتان على الأكثر من التاريخ الحالي، فالأرقام مؤشر لا وعد. النمط الأسبوعي والشهري من الأيام التي فيها أوزان معروفة حتى أمس؛ اليوم الذي لا فواتير فيه صفر حقيقي.")
        return {"meaning": meaning, "tables": tables, "totals": cards}

    def _demand_daily(self, repo):
        """The sold grams per DAY: the old shop's imported history up to the day before the owner's initial balance, then the new system from that day. Returns (daily, legacy rows or None, opening date or None)."""
        legacy = repo.sales_history_legacy()
        opening = repo.opening_date()
        daily = []
        for r in legacy or []:
            day = _dateish(r["day"]).date()
            if opening is None or day < opening:
                daily.append({"date": day, "bills": int(r["bills"]), "bills_with_weight": int(r["bills_with_weight"]), "grams": _dec(r["grams"])})
        if opening is not None:
            daily += [{"date": _dateish(r["day"]).date(), "bills": int(r["bills"]), "bills_with_weight": int(r["bills_with_weight"]), "grams": _dec(r["grams"])} for r in repo.sales_new_daily(opening)]
        return daily, legacy, opening

    MAX_CHART_YEARS = 6

    def demand_chart(self, granularity, years, *, _expected_actor=None):
        """The sales line chart over the year: `granularity` M = the 12 months, W = the ISO weeks; one line per YEAR the owner chose to compare (`years` = "2026,2025,2024"; empty = the latest year with data and
        the one before it). Unfinished or unknown periods are gaps, never zeros."""
        from services import demand_forecast as forecast_math
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if granularity not in ("M", "W"):
            raise ReportError("FILTER_INVALID", "نوع الرسم غير صحيح.")
        with self._transaction() as connection:
            gate = _Gate(self._authorization, connection, actor)
            if not gate(BASE_PERMISSION) or not gate("INVOICE_VIEW"):
                raise PermissionError("ليس لديك صلاحية عرض هذا الرسم.")
            daily, _legacy, _opening = self._demand_daily(self._repository(connection))
            if not self._session.is_current(actor):
                raise PermissionError("انتهت جلسة الدخول.")
        today = self._today()
        if granularity == "M":
            periods = {(m["year"], m["month"]): m for m in forecast_math.monthly(daily, today)}
            size = 12
        else:
            periods = {key: w for key, w in forecast_math.weekly(daily, today).items()}
            size = 53
        available = sorted({k[0] for k, p in periods.items() if p["grams"] is not None})
        try:
            chosen = [int(y) for y in str(years or "").replace("،", ",").split(",") if y.strip()]
        except (TypeError, ValueError):
            raise ReportError("FILTER_INVALID", "سنة غير صحيحة.") from None
        chosen = list(dict.fromkeys(chosen))
        if len(chosen) > self.MAX_CHART_YEARS or any(y < 1990 or y > 2200 for y in chosen):
            raise ReportError("FILTER_INVALID", f"اختر حتى {self.MAX_CHART_YEARS} سنوات صحيحة للمقارنة.")
        if not chosen:
            chosen = sorted(available[-2:], reverse=True) if available else [today.year]
        chosen.sort(reverse=True)                                                      # newest first: it gets the first (gold) line
        months_ar = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")
        labels = list(months_ar) if granularity == "M" else [str(i) for i in range(1, size + 1)]
        lines = [{"year": y, "values": forecast_math.year_values(periods, y, size)} for y in chosen]
        known = [v for line in lines for v in line["values"] if v is not None]
        return {"granularity": granularity, "selected": chosen, "available": available, "labels": labels, "lines": lines, "high": max(known) if known else 0.0, "has_data": bool(known)}

    @classmethod
    def _gold_filter_values(cls, filters):
        """The validated filters of the gold movement report (shared by the report and its monthly chart): karat, factory, weight range and the search."""
        karat = str(filters.get("karat") or "")
        if karat not in ("", "18", "21", "22", "24"):
            raise ReportError("FILTER_INVALID", "عيار غير صحيح.")
        factory = str(filters.get("factory") or "")
        weight_min, weight_max = cls._weight_filter(filters.get("weight_min"), "الوزن من"), cls._weight_filter(filters.get("weight_max"), "الوزن إلى")
        if weight_min is not None and weight_max is not None and weight_min > weight_max:
            raise ReportError("RANGE_INVALID", "الوزن (من) أكبر من الوزن (إلى).")
        folded, raw = _text_filter(filters)
        return karat, factory, weight_min, weight_max, folded, raw

    @staticmethod
    def _gold_keep(r, karat, factory, weight_min, weight_max, folded, raw):
        """True when a sold piece counts under the filters (a reversed invoice's sale did not happen)."""
        if r["reversal_id"]:
            return False
        weight = _dec(r["weight_grams"])
        if karat and str(r["karat_value"]) != karat:
            return False
        if factory and str(r["factory_id"] or "") != factory:
            return False
        if (weight_min is not None and weight < weight_min) or (weight_max is not None and weight > weight_max):
            return False
        return _matches(folded, raw, r["item_type"], r["category"], r["factory"])

    def gold_chart(self, filters, item_type, karat=None, *, _expected_actor=None):
        """The MONTHLY sales of ONE item type (count and physical grams per calendar month of the period) under the SAME filters as the report (period, karat, factory, weight range,
        search). Months without a sale are shown as zero bars. Needs the invoice and inventory rights, re-read inside the transaction."""
        filters = dict(filters or {})
        if str(karat or "").strip():
            filters["karat"] = str(karat).strip()                                                      # the chart of a row of a KARAT table is that karat's
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        first, last, start, end = _range(filters, self._today())
        filters = {**filters, "from": first.isoformat(), "to": last.isoformat()}
        karat, factory, weight_min, weight_max, folded, raw = self._gold_filter_values(filters)
        label = str(item_type or "").strip() or NO_TYPE
        with self._transaction() as connection:
            gate = _Gate(self._authorization, connection, actor)
            if not (gate(BASE_PERMISSION) and gate("INVOICE_VIEW") and gate("INVENTORY_VIEW")):
                raise PermissionError("ليس لديك صلاحية عرض هذا الرسم.")
            repo = self._repository(connection)
            sold = repo.gold_sold_pieces(start, end)
            if factory:
                filters["factory_name"] = next((f["name"] for f in repo.factories() if str(f["id"]) == factory), factory)
            if not self._session.is_current(actor):
                raise PermissionError("انتهت جلسة الدخول.")
        months, cursor = {}, first.replace(day=1)
        while cursor <= last:
            months[f"{cursor.year}-{cursor.month:02d}"] = {"count": 0, "weight": ZERO3}
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        for r in sold:
            if (r["item_type"] or NO_TYPE) != label or not self._gold_keep(r, karat, factory, weight_min, weight_max, folded, raw):
                continue
            when = _dateish(r["doc_date"])
            key = f"{when.year}-{when.month:02d}"
            if key in months:
                months[key]["count"] += 1
                months[key]["weight"] += _dec(r["weight_grams"])
        points = [{"month": m, "count": v["count"], "weight": v["weight"]} for m, v in months.items()]
        return {"type": label, "points": points, "count": sum(p["count"] for p in points), "weight": sum((p["weight"] for p in points), ZERO3), "applied": self._applied(filters)}

    @staticmethod
    def _weight_filter(value, label):
        text = str(value or "").strip()
        if not text:
            return None
        try:
            number = Decimal(text.replace(",", ""))
        except Exception:
            raise ReportError("FILTER_INVALID", f"{label}: أدخل رقمًا صحيحًا.") from None
        if not number.is_finite() or number < 0:
            raise ReportError("FILTER_INVALID", f"{label}: أدخل رقمًا صحيحًا.")
        return number

    # ------------------------------------------------------------------ 4. customers
    @staticmethod
    def open_items(ledger):
        """The OPEN items of ONE customer's ledger (rows in date order, signed: positive = owed by the customer, negative = owed to the customer). A new row of the same sign as the
        open items is appended; a row of the opposite sign consumes the OLDEST open items first (oldest first) and any remainder becomes an item of its own sign. The open items always add up
        to the customer's real balance; which invoice a payment settled is not stored (a settlement is not allocated to an invoice), so this is the oldest-first reading of the same ledger."""
        queue = []
        for row in ledger:
            amount = _dec(row["amount_delta"])
            if amount == 0:
                continue
            while queue and amount != 0 and (queue[0]["amount"] > 0) != (amount > 0):
                head = queue[0]
                take = min(abs(head["amount"]), abs(amount))
                head["amount"] += take if head["amount"] < 0 else -take
                amount += take if amount < 0 else -take
                if head["amount"] == 0:
                    queue.pop(0)
            if amount != 0:
                queue.append({"amount": amount, "row": row})
        return queue

    def _customers(self, repo, gate, filters, start, end):
        """Owner redesign: how much WE owe the customers and how much THEY owe us, detailed by customer and document. Two cards (the two totals), one table of the open items. Current state
        (the period dates do not apply)."""
        side = str(filters.get("balance") or "ALL")
        if side not in ("ALL", "OWES", "CREDIT"):
            raise ReportError("FILTER_INVALID", "فلتر الرصيد غير صحيح.")
        include_inactive = str(filters.get("inactive") or "") in ("1", "true", "True")
        raw, folded = _text_filter(filters)
        by_customer = {}
        for r in repo.customer_open_ledger():
            by_customer.setdefault(int(r["customer_id"]), []).append(r)
        rows, receivable, payable, shown = [], ZERO2, ZERO2, 0
        for cid, ledger in by_customer.items():
            head = ledger[0]
            if not head["is_active"] and not include_inactive:
                continue
            items = self.open_items(ledger)
            balance = sum((i["amount"] for i in items), ZERO2)
            if balance == 0 or (side == "OWES" and balance < 0) or (side == "CREDIT" and balance > 0):
                continue
            matched = [i for i in items if _matches(folded, raw, head["name"], head["mobile"], head["customer_code"], self._document_label(i["row"]))]
            if not matched:
                continue
            shown += 1
            for item in matched:
                amount = item["amount"]
                receivable += amount if amount > 0 else ZERO2
                payable += -amount if amount < 0 else ZERO2
                r = item["row"]
                invoice = r["reference_type"] == "INVOICE" and r["reference_id"]
                rows.append({"name": head["name"], "document": self._document_label(r), "date": _dateish(r["occurred_at"]), "owes": amount if amount > 0 else None,
                             "credit": -amount if amount < 0 else None, "ref": {"type": "INVOICE", "id": str(r["reference_id"])} if invoice else {"type": "CUSTOMER", "id": str(cid)}})
        rows.sort(key=lambda x: (x["name"], x["date"]))
        cols = [col("name", "العميل"), col("document", "الفاتورة / المستند"), col("date", "التاريخ", "date"), col("owes", "عليه", "money"), col("credit", "له", "money")]
        totals = [total("إجمالي اللي علينا (للعملاء)", payable, "money", "أرصدة العملاء الدائنة", "money", tone="payable"),
                  total("إجمالي اللي عليهم (على العملاء)", receivable, "money", "أرصدة العملاء المدينة", "money", tone="receivable")]
        meaning = ["الإشارة: «عليه» مستحق للمحل، «له» مستحق للعميل. المصدر دفتر حساب العميل نفسه الذي يقرأه كشف الحساب.",
                   "السطور هي بنود الحساب المفتوحة بمنطق الأقدم أولًا: كل تحصيل أو مرتجع أو عربون يُخصم من أقدم بند مفتوح لأن التحصيل لا يُربط بفاتورة بعينها. مجموع بنود العميل = رصيده الحالي.",
                   "الأرقام هي رصيد اليوم، ولا تتأثر بتاريخ الفترة."]
        return {"meaning": meaning, "tables": [table("customers", "الأرصدة المفتوحة حسب العميل والفاتورة", cols, rows, paged=True)], "totals": totals}

    # ------------------------------------------------------------------ 4b. Dead Cash (its own report, beside the customers)
    def _deadcash(self, repo, gate, filters, start, end):
        """Owner request: Dead Cash apart from the customers report. Cards: the ACTIVE written-off total and the number of write-offs (a reversed one is listed, dimmed, and not counted); below them
        one table of the write-offs (customer, date, amount, reason, state). Filters: state (active / reversed), inactive customers, search. All-time history (the period dates do not apply)."""
        state = str(filters.get("state") or "ALL")
        if state not in ("ALL", "ACTIVE", "REVERSED"):
            raise ReportError("FILTER_INVALID", "فلتر الحالة غير صحيح.")
        include_inactive = str(filters.get("inactive") or "") in ("1", "true", "True")
        raw, folded = _text_filter(filters)
        rows, active_total, active_count, reversed_count, reversed_total = [], ZERO2, 0, 0, ZERO2
        for w in repo.customer_dead_cash():
            if not w["is_active"] and not include_inactive:
                continue
            if not _matches(folded, raw, w["name"], w["mobile"], w["customer_code"], w["reason"]):
                continue
            amount, reversed_ = _dec(w["amount"]), bool(w["is_reversed"])
            if (state == "ACTIVE" and reversed_) or (state == "REVERSED" and not reversed_):
                continue
            if reversed_:
                reversed_count += 1
                reversed_total += amount
            else:
                active_total += amount
                active_count += 1
            rows.append({"name": w["name"], "date": _dateish(w["occurred_at"]), "amount": amount, "reason": w["reason"] or "", "state": "معكوس" if reversed_ else "ساري",
                         "ref": {"type": "CUSTOMER", "id": str(w["customer_id"])}})
        cols = [col("name", "العميل"), col("date", "تاريخ الشطب", "date"), col("amount", "المبلغ المعدوم", "money"), col("reason", "السبب"), col("state", "الحالة")]
        totals = [total("إجمالي الديون المعدومة", active_total, "money", "الشطب السارية فقط", "deadcash", tone="deadcash"),
                  total("عدد الديون المعدومة", active_count, "count", "", "deadcash", tone="deadcash")]
        if reversed_count:
            totals.append(total("شطب معكوس (غير محسوب)", reversed_total, "money", f"{reversed_count} شطب", "deadcash"))
        meaning = ["الدين المعدوم هو رصيد على العميل شطبه المحل؛ الشطب المعكوس يبقى في القائمة بحالة «معكوس» ولا يدخل الإجماليات."]
        return {"meaning": meaning, "tables": [table("deadcash", "الديون المعدومة", cols, rows, paged=True)], "totals": totals}

    @staticmethod
    def _document_label(row):
        """«فاتورة INV0001» for an invoice, otherwise the kind of document and its code."""
        kind = row["reference_type"] or "UNKNOWN"
        code = row.get("invoice_number") or row.get("reference_code") or ""
        return (REF_TEXT.get(kind, kind) + (f" {code}" if code else "")).strip()

    # ------------------------------------------------------------------ 5. suppliers
    def _suppliers(self, repo, gate, filters, start, end):
        """Owner redesign: TOTALS per supplier. One table row per supplier with the balance of his GOLD account (physical grams of ITS OWN karat, never converted to 21K) and of his MAKING account
        (EGP), each split into «علينا» (we owe the supplier, positive ledger sum) and «لنا» (the supplier owes us); cards: the making totals (we owe / they owe) and one gold card per karat. Gold and
        making are never added together. The balances are the SUM of the immutable ledgers up to the end of the report date (today by default); the movements stay in the CSV, not on the screen."""
        raw, folded = _text_filter(filters)
        supplier_filter = str(filters.get("supplier") or "")
        suppliers = {int(s["id"]): s for s in repo.supplier_rows() if _matches(folded, raw, s["name"], s["phone"]) and (not supplier_filter or str(s["id"]) == supplier_filter)}
        gold_before = {(int(r["supplier_id"]), int(r["karat_id"])): _dec(r["grams"]) for r in repo.supplier_gold_before(start)}
        making_before = {int(r["supplier_id"]): _dec(r["amount"]) for r in repo.supplier_making_before(start)}
        gold = [m for m in repo.supplier_gold_movements(start, end) if int(m["supplier_id"]) in suppliers]
        making = [m for m in repo.supplier_making_movements(start, end) if int(m["supplier_id"]) in suppliers]
        karat_value = {}
        gold_close = dict(gold_before)
        for m in gold:
            k = (int(m["supplier_id"]), int(m["karat_id"]))
            karat_value[k] = int(m["karat_value"])
            gold_close[k] = gold_close.get(k, ZERO3) + _dec(m["grams"])
        for sid, sp in suppliers.items():
            if sp["karat_id"] is not None:
                karat_value[(sid, int(sp["karat_id"]))] = int(sp["karat_value"])
        making_close = dict(making_before)
        for m in making:
            making_close[int(m["supplier_id"])] = making_close.get(int(m["supplier_id"]), ZERO2) + _dec(m["amount"])
        rows, gold_by_karat = [], {}
        making_owed = making_credit = ZERO2
        for sid, sp in sorted(suppliers.items(), key=lambda kv: kv[1]["name"]):
            gold_cells = [(k, v) for (i, k), v in gold_close.items() if i == sid and v != 0]
            g_owed = ZERO3
            g_credit = ZERO3
            karat_text = ""
            for kid, balance in gold_cells:
                value = karat_value.get((sid, kid))
                karat_text = f"عيار {value}" if value else "عيار ؟"
                if balance > 0:
                    g_owed += balance
                else:
                    g_credit += -balance
                if value:
                    c = gold_by_karat.setdefault(value, ZERO3)
                    gold_by_karat[value] = c + balance
            balance_m = making_close.get(sid, ZERO2)
            if not gold_cells and balance_m == 0:
                continue                                                                               # a supplier with nothing open is not listed
            if balance_m > 0:
                making_owed += balance_m
            elif balance_m < 0:
                making_credit += -balance_m
            rows.append({"supplier": sp["name"], "karat": karat_text or "—",
                         "gold_owed": g_owed if g_owed else None, "gold_credit": g_credit if g_credit else None,
                         "making_owed": balance_m if balance_m > 0 else None, "making_credit": -balance_m if balance_m < 0 else None})
        names = {sid: sp["name"] for sid, sp in suppliers.items()}
        det = [{"date": _dateish(m["occurred_at"]), "supplier": names[int(m["supplier_id"])], "account": f"ذهب عيار {m['karat_value']}", "type": m["type_name"],
                "gold": _dec(m["grams"]), "amount": None, "source": m["source_reference"] or "", "description": m["description"] or ""} for m in gold]
        det += [{"date": _dateish(m["occurred_at"]), "supplier": names[int(m["supplier_id"])], "account": "مصنعيات", "type": m["type_name"], "gold": None, "amount": _dec(m["amount"]),
                 "source": m["source_reference"] or "", "description": m["description"] or ""} for m in making]
        det.sort(key=lambda r: r["date"], reverse=True)
        cols = [col("supplier", "المورد"), col("karat", "عيار حساب الذهب"), col("gold_owed", "ذهب علينا (جرام)", "grams"), col("gold_credit", "ذهب لنا (جرام)", "grams"),
                col("making_owed", "مصنعيات علينا", "money"), col("making_credit", "مصنعيات لنا", "money")]
        dcols = [col("date", "التاريخ", "date"), col("supplier", "المورد"), col("account", "الحساب"), col("type", "نوع الحركة (يشمل العكس)"), col("gold", "ذهب (جرام)", "grams"),
                 col("amount", "مصنعيات (ج)", "money"), col("source", "المرجع"), col("description", "البيان")]
        totals = [total("إجمالي مصنعيات علينا للموردين", making_owed, "money", "أرصدة المصنعيات التي علينا", "making", tone="payable"),
                  total("إجمالي مصنعيات لنا عند الموردين", making_credit, "money", "أرصدة المصنعيات التي لنا", "making", tone="receivable")]
        for k, net in sorted(gold_by_karat.items(), reverse=True):
            totals.append(total(f"ذهب عيار {k}", f"{abs(net):,.3f} جرام", "text", "علينا للموردين" if net > 0 else "لنا عند الموردين" if net < 0 else "متوازن", "gold", karat=k))
        meaning = ["علينا = مستحق للمورد، لنا = مستحق لنا عند المورد. الذهب بجرام فعلي بعيار حساب كل مورد ولا يُحوَّل إلى عيار 21، والمصنعيات بالجنيه حساب مستقل ولا يُجمع مع الذهب.",
                   "الأرصدة مجموع دفتر المورد حتى نهاية التاريخ (اليوم افتراضيًا). حركات الدفتر الكاملة (فواتير ومدفوعات وتسويات وعكسها) في ملف CSV."]
        return {"meaning": meaning, "tables": [table("suppliers", "أرصدة الموردين", cols, rows), table("movements", "الحركات", dcols, det, paged=True)], "totals": totals}

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
        money_in = money_out = ZERO2
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
                money_in += inn
                money_out += out
                tot[kind] += closing
                if o or inn or out or t_in or t_out or a["is_active"]:
                    summary.append({"account": f"{label}: {a['name']}", "opening": o, "in": inn, "out": out, "t_in": t_in, "t_out": t_out, "closing": closing})
        det.sort(key=lambda r: r["date"], reverse=True)
        scols = [col("account", "الحساب"), col("opening", "رصيد أول الفترة", "money"), col("in", "داخل", "money"), col("out", "خارج", "money"), col("t_in", "تحويل داخلي وارد", "money"),
                 col("t_out", "تحويل داخلي صادر", "money"), col("closing", "رصيد آخر الفترة", "money")]
        dcols = [col("date", "التاريخ", "date"), col("account", "الحساب"), col("type", "نوع الحركة"), col("amount", "المبلغ (+ داخل / − خارج)", "money"), col("reference", "المرجع"), col("description", "البيان")]
        # Owner review: no movement-based cards (count / in / out / net of the shown movements); the movements table is not shown on screen (the CSV keeps every movement).
        totals = []
        if gate("INVOICE_VIEW"):                                                                       # the month's sales and scrap purchases come from the invoices: left out without the right, never a zero
            documents = {t["label"]: t["value"] for t in self._sales(repo, gate, {"from": filters["from"], "to": filters["to"], "kind": "ALL", "state": "ALL"}, start, end)["totals"]}
            sales_value = next((v for k, v in documents.items() if k.startswith("قيمة البيع")), None)
            buy_value = next((v for k, v in documents.items() if k.startswith("قيمة شراء الكسر")), None)
            if sales_value is not None:
                totals.append(total("المبيعات", sales_value, "money", "قيمة فواتير البيع في الفترة (فعّالة)", "documents"))
            if buy_value is not None:
                totals.append(total("مشتريات الذهب (الكسر)", buy_value, "money", "قيمة شراء الكسر من العملاء في الفترة", "documents"))
        totals.append(total("الإيرادات", money_in, "money", "كل الداخل إلى الحسابات المعروضة بلا التحويلات الداخلية", "flow", tone="receivable"))
        totals.append(total("المصروفات", money_out, "money", "كل الخارج من الحسابات المعروضة بلا التحويلات الداخلية", "flow", tone="payable"))
        if any(k == "CASH" for k, *_ in sections):
            totals.append(total("رصيد آخر الفترة — النقدي", tot["CASH"], "money", "كل حركات الحسابات النقدية", "balances"))
        if any(k == "BANK" for k, *_ in sections):
            totals.append(total("رصيد آخر الفترة — البنكي والمحافظ", tot["BANK"], "money", "كل حركات الحسابات البنكية والمحافظ", "balances"))
        if not missing and not account_filter:
            totals.append(total("صافي التحويلات الداخلية لكل الحسابات (يجب أن يساوي صفرًا)", net_transfers, "money", "كل الحسابات", "balances"))
        meaning = ["الرصيد = مجموع حركات حساب الخزنة أو الحساب البنكي نفسه (دفتر الحركات)؛ رصيد أول الفترة = كل الحركات قبل تاريخ البداية. البحث يصفّي جدول الحركات وإجماليات «المطابقة» فقط، وأرصدة الحسابات كاملة دائمًا.",
                   "«داخل/خارج» للحركات العادية فقط؛ التحويل بين حسابين يظهر في عموده الخاص ولا يُحتسب إيرادًا ولا مصروفًا.",
                   "حركة الفترة لا تساوي بالضرورة مجموع المستندات الفعّالة في تقرير المبيعات (آجل، تحصيل لاحق، شراء كسر، مرتجع...)."]
        tables = [table("accounts", "الحسابات (أرصدة كاملة للفترة — لا يغيّرها البحث)", scols, summary), table("movements", "الحركات", dcols, det, paged=True)]
        for label in missing:
            tables.append(self._unavailable("missing_" + label, f"الحسابات ({label})", "ليست لديك صلاحية عرض هذا القسم."))
        return {"meaning": meaning, "tables": tables, "totals": totals}
