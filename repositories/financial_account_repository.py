"""Financial Accounts V1 (025): the canonical money ledgers as one operational read model, plus the
persistence of manual financial operations (deposit / withdrawal / internal transfer).

Balances are always SUM(amount_delta) of cash_movements / bank_movements; nothing is cached. The
statement resolves each movement's source in ONE bounded query (window running balance before the
filters and paging, one-to-one joins, never a query per row). Every write runs on the caller's
transaction; nothing here commits, and no original row is ever updated or deleted.
"""
import json

from mysql.connector import ProgrammingError

from repositories.base_repository import BaseRepository

SOURCE = "FINANCIAL_OPERATION"
LEDGERS = {"CASH": ("cash_movements", "cash_account_id", "cash_movement_types", "cash_accounts"),
           "BANK": ("bank_movements", "bank_account_id", "bank_movement_types", "bank_accounts")}
# Source classification of a ledger row (the statement filter values).
SOURCE_TYPES = ("INVOICE", "EXCHANGE", "RETURN", "CUSTOMER_ACCOUNT_SETTLEMENT", "CUSTOMER_DEPOSIT", "FULL_REVERSAL",
                "SETTLEMENT_CORRECTION", "MANUAL_DEPOSIT", "MANUAL_WITHDRAWAL", "ACCOUNT_TRANSFER",
                "OPENING_BALANCE", "SUPPLIER_MAKING_PAYMENT", "MARKET_TRIP_ENTRY", "UNKNOWN")


def operation_code(operation_type, operation_id):
    """TR + 6 digits for an internal transfer, FO + 6 digits for a manual deposit / withdrawal."""
    return f"{'TR' if operation_type == 'ACCOUNT_TRANSFER' else 'FO'}{int(operation_id):06d}"


def account_key(ledger, account_id):
    return f"{ledger}:{int(account_id)}"


def parse_account_key(key):
    """'CASH:1' / 'BANK:3' → (ledger, int id); None when malformed."""
    ledger, _, number = str(key or "").partition(":")
    if ledger not in LEDGERS or not number.isdigit() or not 0 < int(number) < 65536:
        return None
    return ledger, int(number)


