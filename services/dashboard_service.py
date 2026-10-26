"""الرئيسية: one read-only snapshot of today's shop activity.

Every figure has one meaning (no mixing of commercial value, real money and customer ledger):
- invoice_count / sale & buy invoice counts: today's FINALIZED invoices;
- sell_total: the commercial value of today's sales (invoices.sell_total);
- buy_total: the commercial value of today's old-gold purchases (invoices.buy_total);
- sold grams per karat: today's sale lines (incl. sales from old-gold stock, shown separately);
- old gold: COUNTED balance per karat (same ledger as the MainWindow strip) + today's IN / OUT;
- unidentified_lines: today's manual (F5) sale lines awaiting reconciliation;
- top types: the best-selling item types of the month (SELL_INVENTORY lines only);
- price hint: the 21K shop BUY price against its 30-day average and the 90-day hint, from the
  stored daily prices (the same formulas and source as the price-indicators report);
- overdue debts: the customers' OPEN owed items (the oldest-first reading of the customer ledger, as in the customers report) older than OVERDUE_DAYS;
- today's scrap price: the average price per gram paid for today's scrap purchases against the latest stored SHOP_BUY of that karat;
- customer balances: the TRUE ledger-derived balance of customers active from the first day of
  the current month (customers permission), settled accounts excluded.
Money received / paid and bank / wallet balances are NOT on this page.

Sales figures need a billing permission; old-gold weights are the same
operational information the MainWindow strip already shows every logged-in user.
"""
from decimal import ROUND_HALF_UP, Decimal

from repositories.dashboard_repository import DashboardRepository
from repositories.financial_balance_repository import FinancialBalanceRepository
from repositories.report_repository import ReportRepository
from services import price_indicators as indicators_math, setting_rules
from services.invoice_service import invoice_transaction
from services.report_service import DEAD_STOCK_DAYS, ReportService

DASHBOARD_KARATS = (21, 18, 22, 24)
SALES_PERMISSIONS = frozenset({"INVOICE_CREATE", "INVOICE_FINALIZE"})
STOCK_PERMISSIONS = frozenset({"INVENTORY_VIEW", "INVENTORY_MANAGE"})
OVERDUE_DAYS = 30
GRAMS, MONEY = Decimal("0.001"), Decimal("0.01")
HINT_TEXT = {indicators_math.BUY_FAVOURABLE: "الشراء مناسب نسبيًا", indicators_math.SELL_FAVOURABLE: "البيع مناسب نسبيًا", indicators_math.NEUTRAL: "محايد",
             indicators_math.UNKNOWN: "بيانات غير كافية"}
HINT_TONE = {indicators_math.BUY_FAVOURABLE: "buy", indicators_math.SELL_FAVOURABLE: "sell", indicators_math.NEUTRAL: "muted",
             indicators_math.UNKNOWN: "muted"}


class _Discard(Exception):
    pass


