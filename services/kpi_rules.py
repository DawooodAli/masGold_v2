"""KPI rules (pure, no database): turns the sale / scrap-purchase lines of two equal periods into «am I on track?» items and per-employee flags.

Every item has a status (GREEN / YELLOW / RED / GRAY = not enough data) and one decision sentence. Thresholds are constants here so they are written once; they are indicators for the
owner's attention, NOT proof that anybody did wrong. Money and weights are Decimal. A line without a stored making is never turned into zero.
"""
from decimal import Decimal, ROUND_HALF_UP

GREEN, YELLOW, RED, GRAY = "GREEN", "YELLOW", "RED", "GRAY"
STATUS_TEXT = {GREEN: "🟢 ماشي كويس", YELLOW: "🟡 انتبه", RED: "🔴 غلط", GRAY: "⚪ بيانات غير كافية"}

SELL_LINES = frozenset({"SELL_INVENTORY", "SELL_OLD_GOLD", "SELL_UNIDENTIFIED", "SELL_RAFAYEA"})
BUY_LINE = "BUY_OLD_GOLD"
BULLION_TYPES = frozenset({"سبيكة", "جنية", "جنيه"})          # the item types of bars and gold pounds (the same names the dashboard uses)


def is_bullion(line):
    name = str(line.get("item_type") or "").strip()
    if name in BULLION_TYPES:
        return True
    # tolerant: «جنيه ذهب», «نص جنيه», «سبيكه 10 جرام», «جنية» / «جنيه» / «سبيكة» / «سبيكه»
    return any(word in name for word in ("سبيك", "جني", "جنى"))


def crafted(lines):
    """Owner 2026-11-08: bars and pounds are sold at almost no making and would drag the making averages and the employee verdicts down: the making indicators use the crafted pieces only."""
    return [l for l in lines if not is_bullion(l)]
ONE_GRAM = Decimal("1")                  # a piece under 1 g carries a making per piece: it never enters a per-gram figure (owner 2026-11-04)
LOW_RATIO = Decimal("0.80")              # a line whose making per gram is under 80% of the other employees' weighted average is «low»
MIN_OTHERS_GRAMS = Decimal("20")         # the other employees must have sold at least this many grams of the karat for the comparison to mean anything
DEFAULT_DISCOUNT = Decimal("1.50")       # the default old-gold discount percent when the setting cannot be read
DISCOUNT_TOLERANCE = Decimal("0.05")     # percentage points under the default that are still «as expected» (the period-average scrap item only)
MIN_PROFIT_PCT = Decimal("2")            # owner 2026-11-13: a sale line whose profit is under 2% of its gold value needs attention
MIN_DISCOUNT_PCT = Decimal("1.5")        # owner 2026-11-13: a scrap line whose discount is under 1.5% needs attention
CENT = Decimal("0.01")
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


def sales_item(now, before, scale=Decimal("1")):
    """`before` holds the baseline lines (the 7 days before); `scale` turns their total into the average for a period of the length of this one."""
    a, b = sold_grams(now), sold_grams(before) * _d(scale)
    pct = change_pct(a, b)
    status = higher_is_better(pct, SALES_DROP)
    advice = {GREEN: "المبيعات ماشية كويس مقارنة بالفترة اللي قبلها.", YELLOW: "المبيعات أقل من الفترة اللي قبلها: راجع الأصناف والعروض.", RED: "المبيعات نزلت كتير: شوف إيه اللي اتغير (سعر، مخزون ناقص، موسم).",
              GRAY: "مفيش مبيعات في الفترة اللي قبلها للمقارنة."}[status]
    return {"key": "sales", "title": "مبيعات الذهب (جرام)", "status": status, "value": f"{a:,.3f} جرام", "compare": f"{b:,.3f} جرام" + (f" ({_pct_text(pct)})" if pct is not None else ""), "advice": advice}


