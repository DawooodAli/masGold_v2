"""Supplier making payments (036): the immutable payment document, its ledger links, and the real money movement.

All writes use the caller's transaction and only ever INSERT: a payment, its legs and every ledger row are never
updated or deleted (a mistake is corrected by a full reversal through transaction_corrections). The payment's money
movement lands on the canonical cash_movements / bank_movements ledgers; its making movement on the supplier-making
ledger. Nothing here touches the supplier GOLD ledger."""
from repositories.request_keys import require_no_marketer_key
from repositories.supplier_repository import SupplierRepository, _id

SOURCE = "SUPPLIER_MAKING_PAYMENT"          # transaction_corrections.source_type, cash/bank reference_type, making source_type
PAYMENT_MOVEMENT, REVERSAL_MOVEMENT = "PAYMENT", "PAYMENT_REVERSAL"
BAR_FEE_MOVEMENT, BAR_FEE_REVERSAL_MOVEMENT = "BAR_FEE", "BAR_FEE_REVERSAL"
MAIN_SAFE = "MAIN_SAFE"
CORRECTION_REFERENCE = "TRANSACTION_CORRECTION"
CASH, BANK = "CASH", "BANK"


def payment_code(payment_id):
    """SM + 6 digits (سداد مصنعيات). Distinct from S (supplier), SP (supplier invoice), DP/DR, CS, FO/TR, RV/FC."""
    return f"SM{int(payment_id):06d}"


