"""Supplier-to-supplier gold transfers (migration 108): the immutable GT document and its two supplier gold ledger movements.

Insert only: a transfer and its ledger rows are never updated or deleted; a mistake is undone by a reversal row of the same table that names the original (reverses_id) and posts the exact
opposite movements. Nothing here touches Scrap, custody, cash, bank, making or inventory."""
from repositories.request_keys import marketer_key_in_use, require_no_marketer_key
from repositories.supplier_repository import SupplierRepository, _id

SOURCE = "SUPPLIER_GOLD_TRANSFER"                        # supplier_gold_movements.source_type
OUT, IN = "ACCOUNT_TRANSFER_OUT", "ACCOUNT_TRANSFER_IN"
OUT_REVERSAL, IN_REVERSAL = "ACCOUNT_TRANSFER_OUT_REVERSAL", "ACCOUNT_TRANSFER_IN_REVERSAL"
TYPES = (OUT, IN, OUT_REVERSAL, IN_REVERSAL)
MARKER, MARKER_REVERSAL = "SUP_XFER", "SUP_XFER_REV"         # 109: the market custody log entries that record a transfer / its reversal (no cash, gold or Scrap leg)


def transfer_code(transfer_id):
    """GT + 6 digits (تحويل ذهب بين موردين). Distinct from S, SP, SM, SG, DP/DR, CS, FO/TR, RV/FC, RT, EX, MT."""
    return f"GT{int(transfer_id):06d}"


