"""Persistence of the position snapshots (migration 067) and the two plain reads they need (net supplier gold, cash / bank balances). Insert-only: nothing here edits or deletes a snapshot."""
import json

from repositories.base_repository import BaseRepository

COLUMNS = ("snapshot_kind", "taken_at", "gold_shop_eq21", "gold_custody_eq21", "gold_intermediary_eq21", "gold_book_eq21", "gold_suppliers_eq21", "gold_total_eq21",
           "unknown_grams", "cash_egp", "bank_egp", "created_by_user_id")


class PositionSnapshotRepository(BaseRepository):
    def now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)

    def opening(self):
        return self._fetch_one("SELECT * FROM position_snapshots WHERE snapshot_kind = 'OPENING' ORDER BY id LIMIT 1")

    def latest_on_date(self, day):
        """The newest non-opening snapshot taken on this calendar day, or None."""
        return self._fetch_one("SELECT * FROM position_snapshots WHERE snapshot_kind <> 'OPENING' AND DATE(taken_at) = %s ORDER BY taken_at DESC, id DESC LIMIT 1", (day,))

    def supplier_gold(self):
        """The supplier gold ledger summed per supplier karat (positive = the shop owes the supplier)."""
        return self._fetch_all("SELECT k.karat_value, COALESCE(SUM(m.weight_delta_grams), 0) AS grams FROM supplier_gold_movements m JOIN karats k ON k.id = m.karat_id GROUP BY k.karat_value")

    def cash_and_bank(self):
        cash = self._fetch_one("SELECT COALESCE(SUM(amount_delta), 0) AS total FROM cash_movements")["total"]
        bank = self._fetch_one("SELECT COALESCE(SUM(amount_delta), 0) AS total FROM bank_movements")["total"]
        return cash, bank

    # ---- the two stages of the initial balance
    def ledgers_posted(self):
        """Stage 1 (scrap, cash and bank openings) was done: its marker audit row exists."""
        return self._fetch_one("SELECT id FROM audit_log WHERE event_type = 'POSITION_OPENING_LEDGERS' ORDER BY id LIMIT 1") is not None

    def accounts_for_opening(self, lock=False):
        """The main safe and every active bank / wallet account, each with how many movements it already has (an opening is allowed only on an account with none)."""
        suffix = " FOR UPDATE" if lock else ""
        safe = self._fetch_all("SELECT a.id, a.name, 'CASH' AS ledger, 'CASH' AS account_type, (SELECT COUNT(*) FROM cash_movements m WHERE m.cash_account_id = a.id) AS movements "
                               "FROM cash_accounts a WHERE a.code = 'MAIN_SAFE' AND a.is_active = TRUE" + suffix)
        banks = self._fetch_all("SELECT a.id, a.name, 'BANK' AS ledger, a.account_type, (SELECT COUNT(*) FROM bank_movements m WHERE m.bank_account_id = a.id) AS movements "
                                "FROM bank_accounts a WHERE a.is_active = TRUE ORDER BY a.id" + suffix)
        return safe + banks

    def add_money_opening(self, *, ledger, account_id, amount, user_id, now):
        if ledger == "CASH":
            query = ("INSERT INTO cash_movements (cash_account_id, movement_type_id, amount_delta, reference_type, description, occurred_at, created_by_user_id) "
                     "SELECT %s, t.id, %s, 'OPENING', %s, %s, %s FROM cash_movement_types t WHERE t.code = 'OPENING_BALANCE'")
        else:
            query = ("INSERT INTO bank_movements (bank_account_id, movement_type_id, amount_delta, reference_type, description, occurred_at, created_by_user_id) "
                     "SELECT %s, t.id, %s, 'OPENING', %s, %s, %s FROM bank_movement_types t WHERE t.code = 'OPENING_BALANCE'")
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, (int(account_id), amount, "رصيد افتتاحي", now, user_id))
            if not cursor.rowcount:
                raise RuntimeError("OPENING_BALANCE movement type is missing (migration 018)")
        finally:
            cursor.close()

    def add_scrap_opening(self, *, karat_value, grams, user_id, now):
        """The counted opening balance of the scrap of one karat (an ordinary OPENING_BALANCE old-gold movement)."""
        cursor = self.connection.cursor()
        try:
            cursor.execute("INSERT INTO old_gold_movements (movement_type_id, karat_id, weight_delta_grams, reference_type, notes, occurred_at, created_by_user_id) "
                           "SELECT t.id, k.id, %s, 'OPENING', %s, %s, %s FROM old_gold_movement_types t JOIN karats k ON k.karat_value = %s AND k.is_active = TRUE WHERE t.code = 'OPENING_BALANCE'",
                           (grams, "رصيد افتتاحي للكسر", now, user_id, int(karat_value)))
            if not cursor.rowcount:
                raise RuntimeError("the OPENING_BALANCE old-gold type or the karat is missing")
        finally:
            cursor.close()

    def opening_totals(self):
        """What stage 1 posted: scrap grams per karat, the cash opening and the bank / wallet openings."""
        scrap = self._fetch_all("SELECT k.karat_value, COALESCE(SUM(m.weight_delta_grams), 0) AS grams FROM old_gold_movements m JOIN old_gold_movement_types t ON t.id = m.movement_type_id "
                                "JOIN karats k ON k.id = m.karat_id WHERE t.code = 'OPENING_BALANCE' AND m.balance_effect = 'COUNTED' GROUP BY k.karat_value")
        cash = self._fetch_one("SELECT COALESCE(SUM(m.amount_delta), 0) AS total FROM cash_movements m JOIN cash_movement_types t ON t.id = m.movement_type_id WHERE t.code = 'OPENING_BALANCE'")["total"]
        bank = self._fetch_one("SELECT COALESCE(SUM(m.amount_delta), 0) AS total FROM bank_movements m JOIN bank_movement_types t ON t.id = m.movement_type_id WHERE t.code = 'OPENING_BALANCE'")["total"]
        return scrap, cash, bank

    def insert(self, values):
        cursor = self.connection.cursor()
        try:
            cursor.execute(f"INSERT INTO position_snapshots ({', '.join(COLUMNS)}) VALUES ({', '.join(['%s'] * len(COLUMNS))})", tuple(values[c] for c in COLUMNS))
            return cursor.lastrowid
        finally:
            cursor.close()

    def audit(self, *, user_id, event_type, entity_id, description, metadata, occurred_at):
        cursor = self.connection.cursor()
        try:
            cursor.execute("INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                           (user_id, event_type, "POSITION_SNAPSHOT", int(entity_id), str(entity_id), description, json.dumps(metadata, ensure_ascii=False, default=str), occurred_at))
        finally:
            cursor.close()
