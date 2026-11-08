"""KPI rules (pure, no database): turns the sale / scrap-purchase lines of two equal periods into «am I on track?» items and per-employee flags.

Every item has a status (GREEN / YELLOW / RED / GRAY = not enough data) and one decision sentence. Thresholds are constants here so they are written once; they are indicators for the
owner's attention, NOT proof that anybody did wrong. Money and weights are Decimal. A line without a stored making is never turned into zero.
"""
from decimal import Decimal, ROUND_HALF_UP

GREEN, YELLOW, RED, GRAY = "GREEN", "YELLOW", "RED", "GRAY"
STATUS_TEXT = {GREEN: "🟢 ماشي كويس", YELLOW: "🟡 انتبه", RED: "🔴 غلط", GRAY: "⚪ بيانات غير كافية"}

SELL_LINES = frozenset({"SELL_INVENTORY", "SELL_OLD_GOLD", "SELL_UNIDENTIFIED", "SELL_RAFAYEA"})
BUY_LINE = "BUY_OLD_GOLD"
ONE_GRAM = Decimal("1")                  # a piece under 1 g carries a making per piece: it never enters a per-gram figure (owner 2026-11-04)
LOW_RATIO = Decimal("0.80")              # a line whose making per gram is under 80% of the other employees' weighted average is «low»
MIN_OTHERS_GRAMS = Decimal("20")         # the other employees must have sold at least this many grams of the karat for the comparison to mean anything
DEFAULT_DISCOUNT = Decimal("1.50")       # the default old-gold discount percent when the setting cannot be read
DISCOUNT_TOLERANCE = Decimal("0.05")     # percentage points under the default that are still «as expected»
MIN_EMPLOYEE_LINES = 5                   # fewer sale / purchase lines than this: no verdict about a person
SALES_DROP = (Decimal("5"), Decimal("20"))        # % fall against the previous period: yellow from 5, red from 20
MAKING_DROP = (Decimal("5"), Decimal("15"))
EXPENSE_RISE = (Decimal("10"), Decimal("30"))     # % rise: yellow from 10, red from 30
HUNDRED = Decimal("100")
Q1 = Decimal("0.1")
Q2 = Decimal("0.01")


