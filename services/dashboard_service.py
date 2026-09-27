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

DASHBOARD_KARATS = (21, 18, 24)
SALES_PERMISSIONS = frozenset({"INVOICE_CREATE", "INVOICE_FINALIZE"})
GRAMS, MONEY = Decimal("0.001"), Decimal("0.01")


class _Discard(Exception):
    pass


class DashboardService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=DashboardRepository,
                 balance_repository=FinancialBalanceRepository):
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

    def today(self, *, _expected_actor=None):
        actor = self._actor(_expected_actor)
        sales = self.can_see_sales(actor)

        def read(connection):
            dashboard, balances = self._repository(connection), self._balances(connection)
            result = {"old_gold_balance": balances.old_gold_counted(), "old_gold_flow": dashboard.today_old_gold_flow()}
            if sales:
                result.update(invoices=dashboard.today_invoices(), sold=dashboard.today_sold_by_karat(),
                              recent=dashboard.recent_invoices(5))
            return result
        try:
            with self._transaction() as connection:
                raise _Discard(read(connection))
        except _Discard as done:
            raw = done.args[0]

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
                recent=[{"invoice_number": r["invoice_number"], "finalized_at": r["finalized_at"],
                         "net_amount": str(Decimal(str(r["net_amount"])).quantize(MONEY)),
                         "net_direction": r["net_direction"],
                         "customer": r.get("customer_name_snapshot") or "",
                         "salesperson": r.get("employee_name") or ""} for r in raw["recent"]])
        return snapshot