def pieces(lines):
    """Owner 2026-11-11: crafted pieces, scrap sold by weight (F7) and bars / pounds carry very different making: each kind is averaged on its own and only the crafted pieces drive the verdict."""
    return [l for l in lines if not is_bullion(l) and l["line_type"] != "SELL_OLD_GOLD"]


def scrap_sales(lines):
    return [l for l in lines if l["line_type"] == "SELL_OLD_GOLD" and not is_bullion(l)]


def bullion(lines):
    return [l for l in lines if is_bullion(l)]


def making_info_item(key, title, now, before):
    """Average making per gram of one other kind of sale (scrap sold by weight, bars / pounds): information beside the crafted-pieces indicator, never a verdict (always gray)."""
    a, b = _making_per_gram(now), _making_per_gram(before)
    pct = change_pct(a, b)
    return {"key": key, "title": title, "status": GRAY, "value": f"{a:,.2f} ج" if a is not None else "—",
            "compare": (f"{b:,.2f} ج" + (f" ({_pct_text(pct)})" if pct is not None else "") if b is not None else "—"),
            "advice": "للعلم فقط: مصنعيته مختلفة عن المشغولات فمحسوب لوحده." if a is not None else "مفيش بيع من النوع ده في الفترة."}


def making_item(now, before, below_cost_lines, known_lines):
    now, before = pieces(now), pieces(before)
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
    return {"key": "making", "title": "متوسط مصنعية المشغولات للجرام (من جرام فأكثر)", "status": status, "value": f"{a:,.2f} ج" if a is not None else "—",
            "compare": (f"{b:,.2f} ج" + (f" ({_pct_text(pct)})" if pct is not None else "") if b is not None else "—") + extra, "advice": advice}


MIN_DISCOUNT_RED = Decimal("1.25")       # owner 2026-11-13: under 1.25% is red, 1.25 - 1.5% yellow, 1.5% and above green


def scrap_gross(lines):
    """Gross value of the ordinary scrap lines the discount percent is measured on (the same lines as discount_percent)."""
    return sum((_d(l["gross_total"]) for l in lines if l["line_type"] == BUY_LINE and not l.get("scrap_kind") and l.get("gross_total") is not None and l.get("discount_amount") is not None), Decimal("0"))


def scrap_item(now, default_percent=None):
    """Owner 2026-11-13: the old-gold discount actually given against a FIXED 1.5% (green from 1.5, yellow from 1.25, red below), with what the shortfall cost in pounds."""
    given = discount_percent(now)
    if given is None:
        status = GRAY
    elif given >= MIN_DISCOUNT_PCT:
        status = GREEN
    elif given >= MIN_DISCOUNT_RED:
        status = YELLOW
    else:
        status = RED
    lost = ((MIN_DISCOUNT_PCT - given) / HUNDRED * scrap_gross(now)).quantize(Q2, rounding=ROUND_HALF_UP) if given is not None and given < MIN_DISCOUNT_PCT else None
    advice = {GREEN: "خصم شراء الكسر في المعتاد.", YELLOW: "خصم الكسر الفعلي أقل شوية من 1.5%: بتدفع للعملاء أكتر من اللازم.",
              RED: "خصم الكسر الفعلي أقل بكتير من 1.5%: بتشتري الكسر بسعر أغلى من المفروض.", GRAY: "مفيش شراء كسر عادي في الفترة."}[status]
    compare = f"الحد {MIN_DISCOUNT_PCT}%" + (f" • الفرق بالجنيه: {lost:,.0f} ج" if lost is not None else "")
    return {"key": "scrap", "title": "خصم شراء الكسر الفعلي", "status": status, "value": f"{given}%" if given is not None else "—", "compare": compare, "advice": advice}