def _d(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def change_pct(now, before):
    """(now - before) / before in percent (one decimal), or None when there is no usable previous figure."""
    if before is None or now is None or _d(before) == 0:
        return None
    return ((_d(now) - _d(before)) / _d(before) * HUNDRED).quantize(Q1, rounding=ROUND_HALF_UP)


def higher_is_better(pct, limits):
    """GREEN while the figure did not fall more than limits[0] %, YELLOW up to limits[1] %, RED beyond; GRAY without a comparison."""
    if pct is None:
        return GRAY
    if pct >= -limits[0]:
        return GREEN
    return YELLOW if pct > -limits[1] else RED


def lower_is_better(pct, limits):
    if pct is None:
        return GRAY
    if pct <= limits[0]:
        return GREEN
    return YELLOW if pct < limits[1] else RED


def split(lines, start, end, previous_start):
    """(this period lines, previous period lines): fully reversed invoices are left out of both; `end` is exclusive."""
    now, before = [], []
    for line in lines:
        if line.get("reversal_id"):
            continue
        day = line["invoice_date"]
        if start <= day < end:
            now.append(line)
        elif previous_start <= day < start:
            before.append(line)
    return now, before


def _making_per_gram(lines):
    making, grams = Decimal("0"), Decimal("0")
    for line in lines:
        if line["line_type"] in SELL_LINES and line.get("making_total") is not None and _d(line["weight_grams"]) >= ONE_GRAM:
            making += _d(line["making_total"])
            grams += _d(line["weight_grams"])
    return (making / grams).quantize(Q2, rounding=ROUND_HALF_UP) if grams else None


def sold_grams(lines):
    return sum((_d(l["weight_grams"]) for l in lines if l["line_type"] in SELL_LINES), Decimal("0"))


def discount_percent(lines):
    """Weighted old-gold discount actually given on ordinary scrap lines (a pound / bar line has a cashback, not a discount): sum(discount) / sum(gross) in percent, or None."""
    gross, discount = Decimal("0"), Decimal("0")
    for l in lines:
        if l["line_type"] == BUY_LINE and not l.get("scrap_kind") and l.get("gross_total") is not None and l.get("discount_amount") is not None:
            gross += _d(l["gross_total"])
            discount += _d(l["discount_amount"])
    return (discount / gross * HUNDRED).quantize(Q2, rounding=ROUND_HALF_UP) if gross else None


def _pct_text(value):
    return f"{value:+,.1f}%"


def sales_item(now, before):
    a, b = sold_grams(now), sold_grams(before)
    pct = change_pct(a, b)
    status = higher_is_better(pct, SALES_DROP)
    advice = {GREEN: "المبيعات ماشية كويس مقارنة بالفترة اللي قبلها.", YELLOW: "المبيعات أقل من الفترة اللي قبلها: راجع الأصناف والعروض.", RED: "المبيعات نزلت كتير: شوف إيه اللي اتغير (سعر، مخزون ناقص، موسم).",
              GRAY: "مفيش مبيعات في الفترة اللي قبلها للمقارنة."}[status]
    return {"key": "sales", "title": "مبيعات الذهب (جرام)", "status": status, "value": f"{a:,.3f} جرام", "compare": f"{b:,.3f} جرام" + (f" ({_pct_text(pct)})" if pct is not None else ""), "advice": advice}


def making_item(now, before, below_cost_lines, known_lines):
    a, b = _making_per_gram(now), _making_per_gram(before)
    pct = change_pct(a, b)
    status = higher_is_better(pct, MAKING_DROP)
    if a is None:
        status = GRAY
    share = (Decimal(below_cost_lines) / Decimal(known_lines) * HUNDRED).quantize(Q1, rounding=ROUND_HALF_UP) if known_lines else None
    if status == GREEN and share is not None and share >= Decimal("20"):
        status = YELLOW
    advice = {GREEN: "متوسط المصنعية مستقر أو أحسن.", YELLOW: "المصنعية أقل من اللي فات أو في قطع كتير بتتباع تحت تكلفتها: راجع التسعير.",
              RED: "المصنعية نزلت كتير: راجع الأسعار والخصومات والموظفين اللي بيبيعوا بمصنعية قليلة.", GRAY: "مفيش مصنعية معروفة في الفترة."}[status]
    extra = f" • تحت التكلفة: {below_cost_lines} من {known_lines} ({share}%)" if share is not None else ""
    return {"key": "making", "title": "متوسط المصنعية للجرام (من جرام فأكثر)", "status": status, "value": f"{a:,.2f} ج" if a is not None else "—",
            "compare": (f"{b:,.2f} ج" + (f" ({_pct_text(pct)})" if pct is not None else "") if b is not None else "—") + extra, "advice": advice}


def scrap_item(now, default_percent):
    given = discount_percent(now)
    default = _d(default_percent)
    if given is None:
        status = GRAY
    elif given >= default - DISCOUNT_TOLERANCE:
        status = GREEN
    elif given >= default - Decimal("0.25"):
        status = YELLOW
    else:
        status = RED
    advice = {GREEN: "خصم شراء الكسر في المعتاد.", YELLOW: "خصم الكسر الفعلي أقل شوية من الافتراضي: بتدفع للعملاء أكتر من اللازم.",
              RED: "خصم الكسر الفعلي أقل بكتير من الافتراضي: بتشتري الكسر بسعر أغلى من المفروض.", GRAY: "مفيش شراء كسر عادي في الفترة."}[status]
    return {"key": "scrap", "title": "خصم شراء الكسر الفعلي", "status": status, "value": f"{given}%" if given is not None else "—", "compare": f"الافتراضي {default}%", "advice": advice}


def profit_item(now, before):
    """Gold profit (21K-equivalent grams) of this period against the previous one: GRAY without two comparable snapshot pairs."""
    if now is None:
        return {"key": "profit", "title": "ربح الذهب (مكافئ 21)", "status": GRAY, "value": "—", "compare": "—", "advice": "محتاج لقطات جرد كفاية: سجّل الرصيد الافتتاحي وافتح البرنامج كل يوم."}
    now = _d(now)
    if now < 0:
        status, advice = RED, "الذهب في المحل نقص في الفترة دي: راجع الشراء بكاش والسحب والمصاريف."
    elif before is None:
        status, advice = GRAY, "مفيش فترة سابقة للمقارنة."
    else:
        before = _d(before)
        if before <= 0:
            status, advice = GREEN, "الربح موجب بعد فترة ما كانتش رابحة."
        elif now >= before:
            status, advice = GREEN, "الربح بيزيد بنفس السرعة أو أسرع."
        elif now >= before / 2:
            status, advice = YELLOW, "الربح لسه بيزيد لكن أبطأ من الفترة اللي قبلها."
        else:
            status, advice = RED, "سرعة زيادة الربح وقعت للنص أو أقل."
    compare = f"{_d(before):+,.3f} جرام" if before is not None else "—"
    return {"key": "profit", "title": "ربح الذهب (مكافئ 21)", "status": status, "value": f"{now:+,.3f} جرام", "compare": compare, "advice": advice}


def expenses_item(now, before, profit_status):
    if now is None:
        return {"key": "expenses", "title": "المصروفات المسجلة", "status": GRAY, "value": "—", "compare": "—", "advice": "غير متاحة (صلاحية أو بيانات)."}
    pct = change_pct(now, before)
    status = lower_is_better(pct, EXPENSE_RISE)
    if status in (YELLOW, RED) and profit_status in (YELLOW, RED):
        status = RED
    advice = {GREEN: "المصاريف تحت السيطرة.", YELLOW: "المصاريف زادت عن الفترة اللي قبلها: شوف البنود.", RED: "المصاريف زادت والربح مش بيواكبها: راجع أكبر البنود.",
              GRAY: "مفيش مصاريف في الفترة اللي قبلها للمقارنة."}[status]
    return {"key": "expenses", "title": "المصروفات المسجلة", "status": status, "value": f"{_d(now):,.2f} ج", "compare": (f"{_d(before):,.2f} ج" + (f" ({_pct_text(pct)})" if pct is not None else "")) if before is not None else "—", "advice": advice}


def is_mandatory_expense(label):
    """Owner 2026-11-08: salaries and invoice differences are compulsory, so they are not an indicator of how the shop is doing: the KPI leaves them out of the expenses comparison (they stay in the
    cash-flow report and are listed apart in the KPI detail table)."""
    text = str(label or "")
    return "مرتب" in text or ("فرق" in text and ("فواتير" in text or "فاتورة" in text or "فاتوره" in text))


def income_item(now, before):
    """The recorded income (the categorised «إيراد» entries of the treasury) against the previous period: higher is better, never red on its own (income is not a promise)."""
    if now is None:
        return {"key": "income", "title": "الإيرادات المسجلة", "status": GRAY, "value": "—", "compare": "—", "advice": "غير متاحة (صلاحية أو بيانات)."}
    pct = change_pct(now, before)
    status = higher_is_better(pct, (Decimal("20"), Decimal("1000")))
    advice = {GREEN: "الإيرادات المسجلة ماشية كويس مقارنة بالفترة اللي قبلها.", YELLOW: "الإيرادات المسجلة أقل من الفترة اللي قبلها.", RED: "الإيرادات المسجلة نزلت كتير.",
              GRAY: "مفيش إيرادات مسجلة في الفترة اللي قبلها للمقارنة (أو مفيش إيراد مسجل أصلًا: دي بنود «إيراد» اللي بتتسجل في حركة الخزنة)."}[status]
    return {"key": "income", "title": "الإيرادات المسجلة", "status": status, "value": f"{_d(now):,.2f} ج", "compare": (f"{_d(before):,.2f} ج" + (f" ({_pct_text(pct)})" if pct is not None else "")) if before is not None else "—", "advice": advice}


def making_flags(making_per_gram, cost_per_gram, others_average):
    """Why one sale line counts as a LOW making (both reasons may hold): under the piece's cost, and / or under 80% of the other employees' weighted average of the same karat."""
    reasons = []
    if cost_per_gram is not None and making_per_gram < _d(cost_per_gram):
        reasons.append("BELOW_COST")
    if others_average is not None and making_per_gram < _d(others_average) * LOW_RATIO:
        reasons.append("BELOW_OTHERS")
    return reasons


def employee_rows(now, default_percent):
    """One verdict per employee over the lines of THIS period. Counted lines: sale lines of 1 g or more with a known making, ordinary scrap lines, and the employee's invoices."""
    default = _d(default_percent)
    sales = {}
    for l in now:
        if l["line_type"] in SELL_LINES:
            s = sales.setdefault(int(l["karat_value"]), {})
            e = s.setdefault(str(l.get("employee_id") or 0), [Decimal("0"), Decimal("0")])
            if l.get("making_total") is not None and _d(l["weight_grams"]) >= ONE_GRAM:
                e[0] += _d(l["making_total"])
                e[1] += _d(l["weight_grams"])
    people = {}
    for l in now:
        key = str(l.get("employee_id") or 0)
        p = people.setdefault(key, {"name": l.get("employee") or "غير محدد", "lines": 0, "low_making": 0, "low_discount": 0, "invoices": set(), "reasons": set()})
        p["invoices"].add(int(l["invoice_id"]))
        if l["line_type"] in SELL_LINES:
            if l.get("making_total") is None or _d(l["weight_grams"]) < ONE_GRAM:
                continue
            p["lines"] += 1
            karat = int(l["karat_value"])
            others_making = sum((v[0] for k, v in sales[karat].items() if k != key), Decimal("0"))
            others_grams = sum((v[1] for k, v in sales[karat].items() if k != key), Decimal("0"))
            average = (others_making / others_grams) if others_grams >= MIN_OTHERS_GRAMS else None
            mpg = _d(l["making_total"]) / _d(l["weight_grams"])
            cost = l.get("category_cost") if l.get("category_cost") is not None else l.get("piece_cost")       # the letter's CURRENT cost first (owner 2026-11-08)
            reasons = making_flags(mpg, cost, average)
            if reasons:
                p["low_making"] += 1
                p["reasons"].update(reasons)
        elif l["line_type"] == BUY_LINE and not l.get("scrap_kind") and l.get("gross_total") is not None and l.get("discount_amount") is not None and _d(l["gross_total"]) > 0:
            p["lines"] += 1
            percent = _d(l["discount_amount"]) / _d(l["gross_total"]) * HUNDRED
            if percent < default - DISCOUNT_TOLERANCE:
                p["low_discount"] += 1
                p["reasons"].add("LOW_DISCOUNT")
    rows = []
    for key, p in people.items():
        flagged = p["low_making"] + p["low_discount"]
        status, note = employee_status(p["lines"], flagged)
        rows.append({"employee": p["name"], "lines": p["lines"], "low_making": p["low_making"], "low_discount": p["low_discount"], "status": status, "status_text": STATUS_TEXT[status], "note": note})
    order = {RED: 0, YELLOW: 1, GREEN: 2, GRAY: 3}
    return sorted(rows, key=lambda r: (order[r["status"]], r["employee"]))


def employee_status(lines, flagged):
    if lines < MIN_EMPLOYEE_LINES:
        return GRAY, "عدد البنود قليل: مفيش حكم على الموظف."
    share = Decimal(flagged) / Decimal(lines) * HUNDRED
    if flagged >= 3 and share >= Decimal("30"):
        return RED, f"{flagged} بند من {lines} فيهم مصنعية قليلة أو خصم كسر أقل من المعتاد: راجع فواتيره بنفسك."
    if flagged >= 2 and share >= Decimal("15"):
        return YELLOW, f"{flagged} بند من {lines} محتاجين انتباه."
    return GREEN, "في المعتاد."


def below_cost_counts(now):
    """(sale lines of 1 g or more whose making per gram is under the piece's cost, sale lines of 1 g or more that have both a known making and a cost)."""
    below, known = 0, 0
    for l in now:
        if l["line_type"] not in SELL_LINES or l.get("making_total") is None or _d(l["weight_grams"]) < ONE_GRAM:
            continue
        cost = l.get("category_cost") if l.get("category_cost") is not None else l.get("piece_cost")
        if cost is None:
            continue
        known += 1
        if _d(l["making_total"]) / _d(l["weight_grams"]) < _d(cost):
            below += 1
    return below, known
