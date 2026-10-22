"""الرئيسية: one read-only snapshot of today's shop activity.

Every figure has one meaning (no mixing of commercial value, real money and customer ledger):
- invoice_count / sale & buy invoice counts: today's FINALIZED invoices;
- sell_total: the commercial value of today's sales (invoices.sell_total);
- buy_total: the commercial value of today's old-gold purchases (invoices.buy_total);
- sold grams per karat: today's sale lines (incl. sales from old-gold stock, shown separately);
- old gold: COUNTED balance per karat (same ledger as the MainWindow strip) + today's IN / OUT;
- unidentified_lines: today's manual (F5) sale lines awaiting reconciliation.
Money received / paid, customer balances and bank / wallet balances are NOT on this page.

Sales figures and the recent invoices need a billing permission; old-gold weights are the same
operational information the MainWindow strip already shows every logged-in user.
"""
from decimal import Decimal

from repositories.dashboard_repository import DashboardRepository
from repositories.financial_balance_repository import FinancialBalanceRepository
from services.invoice_service import invoice_transaction

DASHBOARD_KARATS = (21, 18, 22, 24)
SALES_PERMISSIONS = frozenset({"INVOICE_CREATE", "INVOICE_FINALIZE"})
STOCK_PERMISSIONS = frozenset({"INVENTORY_VIEW", "INVENTORY_MANAGE"})
GRAMS, MONEY = Decimal("0.001"), Decimal("0.01")


class _Discard(Exception):
    pass


class DashboardService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=DashboardRepository,
                 balance_repository=FinancialBalanceRepository, authorization=None):
        from services.authorization_service import AuthorizationService
        self._authorization = authorization or AuthorizationService(session)
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        self._balances = balance_repository

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
        """Per karat (never summed across karats): the grams and pieces sold so far this month and the grams over the same span of the previous month."""
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

    def today(self, *, _expected_actor=None):
        actor = self._actor(_expected_actor)

        def read(connection):
            # الرئيسية is open to every logged-in user; the sales figures follow the CURRENT database
            # grants (non-locking read), never the permissions cached at login.
            verified = self._authorization.require_permission_read(connection, actor, "APP_LOGIN")
            sales = self.can_see_sales(verified)
            dashboard, balances = self._repository(connection), self._balances(connection)
            result = {"old_gold_balance": balances.old_gold_counted(), "old_gold_flow": dashboard.today_old_gold_flow()}
            if sales:
                result.update(invoices=dashboard.today_invoices(), sold=dashboard.today_sold_by_karat(),
                              recent=dashboard.recent_invoices(5), month=dashboard.month_sold_by_karat(), prev_month=dashboard.month_sold_by_karat(previous=True),
                              month_info=dashboard.month_start())
            if STOCK_PERMISSIONS & set(verified.permissions):                  # the minimum-stock shortages follow the CURRENT inventory grants
                result["below_min"] = dashboard.below_min_levels()
                result["can_see_stock"] = True
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
                month=self.month_summary(raw["month"], raw["prev_month"]), month_day=int((raw.get("month_info") or {}).get("day") or 0),
                recent=[{"invoice_number": r["invoice_number"], "finalized_at": r["finalized_at"],
                         "net_amount": str(Decimal(str(r["net_amount"])).quantize(MONEY)),
                         "net_direction": r["net_direction"],
                         "customer": r.get("customer_name_snapshot") or "",
                         "salesperson": r.get("employee_name") or ""} for r in raw["recent"]])
        return snapshot
