"""تسليم وإقفال اليوم (persistence only): reads the cash balances and the unfinished invoices, inserts the insert-only close record. Nothing here commits, updates or deletes."""
import json

from repositories.base_repository import BaseRepository


class DayCloseRepository(BaseRepository):

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid
        finally:
            cursor.close()

    def has_schema(self):
        return self._fetch_one("SELECT 1 AS ok FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'day_closes'") is not None

    def cash_accounts(self):
        """Every ACTIVE cash account with its ledger balance (SUM of the movements)."""
        return self._fetch_all(
            "SELECT ca.id, ca.name, COALESCE(SUM(cm.amount_delta), 0) AS balance FROM cash_accounts ca "
            "LEFT JOIN cash_movements cm ON cm.cash_account_id = ca.id WHERE ca.is_active = TRUE GROUP BY ca.id, ca.name ORDER BY ca.id")

    def bank_accounts(self):
        return self._fetch_all(
            "SELECT ba.id, ba.name, COALESCE(SUM(bm.amount_delta), 0) AS balance FROM bank_accounts ba "
            "LEFT JOIN bank_movements bm ON bm.bank_account_id = ba.id WHERE ba.is_active = TRUE GROUP BY ba.id, ba.name ORDER BY ba.id")

    def draft_invoices(self):
        row = self._fetch_one("SELECT COUNT(*) AS n FROM invoices WHERE invoice_status = 'DRAFT'")
        return int(row["n"])

    def by_key(self, request_key):
        return self._fetch_one("SELECT id, business_date, counted_cash, expected_cash, closed_by_user_id FROM day_closes WHERE request_key = %s", (request_key,))

    def recent(self, limit=15):
        return self._fetch_all(
            "SELECT d.id, d.business_date, d.expected_cash, d.counted_cash, d.difference, d.difference_reason, d.draft_invoices, d.note, d.closed_at, u.username "
            "FROM day_closes d JOIN users u ON u.id = d.closed_by_user_id ORDER BY d.id DESC LIMIT %s", (int(limit),))

    def insert(self, *, business_date, expected, counted, difference, reason, drafts, balances, note, request_key, user_id):
        return str(self._execute(
            "INSERT INTO day_closes (business_date, expected_cash, counted_cash, difference, difference_reason, draft_invoices, balances_json, note, request_key, closed_by_user_id, closed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())",
            (business_date, expected, counted, difference, reason, drafts, json.dumps(balances, ensure_ascii=False), note, request_key, user_id)))

    def audit(self, *, user_id, close_id, description, metadata):
        self._execute(
            "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) "
            "VALUES (%s, 'DAY_CLOSED', 'DAY_CLOSE', %s, %s, %s, %s, NOW())",
            (user_id, int(close_id), f"DC{int(close_id):06d}", description, json.dumps(metadata, ensure_ascii=False, default=str)))
