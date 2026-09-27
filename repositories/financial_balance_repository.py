"""Read-only balances from the authoritative ledgers (never cached, never recomputed in QML).

Cash: cash_movements through vw_cash_balances (007). Bank / wallet accounts:
bank_movements through bank_accounts (007 + 017 account_type). Old gold: the
COUNTED old_gold_movements ledger per karat (REFERENCE_ONLY history excluded).
"""
import json

from repositories.base_repository import BaseRepository


class FinancialBalanceRepository(BaseRepository):
    def cash_balance(self):
        row = self._fetch_one("SELECT COALESCE(SUM(balance), 0) AS balance FROM vw_cash_balances WHERE is_active = TRUE")
        return row["balance"]

    def old_gold_counted(self):
        return self._fetch_all(
            """
            SELECT k.id AS karat_id, k.karat_value, COALESCE(SUM(og.weight_delta_grams), 0) AS balance_grams
            FROM karats k
            LEFT JOIN old_gold_movements og
                ON og.karat_id = k.id AND og.weight_delta_grams IS NOT NULL AND og.balance_effect = 'COUNTED'
            WHERE k.is_active = TRUE
            GROUP BY k.id, k.karat_value
            ORDER BY k.karat_value
            """)

    def account_summaries(self):
        """Every cash and bank / wallet account: balance, money in, money out, last movement."""
        return self._fetch_all(
            """
            SELECT 'CASH' AS ledger, ca.id AS account_id, ca.name, 'CASH' AS account_type, NULL AS bank_name,
                   ca.is_active,
                   COALESCE(SUM(cm.amount_delta), 0) AS balance,
                   COALESCE(SUM(CASE WHEN cm.amount_delta > 0 THEN cm.amount_delta END), 0) AS money_in,
                   COALESCE(-SUM(CASE WHEN cm.amount_delta < 0 THEN cm.amount_delta END), 0) AS money_out,
                   COUNT(cm.id) AS movement_count, MAX(cm.occurred_at) AS last_movement_at
            FROM cash_accounts ca
            LEFT JOIN cash_movements cm ON cm.cash_account_id = ca.id
            GROUP BY ca.id, ca.name, ca.is_active
            UNION ALL
            SELECT 'BANK', ba.id, ba.name, ba.account_type, ba.bank_name, ba.is_active,
                   COALESCE(SUM(bm.amount_delta), 0),
                   COALESCE(SUM(CASE WHEN bm.amount_delta > 0 THEN bm.amount_delta END), 0),
                   COALESCE(-SUM(CASE WHEN bm.amount_delta < 0 THEN bm.amount_delta END), 0),
                   COUNT(bm.id), MAX(bm.occurred_at)
            FROM bank_accounts ba
            LEFT JOIN bank_movements bm ON bm.bank_account_id = ba.id
            GROUP BY ba.id, ba.name, ba.account_type, ba.bank_name, ba.is_active
            ORDER BY ledger DESC, account_type, name
            """)

    def recent_movements(self, limit=5):
        """The latest movements of every account (drill-down preview), newest first."""
        return self._fetch_all(
            """
            SELECT * FROM (
                SELECT 'CASH' AS ledger, cm.cash_account_id AS account_id, cm.amount_delta, cm.occurred_at,
                       cm.reference_code, cm.description, t.name AS movement_type,
                       ROW_NUMBER() OVER (PARTITION BY cm.cash_account_id ORDER BY cm.occurred_at DESC, cm.id DESC) AS n
                FROM cash_movements cm JOIN cash_movement_types t ON t.id = cm.movement_type_id
                UNION ALL
                SELECT 'BANK', bm.bank_account_id, bm.amount_delta, bm.occurred_at, bm.reference_code,
                       bm.description, t.name,
                       ROW_NUMBER() OVER (PARTITION BY bm.bank_account_id ORDER BY bm.occurred_at DESC, bm.id DESC)
                FROM bank_movements bm JOIN bank_movement_types t ON t.id = bm.movement_type_id
            ) latest
            WHERE n <= %s
            ORDER BY ledger, account_id, occurred_at DESC
            """, (int(limit),))

    # ------------------------------------------------------------ manager setup (writes)

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    def create_bank_account(self, *, name, account_type, bank_name, account_label):
        account_id, _ = self._execute(
            "INSERT INTO bank_accounts (name, account_type, bank_name, account_label, is_active) "
            "VALUES (%s, %s, %s, %s, TRUE)", (name, account_type, bank_name, account_label))
        return int(account_id)

    def set_bank_account_active(self, account_id, active):
        _, rows = self._execute("UPDATE bank_accounts SET is_active = %s WHERE id = %s", (bool(active), int(account_id)))
        return rows

    def lock_account(self, ledger, account_id):
        """The account row, locked, with its movement count (0 = never used, opening allowed)."""
        if ledger == "CASH":
            return self._fetch_one(
                "SELECT a.id, a.name, a.is_active, (SELECT COUNT(*) FROM cash_movements m "
                "WHERE m.cash_account_id = a.id) AS movement_count FROM cash_accounts a WHERE a.id = %s FOR UPDATE",
                (int(account_id),))
        return self._fetch_one(
            "SELECT a.id, a.name, a.account_type, a.is_active, (SELECT COUNT(*) FROM bank_movements m "
            "WHERE m.bank_account_id = a.id) AS movement_count FROM bank_accounts a WHERE a.id = %s FOR UPDATE",
            (int(account_id),))

    def add_opening_balance(self, *, ledger, account_id, amount, created_by_user_id, occurred_at):
        """The real opening count as an ordinary OPENING_BALANCE ledger movement (018)."""
        if ledger == "CASH":
            query = ("INSERT INTO cash_movements (cash_account_id, movement_type_id, amount_delta, reference_type, "
                     "description, occurred_at, created_by_user_id) SELECT %s, t.id, %s, 'OPENING', %s, %s, %s "
                     "FROM cash_movement_types t WHERE t.code = 'OPENING_BALANCE'")
        else:
            query = ("INSERT INTO bank_movements (bank_account_id, movement_type_id, amount_delta, reference_type, "
                     "description, occurred_at, created_by_user_id) SELECT %s, t.id, %s, 'OPENING', %s, %s, %s "
                     "FROM bank_movement_types t WHERE t.code = 'OPENING_BALANCE'")
        movement_id, rows = self._execute(query, (int(account_id), amount, "رصيد افتتاحي", occurred_at,
                                                  created_by_user_id))
        if not rows:
            raise RuntimeError("OPENING_BALANCE movement type is missing (migration 018)")
        return int(movement_id)

    def audit(self, *, user_id, event_type, entity_type, entity_id, description, metadata, occurred_at):
        self._execute(
            "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, "
            "metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (user_id, event_type, entity_type, int(entity_id), str(entity_id), description,
             json.dumps(metadata, ensure_ascii=False, default=str), occurred_at))

    def now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)