def profit_item(now, before):
    """Gold profit (21K-equivalent grams: gold net of suppliers + cash and bank converted to gold, owner 2026-11-12) of this period against the previous one: GRAY without two comparable
    snapshot pairs or a price."""
    if now is None:
        return {"key": "profit", "title": "ربح الذهب (مكافئ 21)", "status": GRAY, "value": "—", "compare": "—", "advice": "محتاج لقطات جرد كفاية وسعر بيع 21 مخزن: سجّل الرصيد الافتتاحي وافتح البرنامج كل يوم."}
    now = _d(now)
    if now < 0:
        status, advice = RED, "الذهب مع الفلوس محوّلة لذهب نقص في الفترة دي: راجع المصاريف والسحب والخسية."
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
    return "مرتب" in str(label or "") or is_invoice_difference(label)


def is_invoice_difference(label):
    """The treasury category of invoice differences («فرق فواتير» / «فرق فاتورة»): its money out is the shop's give-away on invoices, judged by the invoice-difference KPI."""
    text = str(label or "")
    return "فرق" in text and ("فواتير" in text or "فاتورة" in text or "فاتوره" in text)


def income_item(now, before):
    """The recorded income (the categorised «إيراد» entries of the treasury) against the previous period: higher is better, never red on its own (income is not a promise)."""
    if now is None:
        return {"key": "income", "title": "الإيرادات المسجلة", "status": GRAY, "value": "—", "compare": "—", "advice": "غير متاحة (صلاحية أو بيانات)."}
    pct = change_pct(now, before)
    status = higher_is_better(pct, (Decimal("20"), Decimal("1000")))
    advice = {GREEN: "الإيرادات المسجلة ماشية كويس مقارنة بالفترة اللي قبلها.", YELLOW: "الإيرادات المسجلة أقل من الفترة اللي قبلها.", RED: "الإيرادات المسجلة نزلت كتير.",
              GRAY: "مفيش إيرادات مسجلة في الفترة اللي قبلها للمقارنة (أو مفيش إيراد مسجل أصلًا: دي بنود «إيراد» اللي بتتسجل في حركة الخزنة)."}[status]
    return {"key": "income", "title": "الإيرادات المسجلة", "status": status, "value": f"{_d(now):,.2f} ج", "compare": (f"{_d(before):,.2f} ج" + (f" ({_pct_text(pct)})" if pct is not None else "")) if before is not None else "—", "advice": advice}


def below_minimum(percent, minimum):
    """The percent is judged as displayed (two decimals, half up): 1.995 shows 2.00 and is not low."""
    return _d(percent).quantize(CENT, rounding=ROUND_HALF_UP) < minimum


def is_crafted_sale(l):
    """Owner 2026-11-13 (after the production check): bars / pounds and scrap sold by weight earn about 1% by nature, so the fixed 2% rule judges CRAFTED pieces only (the 2026-11-08 decision)."""
    return l.get("line_type") in SELL_LINES and not is_bullion(l) and l.get("line_type") != "SELL_OLD_GOLD"


def making_flags(margin_pct, others_average_pct=None):
    """Why one sale line counts as a LOW profit (owner 2026-11-13: ONE fixed rule, profit under 2%). The profit PERCENT = (making - the letter's cost x grams) / the gold value of the line,
    so a heavy piece, a costly letter, a karat or a bar / pound are comparable. `others_average_pct` is kept for the callers' signature and no longer decides anything."""
    return ["BELOW_MIN_PROFIT"] if below_minimum(margin_pct, MIN_PROFIT_PCT) else []


def line_cost(l):
    if l.get("line_type") == "SELL_OLD_GOLD":
        return Decimal("0")                      # owner 2026-11-11: scrap sold by weight costs no making (as in the making-profit report)
    if l.get("real_cost") is not None:
        return l["real_cost"]                    # owner 2026-11-11: a piece bought from a supplier keeps its REAL purchase making; the letter's cost is for the rest
    return l.get("category_cost") if l.get("category_cost") is not None else l.get("piece_cost")       # the letter's CURRENT cost first (owner 2026-11-08)


def _group(l):
    return (int(l["karat_value"]), is_bullion(l), l.get("line_type") == "SELL_OLD_GOLD")      # scrap sales are compared only with other scrap sales


