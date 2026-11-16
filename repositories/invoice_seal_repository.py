"""Reads for the invoice seal: the secret key (system_settings, created once) and the stored facts the code covers."""
from mysql.connector import Error as MySQLError

from repositories.base_repository import BaseRepository
from services import invoice_seal as rules


class InvoiceSealRepository(BaseRepository):
    def key(self, *, create=True):
        """The secret key, or None when the database has no `system_settings` (below migration 081). Created ONCE under the table's primary key, so two
        computers racing to create it end with the same value (INSERT IGNORE, then read back)."""
        try:
            value = self.business_values((rules.KEY_SETTING,)).get(rules.KEY_SETTING)
            if value or not create:
                return value or None
            cursor = self.connection.cursor()
            try:
                cursor.execute(
                    "INSERT IGNORE INTO system_settings (setting_key, setting_value, setting_type, description, is_public) "
                    "VALUES (%s, %s, 'STRING', %s, 0)", (rules.KEY_SETTING, rules.new_key(), rules.KEY_DESCRIPTION))
            finally:
                cursor.close()
            return self.business_values((rules.KEY_SETTING,)).get(rules.KEY_SETTING) or None
        except MySQLError:
            return None