class SupplierGoldTransferRepository(SupplierRepository):
    _schema = None

    def has_schema(self):
        if self._schema is None:
            row = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'supplier_gold_transfers'")
            self._schema = bool(row and int(row["n"]))
        return self._schema

    _marker_schema = None

    def has_marker_schema(self):
        """True from migration 109 (the marker link table exists)."""
        if self._marker_schema is None:
            row = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'supplier_gold_transfer_entries'")
            self._marker_schema = bool(row and int(row["n"]))
        return self._marker_schema

    def latest_open_trip(self):
        """The id of the newest OPEN legacy market custody (the first one the custody screen lists), or None. A plain read: the caller locks the trip row and checks it again."""
        row = self._fetch_one("SELECT id FROM market_trips WHERE status = 'OPEN' AND accounting_mode = 'LEGACY' ORDER BY created_at DESC, id DESC LIMIT 1")
        return None if row is None else int(row["id"])

    def link_entry(self, transfer_id, entry_id):
        self._execute("INSERT INTO supplier_gold_transfer_entries (transfer_id, entry_id) VALUES (%s, %s)", (_id(transfer_id), _id(entry_id)))

    # ------------------------------------------------------------------ reads
    def source_candidates(self, exclude_supplier_id):
        """Suppliers (other than the recipient) that HOLD gold for the shop in their own dedicated karat: ledger balance < 0. `held` is the positive grams held. A display read only;
        the chosen source is read again under the locks."""
        return self._fetch_all(
            """
            SELECT s.id, s.name, s.is_active, s.karat_id, k.karat_value, -SUM(m.weight_delta_grams) AS held
            FROM suppliers s
            JOIN karats k ON k.id = s.karat_id
            JOIN supplier_gold_movements m ON m.supplier_id = s.id AND m.karat_id = s.karat_id
            WHERE s.is_supplier = 1 AND s.id <> %s
            GROUP BY s.id, s.name, s.is_active, s.karat_id, k.karat_value
            HAVING SUM(m.weight_delta_grams) < 0
            ORDER BY s.is_active DESC, s.name, s.id
            """, (_id(exclude_supplier_id),))

    def karat_balance(self, supplier_id, karat_id):
        """The supplier's gold ledger balance in one karat (positive = the shop owes it, negative = it holds gold for the shop). Read under the supplier locks the caller holds."""
        row = self._fetch_one("SELECT COALESCE(SUM(weight_delta_grams), 0) AS balance FROM supplier_gold_movements WHERE supplier_id = %s AND karat_id = %s",
                              (_id(supplier_id), int(karat_id)))
        return row["balance"]

    def transfer(self, transfer_id, lock=False):
        return self._fetch_one("SELECT t.*, f.name AS from_name, r.name AS to_name FROM supplier_gold_transfers t "
                               "JOIN suppliers f ON f.id = t.from_supplier_id JOIN suppliers r ON r.id = t.to_supplier_id WHERE t.id = %s"
                               + (" FOR UPDATE OF t" if lock else ""), (_id(transfer_id),))

    def by_request(self, key):
        return self._fetch_one("SELECT * FROM supplier_gold_transfers WHERE request_key = %s FOR UPDATE", (key,))

    def reversal_of(self, transfer_id):
        return self._fetch_one("SELECT id FROM supplier_gold_transfers WHERE reverses_id = %s", (_id(transfer_id),))

    def request_key_used_elsewhere(self, key):
        """True when another flow (custody entry, holding document / receipt, marketer document, a gold settlement) already carries this key (a CURRENT read)."""
        row = self._fetch_one(
            "SELECT (SELECT COUNT(*) FROM market_trip_entries WHERE request_key = %s FOR SHARE) "
            "+ (SELECT COUNT(*) FROM intermediary_holding_documents WHERE request_key = %s FOR SHARE) "
            "+ (SELECT COUNT(*) FROM logo_holding_documents WHERE request_key = %s FOR SHARE) "
            "+ (SELECT COUNT(*) FROM holding_receipts WHERE request_key = %s FOR SHARE) "
            "+ (SELECT COUNT(*) FROM supplier_gold_settlements WHERE request_key = %s FOR SHARE) AS used", (key,) * 5)
        return int(row["used"]) > 0 or marketer_key_in_use(self._fetch_one, key)

    # ------------------------------------------------------------------ writes
    def insert_transfer(self, *, source, recipient, from_weight, to_weight, analytical, difference, transfer_date, reference, notes,
                        key, digest, actor, now, reverses_id=None, reason=None):
        """The GT row. `source` / `recipient` carry id, karat_id and karat_value (read under the lock, or the stored values of the original on a reversal)."""
        require_no_marketer_key(self._fetch_one, key)                    # a marketer document owns this key -> REQUEST_CONFLICT, nothing is written
        new_id, _ = self._execute(
            """
            INSERT INTO supplier_gold_transfers (from_supplier_id, to_supplier_id, from_karat_id, from_karat_value, to_karat_id, to_karat_value,
                from_weight_grams, to_weight_grams, to_analytical_grams, rounding_difference_grams, transfer_date, reference, notes,
                reverses_id, reason, request_key, request_hash, performed_by_user_id, performed_by_employee_id, occurred_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (_id(source["id"]), _id(recipient["id"]), int(source["karat_id"]), int(source["karat_value"]), int(recipient["karat_id"]), int(recipient["karat_value"]),
             from_weight, to_weight, analytical, difference, transfer_date, reference, notes,
             None if reverses_id is None else int(reverses_id), reason, key, digest, actor.user_id, actor.employee_id, now))
        return int(new_id)

    def gold_type_id(self, code):
        row = self._fetch_one("SELECT id FROM supplier_gold_movement_types WHERE code = %s AND is_active = TRUE", (code,))
        return None if row is None else int(row["id"])

    def insert_gold_movement(self, *, supplier_id, type_id, karat_id, weight_delta, eq21_delta, source_id, reference, description, now, user_id):
        new_id, _ = self._execute(
            """
            INSERT INTO supplier_gold_movements (supplier_id, movement_type_id, karat_id, weight_delta_grams, equivalent_21_delta_grams,
                source_type, source_id, source_reference, description, occurred_at, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (_id(supplier_id), int(type_id), int(karat_id), weight_delta, eq21_delta, SOURCE, _id(source_id), reference, description, now, user_id))
        return int(new_id)
