"""Persistence of «تغيير البائع»: the employee a finalized invoice names as its seller. The invoice, its lines, payments and ledgers are not touched; the change is audited with the old and the new seller."""
import json

from repositories.base_repository import BaseRepository


class InvoiceSellerRepository(BaseRepository):
    def lock_invoice(self, invoice_id):
        return self._fetch_one("SELECT i.id, i.invoice_number, i.invoice_status, i.employee_id, e.name AS employee_name FROM invoices i LEFT JOIN employees e ON e.id = i.employee_id "
                               "WHERE i.id = %s FOR UPDATE", (int(invoice_id),))

    def employee(self, employee_id):
        return self._fetch_one("SELECT id, name, is_active FROM employees WHERE id = %s", (int(employee_id),))

    def active_employees(self):
        return self._fetch_all("SELECT id, name FROM employees WHERE is_active = TRUE ORDER BY name, id")

    def set_seller(self, invoice_id, employee_id):
        cursor = self.connection.cursor()
        try:
            cursor.execute("UPDATE invoices SET employee_id = %s WHERE id = %s", (int(employee_id), int(invoice_id)))
            return cursor.rowcount
        finally:
            cursor.close()

    def audit(self, *, user_id, invoice, description, metadata):
        cursor = self.connection.cursor()
        try:
            cursor.execute("INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) "
                           "VALUES (%s, 'INVOICE_SELLER_CHANGED', 'INVOICE', %s, %s, %s, %s, NOW())",
                           (user_id, int(invoice["id"]), invoice["invoice_number"], description, json.dumps(metadata, ensure_ascii=False, default=str)))
        finally:
            cursor.close()
