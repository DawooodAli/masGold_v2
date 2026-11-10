"""الرئيسية: today's activity, formatted in Python (QML only binds strings and lists)."""
from datetime import datetime
from decimal import Decimal

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from services.price_alert import PriceAlertSettings, evaluate
from app.formatting import grams, ltr, money, number, rate
from app.workers.invoice_worker import InvoiceWorker

MONTHS = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")
WEEKDAYS = ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")


class DashboardViewModel(QObject):
    changed = Signal()

    def __init__(self, session, service=None, parent=None, refresh_ms=120000, clock=datetime.now):
        super().__init__(parent)
        self._session, self._service, self._clock = session, service, clock
        self._workers, self._request, self._shutdown = set(), 0, False
        self._data, self._error, self._busy = {}, "", False
        self._alerts = None
        # A gentle safety refresh; the real triggers are opening the page and a finalized invoice.
        self._timer = QTimer(self)
        self._timer.setInterval(refresh_ms)
        self._timer.timeout.connect(self.refresh)

    def _service_instance(self):
        if self._service is None:
            from services.dashboard_service import DashboardService
            from services import currency_rates, gold_news
            self._service = DashboardService(self._session, currency_rates=lambda: currency_rates.shared().snapshot(),
                                        gold_news=lambda: gold_news.shared().snapshot())
        return self._service

    @Slot()
    def activate(self):
        self.refresh()
        self._timer.start()

    @Slot()
    @Slot(str)
    def refresh(self, _reason=""):
        actor = self._session.current_actor()
        if self._shutdown or actor is None:
            return
        service = self._service_instance()
        self._request += 1
        self._busy = True
        worker = InvoiceWorker(lambda _expected_actor: service.today(_expected_actor=_expected_actor),
                               self._session, actor, self._request, "dashboard", self)
        worker.completed.connect(self._completed)
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        self.changed.emit()
        worker.start()

    @Slot(int, str, str, object, str, str, str)
    def _completed(self, request, generation, operation, result, error, code, kind):
        actor = self._session.current_actor()
        if self._shutdown or actor is None or str(actor.generation) != generation or request != self._request:
            return
        self._busy = False
        if error:
            self._error = "تعذر تحميل ملخص اليوم. سيعاد المحاولة تلقائياً."
        else:
            self._data, self._error = result, ""
        self.changed.emit()

    @Slot()
    def shutdown(self):
        self._shutdown = True
        self._timer.stop()
        for worker in list(self._workers):
            worker.wait(5000)

    # ------------------------------------------------------------ state
    @Property(bool, notify=changed)
    def ready(self):
        return bool(self._data)

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property(str, notify=changed)
    def dateText(self):
        now = self._clock()
        return f"{WEEKDAYS[now.weekday()]} {ltr(f'{now:%d-%m-%Y}')}"

    @Property(bool, notify=changed)
    def canSeeSales(self):
        return bool(self._data.get("can_see_sales"))

    # ------------------------------------------------------------ today (billing permission)
    @Property(int, notify=changed)
    def invoiceCount(self):
        return int(self._data.get("invoice_count", 0))

    @Property(str, notify=changed)
    def invoiceBreakdown(self):
        if not self.invoiceCount:
            return "لا توجد فواتير اليوم بعد"
        return f"بيع {self._data.get('sale_invoice_count', 0)} • شراء كسر {self._data.get('buy_invoice_count', 0)}"

    @Property(str, notify=changed)
    def sellTotalText(self):
        return money(self._data.get("sell_total", "0"))

    @Property(str, notify=changed)
    def buyTotalText(self):
        return money(self._data.get("buy_total", "0"))

    @Property(int, notify=changed)
    def unidentifiedCount(self):
        return int(self._data.get("unidentified_lines", 0))

    @Property("QVariantList", notify=changed)
    def soldByKarat(self):
        return [{"karat": str(r["karat"]), "grams": grams(r["grams"]),
                 "pieces": f"{r['pieces']} قطعة",
                 "fromOldGold": (f"منها من الكسر {grams(r['from_old_gold'])}"
                                 if Decimal(r["from_old_gold"]) else "")}
                for r in self._data.get("sold", [])]

    @Property("QVariantList", notify=changed)
    def topTypes(self):
        """The best-selling item types of the month (one row per type)."""
        return [{"type": r["type"], "pieces": f"{r['pieces']} قطعة",
                 "grams": grams(r["grams"]), "value": money(r["value"])}
                for r in self._data.get("top_types", [])]

    @Property("QVariantList", notify=changed)
    def salesDays(self):
        """The sale value of the last days as bar rows: label (day/month), money text and the share of the largest day."""
        rows = self._data.get("sales_days") or []
        top = max((Decimal(r["total"]) for r in rows), default=Decimal("0"))
        return [{"label": r["day"][8:10] + "/" + r["day"][5:7], "text": money(r["total"]) if Decimal(r["total"]) else "—",
                 "ratio": float(Decimal(r["total"]) / top) if top else 0.0} for r in rows]

    @Property("QVariantMap", notify=changed)
    def priceChart(self):
        """The stored daily 21K shop BUY / SELL prices of the last days, as plain floats for drawing only (the figures shown as text come formatted)."""
        series = self._data.get("price_series") or []
        if len(series) < 2:
            return {"available": False, "buy": [], "sell": [], "ma": [], "low": 0.0, "high": 0.0, "first": "", "last": "", "lowText": "", "highText": ""}
        buys, sells = [float(r["buy"]) for r in series], [float(r["sell"]) for r in series]
        averages = [float(r["ma"]) if r.get("ma") is not None else None for r in series]      # None = not enough stored days yet (drawn as a gap)
        low, high = min(buys + sells + [a for a in averages if a is not None]), max(buys + sells + [a for a in averages if a is not None])
        return {"available": True, "buy": buys, "sell": sells, "ma": averages, "low": low, "high": high, "first": series[0]["day"], "last": series[-1]["day"],
                "lowText": rate(str(low)), "highText": rate(str(high))}

    @Property("QVariantMap", notify=changed)
    def priceHint(self):
        """The 21K price indicator card: the 30-day average of the shop BUY price, the current price against it and the plain hint."""
        hint = self._data.get("price_hint") or {}
        average, versus = hint.get("average30"), hint.get("vs_average30_percent")
        action = hint.get("hint_text") or "بيانات غير كافية"
        if average is None:
            return {"available": bool(hint.get("available")), "average": "—",
                    "versus": "بيانات غير كافية (تحتاج 10 أيام أسعار على الأقل)",
                    "action": action, "tone": hint.get("hint_tone") or "muted"}
        value = Decimal(versus)
        return {"available": True, "average": rate(average),
                "versus": f"سعر الشراء الآن {'أعلى' if value > 0 else 'أقل' if value < 0 else 'مساوي'} منه بنسبة {abs(value):.2f}%",
                "action": action, "tone": hint.get("hint_tone") or "muted"}

    # ------------------------------------------------------------ overdue debts (customers permission)
    @Property("QVariantMap", notify=changed)
    def overdueDebts(self):
        """Customers' open owed items at least N days old (N shown in the title text): the total, the number of customers and the oldest rows."""
        data = self._data.get("overdue") or {}
        return {"days": int(data.get("days", 0)), "total": money(data.get("total", "0")), "count": int(data.get("customers", 0)),
                "rows": [{"name": r["customer"] + (f"  ({r['code']})" if r["code"] else ""), "amount": money(r["amount"]), "age": f"منذ {r['age']} يوم"}
                         for r in data.get("rows", [])]}

    # ------------------------------------------------------------ today's scrap price (billing permission)
    @Property("QVariantList", notify=changed)
    def scrapToday(self):
        """Per karat: today's scrap bought, the average price per gram paid and the latest stored SHOP_BUY; difference > 0 = paid more than today's buying price (amber), <= 0 = green."""
        rows = []
        for r in self._data.get("scrap_today", []):
            diff = Decimal(r["diff"]) if r["diff"] is not None else None
            rows.append({"karat": str(r["karat"]), "grams": grams(r["grams"]), "average": rate(r["average"]),
                         "price": rate(r["price"]) if r["price"] is not None else "السعر غير متاح",
                         "diff": ("" if diff is None else f"{'+' if diff > 0 else ''}{rate(diff)}"), "over": bool(diff is not None and diff > 0),
                         "discount": "" if r.get("discount") is None else f"نسبة الخصم {Decimal(r['discount']):.2f}%"})
        return rows

    @Property("QVariantList", notify=changed)
    def makingAverages(self):
        """Today's average making per gram: pieces, bullion and coins, scrap sales (label + text; «—» without a sale, «غير معلوم» when the making is unknown)."""
        data = self._data.get("making_averages") or {}
        rows = []
        for key, label in (("PIECES", "متوسط مصنعية المشغولات"), ("BULLION", "متوسط مصنعية السبائك والجنيهات"), ("SCRAP", "متوسط مصنعية بيع الكسر")):
            item = data.get(key) or {}
            text = "—" if not item.get("grams") or not Decimal(item["grams"]) else "غير معلوم" if item.get("unknown") else rate(item["average"]) + " / جرام"
            rows.append({"key": key, "label": label, "text": text})
        return rows

    # ------------------------------------------------------------ currency rates (GoldTimo /rates; every logged-in user, public market prices)
    @Property("QVariantMap", notify=changed)
    def currencyRates(self):
        data = self._data.get("currency_rates") or {}
        return {"offered": "currency_rates" in self._data and bool(data.get("enabled", False)), "available": bool(data.get("available")),
                "rows": [{"code": r["code"], "name": r["name"], "price": rate(r["price"])} for r in data.get("rows", [])],
                "updated": data.get("updated", ""), "error": data.get("error", "")}

    # ------------------------------------------------------------ quick insights (owner 2026-11-10): week comparison, scrap vs sales, 52-week range
    @Property("QVariantMap", notify=changed)
    def insights(self):
        week, rng = self._data.get("week_compare"), self._data.get("year_range")
        change = week.get("change") if week else None
        return {"hasWeek": bool(week), "weekToday": money(week["today"]) if week else "", "weekBefore": money(week["before"]) if week else "",
                "weekChange": "" if change is None else ("▲ " if Decimal(change) > 0 else "▼ " if Decimal(change) < 0 else "") + f"{abs(Decimal(change))}%",
                "weekUp": change is not None and Decimal(change) > 0,
                "scrap": [{"karat": r["karat"], "bought": grams(r["bought"]), "sold": grams(r["sold"])} for r in self._data.get("scrap_vs_sold", [])],
                "hasRange": bool(rng), "rangeLow": rate(rng["low"]) if rng else "", "rangeHigh": rate(rng["high"]) if rng else "", "rangeNow": rate(rng["now"]) if rng else "",
                "rangePosition": rng["position"] if rng else 0, "rangeDays": rng["days"] if rng else 0}

    # ------------------------------------------------------------ price alert (a look of this computer)
    def _alert_settings(self):
        if self._alerts is None:
            self._alerts = PriceAlertSettings()
        return self._alerts

    @Property("QVariantMap", notify=changed)
    def priceAlert(self):
        settings = self._alert_settings()
        low, high = settings.buy_below(), settings.sell_above()
        latest = self._data.get("latest_price") or {}
        lines = evaluate(low, high, latest.get("buy"), latest.get("sell"))
        return {"buyBelow": "" if low is None else str(low), "sellAbove": "" if high is None else str(high), "lines": lines, "triggered": bool(lines)}

    @Slot(str, str, result=str)
    def savePriceAlert(self, buy_below, sell_above):
        try:
            self._alert_settings().save(buy_below, sell_above)
        except ValueError as exc:
            return str(exc)
        self.changed.emit()
        return ""

    # ------------------------------------------------------------ a reminder text for an overdue customer (copied to the clipboard, never sent)
    @Slot(int, result=str)
    def copyReminder(self, index):
        rows = (self._data.get("overdue") or {}).get("rows", [])
        if index < 0 or index >= len(rows):
            return ""
        row = rows[index]
        text = (f"السلام عليكم أستاذ/ة {row['customer']}،\nنذكّر حضرتك بوجود مستحقات مفتوحة على حسابك لدى مجوهرات ماس بمبلغ {money(row['amount'])}"
                f" (منذ {row['age']} يوم).\nبرجاء التكرم بالتسوية في أقرب وقت. شكراً لتعاملكم.")
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(text)
        return text

    @Property("QVariantMap", notify=changed)
    def goldNews(self):
        data = self._data.get("gold_news") or {}
        return {"available": bool(data.get("available")), "items": list(data.get("items", [])), "updated": data.get("updated", ""), "error": data.get("error", "")}

    # ------------------------------------------------------------ dead stock (inventory permission)
    @Property("QVariantMap", notify=changed)
    def deadStock(self):
        data = self._data.get("dead_stock") or {}
        return {"days": int(data.get("days", 0)),
                "rows": [{"karat": str(r["karat"]), "pieces": f"{r['pieces']} قطعة", "grams": grams(r["grams"]), "oldest": f"أقدمها {r['oldest']} يوم"}
                         for r in data.get("rows", [])]}

    # ------------------------------------------------------------ customer balances (customers permission)
    @Property(bool, notify=changed)
    def canSeeBalances(self):
        """True when the CURRENT database grants the user the customers permission (checked in the read transaction)."""
        return bool(self._data.get("can_see_balances"))

    @Property("QVariantList", notify=changed)
    def customerBalances(self):
        """The customers who had account activity from the first day of the current month and are NOT settled: the true ledger-derived balance, shown by colour only —
        owes = RED («على العميل»), credit = BLUE («للعميل»); a settled account disappears on its own."""
        return [{"name": r["customer"], "code": r["code"], "balance": money(r["balance"]),
                 "owes": r["direction"] == "OWED"}
                for r in self._data.get("customer_balances", [])]

    # ------------------------------------------------------------ the month so far (billing permission)
    @Property(str, notify=changed)
    def monthTitle(self):
        day = int(self._data.get("month_day", 0))
        now = self._clock()
        return f"مبيعات شهر {MONTHS[now.month - 1]} (حتى يوم {day})" if day else "مبيعات الشهر"

    @Property("QVariantList", notify=changed)
    def monthByKarat(self):
        rows = []
        for r in self._data.get("month", []):
            now, before = Decimal(r["grams"]), Decimal(r["previous"])
            if before:
                change = (now - before) / before * 100
                versus = f"{'▲' if change > 0 else '▼' if change < 0 else '●'} {abs(change):.0f}% عن نفس الفترة من السنة الماضية ({grams(before)})"
                direction = "up" if change > 0 else "down" if change < 0 else "flat"
            else:
                versus, direction = ("لا مبيعات بنفس الفترة من السنة الماضية" if not now else "لا مقارنة: السنة الماضية بلا مبيعات في نفس الفترة"), "flat"
            rows.append({"karat": str(r["karat"]), "grams": grams(now), "pieces": f"{r['pieces']} قطعة", "versus": versus, "direction": direction})
        return rows

    # ------------------------------------------------------------ minimum stock (inventory permission)
    @Property(bool, notify=changed)
    def canSeeStock(self):
        return bool(self._data.get("can_see_stock"))

    @Property("QVariantList", notify=changed)
    def belowMin(self):
        out = []
        for r in self._data.get("below_min", []):
            parts = [f"{r['short_pieces']} قطعة"] if r["short_pieces"] else []
            if Decimal(r["short_grams"]):
                parts.append(grams(r["short_grams"]))
            out.append({"title": f"{r['type']} عيار {r['karat']}", "short": "ناقص " + " و ".join(parts)})
        return out

    # ------------------------------------------------------------ old gold (every logged-in user)
    @Property("QVariantList", notify=changed)
    def oldGold(self):
        rows = []
        for r in self._data.get("old_gold", []):
            grams_in, grams_out = Decimal(r["in"]), Decimal(r["out"])
            moved = grams_in or grams_out
            rows.append({"karat": str(r["karat"]), "balance": grams(r["balance"]),
                         "zero": Decimal(r["balance"]) == 0,
                         "inText": number(grams_in, 2, signed=True) if grams_in else "",
                         "outText": number(-grams_out, 2) if grams_out else "",
                         "todayText": "" if moved else "لا حركة اليوم"})
        return rows
