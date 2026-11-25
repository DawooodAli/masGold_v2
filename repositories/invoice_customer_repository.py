"""Persistence of «تغيير العميل»: the customer a finalized invoice belongs to. Runs on the caller's transaction; BIGINT ids leave as decimal strings.

What moves with the invoice (and only this invoice): the invoice header, its customer snapshot (the printed name, code, mobile and place), the account effect THIS invoice wrote (two new ledger rows,
the original is never rewritten) and the customer attribution of the old-gold movements it recorded. Nothing else of the old customer moves (later settlements, deposits, other invoices stay)."""
from repositories.base_repository import BaseRepository
from repositories.customer_repository import CustomerRepository

TYPE_CODE = "INVOICE_CUSTOMER_CHANGE"                                  # customer ledger type, migration 110


def _text(row):
    return None if row is None else {key: (str(value) if key.endswith("id") and value is not None else value) for key, value in row.items()}


class InvoiceCustomerRepository(BaseRepository):
    def invoice(self, invoice_id, lock=False):
        suffix = " FOR UPDATE OF i" if lock else ""
        return _text(self._fetch_one(
            "SELECT i.id, i.invoice_number, i.invoice_status, i.customer_id, s.customer_code_snapshot, s.customer_name_snapshot "
            "FROM invoices i LEFT JOIN invoice_customer_snapshots s ON s.invoice_id = i.id WHERE i.id = %s" + suffix, (int(invoice_id),)))

    def customer(self, customer_id, lock=False):
        suffix = " FOR UPDATE OF c" if lock else ""
        return _text(self._fetch_one(
            "SELECT c.id, c.customer_code, c.name, c.mobile, c.place, c.is_active, c.merged_into_customer_id, ty.name AS customer_type_name "
            "FROM customers c LEFT JOIN customer_types ty ON ty.id = c.customer_type_id WHERE c.id = %s" + suffix, (int(customer_id),)))

    def balance(self, customer_id):
        return self._fetch_one("SELECT CAST(COALESCE(SUM(amount_delta), 0) AS DECIMAL(16,2)) AS balance FROM customer_transactions WHERE customer_id = %s", (int(customer_id),))["balance"]

    def ledger_rows(self, invoice_id, lock=False):
        """The customer-ledger rows tagged with this invoice (its own entry and any INVOICE_CUSTOMER_CHANGE rows; على العميل +, للعميل −), oldest first."""
        suffix = " FOR UPDATE" if lock else ""
        return [_text(r) for r in self._fetch_all(
            "SELECT id, customer_id, amount_delta FROM customer_transactions WHERE reference_type = 'INVOICE' AND reference_id = %s ORDER BY id" + suffix, (int(invoice_id),))]

    def dependents(self, invoice_id):
        """{name: count} of the later records that point at this invoice: an Exchange, a Return, a correction / reversal."""
        queries = {"exchange": "SELECT COUNT(*) AS n FROM invoice_lifecycle_transactions WHERE source_invoice_id = %s",
                   "return": "SELECT COUNT(*) AS n FROM invoice_returns WHERE source_invoice_id = %s",
                   "correction": "SELECT COUNT(*) AS n FROM transaction_corrections WHERE source_type = 'INVOICE' AND source_id = %s"}
        return {name: int(self._fetch_one(sql, (int(invoice_id),))["n"]) for name, sql in queries.items()}

    # ------------------------------------------------------------------ writes
    def _update(self, sql, params):
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            return cursor.rowcount
        finally:
            cursor.close()

    def move(self, invoice, old, customer, effect, *, user_id, reason):
        """Point the invoice at the new customer. The snapshot takes the new customer's CURRENT identity; its balance before / after belonged to the old customer and are unknown for the new one, so they become NULL
        (never invented). The account effect moves with two NEW ledger rows (the reverse on the old customer, the same amount on the new one); the invoice's original row is never touched. The old-gold
        movements only change their customer attribution. Returns the number of ledger rows posted and of old-gold rows that followed."""
        invoice_id, new_id = int(invoice["id"]), int(customer["id"])
        old_id = None if old is None else int(old["id"])
        self._update("UPDATE invoices SET customer_id = %s WHERE id = %s", (new_id, invoice_id))
        self._update("UPDATE invoice_customer_snapshots SET customer_id = %s, customer_code_snapshot = %s, customer_name_snapshot = %s, mobile_snapshot = %s, place_snapshot = %s, "
                     "customer_type_snapshot = %s, balance_before = NULL, balance_after = NULL WHERE invoice_id = %s",
                     (new_id, customer["customer_code"], customer["name"], customer["mobile"], customer["place"], customer["customer_type_name"], invoice_id))
        posted = 0
        if old_id is not None and effect != 0:
            ledger = CustomerRepository(self.connection)
            number = invoice["invoice_number"]
            common = {"type_code": TYPE_CODE, "created_by_user_id": user_id, "reference_type": "INVOICE", "reference_id": invoice_id, "reference_code": number, "notes": reason,
                      "metadata": {"old_customer_id": old_id, "new_customer_id": new_id}}
            ledger.add_transaction(customer_id=old_id, amount_delta=-effect, description=f"نقل الفاتورة {number} إلى العميل {customer['name']}", **common)
            ledger.add_transaction(customer_id=new_id, amount_delta=effect, description=f"نقل الفاتورة {number} من العميل {old['name']}", **common)
            posted = 2
        if old_id is not None:
            gold = self._update("UPDATE old_gold_movements SET customer_id = %s WHERE invoice_id = %s AND customer_id = %s", (new_id, invoice_id, old_id))
        else:
            gold = self._update("UPDATE old_gold_movements SET customer_id = %s WHERE invoice_id = %s AND customer_id IS NULL", (new_id, invoice_id))
        return posted, gold

    def audit(self, *, user_id, invoice, description, metadata):
        import json
        cursor = self.connection.cursor()
        try:
            cursor.execute("INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) "
                           "VALUES (%s, 'INVOICE_CUSTOMER_CHANGED', 'INVOICE', %s, %s, %s, %s, NOW())",
                           (user_id, int(invoice["id"]), invoice["invoice_number"], description, json.dumps(metadata, ensure_ascii=False, default=str)))
        finally:
            cursor.close()