class SupplierPaymentRepository(SupplierRepository):

    # ------------------------------------------------------------------ accounts
    def main_safe(self):
        """The Main Safe resolved by its stable code (never a hard-coded database id)."""
        return self._fetch_one("SELECT id, code, name, is_active FROM cash_accounts WHERE code = %s", (MAIN_SAFE,))

    def active_bank_accounts(self):
        return self._fetch_all("SELECT id, name, account_type, bank_name, account_label FROM bank_accounts "
                               "WHERE is_active = TRUE ORDER BY account_type, name, id")

    # ------------------------------------------------------------------ the making position (read under the supplier lock)
    def making_balance_current(self, supplier_id):
        """SUM of the immutable making ledger as a CURRENT (locking) read, so a concurrent commit is never missed by an
        older snapshot. Call it while holding the supplier row lock, which serializes every making-ledger writer."""
        row = self._fetch_one("SELECT COALESCE(SUM(amount_delta), 0) AS balance FROM supplier_making_movements "
                              "WHERE supplier_id = %s FOR SHARE", (_id(supplier_id),))
        return row["balance"]

    # ------------------------------------------------------------------ idempotency
    def by_request(self, key):
        return self._fetch_one("SELECT * FROM supplier_making_payments WHERE request_key = %s FOR UPDATE", (key,))

    # ------------------------------------------------------------------ the payment
    def insert_header(self, *, supplier_id, method, cash_account_id, bank_account_id, amount, payment_date, reference,
                      notes, before, after, key, digest, actor, now, marketer_party_id=None, self_payment_confirmed=False):
        require_no_marketer_key(self._fetch_one, key)                    # 055: a marketer document owns this key -> REQUEST_CONFLICT, nothing is written
        payment_id, _ = self._execute(
            """
            INSERT INTO supplier_making_payments (supplier_id, payment_method, cash_account_id, bank_account_id, marketer_party_id,
                self_payment_confirmed, amount,
                payment_date, reference, notes, balance_before, balance_after, request_key, request_hash,
                performed_by_user_id, performed_by_employee_id, occurred_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (_id(supplier_id), method, cash_account_id, bank_account_id, marketer_party_id, 1 if self_payment_confirmed else 0,
             amount, payment_date, reference, notes, before,
             after, key, digest, actor.user_id, actor.employee_id, now))
        return int(payment_id)

    def post_money(self, *, ledger, account_id, payment_id, code, amount, description, notes, actor, now):
        """The real money OUT (a negative balance is allowed; no new funds policy). Returns the movement id."""
        table, column, types = (("cash_movements", "cash_account_id", "cash_movement_types") if ledger == CASH
                                else ("bank_movements", "bank_account_id", "bank_movement_types"))
        movement_id, count = self._execute(
            f"""
            INSERT INTO {table} ({column}, movement_type_id, amount_delta, reference_type, reference_id, reference_code,
                description, notes, occurred_at, created_by_user_id)
            SELECT %s, t.id, %s, %s, %s, %s, %s, %s, %s, %s FROM {types} t WHERE t.code = %s AND t.is_active = TRUE
            """,
            (int(account_id), -amount, SOURCE, _id(payment_id), code, description, notes, now, actor.user_id, SOURCE))
        if count != 1:
            raise RuntimeError(f"{types} {SOURCE} is missing (migration 036)")
        return int(movement_id)

    def making_type_id(self, code):
        row = self._fetch_one("SELECT id FROM supplier_making_movement_types WHERE code = %s AND is_active = TRUE", (code,))
        return None if row is None else int(row["id"])

    def insert_making_movement(self, *, supplier_id, type_id, amount_delta, source_type, source_id, reference,
                               description, now, user_id):
        new_id, _ = self._execute(
            """
            INSERT INTO supplier_making_movements (supplier_id, movement_type_id, amount_delta, source_type,
                source_id, source_reference, description, occurred_at, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (_id(supplier_id), int(type_id), amount_delta, source_type, _id(source_id), reference, description,
                  now, user_id))
        return int(new_id)

    def insert_leg(self, *, payment_id, kind, cash_movement_id=None, bank_movement_id=None, making_movement_id=None):
        self._execute(
            "INSERT INTO supplier_making_payment_legs (payment_id, leg_kind, cash_movement_id, bank_movement_id, "
            "making_movement_id) VALUES (%s, %s, %s, %s, %s)",
            (_id(payment_id), kind, cash_movement_id, bank_movement_id, making_movement_id))

    # ------------------------------------------------------------------ reads
    _SELECT = """
        SELECT p.*, s.name AS supplier_name, s.is_active AS supplier_active, te.trip_id AS custody_trip_id, te.entry_no AS custody_entry_no,
               ca.name AS cash_account_name, ba.name AS bank_account_name, ba.account_type AS bank_account_type,
               mp.name AS marketer_name,
               u.display_name AS actor_name,
               c.id AS reversal_id, c.reason AS reversal_reason, c.occurred_at AS reversal_at,
               cu.display_name AS reversal_actor_name
        FROM supplier_making_payments p
        JOIN suppliers s ON s.id = p.supplier_id
        LEFT JOIN cash_accounts ca ON ca.id = p.cash_account_id
        LEFT JOIN bank_accounts ba ON ba.id = p.bank_account_id
        LEFT JOIN suppliers mp ON mp.id = p.marketer_party_id
        LEFT JOIN users u ON u.id = p.performed_by_user_id
        LEFT JOIN market_trip_supplier_payments tl ON tl.payment_id = p.id
        LEFT JOIN market_trip_entries te ON te.id = tl.entry_id
        LEFT JOIN transaction_corrections c ON c.correction_type = 'FULL_REVERSAL'
             AND ((c.source_type = '""" + SOURCE + """' AND c.source_id = p.id)
                  OR (tl.entry_id IS NOT NULL AND c.source_type = 'MARKET_TRIP_ENTRY' AND c.source_id = tl.entry_id))
        LEFT JOIN users cu ON cu.id = c.performed_by_user_id
    """

    def payment(self, payment_id):
        return self._fetch_one(self._SELECT + " WHERE p.id = %s", (_id(payment_id),))

    def source(self, payment_id, lock=False):
        """The payment as a correction source. With lock=True the payment row is locked (the caller already holds the
        supplier and account locks, in the same order the payment itself took them)."""
        return self._fetch_one("SELECT p.*, s.name AS supplier_name FROM supplier_making_payments p "
                               "JOIN suppliers s ON s.id = p.supplier_id WHERE p.id = %s" + (" FOR UPDATE OF p" if lock else ""),
                               (_id(payment_id),))

    def payment_making_movement(self, payment_id):
        """The making movement the payment posted (its stored amount is what a reversal restores)."""
        return self._fetch_one(
            "SELECT m.id, m.supplier_id, m.amount_delta FROM supplier_making_payment_legs l "
            "JOIN supplier_making_movements m ON m.id = l.making_movement_id "
            "WHERE l.payment_id = %s AND l.leg_kind = 'MAKING'", (_id(payment_id),))

    def marketer_link(self, payment_id):
        return self._fetch_one("SELECT * FROM supplier_making_marketer_payments WHERE payment_id = %s", (_id(payment_id),))

    def payments_of(self, supplier_id, limit=200):
        return self._fetch_all(self._SELECT + " WHERE p.supplier_id = %s ORDER BY p.id DESC LIMIT %s",
                               (_id(supplier_id), int(limit)))