def _judgeable(l):
    return (l["line_type"] in SELL_LINES and l.get("making_total") is not None and _d(l["weight_grams"]) >= ONE_GRAM and line_cost(l) is not None
            and l.get("gross_total") is not None and _d(l["gross_total"]) > 0)


def margin_sales(now):
    """{(karat, bullion?): {employee: [profit, gold value, grams]}} over the judgeable sale lines (known making, known cost, a gold value, 1 g or more)."""
    sales = {}
    for l in now:
        if _judgeable(l):
            e = sales.setdefault(_group(l), {}).setdefault(str(l.get("employee_id") or 0), [Decimal("0"), Decimal("0"), Decimal("0")])
            e[0] += _d(l["making_total"]) - _d(line_cost(l)) * _d(l["weight_grams"])
            e[1] += _d(l["gross_total"])
            e[2] += _d(l["weight_grams"])
    return sales


def judge_sale(l, sales):
    """(profit percent, cost per gram, others' average profit percent, reasons) of one sale line, or None when it cannot be judged (unknown making / cost / gold value: never guessed)."""
    if not _judgeable(l):
        return None
    key = str(l.get("employee_id") or 0)
    group = sales.get(_group(l), {})
    others_profit = sum((v[0] for k, v in group.items() if k != key), Decimal("0"))
    others_value = sum((v[1] for k, v in group.items() if k != key), Decimal("0"))
    others_grams = sum((v[2] for k, v in group.items() if k != key), Decimal("0"))
    average = (others_profit / others_value * HUNDRED) if others_grams >= MIN_OTHERS_GRAMS and others_value > 0 else None
    cost = _d(line_cost(l))
    pct = (_d(l["making_total"]) - cost * _d(l["weight_grams"])) / _d(l["gross_total"]) * HUNDRED
    return pct, cost, average, making_flags(pct, average)


def employee_rows(now, default_percent):
    """One verdict per employee over the lines of THIS period. Counted lines: sale lines of 1 g or more with a known making, ordinary scrap lines, and the employee's invoices."""
    sales = margin_sales(now)
    people = {}
    for l in now:
        key = str(l.get("employee_id") or 0)
        p = people.setdefault(key, {"name": l.get("employee") or "غير محدد", "lines": 0, "low_making": 0, "low_discount": 0, "invoices": set(), "reasons": set()})
        p["invoices"].add(int(l["invoice_id"]))
        if l["line_type"] in SELL_LINES:
            if not is_crafted_sale(l):
                continue
            judged = judge_sale(l, sales)
            if judged is None:
                continue
            p["lines"] += 1
            if judged[3]:
                p["low_making"] += 1
                p["reasons"].update(judged[3])
        elif l["line_type"] == BUY_LINE and not l.get("scrap_kind") and l.get("gross_total") is not None and l.get("discount_amount") is not None and _d(l["gross_total"]) > 0:
            p["lines"] += 1
            percent = _d(l["discount_amount"]) / _d(l["gross_total"]) * HUNDRED
            if below_minimum(percent, MIN_DISCOUNT_PCT):
                p["low_discount"] += 1
                p["reasons"].add("LOW_DISCOUNT")
    rows = []
    for key, p in people.items():
        flagged = p["low_making"] + p["low_discount"]
        status, note = employee_status(p["lines"], flagged)
        rows.append({"employee": p["name"], "lines": p["lines"], "low_making": p["low_making"], "low_discount": p["low_discount"], "status": status, "status_text": STATUS_TEXT[status], "note": note})
    order = {RED: 0, YELLOW: 1, GREEN: 2, GRAY: 3}
    return sorted(rows, key=lambda r: (order[r["status"]], r["employee"]))