class FinancialAccountRepository(BaseRepository):

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    # ------------------------------------------------------------ accounts

    def accounts(self, ledgers=("CASH", "BANK")):
        """The cash and / or bank / wallet accounts the caller may see, with their authoritative balance.

        Only the permitted ledgers are queried at all (active accounts first)."""
        parts = {
            "CASH": """
            SELECT 'CASH' AS ledger, ca.id AS account_id, ca.name, 'CASH' AS account_type, NULL AS bank_name,
                   ca.is_active, COALESCE(SUM(cm.amount_delta), 0) AS balance, COUNT(cm.id) AS movement_count,
                   MAX(cm.occurred_at) AS last_movement_at
            FROM cash_accounts ca LEFT JOIN cash_movements cm ON cm.cash_account_id = ca.id
            GROUP BY ca.id, ca.name, ca.is_active""",
            "BANK": """
            SELECT 'BANK' AS ledger, ba.id AS account_id, ba.name, ba.account_type, ba.bank_name, ba.is_active,
                   COALESCE(SUM(bm.amount_delta), 0) AS balance, COUNT(bm.id) AS movement_count,
                   MAX(bm.occurred_at) AS last_movement_at
            FROM bank_accounts ba LEFT JOIN bank_movements bm ON bm.bank_account_id = ba.id
            GROUP BY ba.id, ba.name, ba.account_type, ba.bank_name, ba.is_active"""}
        selected = [parts[ledger] for ledger in ("CASH", "BANK") if ledger in ledgers]
        if not selected:
            return []
        return self._fetch_all(" UNION ALL ".join(selected)
                               + " ORDER BY is_active DESC, ledger DESC, account_type, name, account_id")

    def account(self, ledger, account_id, lock=False):
        """One account row (locked when writing: serializes every operation on this account)."""
        table = LEDGERS[ledger][3]
        extra = "account_type" if ledger == "BANK" else "'CASH' AS account_type"
        return self._fetch_one(f"SELECT id, name, {extra}, is_active FROM {table} WHERE id = %s"
                               + (" FOR UPDATE" if lock else ""), (int(account_id),))

    def balance(self, ledger, account_id):
        table, column = LEDGERS[ledger][:2]
        return self._fetch_one(f"SELECT COALESCE(SUM(amount_delta), 0) AS balance FROM {table} WHERE {column} = %s",
                               (int(account_id),))["balance"]

    # ------------------------------------------------------------ statement

    def _resolved(self, ledger):
        """WITH-clause of the read model: every movement of one account, its balance after the movement and
        its resolved source. `label_key` is the ONE business classification of a row (the service maps it
        to its Arabic label); it is derived from the posted direction and the stored source, never guessed."""
        table, column, types, _ = LEDGERS[ledger]
        return f"""
            WITH ordered AS (
              SELECT m.id, m.amount_delta, m.occurred_at, m.reference_type, m.reference_id, m.reference_code,
                     m.description, m.notes, m.created_by_user_id, t.code AS movement_code,
                     SUM(m.amount_delta) OVER (ORDER BY m.occurred_at, m.id ROWS UNBOUNDED PRECEDING) AS balance_after
              FROM {table} m JOIN {types} t ON t.id = m.movement_type_id
              WHERE m.{column} = %s
            ), resolved AS (
              SELECT o.*, u.display_name AS actor_name,
                CASE WHEN i.id IS NOT NULL THEN 'INVOICE'
                     WHEN x.id IS NOT NULL THEN 'EXCHANGE'
                     WHEN r.id IS NOT NULL THEN 'RETURN'
                     WHEN cs.id IS NOT NULL THEN 'CUSTOMER_ACCOUNT_SETTLEMENT'
                     WHEN cd.id IS NOT NULL THEN 'CUSTOMER_DEPOSIT'
                     WHEN smp.id IS NOT NULL THEN 'SUPPLIER_MAKING_PAYMENT'
                     WHEN met.id IS NOT NULL THEN 'MARKET_TRIP_ENTRY'
                     WHEN c.id IS NOT NULL THEN c.correction_type
                     WHEN fo.id IS NOT NULL THEN fo.operation_type
                     WHEN o.movement_code = 'OPENING_BALANCE' THEN 'OPENING_BALANCE'
                     ELSE 'UNKNOWN' END AS source_type,
                CASE WHEN i.id IS NOT NULL THEN IF(o.amount_delta > 0, 'SALE', 'OLD_GOLD_PURCHASE')
                     WHEN x.id IS NOT NULL THEN 'EXCHANGE'
                     WHEN r.id IS NOT NULL THEN 'RETURN'
                     WHEN cs.id IS NOT NULL THEN IF(o.amount_delta > 0, 'CUSTOMER_PAYMENT', 'CUSTOMER_PAYOUT')
                     WHEN cd.id IS NOT NULL THEN IF(o.amount_delta > 0, 'DEPOSIT', 'DEPOSIT_REFUND')
                     WHEN smp.id IS NOT NULL THEN 'SUPPLIER_PAYMENT'
                     WHEN met.id IS NOT NULL THEN IF(o.amount_delta < 0, 'TRIP_CASH_ISSUE', 'TRIP_CASH_RETURN')
                     WHEN c.id IS NOT NULL THEN IF(c.correction_type = 'FULL_REVERSAL', 'REVERSAL', 'CORRECTION')
                     WHEN fo.operation_type = 'ACCOUNT_TRANSFER' THEN IF(o.amount_delta > 0, 'TRANSFER_IN', 'TRANSFER_OUT')
                     WHEN fo.id IS NOT NULL THEN COALESCE(fo.category, fo.operation_type)
                     WHEN o.movement_code = 'OPENING_BALANCE' THEN 'OPENING_BALANCE'
                     ELSE 'OTHER' END AS label_key,
                COALESCE(i.id, x.source_invoice_id, r.source_invoice_id, c.invoice_id) AS invoice_id,
                COALESCE(i.invoice_number, origin.invoice_number) AS invoice_number,
                COALESCE(i.invoice_number,
                  CASE WHEN x.id IS NOT NULL THEN CONCAT('EX', LPAD(x.id, GREATEST(6, CHAR_LENGTH(x.id)), '0'))
                       WHEN r.id IS NOT NULL THEN CONCAT('RT', LPAD(r.id, GREATEST(6, CHAR_LENGTH(r.id)), '0'))
                       WHEN cs.id IS NOT NULL THEN CONCAT('CS', LPAD(cs.id, GREATEST(6, CHAR_LENGTH(cs.id)), '0'))
                       WHEN cd.id IS NOT NULL THEN CONCAT(IF(cd.direction = 'IN', 'DP', 'DR'),
                                                          LPAD(cd.id, GREATEST(6, CHAR_LENGTH(cd.id)), '0'))
                       WHEN smp.id IS NOT NULL THEN CONCAT('SM', LPAD(smp.id, GREATEST(6, CHAR_LENGTH(smp.id)), '0'))
                       WHEN met.id IS NOT NULL THEN CONCAT('MT', LPAD(met.trip_id, GREATEST(6, CHAR_LENGTH(met.trip_id)), '0'), '-', met.entry_no)
                       WHEN c.id IS NOT NULL THEN CONCAT(IF(c.correction_type = 'FULL_REVERSAL', 'RV', 'FC'),
                                                         LPAD(c.id, GREATEST(6, CHAR_LENGTH(c.id)), '0'))
                       WHEN fo.id IS NOT NULL THEN CONCAT(IF(fo.operation_type = 'ACCOUNT_TRANSFER', 'TR', 'FO'),
                                                          LPAD(fo.id, GREATEST(6, CHAR_LENGTH(fo.id)), '0')) END,
                  o.reference_code) AS source_reference,
                c.source_type AS corrected_source_type, c.source_id AS corrected_source_id,
                c.source_reference AS corrected_reference,
                fo.id AS operation_id, fo.reason AS operation_reason, fo.category AS operation_category,
                COALESCE(csc.name, csettle.name, cdc.name, isnap.customer_name_snapshot) AS customer_name,
                COALESCE(csc.id, csettle.id, cdc.id, isnap.customer_id) AS customer_id
              FROM ordered o
              LEFT JOIN users u ON u.id = o.created_by_user_id
              LEFT JOIN invoices i ON o.reference_type = 'INVOICE' AND i.id = o.reference_id
              LEFT JOIN invoice_lifecycle_transactions x ON o.reference_type = 'INVOICE_LIFECYCLE' AND x.id = o.reference_id
              LEFT JOIN invoice_returns r ON o.reference_type = 'INVOICE_RETURN' AND r.id = o.reference_id
              LEFT JOIN customer_account_settlements cs
                ON o.reference_type = 'CUSTOMER_ACCOUNT_SETTLEMENT' AND cs.id = o.reference_id
              LEFT JOIN customer_deposits cd ON o.reference_type = 'CUSTOMER_DEPOSIT' AND cd.id = o.reference_id
              LEFT JOIN customers cdc ON cdc.id = cd.customer_id
              LEFT JOIN supplier_making_payments smp
                ON o.reference_type = 'SUPPLIER_MAKING_PAYMENT' AND smp.id = o.reference_id
              LEFT JOIN market_trip_entries met ON o.reference_type = 'MARKET_TRIP_ENTRY' AND met.id = o.reference_id
              LEFT JOIN transaction_corrections c ON o.reference_type = 'TRANSACTION_CORRECTION' AND c.id = o.reference_id
              LEFT JOIN financial_operations fo ON o.reference_type = '{SOURCE}' AND fo.id = o.reference_id
              LEFT JOIN invoices origin ON origin.id = COALESCE(x.source_invoice_id, r.source_invoice_id, c.invoice_id)
              LEFT JOIN customers csc ON csc.id = cs.customer_id
              LEFT JOIN customer_account_settlements ccs
                ON c.source_type = 'CUSTOMER_ACCOUNT_SETTLEMENT' AND ccs.id = c.source_id
              LEFT JOIN customers csettle ON csettle.id = ccs.customer_id
              LEFT JOIN invoice_customer_snapshots isnap
                ON isnap.invoice_id = COALESCE(i.id, x.source_invoice_id, r.source_invoice_id, c.invoice_id)
            )"""

    def statement(self, ledger, account_id, start=None, end=None, direction="", source="", search="", offset=0):
        """50 + 1 rows, newest first, each with its balance after the movement and its resolved source."""
        return self._fetch_all(
            self._resolved(ledger) + """
            SELECT * FROM resolved
            WHERE (%s IS NULL OR occurred_at >= %s) AND (%s IS NULL OR occurred_at < %s)
              AND (%s = '' OR (%s = 'IN' AND amount_delta > 0) OR (%s = 'OUT' AND amount_delta < 0))
              AND (%s = '' OR source_type = %s)
              AND (%s = '' OR LOCATE(%s, COALESCE(source_reference, '')) > 0
                   OR LOCATE(%s, COALESCE(invoice_number, '')) > 0 OR LOCATE(%s, COALESCE(description, '')) > 0
                   OR LOCATE(%s, COALESCE(operation_reason, '')) > 0)
            ORDER BY occurred_at DESC, id DESC LIMIT 51 OFFSET %s
            """,
            (int(account_id), start, start, end, end, direction, direction, direction, source, source,
             search, search, search, search, search, int(offset)))

    def cash_flow(self, ledger, account_id, start, end, label="", limit=200):
        """The cash screen: IN and OUT movements of the period (newest first, bounded) and the exact period
        totals. One read-model query; totals come from the same rows before the display limit."""
        rows = self._fetch_all(
            self._resolved(ledger) + """
            , period AS (
              SELECT r.*, ROW_NUMBER() OVER (PARTITION BY SIGN(r.amount_delta) ORDER BY r.occurred_at DESC, r.id DESC) AS n,
                     SUM(r.amount_delta) OVER (PARTITION BY SIGN(r.amount_delta)) AS side_total,
                     COUNT(*) OVER (PARTITION BY SIGN(r.amount_delta)) AS side_count
              FROM resolved r
              WHERE r.occurred_at >= %s AND r.occurred_at < %s AND (%s = '' OR r.label_key = %s)
            )
            SELECT * FROM period WHERE n <= %s ORDER BY occurred_at DESC, id DESC
            """,
            (int(account_id), start, end, label, label, int(limit)))
        return rows

    def operation_accounts(self):
        """Active bank / wallet accounts by name only (no balance): the إيداع / سحب بنك selector."""
        return self._fetch_all("SELECT id, name, account_type FROM bank_accounts WHERE is_active = TRUE "
                               "ORDER BY account_type, name, id")

    # ------------------------------------------------------------ manual operations

    def by_request(self, key):
        return self._fetch_one("SELECT * FROM financial_operations WHERE request_key = %s FOR UPDATE", (key,))

    def operation(self, operation_id, lock=False):
        row = self._fetch_one(
            """
            SELECT fo.*, u.display_name AS actor_name,
                   COALESCE(fc.name, fb.name) AS from_name, COALESCE(tc.name, tb.name) AS to_name
            FROM financial_operations fo
            LEFT JOIN users u ON u.id = fo.performed_by_user_id
            LEFT JOIN cash_accounts fc ON fc.id = fo.from_cash_account_id
            LEFT JOIN bank_accounts fb ON fb.id = fo.from_bank_account_id
            LEFT JOIN cash_accounts tc ON tc.id = fo.to_cash_account_id
            LEFT JOIN bank_accounts tb ON tb.id = fo.to_bank_account_id
            WHERE fo.id = %s
            """ + (" FOR UPDATE" if lock else ""), (int(operation_id),))
        if row is None:
            return None
        row["legs"] = self._fetch_all(
            "SELECT leg_role, amount, cash_movement_id, bank_movement_id FROM financial_operation_legs "
            "WHERE operation_id = %s ORDER BY id", (int(operation_id),))
        row["reversal"] = self._fetch_one(
            "SELECT id AS correction_id, reason, occurred_at FROM transaction_corrections "
            "WHERE source_type = %s AND source_id = %s AND correction_type = 'FULL_REVERSAL'",
            (SOURCE, int(operation_id)))
        return row

    def insert_operation(self, *, operation_type, key, digest, amount, source, destination, reason, actor, now,
                         category=None):
        """source / destination: (ledger, account id) or None."""
        def columns(side):
            return (side[1] if side and side[0] == "CASH" else None, side[1] if side and side[0] == "BANK" else None)
        operation_id, _ = self._execute(
            """
            INSERT INTO financial_operations (operation_type, category, request_key, request_hash, amount,
                from_cash_account_id, from_bank_account_id, to_cash_account_id, to_bank_account_id, reason,
                performed_by_user_id, performed_by_employee_id, occurred_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (operation_type, category, key, digest, amount, *columns(source), *columns(destination), reason,
             actor.user_id, actor.employee_id, now))
        return str(operation_id)

    def post_leg(self, *, operation_id, code, leg_role, ledger, account_id, amount, type_code, description, notes,
                 group, actor, now):
        """One signed movement on the real ledger and its leg row (a negative balance is allowed)."""
        table, column, types, _ = LEDGERS[ledger]
        delta = amount if leg_role == "IN" else -amount
        movement_id, count = self._execute(
            f"""
            INSERT INTO {table} ({column}, movement_type_id, amount_delta, transfer_group_id, reference_type,
                reference_id, reference_code, description, notes, occurred_at, created_by_user_id)
            SELECT %s, t.id, %s, %s, %s, %s, %s, %s, %s, %s, %s FROM {types} t WHERE t.code = %s AND t.is_active = TRUE
            """,
            (int(account_id), delta, group, SOURCE, int(operation_id), code, description, notes, now,
             actor.user_id, type_code))
        if count != 1:
            raise RuntimeError(f"{types} {type_code} is missing (migration 025)")
        self._execute(
            "INSERT INTO financial_operation_legs (operation_id, leg_role, amount, cash_movement_id, bank_movement_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (int(operation_id), leg_role, delta, movement_id if ledger == "CASH" else None,
             movement_id if ledger == "BANK" else None))
        return str(movement_id)

    def cash_categories(self):
        """The manageable income / expense categories (migration 069) in display order, or None while the table does not exist (a database below 069 keeps the fixed list)."""
        try:
            return self._fetch_all("SELECT code, name, direction, is_active FROM cash_categories ORDER BY sort_order, name, id")
        except ProgrammingError as exc:
            if getattr(exc, "errno", None) == 1146:
                return None
            raise

    def audit(self, *, actor, event_type, operation_id, code, description, metadata, now):
        self._execute(
            "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, "
            "metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (actor.user_id, event_type, SOURCE, int(operation_id), code, description,
             json.dumps(metadata, ensure_ascii=False, default=str), now))

    def db_now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)
