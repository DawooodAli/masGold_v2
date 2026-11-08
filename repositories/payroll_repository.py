"""المرتبات (persistence only, migration 104): salary rates, bonuses / penalties of a month, monthly slips and the advances a slip deducts. Nothing here commits or deletes; a slip is never
rewritten (a reversal of its salary operation voids it, derived here from transaction_corrections like the advances)."""
import json

from repositories.base_repository import BaseRepository

REVERSED = ("(SELECT COUNT(*) FROM transaction_corrections k WHERE k.source_type = 'FINANCIAL_OPERATION' "
            "AND k.source_id = {op} AND k.correction_type = 'FULL_REVERSAL')")


class PayrollRepository(BaseRepository):

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    def has_schema(self):
        return self._fetch_one("SELECT 1 AS ok FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'payroll_slips'") is not None

    # ------------------------------------------------------------ employees and salary rates
    def active_employees(self):
        return self._fetch_all("SELECT id, name FROM employees WHERE is_active = TRUE ORDER BY name, id")

    def lock_employee(self, employee_id):
        return self._fetch_one("SELECT id, name, is_active FROM employees WHERE id = %s FOR UPDATE", (int(employee_id),))

    def salary_rate(self, employee_id, month):
        """The rate in force for the month: the latest one whose effective month is not after it."""
        row = self._fetch_one("SELECT monthly_amount, effective_month FROM employee_salary_rates WHERE employee_id = %s AND effective_month <= %s "
                              "ORDER BY effective_month DESC LIMIT 1", (int(employee_id), month))
        return row

    def rates(self, month):
        """{employee_id: row} of the rate in force for the month for every employee."""
        rows = self._fetch_all(
            "SELECT r.employee_id, r.monthly_amount, r.effective_month FROM employee_salary_rates r "
            "WHERE r.effective_month = (SELECT MAX(x.effective_month) FROM employee_salary_rates x WHERE x.employee_id = r.employee_id AND x.effective_month <= %s)", (month,))
        return {int(r["employee_id"]): r for r in rows}

    def set_rate(self, employee_id, month, amount, user_id):
        self._execute("INSERT INTO employee_salary_rates (employee_id, monthly_amount, effective_month, created_by_user_id) VALUES (%s, %s, %s, %s) "
                      "ON DUPLICATE KEY UPDATE monthly_amount = VALUES(monthly_amount), created_by_user_id = VALUES(created_by_user_id)",
                      (int(employee_id), amount, month, user_id))

    # ------------------------------------------------------------ bonuses and penalties
    def adjustments(self, month):
        return self._fetch_all(
            "SELECT a.id, a.employee_id, e.name AS employee_name, a.kind, a.amount, a.reason, a.slip_id, a.created_at, a.voided_at, a.void_reason, "
            "COALESCE(" + REVERSED.format(op="s.operation_id") + ", 0) AS slip_voided "
            "FROM payroll_adjustments a JOIN employees e ON e.id = a.employee_id LEFT JOIN payroll_slips s ON s.id = a.slip_id WHERE a.period_month = %s ORDER BY a.id DESC", (month,))

    def open_adjustments(self, employee_id, month, lock=False):
        """The bonuses / penalties of the month that are neither voided nor on a slip yet."""
        # an adjustment that sat on a slip whose salary operation was reversed is open again
        return self._fetch_all("SELECT a.id, a.kind, a.amount FROM payroll_adjustments a LEFT JOIN payroll_slips s ON s.id = a.slip_id WHERE a.employee_id = %s AND a.period_month = %s "
                               "AND a.voided_at IS NULL AND (a.slip_id IS NULL OR " + REVERSED.format(op="s.operation_id") + " > 0) ORDER BY a.id"
                               + (" FOR UPDATE" if lock else ""), (int(employee_id), month))

    def insert_adjustment(self, *, employee_id, month, kind, amount, reason, user_id):
        new_id, _ = self._execute("INSERT INTO payroll_adjustments (employee_id, period_month, kind, amount, reason, created_by_user_id) VALUES (%s, %s, %s, %s, %s, %s)",
                                  (int(employee_id), month, kind, amount, reason, user_id))
        return str(new_id)

    def adjustment(self, adjustment_id, lock=False):
        return self._fetch_one("SELECT id, employee_id, period_month, kind, amount, slip_id, voided_at FROM payroll_adjustments WHERE id = %s" + (" FOR UPDATE" if lock else ""),
                               (int(adjustment_id),))

    def void_adjustment(self, adjustment_id, reason, user_id):
        _, count = self._execute("UPDATE payroll_adjustments SET voided_at = NOW(), voided_by_user_id = %s, void_reason = %s WHERE id = %s AND slip_id IS NULL AND voided_at IS NULL",
                                 (user_id, reason, int(adjustment_id)))
        return count == 1

    def attach_adjustments(self, ids, slip_id):
        for adjustment_id in ids:
            self._execute("UPDATE payroll_adjustments a LEFT JOIN payroll_slips s ON s.id = a.slip_id SET a.slip_id = %s WHERE a.id = %s AND a.voided_at IS NULL "
                          "AND (a.slip_id IS NULL OR " + REVERSED.format(op="s.operation_id") + " > 0)", (int(slip_id), int(adjustment_id)))

    # ------------------------------------------------------------ slips
    def slips(self, month):
        return self._fetch_all(
            "SELECT s.id, s.employee_id, s.base_salary, s.bonuses, s.penalties, s.advances_deducted, s.net_amount, s.operation_id, s.note, s.created_at, "
            + REVERSED.format(op="s.operation_id") + " AS voided FROM payroll_slips s WHERE s.period_month = %s ORDER BY s.id DESC", (month,))

    def effective_slip(self, employee_id, month):
        """The slip of the employee for the month that was not voided (None when none)."""
        rows = self._fetch_all("SELECT s.id, s.net_amount FROM payroll_slips s WHERE s.employee_id = %s AND s.period_month = %s AND "
                               + REVERSED.format(op="s.operation_id") + " = 0", (int(employee_id), month))
        return rows[0] if rows else None

    def insert_slip(self, *, employee_id, month, base, bonuses, penalties, advances, net, operation_id, note, user_id):
        new_id, _ = self._execute(
            "INSERT INTO payroll_slips (employee_id, period_month, base_salary, bonuses, penalties, advances_deducted, net_amount, operation_id, note, created_by_user_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (int(employee_id), month, base, bonuses, penalties, advances, net, int(operation_id), note, user_id))
        return str(new_id)

    # ------------------------------------------------------------ the advances a slip deducts
    def open_advances(self, employee_id, lock=False):
        """The employee's advances the shop LENT that are still owed (not reversed, not fully repaid), oldest first."""
        if lock:
            self._fetch_all("SELECT id FROM cash_advances WHERE kind = 'EMPLOYEE' AND employee_id = %s FOR UPDATE", (int(employee_id),))
        rows = self._fetch_all(
            "SELECT a.id, a.amount, "
            "COALESCE((SELECT SUM(r.amount) FROM cash_advance_repayments r WHERE r.advance_id = a.id AND NOT EXISTS (SELECT 1 FROM transaction_corrections k "
            "WHERE k.source_type = 'FINANCIAL_OPERATION' AND k.source_id = r.operation_id AND k.correction_type = 'FULL_REVERSAL')), 0) AS repaid, "
            + REVERSED.format(op="a.operation_id") + " AS voided "
            "FROM cash_advances a WHERE a.kind = 'EMPLOYEE' AND a.employee_id = %s AND COALESCE(a.direction, 'LEND') = 'LEND' ORDER BY a.id", (int(employee_id),))
        return [r for r in rows if not r["voided"] and r["amount"] - r["repaid"] > 0]

    def insert_repayment(self, *, advance_id, amount, operation_id, user_id):
        self._execute("INSERT INTO cash_advance_repayments (advance_id, amount, operation_id, created_by_user_id, created_at) VALUES (%s, %s, %s, %s, NOW())",
                      (int(advance_id), amount, int(operation_id), user_id))

    # ------------------------------------------------------------ audit
    def audit(self, *, user_id, event_type, entity_id, reference, description, metadata):
        self._execute(
            "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) "
            "VALUES (%s, %s, 'PAYROLL', %s, %s, %s, %s, NOW())",
            (user_id, event_type, int(entity_id), reference, description, json.dumps(metadata, ensure_ascii=False, default=str)))