class DashboardService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=DashboardRepository,
                 balance_repository=FinancialBalanceRepository, report_repository=ReportRepository, authorization=None):
        from services.authorization_service import AuthorizationService
        self._authorization = authorization or AuthorizationService(session)
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        self._balances = balance_repository
        self._reports = report_repository

    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        return actor

    @staticmethod
    def can_see_sales(actor):
        return bool(SALES_PERMISSIONS & set(actor.permissions))

    @staticmethod
    def month_summary(current, previous):
        """Per karat (never summed across karats): the grams and pieces sold so far this month and the grams over the same span of the same month LAST YEAR."""
        now = {int(r["karat_value"]): r for r in current}
        before = {int(r["karat_value"]): Decimal(str(r["grams"])) for r in previous}
        out = []
        for karat in sorted(set(now) | set(before), key=lambda k: (k not in DASHBOARD_KARATS, DASHBOARD_KARATS.index(k) if k in DASHBOARD_KARATS else k)):
            grams = Decimal(str(now[karat]["grams"])) if karat in now else Decimal(0)
            out.append({"karat": karat, "grams": str(grams.quantize(GRAMS)), "pieces": int(now[karat]["pieces"]) if karat in now else 0,
                        "previous": str(before.get(karat, Decimal(0)).quantize(GRAMS))})
        return out

    @staticmethod
    def shortages(rows):
        """The minimum-stock rules the stock is below: [{type, karat, short}] with what is missing in pieces and / or grams (None below migration 072 -> [])."""
        out = []
        for r in rows or []:
            short_p = max(0, int(r["min_pieces"]) - int(r["pieces"])) if r["min_pieces"] else 0
            short_g = max(Decimal(0), Decimal(str(r["min_grams"])) - Decimal(str(r["grams"]))) if r["min_grams"] else Decimal(0)
            if short_p or short_g:
                out.append({"type": r["item_type"], "karat": int(r["karat_value"]), "short_pieces": short_p, "short_grams": str(short_g.quantize(GRAMS))})
        return out

    @staticmethod
    def top_types(rows):
        """The best-selling item types of the month: [{type, pieces, grams, value}], one row per type (never summed across types)."""
        return [{"type": r["item_type"] or "بدون صنف", "pieces": int(r["pieces"]),
                 "grams": str(Decimal(str(r["grams"])).quantize(GRAMS)),
                 "value": str(Decimal(str(r["value"])).quantize(MONEY))} for r in rows or []]

    @staticmethod
    def balances(rows):
        """The customer balances shown on the dashboard: [{customer, code, balance, direction}] with the TRUE ledger-derived balance of vw_customer_balances (never a period
        balance); balance > 0 = OWED (red, «على العميل»), balance < 0 = CREDIT (blue, «للعميل») — the card distinguishes them by COLOUR only, no wording.
        A settled customer never reaches the card (the query only returns balance <> 0)."""
        out = []
        for r in rows or []:
            balance = Decimal(str(r["balance"]))
            if not balance:                                                       # a settled account never reaches the card (the query already refuses it; kept here as defence)
                continue
            out.append({"customer": r["name"], "code": r.get("customer_code") or "",
                        "balance": str(abs(balance).quantize(MONEY)),
                        "direction": "OWED" if balance > 0 else "CREDIT"})
        return out

    @staticmethod
    def overdue(ledger_rows, today, days=OVERDUE_DAYS, limit=3):
        """The customers' OVERDUE debts: the OPEN owed items (oldest-first reading of each ACTIVE customer's ledger, the same as the customers report) at least `days` old.
        {days, total, customers, rows: [{customer, code, amount, age}]} oldest first; age = days since the oldest overdue item."""
        by_customer = {}
        for r in ledger_rows or []:
            by_customer.setdefault(int(r["customer_id"]), []).append(r)
        day_of = lambda value: value.date() if hasattr(value, "date") else value
        found, total = [], Decimal(0)
        for ledger in by_customer.values():
            head = ledger[0]
            if not head["is_active"]:
                continue
            owed = [(i["amount"], (today - day_of(i["row"]["occurred_at"])).days) for i in ReportService.open_items(ledger) if i["amount"] > 0]
            amounts = [a for a, age in owed if age >= days]
            if not amounts:
                continue
            amount = sum(amounts, Decimal(0))
            total += amount
            found.append({"customer": head["name"], "code": head.get("customer_code") or "", "amount": amount, "age": max(age for _a, age in owed)})
        found.sort(key=lambda r: (-r["age"], -r["amount"]))
        return {"days": days, "total": str(total.quantize(MONEY)), "customers": len(found),
                "rows": [{"customer": r["customer"], "code": r["code"], "amount": str(r["amount"].quantize(MONEY)), "age": r["age"]} for r in found[:limit]]}

    @staticmethod
    def dead_stock(rows, days):
        """Dead stock per KARAT (never summed across karats): pieces IN_STOCK for `days` or more that were never on an invoice: {days, rows: [{karat, pieces, grams, oldest}]}."""
        out = [{"karat": int(r["karat_value"]), "pieces": int(r["pieces"]), "grams": str(Decimal(str(r["grams"])).quantize(GRAMS)), "oldest": int(r["oldest_days"] or 0)}
               for r in rows or [] if int(r["pieces"])]
        out.sort(key=lambda r: (r["karat"] not in DASHBOARD_KARATS, DASHBOARD_KARATS.index(r["karat"]) if r["karat"] in DASHBOARD_KARATS else r["karat"]))
        return {"days": days, "rows": out}

    @staticmethod
    def scrap_today(purchases, prices):
        """Per karat (never summed across karats): today's scrap bought, the weighted average price per gram paid and the latest stored SHOP_BUY of that karat;
        diff = average - price (positive = paid more than today's buying price). A karat without a stored price has price / diff None."""
        price = {int(r["karat_value"]): Decimal(str(r["buy"])) for r in prices or []}
        out = []
        for r in sorted(purchases or [], key=lambda r: (int(r["karat_value"]) not in DASHBOARD_KARATS,
                                                        DASHBOARD_KARATS.index(int(r["karat_value"])) if int(r["karat_value"]) in DASHBOARD_KARATS else int(r["karat_value"]))):
            weight, paid = Decimal(str(r["grams"])), Decimal(str(r["paid"]))
            if not weight:
                continue
            average = (paid / weight).quantize(MONEY, rounding=ROUND_HALF_UP)
            now = price.get(int(r["karat_value"]))
            out.append({"karat": int(r["karat_value"]), "grams": str(weight.quantize(GRAMS)), "average": str(average),
                        "price": str(now.quantize(MONEY, rounding=ROUND_HALF_UP)) if now is not None else None,
                        "diff": str((average - now).quantize(MONEY, rounding=ROUND_HALF_UP)) if now is not None else None})
        return out

    @staticmethod
    def price_hint(series):
        """The dashboard's plain 21K price indicator (the same formulas and definitions as the price-indicators report): the 30-day average of the shop BUY price, the current
        price against it in percent and the 90-day hint. A figure that needs more history than stored is None / «بيانات غير كافية», never invented."""
        latest = series[-1] if series else None
        average = indicators_math.moving_average(series, 1, 30)
        code, _buy_position, _sell_position = indicators_math.hint(series)
        return {"available": latest is not None,
                "average30": str(average[0]) if average else None,
                "vs_average30_percent": str(average[1]) if average else None,
                "hint_text": HINT_TEXT[code], "hint_tone": HINT_TONE[code]}

    def today(self, *, _expected_actor=None):
        actor = self._actor(_expected_actor)

        def read(connection):
            # الرئيسية is open to every logged-in user; the sales figures follow the CURRENT database
            # grants (non-locking read), never the permissions cached at login.
            verified = self._authorization.require_permission_read(connection, actor, "APP_LOGIN")
            sales = self.can_see_sales(verified)
            dashboard, balances = self._repository(connection), self._balances(connection)
            result = {"old_gold_balance": balances.old_gold_counted(), "old_gold_flow": dashboard.today_old_gold_flow(),
                      "price_daily": dashboard.price_daily_21(), "price_legacy": dashboard.price_history_legacy_21()}
            if sales:
                result.update(scrap=dashboard.today_scrap_purchases(), scrap_prices=dashboard.latest_shop_buy_prices(),
                              invoices=dashboard.today_invoices(), sold=dashboard.today_sold_by_karat(),
                              month=dashboard.month_sold_by_karat(), prev_year=dashboard.month_sold_by_karat(previous=True),
                              month_info=dashboard.month_start(), top_types=dashboard.month_top_types())
            if STOCK_PERMISSIONS & set(verified.permissions):                  # the minimum-stock shortages follow the CURRENT inventory grants
                result["below_min"] = dashboard.below_min_levels()
                result["dead_days"] = int(setting_rules.report_thresholds(dashboard)["dead_stock_days"])
                result["dead_stock"] = dashboard.dead_stock_by_karat(result["dead_days"])
                result["can_see_stock"] = True
            if "CUSTOMERS_VIEW" in set(verified.permissions):                 # the customer balances follow the CURRENT customers grants
                result["can_see_balances"] = True
                result["customer_balances"] = dashboard.customer_balances()
                result["debt_today"] = dashboard.db_today()
                result["debt_ledger"] = self._reports(connection).customer_open_ledger()
            return sales, result
        try:
            with self._transaction() as connection:
                raise _Discard(read(connection))
        except _Discard as done:
            sales, raw = done.args[0]

        balance = {int(r["karat_value"]): Decimal(str(r["balance_grams"])) for r in raw["old_gold_balance"]}
        flow = {int(r["karat_value"]): r for r in raw["old_gold_flow"]}
        snapshot = {
            "can_see_sales": sales,
            "old_gold": [{"karat": karat,
                          "balance": str(balance.get(karat, Decimal("0")).quantize(GRAMS)),
                          "in": str(Decimal(str(flow.get(karat, {}).get("grams_in", 0))).quantize(GRAMS)),
                          "out": str(Decimal(str(flow.get(karat, {}).get("grams_out", 0))).quantize(GRAMS))}
                         for karat in DASHBOARD_KARATS],
        }
        snapshot["can_see_stock"] = bool(raw.get("can_see_stock"))
        if snapshot["can_see_stock"]:
            snapshot["below_min"] = self.shortages(raw.get("below_min"))
            snapshot["dead_stock"] = self.dead_stock(raw.get("dead_stock"), raw.get("dead_days") or DEAD_STOCK_DAYS)
        day_of = lambda value: value.date() if hasattr(value, "date") else value
        live = [(day_of(r["day"]), Decimal(str(r["buy"])), Decimal(str(r["sell"]))) for r in raw.get("price_daily") or []]
        legacy = [(day_of(r["day"]), Decimal(str(r["buy"])), Decimal(str(r["sell"]))) for r in raw.get("price_legacy") or []]
        snapshot["price_hint"] = self.price_hint(indicators_math.merge_series(legacy, live))
        snapshot["can_see_balances"] = bool(raw.get("can_see_balances"))
        if snapshot["can_see_balances"]:
            snapshot["customer_balances"] = self.balances(raw.get("customer_balances"))
            snapshot["overdue"] = self.overdue(raw.get("debt_ledger"), raw["debt_today"])
        if sales:
            invoices = raw["invoices"] or {}
            sold = {int(r["karat_value"]): r for r in raw["sold"]}
            snapshot.update(
                invoice_count=int(invoices.get("invoice_count") or 0),
                sale_invoice_count=int(invoices.get("sale_invoice_count") or 0),
                buy_invoice_count=int(invoices.get("buy_invoice_count") or 0),
                unidentified_lines=int(invoices.get("unidentified_lines") or 0),
                sell_total=str(Decimal(str(invoices.get("sell_total") or 0)).quantize(MONEY)),
                buy_total=str(Decimal(str(invoices.get("buy_total") or 0)).quantize(MONEY)),
                sold=[{"karat": karat,
                       "grams": str(Decimal(str(sold[karat]["grams"])).quantize(GRAMS)),
                       "pieces": int(sold[karat]["pieces"]),
                       "from_old_gold": str(Decimal(str(sold[karat]["from_old_gold_grams"])).quantize(GRAMS))}
                      for karat in sorted(sold, key=lambda k: (k not in DASHBOARD_KARATS,
                                                              DASHBOARD_KARATS.index(k) if k in DASHBOARD_KARATS else k))],
                month=self.month_summary(raw["month"], raw["prev_year"]), month_day=int((raw.get("month_info") or {}).get("day") or 0),
                top_types=self.top_types(raw.get("top_types")), scrap_today=self.scrap_today(raw.get("scrap"), raw.get("scrap_prices")))
        return snapshot