def flagged_lines(now, default_percent, limit=300):
    """The LINES behind every «انتبه» (owner 2026-11-13, fixed rules): each sale line whose profit is under 2% of its gold value and each scrap line whose discount is under 1.5%, newest first, with the invoice so the owner can open it. The same rules as employee_rows (one place decides what is flagged)."""
    sales = margin_sales(now)
    rows = []
    for l in now:
        key = str(l.get("employee_id") or 0)
        base = {"employee": l.get("employee") or "غير محدد", "invoice": l.get("invoice_number") or f"#{l['invoice_id']}", "date": l.get("invoice_date"), "karat": f"عيار {int(l['karat_value'])}",
                "weight": _d(l["weight_grams"]), "ref": {"type": "INVOICE", "id": str(l["invoice_id"])}}
        if l["line_type"] in SELL_LINES:
            judged = judge_sale(l, sales) if is_crafted_sale(l) else None
            if judged is not None and judged[3]:
                margin = judged[0]
                rows.append({**base, "kind": "ربح مصنعية قليل", "detail": f"نسبة الربح {margin:,.2f}% من قيمة الذهب (المصنعية − تكلفة الحرف): أقل من {MIN_PROFIT_PCT:.0f}%"})
        elif l["line_type"] == BUY_LINE and not l.get("scrap_kind") and l.get("gross_total") is not None and l.get("discount_amount") is not None and _d(l["gross_total"]) > 0:
            percent = _d(l["discount_amount"]) / _d(l["gross_total"]) * HUNDRED
            if below_minimum(percent, MIN_DISCOUNT_PCT):
                rows.append({**base, "kind": "خصم كسر قليل", "detail": f"الخصم {percent:.2f}% أقل من {MIN_DISCOUNT_PCT:.2f}%"})
    rows.sort(key=lambda r: (str(r["date"]), r["invoice"]), reverse=True)
    return rows[:limit]


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


# ---------------------------------------------------------------- owner 2026-11-13: the items to catch early (dead stock, invoice differences, an employee's making, market loss, sold-out fast sellers)
DEAD_SHARE_LIMITS = (Decimal("15"), Decimal("30"))     # % of the shop's gold: green below 15, yellow up to 30, red above
DIFF_SHARE_LIMITS = (Decimal("5"), Decimal("15"))      # % of the net making
MAKING21_RATIOS = (Decimal("0.90"), Decimal("0.80"))   # an employee's making per gram against the others': green from 90%, yellow from 80%, red below
MARKET_LOSS_LIMITS = (Decimal("0.5"), Decimal("1"))    # % of the gold sent to the market (last 90 days)
STOCKOUT_LIMITS = (0, 3)                               # sold-out fast sellers: none = green, 1 - 3 yellow, 4 or more red
MIN_KARAT_GRAMS = Decimal("20")                        # grams of the karat an employee must have sold before he is compared
MARKET_WINDOW_DAYS = 90


def _by_share(pct, limits):
    return GREEN if pct < limits[0] else YELLOW if pct <= limits[1] else RED


def _item(key, title, status, value, compare, advice):
    return {"key": key, "title": title, "status": status, "value": value, "compare": compare, "advice": advice}


def dead_stock_item(dead_eq21, shop_eq21, pieces):
    """Gold idle for the dead-stock days as a share of the shop's gold (the latest stocktake sheet's shop gold, 21K-equivalent)."""
    title = "ذهب راكد (قطع واقفة من غير بيع)"
    if dead_eq21 is None:
        return _item("dead_stock", title, GRAY, "—", "—", "غير متاح (صلاحية المخزون).")
    dead = _d(dead_eq21)
    if shop_eq21 is None or _d(shop_eq21) <= 0:
        return _item("dead_stock", title, GRAY, f"{dead:,.0f} جرام (مكافئ 21)", f"{pieces} قطعة", "محتاج لقطة جرد لحساب النسبة من ذهب المحل.")
    pct = (dead / _d(shop_eq21) * HUNDRED).quantize(Q1, rounding=ROUND_HALF_UP)
    status = _by_share(pct, DEAD_SHARE_LIMITS)
    advice = {GREEN: "الراكد قليل.", YELLOW: "الراكد بيكبر: ابدأ عروض على أقدم الأصناف وارجع جزء للمصانع.",
              RED: "جزء كبير من ذهب المحل واقف: اعمل عرض على الأقدم، رجّع للمصنع، أو حوّله لكسر وأعد تصنيعه. القطع بالباركود تحت."}[status]
    return _item("dead_stock", title, status, f"{pct}% من ذهب المحل", f"{dead:,.0f} جرام (مكافئ 21) • {pieces} قطعة من {_d(shop_eq21):,.0f} جرام", advice)


