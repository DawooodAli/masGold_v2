"""Plain-language readings of the market charts (display only: nothing is stored in the database, nothing is posted, nothing is ordered).

Every sentence is made from numbers the shop itself holds - its own stored daily 21K prices, the daily dollar / euro rates it recorded since it started
(`services/fx_history.py`) and its own scrap balance - with fixed, explainable rules: the position of today's price inside the last 90 days (the lower
third = relatively cheap, the upper third = relatively expensive), the 30-day average and the last week's move. A series with fewer than MIN_DAYS stored days
gives «بيانات غير كافية», never a guess. It is guidance, not a recommendation."""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

WINDOW_DAYS = 90
MIN_DAYS = 10
LOW_THIRD, HIGH_THIRD = 30, 70
DISCLAIMER = "إرشاد مبني على الأسعار المخزنة عندك وليس توصية مالية."
CENT = Decimal("0.01")


def _pct(value):
    return Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def stats(points, today=None):
    """points = [(date, Decimal)] oldest first. {now, low, high, position, avg30, vs_avg, week, days} or None below MIN_DAYS stored days inside the window."""
    rows = sorted((d, Decimal(str(v))) for d, v in points or [])
    if not rows:
        return None
    end = today or rows[-1][0]
    rows = [r for r in rows if end - timedelta(days=WINDOW_DAYS) < r[0] <= end]
    if len(rows) < MIN_DAYS:
        return None
    values = [v for _, v in rows]
    now, low, high = values[-1], min(values), max(values)
    position = int(((now - low) * 100 / (high - low)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) if high > low else 50
    last30 = [v for d, v in rows if d > end - timedelta(days=30)]
    avg30 = sum(last30, Decimal(0)) / len(last30)
    week_rows = [v for d, v in rows if d <= end - timedelta(days=7)]
    week = ((now - week_rows[-1]) * 100 / week_rows[-1]) if week_rows and week_rows[-1] else None
    return {"now": now, "low": low, "high": high, "position": position, "avg30": avg30, "vs_avg": (now - avg30) * 100 / avg30 if avg30 else Decimal(0),
            "week": week, "days": len(rows)}


def _lines(name, s):
    out = [f"{name} {s['now'].quantize(CENT):,}: {'أعلى' if s['vs_avg'] > 0 else 'أقل' if s['vs_avg'] < 0 else 'مساوٍ'} من متوسط 30 يوم بنسبة {abs(_pct(s['vs_avg']))}%"
           f" ومكانه {s['position']}% من مدى {s['days']} يوم (أقل {s['low'].quantize(CENT):,} / أعلى {s['high'].quantize(CENT):,})."]
    if s["week"] is not None:
        out.append(f"آخر أسبوع: {'ارتفع' if s['week'] > 0 else 'نزل' if s['week'] < 0 else 'ثابت'}" + (f" {abs(_pct(s['week']))}%." if s["week"] else "."))
    return out


def fx_reading(name, points, today=None):
    """{available, text} for the dollar / euro against the pound."""
    s = stats(points, today)
    if s is None:
        return {"available": False, "text": f"{name}: بيانات غير كافية (تحتاج {MIN_DAYS} أيام محفوظة على الأقل؛ التسجيل يومي من تشغيل البرنامج)."}
    lines = _lines(name, s)
    if s["position"] >= HIGH_THIRD:
        lines.append(f"{name} قريب من أعلى مدى: ضغط لرفع سعر الذهب محليًا.")
    elif s["position"] <= LOW_THIRD:
        lines.append(f"{name} قريب من أدنى مدى: ضغط لخفض سعر الذهب محليًا.")
    else:
        lines.append("ضمن النطاق المعتاد، بلا ضغط واضح على سعر الذهب.")
    return {"available": True, "text": " ".join(lines), "position": s["position"]}


def gold_reading(points, scrap21_grams=None, today=None):
    """{available, tone, text} for the shop's 21K SHOP_BUY price, tied to the 21K scrap the shop holds. tone: sell / buy / muted (the colours of the price semantics)."""
    s = stats(points, today)
    if s is None:
        return {"available": False, "tone": "muted", "text": f"عيار 21: بيانات غير كافية (تحتاج {MIN_DAYS} أيام أسعار مخزنة على الأقل)."}
    lines = _lines("شراء المحل عيار 21", s)
    scrap = Decimal(str(scrap21_grams)) if scrap21_grams is not None else None
    held = f" وعندك {scrap.quantize(Decimal('0.001')):,} جرام كسر عيار 21" if scrap is not None and scrap > 0 else ""
    if s["position"] >= HIGH_THIRD:
        tone = "sell"
        lines.append(f"السعر في الثلث الأعلى من المدى{held}: وقت مناسب للبيع والتسعير بحذر، وفكّر في تسييل جزء من الكسر بدل الاحتفاظ به، وتجنّب الشراء من السوق بسعر عالٍ.")
    elif s["position"] <= LOW_THIRD:
        tone = "buy"
        lines.append(f"السعر في الثلث الأدنى من المدى{held}: فرصة نسبية لشراء الكسر وتعبئة المخزون، وراجع الحد الأدنى للمخزون قبل الشراء.")
    else:
        tone = "muted"
        lines.append(f"السعر في منتصف المدى{held}: لا إشارة قوية، كمّل بالمعتاد.")
    lines.append(DISCLAIMER)
    return {"available": True, "tone": tone, "text": " ".join(lines), "position": s["position"]}
