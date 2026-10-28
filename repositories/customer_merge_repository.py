"""Customer merge / delete persistence (migration 087). Runs on the caller's transaction; BIGINT ids leave as decimal strings.

Every table that points at a customer is listed ONCE here: the records are moved (merge) or counted (delete / preview) from this list.
Billing V1 (`billing_invoices`) is frozen history: it is counted, never moved."""
from repositories.base_repository import BaseRepository

# (table, column) whose rows follow the customer to the survivor
MOVED = (
    ("customer_transactions", "customer_id"),
    ("invoices", "customer_id"),
    ("invoice_customer_snapshots", "customer_id"),
    ("invoice_lifecycle_transactions", "customer_id"),
    ("invoice_returns", "customer_id"),
    ("customer_account_settlements", "customer_id"),
    ("customer_deposits", "customer_id"),
    ("dead_cash_writeoffs", "customer_id"),
    ("dead_cash_recoveries", "customer_id"),
    ("cash_advances", "customer_id"),
    ("old_gold_movements", "customer_id"),
)
# counted for the delete rule but never moved
COUNTED_ONLY = (("customer_flags", "customer_id"), ("billing_invoices", "customer_id"))
ALL_TABLES = MOVED + COUNTED_ONLY


class CustomerMergeRepository(BaseRepository):
    def lock_customers(self, ids):
        """Both rows locked in ASCENDING id order (one canonical order for every customer flow)."""
        found = {}
        for cid in sorted({int(i) for i in ids}):
            row = self._fetch_one("SELECT id, customer_code, name, mobile, is_active, merged_into_customer_id "
                                  "FROM customers WHERE id = %s FOR UPDATE", (cid,))
            if row is not None:
                found[str(row["id"])] = row
        return found

    def balance(self, customer_id):
        return self._fetch_one("SELECT CAST(COALESCE(SUM(amount_delta), 0) AS DECIMAL(16,2)) AS balance "
                               "FROM customer_transactions WHERE customer_id = %s", (int(customer_id),))["balance"]

    def usage(self, customer_id):
        """{table: row count} for every table that references the customer."""
        return {table: int(self._fetch_one(f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = %s",
                                           (int(customer_id),))["n"]) for table, column in ALL_TABLES}

    def move(self, table, column, source_id, target_id):
        cursor = self.connection.cursor()
        try:
            cursor.execute(f"UPDATE {table} SET {column} = %s WHERE {column} = %s", (int(target_id), int(source_id)))
            return cursor.rowcount
        finally:
            cursor.close()

    def deactivate_duplicate_flags(self, source_id, target_id, actor_user_id, reason):
        """A flag of a type the survivor already carries is closed (history kept), so only ONE active flag per type exists after the move."""
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "UPDATE customer_flags s SET s.is_active = FALSE, s.deactivated_at = CURRENT_TIMESTAMP, "
                "s.deactivated_by_user_id = %s, s.deactivation_reason = %s "
                "WHERE s.customer_id = %s AND s.is_active = TRUE AND EXISTS ("
                "  SELECT 1 FROM (SELECT flag_type FROM customer_flags WHERE customer_id = %s AND is_active = TRUE) t "
                "  WHERE t.flag_type = s.flag_type)",
                (actor_user_id, reason, int(source_id), int(target_id)))
            return cursor.rowcount
        finally:
            cursor.close()

    def mark_merged(self, source_id, target_id, actor_user_id):
        cursor = self.connection.cursor()
        try:
            cursor.execute("UPDATE customers SET is_active = FALSE, merged_into_customer_id = %s, updated_by_user_id = %s "
                           "WHERE id = %s", (int(target_id), actor_user_id, int(source_id)))
        finally:
            cursor.close()

    def delete_customer(self, customer_id):
        cursor = self.connection.cursor()
        try:
            cursor.execute("DELETE FROM customers WHERE id = %s", (int(customer_id),))
            return cursor.rowcount
        finally:
            cursor.close()

    def audit(self, *, actor, event_type, customer_id, description, metadata):
        import json
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (actor.user_id, event_type, "CUSTOMER", int(customer_id), f"C{int(customer_id):06d}", description,
                 json.dumps(metadata, ensure_ascii=False)))
        finally:
            cursor.close()