def invoice_diff_item(diff_now, diff_before, net_making_value):
    """The treasury category «فرق فواتير» as a share of the net making of the crafted pieces sold in the period."""
    title = "فروق الفواتير (من حركة الخزنة)"
    if diff_now is None:
        return _item("invoice_diff", title, GRAY, "—", "—", "غير متاحة (صلاحية الخزنة).")
    now = _d(diff_now)
    before = f"{_d(diff_before):,.2f} ج" if diff_before is not None else "—"
    if net_making_value is None or _d(net_making_value) <= 0:
        return _item("invoice_diff", title, GRAY, f"{now:,.2f} ج", before, "مفيش صافي مصنعية معروف في الفترة للمقارنة.")
    pct = (now / _d(net_making_value) * HUNDRED).quantize(Q1, rounding=ROUND_HALF_UP)
    status = _by_share(pct, DIFF_SHARE_LIMITS)
    advice = {GREEN: "فروق الفواتير صغيرة.", YELLOW: "فروق الفواتير بتاكل جزء من المصنعية: راجع بند «فرق فواتير» في حركة الخزنة.",
              RED: "فروق الفواتير كبيرة بالنسبة للمصنعية: لو هي خصم، حطه في الفاتورة من الأول عشان يتراقب."}[status]
    return _item("invoice_diff", title, status, f"{now:,.2f} ج ({pct}% من صافي المصنعية)", f"متوسط آخر 7 أيام {before}", advice)


def net_making(lines):
    """Making charged minus the piece's cost over the crafted sale lines with a known making and cost (bars, pounds and scrap sold by weight left out: their cost basis differs). None without any."""
    total, seen = Decimal("0"), False
    for l in pieces(lines):
        if l["line_type"] in SELL_LINES and l.get("making_total") is not None and line_cost(l) is not None:
            total += _d(l["making_total"]) - _d(line_cost(l)) * _d(l["weight_grams"])
            seen = True
    return total if seen else None


def making21_rows(lines, karat=21):
    """One row per employee who sold at least MIN_KARAT_GRAMS of crafted pieces of the karat: his making per gram, the other employees' weighted average and his share of it."""
    per = {}
    for l in pieces(lines):
        if l["line_type"] in SELL_LINES and int(l["karat_value"]) == karat and l.get("making_total") is not None and _d(l["weight_grams"]) >= ONE_GRAM:
            e = per.setdefault(str(l.get("employee_id") or 0), {"name": l.get("employee") or "غير محدد", "making": Decimal("0"), "grams": Decimal("0")})
            e["making"] += _d(l["making_total"])
            e["grams"] += _d(l["weight_grams"])
    all_making, all_grams = sum((e["making"] for e in per.values()), Decimal("0")), sum((e["grams"] for e in per.values()), Decimal("0"))
    rows = []
    for e in per.values():
        others_grams = all_grams - e["grams"]
        if e["grams"] < MIN_KARAT_GRAMS or others_grams < MIN_OTHERS_GRAMS:
            continue
        mine, others = e["making"] / e["grams"], (all_making - e["making"]) / others_grams
        ratio = mine / others if others > 0 else None
        status = GRAY if ratio is None else GREEN if ratio >= MAKING21_RATIOS[0] else YELLOW if ratio >= MAKING21_RATIOS[1] else RED
        rows.append({"employee": e["name"], "grams": e["grams"], "per_gram": mine.quantize(Q2, rounding=ROUND_HALF_UP), "others": others.quantize(Q2, rounding=ROUND_HALF_UP),
                     "share": (ratio * HUNDRED).quantize(Q1, rounding=ROUND_HALF_UP) if ratio is not None else None, "status": status, "status_text": STATUS_TEXT[status]})
    order = {RED: 0, YELLOW: 1, GREEN: 2, GRAY: 3}
    return sorted(rows, key=lambda r: (order[r["status"]], r["share"] if r["share"] is not None else Decimal("999")))


def making21_item(rows, karat=21):
    title = f"مصنعية الموظف عيار {karat} (مقابل باقي الموظفين)"
    if not rows:
        return _item("making21", title, GRAY, "—", "—", f"مفيش موظف باع {MIN_KARAT_GRAMS} جرام فأكثر من عيار {karat} للمقارنة.")
    worst = rows[0]
    advice = {GREEN: "مصنعية الموظفين متقاربة.", YELLOW: "في موظف مصنعيته أقل من باقي الموظفين: راجع فواتيره.", RED: "في موظف بيبيع بمصنعية أقل بكتير من باقي الموظفين: كلمه وراجع فواتيره.",
              GRAY: "مفيش مقارنة ممكنة."}[worst["status"]]
    return _item("making21", title, worst["status"], f"{worst['employee']}: {worst['per_gram']:,.0f} ج للجرام" + (f" ({worst['share']}% من الباقي)" if worst["share"] is not None else ""),
                 f"متوسط باقي الموظفين {worst['others']:,.0f} ج للجرام", advice)


def market_loss_item(loss_period, loss_window, issued_window):
    """Melt loss + crucible + market loss (21K-equivalent grams): the period's figure beside the share of the gold sent to the market over the last MARKET_WINDOW_DAYS days (trips are rare, a week is too short)."""
    title = "خسية السوق وفاقد السبك"
    if loss_period is None or loss_window is None:
        return _item("market_loss", title, GRAY, "—", "—", "غير متاحة (صلاحية العهدة).")
    value = f"{_d(loss_period):,.3f} جرام (مكافئ 21)"
    if not issued_window or _d(issued_window) <= 0:
        return _item("market_loss", title, GRAY, value, f"مفيش ذهب خرج للسوق في آخر {MARKET_WINDOW_DAYS} يوم", "مفيش خروج للسوق للمقارنة.")
    pct = (_d(loss_window) / _d(issued_window) * HUNDRED).quantize(Q2, rounding=ROUND_HALF_UP)
    status = _by_share(pct, MARKET_LOSS_LIMITS)
    advice = {GREEN: "الفقد في السوق في المعتاد.", YELLOW: "الفقد في السوق زاد: راجع الخرجات الأخيرة.", RED: "الفقد في السوق كبير: راجع كل خرجة وسبب الخسية."}[status]
    return _item("market_loss", title, status, value, f"{pct}% من الذهب اللي خرج للسوق في آخر {MARKET_WINDOW_DAYS} يوم", advice)


def stockout_item(count):
    title = "أصناف بتتباع وخلصت"
    if count is None:
        return _item("stockout", title, GRAY, "—", "—", "غير متاحة (صلاحية المخزون أو الفواتير).")
    status = GREEN if count <= STOCKOUT_LIMITS[0] else YELLOW if count <= STOCKOUT_LIMITS[1] else RED
    advice = {GREEN: "مفيش صنف سريع البيع خلص.", YELLOW: "في أصناف بتتباع خلصت: فكّر تعوضها.", RED: "أصناف كتير سريعة البيع خلصت: كل بيعة ضايعة خسارة، عوّضها."}[status]
    return _item("stockout", title, status, f"{count} صنف", "من أصناف بيعت 3 مرات فأكثر في آخر 90 يوم", advice)
