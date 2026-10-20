"""Read-only report reads (packet26). DISPLAY ONLY: plain SELECTs, never a locking clause, never a write, a fixed number of statements per report (no query per row).

The numbers are not computed here: every method returns the stored rows (with a few per-row aggregates that mirror existing screens, e.g. the invoice payments) and the
service does the Decimal arithmetic, the grouping and the paging over the WHOLE filtered result, so a total never depends on the displayed page. A table that does not exist
at the database's schema level is reported through `has_table` (the service shows «غير متاح» instead of an empty result)."""
from repositories.base_repository import BaseRepository
from repositories.invoice_repository import _HISTORY_KIND_SQL

_REVERSAL = ("LEFT JOIN transaction_corrections c ON c.source_type = %s AND c.source_id = {alias}.id AND c.correction_type = 'FULL_REVERSAL'")


def _marks(values):
    return ", ".join(["%s"] * len(values))


class ReportRepository(BaseRepository):
    def has_table(self, name):
        row = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (name,))
        return bool(row and int(row["n"]))

    # ------------------------------------------------------------------ market custody after the trip (settlement steps 2a-2f; read only)
    _TRIP_REVERSAL = "LEFT JOIN transaction_corrections c ON c.source_type = 'MARKET_TRIP_ENTRY' AND c.source_id = e.id AND c.correction_type = 'FULL_REVERSAL'"

    def trip_melts(self, start, end_exclusive):
        """The melts (ST_MELT documents) by their own melt date, with the stored analytical figures and the full-reversal correction of the entry, if any."""
        return self._fetch_all(
            f"""
            SELECT e.id AS entry_id, e.trip_id, e.entry_no, m.melt_date AS doc_date, m.workshop, m.input_weight_grams, m.output_weight_grams, m.fineness_permille,
                   m.theoretical_eq21, m.actual_eq21, m.loss_eq21, m.weight_loss_eq21, m.fineness_loss_eq21, c.id AS reversal_id
            FROM market_trip_settle_melts m JOIN market_trip_entries e ON e.id = m.entry_id {self._TRIP_REVERSAL}
            WHERE m.melt_date >= %s AND m.melt_date < %s ORDER BY m.melt_date, e.id
            """, (start, end_exclusive))

    def trip_settle_lines(self, start, end_exclusive):
        """The lines of the market returns and losses (ST_RET_MKT / ST_LOSS) by the entry date."""
        return self._fetch_all(
            f"""
            SELECT e.id AS entry_id, e.trip_id, e.entry_no, e.entry_kind, e.entry_date AS doc_date, l.line_no, l.source, k.karat_value, l.weight_grams, l.classification, c.id AS reversal_id
            FROM market_trip_settle_lines l JOIN market_trip_entries e ON e.id = l.entry_id LEFT JOIN karats k ON k.id = l.karat_id {self._TRIP_REVERSAL}
            WHERE e.entry_date >= %s AND e.entry_date < %s ORDER BY e.entry_date, e.id, l.line_no
            """, (start, end_exclusive))

    # ------------------------------------------------------------------ sales and returns (documents by their own effective date)
    def sales_invoices(self, start, end_exclusive):
        """Finalized / voided invoices by `invoice_date`. money_in / money_out are the stored payments exactly as Invoice History sums them; account_delta is what the invoice
        wrote to the customer ledger (reference INVOICE); the reversal (RV) is the full reversal correction of the invoice, if any."""
        return self._fetch_all(
            f"""
            SELECT i.id AS id, i.invoice_number AS number, i.invoice_status AS status, i.record_origin AS origin, i.invoice_date AS doc_date, i.customer_id,
                   i.sell_total, i.buy_total, i.net_amount, i.net_direction, i.settlement_status, s.customer_name_snapshot AS customer_name,
                   {_HISTORY_KIND_SQL} AS kind,
                   (SELECT COALESCE(SUM(p.amount), 0) FROM invoice_payments p WHERE p.invoice_id = i.id AND p.direction = 'IN') AS money_in,
                   (SELECT COALESCE(SUM(p.amount), 0) FROM invoice_payments p WHERE p.invoice_id = i.id AND p.direction = 'OUT') AS money_out,
                   (SELECT COALESCE(SUM(ct.amount_delta), 0) FROM customer_transactions ct WHERE ct.reference_type = 'INVOICE' AND ct.reference_id = i.id) AS account_delta,
                   (SELECT COUNT(*) FROM invoice_payments p WHERE p.invoice_id = i.id) AS payment_count,
                   (SELECT COUNT(*) FROM customer_transactions ct WHERE ct.reference_type = 'INVOICE' AND ct.reference_id = i.id) AS account_count,
                   c.id AS reversal_id, c.occurred_at AS reversal_at
            FROM invoices i
            LEFT JOIN invoice_customer_snapshots s ON s.invoice_id = i.id
            {_REVERSAL.format(alias='i')}
            WHERE i.invoice_status <> 'DRAFT' AND i.invoice_date >= %s AND i.invoice_date < %s
            ORDER BY i.invoice_date, i.id
            """, ("INVOICE", start, end_exclusive))

    def sales_returns(self, start, end_exclusive):
        return self._fetch_all(
            f"""
            SELECT r.id AS id, r.source_invoice_id, si.invoice_number AS source_number, r.finalized_at AS doc_date, r.customer_id, s.customer_name_snapshot AS customer_name,
                   r.total_amount, r.gold_amount, r.making_refund, r.money_paid, r.account_amount, c.id AS reversal_id, c.occurred_at AS reversal_at
            FROM invoice_returns r
            JOIN invoices si ON si.id = r.source_invoice_id
            LEFT JOIN invoice_customer_snapshots s ON s.invoice_id = si.id
            {_REVERSAL.format(alias='r')}
            WHERE r.return_status = 'FINALIZED' AND r.finalized_at >= %s AND r.finalized_at < %s
            ORDER BY r.finalized_at, r.id
            """, ("RETURN", start, end_exclusive))

    def sales_exchanges(self, start, end_exclusive):
        return self._fetch_all(
            f"""
            SELECT t.id AS id, t.source_invoice_id, si.invoice_number AS source_number, t.occurred_at AS doc_date, t.customer_id, s.customer_name_snapshot AS customer_name,
                   t.signed_amount, t.money_received, t.money_paid, t.account_signed, c.id AS reversal_id, c.occurred_at AS reversal_at
            FROM invoice_lifecycle_transactions t
            JOIN invoices si ON si.id = t.source_invoice_id
            LEFT JOIN invoice_customer_snapshots s ON s.invoice_id = si.id
            {_REVERSAL.format(alias='t')}
            WHERE t.transaction_type = 'EXCHANGE' AND t.transaction_status = 'FINALIZED' AND t.occurred_at >= %s AND t.occurred_at < %s
            ORDER BY t.occurred_at, t.id
            """, ("EXCHANGE", start, end_exclusive))

    # ------------------------------------------------------------------ available stock (pieces)
    def stock_items(self, statuses):
        """One row per inventory piece in the given statuses with its ACTIVE barcode and the invoice that last used it (a line of a non-draft invoice)."""
        return self._fetch_all(
            f"""
            SELECT ii.id AS item_id, ii.weight_grams, ii.size, ii.stock_classification, ii.inventory_status, ii.acquisition_source, ii.acquired_at,
                   k.id AS karat_id, k.karat_value, it.name AS item_type, cat.name AS category, f.name AS factory,
                   (SELECT b.barcode_value FROM inventory_barcodes b WHERE b.inventory_item_id = ii.id AND b.barcode_status = 'ACTIVE' ORDER BY b.id DESC LIMIT 1) AS barcode,
                   (SELECT il.invoice_id FROM invoice_lines il JOIN invoices iv ON iv.id = il.invoice_id
                     WHERE il.inventory_item_id = ii.id AND iv.invoice_status <> 'DRAFT' ORDER BY iv.invoice_date DESC, iv.id DESC LIMIT 1) AS invoice_id,
                   (SELECT iv.invoice_number FROM invoice_lines il JOIN invoices iv ON iv.id = il.invoice_id
                     WHERE il.inventory_item_id = ii.id AND iv.invoice_status <> 'DRAFT' ORDER BY iv.invoice_date DESC, iv.id DESC LIMIT 1) AS invoice_number,
                   (SELECT il.line_type FROM invoice_lines il JOIN invoices iv ON iv.id = il.invoice_id
                     WHERE il.inventory_item_id = ii.id AND iv.invoice_status <> 'DRAFT' ORDER BY iv.invoice_date DESC, iv.id DESC LIMIT 1) AS invoice_line_type
            FROM inventory_items ii
            JOIN karats k ON k.id = ii.karat_id
            LEFT JOIN item_types it ON it.id = ii.item_type_id
            LEFT JOIN item_categories cat ON cat.id = ii.category_id
            LEFT JOIN factories f ON f.id = ii.factory_id
            WHERE ii.inventory_status IN ({_marks(statuses)})
            ORDER BY k.karat_value DESC, ii.id
            """, tuple(statuses))

    # ------------------------------------------------------------------ the physical gold movement report (sales of pieces by type, customer scrap purchases)
    def gold_sold_pieces(self, start, end_exclusive):
        """One row per SELL_INVENTORY line of a FINALIZED invoice dated in the period, with the piece's type / category / factory / karat / weight and the full-reversal correction of
        the invoice, if any. Plain SELECT: the service filters, groups and totals the whole result."""
        return self._fetch_all(
            """
            SELECT ii.id AS item_id, it.name AS item_type, cat.name AS category, f.id AS factory_id, f.name AS factory, k.karat_value, ii.weight_grams,
                   iv.id AS invoice_id, iv.invoice_number, iv.invoice_date AS doc_date, c.id AS reversal_id
            FROM invoice_lines il
            JOIN invoices iv ON iv.id = il.invoice_id
            JOIN inventory_items ii ON ii.id = il.inventory_item_id
            JOIN karats k ON k.id = ii.karat_id
            LEFT JOIN item_types it ON it.id = ii.item_type_id
            LEFT JOIN item_categories cat ON cat.id = ii.category_id
            LEFT JOIN factories f ON f.id = ii.factory_id
            LEFT JOIN transaction_corrections c ON c.source_type = 'INVOICE' AND c.source_id = iv.id AND c.correction_type = 'FULL_REVERSAL'
            WHERE il.line_type = 'SELL_INVENTORY' AND iv.invoice_status = 'FINALIZED' AND iv.invoice_date >= %s AND iv.invoice_date < %s
            ORDER BY iv.invoice_date, iv.id, il.id
            """, (start, end_exclusive))

    def scrap_purchases(self, start, end_exclusive):
        """COUNTED customer scrap purchases (and their reversals) per karat in the period: the number of purchase movements and the signed grams."""
        return self._fetch_all(
            """
            SELECT k.karat_value, m.movement_code, COUNT(*) AS n, COALESCE(SUM(m.weight_delta_grams), 0) AS grams
            FROM old_gold_movements m JOIN karats k ON k.id = m.karat_id
            WHERE m.balance_effect = 'COUNTED' AND m.weight_delta_grams IS NOT NULL AND m.movement_code IN ('CUSTOMER_BUY_IN', 'CUSTOMER_BUY_IN_REVERSAL')
              AND m.occurred_at >= %s AND m.occurred_at < %s
            GROUP BY k.karat_value, m.movement_code
            """, (start, end_exclusive))

    def factories(self):
        return self._fetch_all("SELECT id, name FROM factories ORDER BY name, id")

    # ------------------------------------------------------------------ gold movements (each source is its own ledger)
    def scrap_opening(self, start):
        """COUNTED Scrap per karat strictly before `start` (reference-only rows are not balance)."""
        return self._fetch_all(
            "SELECT m.karat_id, k.karat_value, COALESCE(SUM(m.weight_delta_grams), 0) AS grams FROM old_gold_movements m JOIN karats k ON k.id = m.karat_id "
            "WHERE m.balance_effect = 'COUNTED' AND m.weight_delta_grams IS NOT NULL AND m.occurred_at < %s GROUP BY m.karat_id, k.karat_value", (start,))

    def scrap_movements(self, start, end_exclusive):
        return self._fetch_all(
            """
            SELECT m.id, m.karat_id, k.karat_value, m.movement_code, m.weight_delta_grams AS grams, m.balance_effect, m.reference_type, m.reference_id, m.reference_code,
                   m.invoice_id, m.customer_id, m.occurred_at
            FROM old_gold_movements m JOIN karats k ON k.id = m.karat_id
            WHERE m.occurred_at >= %s AND m.occurred_at < %s ORDER BY m.occurred_at, m.id
            """, (start, end_exclusive))

    def piece_movements(self, start, end_exclusive):
        return self._fetch_all(
            """
            SELECT im.id, im.inventory_item_id AS item_id, t.code AS type_code, t.name AS type_name, im.from_inventory_status, im.to_inventory_status,
                   im.from_weight_grams, im.to_weight_grams, k.karat_value, im.reference_type, im.reference_id, im.reference_code, im.occurred_at
            FROM inventory_movements im
            JOIN inventory_movement_types t ON t.id = im.movement_type_id
            JOIN inventory_items ii ON ii.id = im.inventory_item_id
            JOIN karats k ON k.id = ii.karat_id
            WHERE im.occurred_at >= %s AND im.occurred_at < %s ORDER BY im.occurred_at, im.id
            """, (start, end_exclusive))

    def assayed_opening(self, start):
        row = self._fetch_one("SELECT COALESCE(SUM(grams_delta), 0) AS grams, COALESCE(SUM(eq21_posted_delta), 0) AS posted FROM shop_assayed_stock_movements WHERE created_at < %s", (start,))
        return row or {"grams": 0, "posted": 0}

    def assayed_movements(self, start, end_exclusive):
        return self._fetch_all(
            """
            SELECT s.id, s.item_entry_id, s.item_line_no, s.movement_type, s.grams_delta, s.eq21_posted_delta, s.source_entry_id, s.correction_id, s.created_at,
                   l.fineness_permille, e.trip_id, e.entry_no
            FROM shop_assayed_stock_movements s
            JOIN market_trip_book_lines l ON l.entry_id = s.item_entry_id AND l.line_no = s.item_line_no
            JOIN market_trip_entries e ON e.id = s.source_entry_id
            WHERE s.created_at >= %s AND s.created_at < %s ORDER BY s.created_at, s.id
            """, (start, end_exclusive))

    def book_groups_current(self):
        """The CURRENT effective BOOK source of every trip, grouped (the Gold Position's own display read: one aggregate query, snapshot reversal lookup, no lock)."""
        from repositories.market_trip_repository import MarketTripRepository
        return MarketTripRepository(self.connection).book_groups_by_trip()

    def book_effective_reconciliations(self):
        """The effective (not reversed) BK_RECON entries with their stored record - the Gold Position's own display read (a database before 057 returns entries with NULL record
        fields, which the merge refuses; it never reads them as zero)."""
        from repositories.market_trip_repository import MarketTripRepository
        return MarketTripRepository(self.connection).effective_reconciliations_display()

    def book_recon_documents(self, start, end_exclusive):
        """Every BK_RECON document dated inside the period, reversed ones too, with its record figures and its CURRENT reversal (display, no lock)."""
        exists = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_trip_reconciliations'")
        if exists and int(exists["n"]):
            record, join = "r.b_star, r.loss_posted, r.rounding_settlement", "LEFT JOIN market_trip_reconciliations r ON r.entry_id = e.id"
        else:
            record, join = "NULL AS b_star, NULL AS loss_posted, NULL AS rounding_settlement", ""
        return self._fetch_all(
            f"""
            SELECT e.id AS entry_id, e.trip_id, e.entry_no, e.entry_date, {record}, c.id AS reversal_id
            FROM market_trip_entries e {join}
            LEFT JOIN transaction_corrections c ON c.source_type = 'MARKET_TRIP_ENTRY' AND c.source_id = e.id AND c.correction_type = 'FULL_REVERSAL'
            WHERE e.entry_kind = 'BK_RECON' AND e.entry_date >= %s AND e.entry_date < %s ORDER BY e.entry_date, e.id
            """, (start, end_exclusive))

    def book_lines(self, end_exclusive):
        """Every BOOK line of entries dated before `end_exclusive` (opening / period / closing are derived in the service) with the reversal of its entry, if any."""
        return self._fetch_all(
            """
            SELECT l.entry_id, l.line_no, l.trip_id, e.entry_no, e.entry_kind, e.entry_date, e.occurred_at, l.direction, l.weight_grams, l.basis, l.karat_value,
                   l.fineness_permille, l.eq21_posted, l.signed_eq21_posted, c.id AS reversal_id
            FROM market_trip_book_lines l
            JOIN market_trip_entries e ON e.id = l.entry_id
            LEFT JOIN transaction_corrections c ON c.source_type = 'MARKET_TRIP_ENTRY' AND c.source_id = e.id AND c.correction_type = 'FULL_REVERSAL'
            WHERE e.entry_date < %s ORDER BY e.entry_date, l.entry_id, l.line_no
            """, (end_exclusive,))

    def marketer_gold(self, end_exclusive):
        """Every marketer 21K gold movement created before `end_exclusive` (the entitlement ledger; opening and period are derived in the service)."""
        legs = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'marketer_gold_movements' AND COLUMN_NAME = 'trip_entry_id'")
        if legs and int(legs["n"]):                                      # 058 added the BOOK trip legs; before it a leg cannot exist
            return self._fetch_all(
                """
                SELECT m.id, m.party_id, s.name AS party_name, m.movement_type, m.weight_delta, m.doc_kind, m.document_id, m.settlement_id, m.trip_entry_id, m.trip_entry_kind,
                       m.correction_id, m.created_at, te.trip_id AS trip_id, te.entry_no AS trip_entry_no
                FROM marketer_gold_movements m
                JOIN suppliers s ON s.id = m.party_id
                LEFT JOIN market_trip_entries te ON te.id = m.trip_entry_id
                WHERE m.created_at < %s ORDER BY m.created_at, m.id
                """, (end_exclusive,))
        return self._fetch_all(
            """
            SELECT m.id, m.party_id, s.name AS party_name, m.movement_type, m.weight_delta, m.doc_kind, m.document_id, m.settlement_id, NULL AS trip_entry_id, NULL AS trip_entry_kind,
                   m.correction_id, m.created_at, NULL AS trip_id, NULL AS trip_entry_no
            FROM marketer_gold_movements m JOIN suppliers s ON s.id = m.party_id
            WHERE m.created_at < %s ORDER BY m.created_at, m.id
            """, (end_exclusive,))

    # ------------------------------------------------------------------ customers
    def customer_rows(self):
        return self._fetch_all(
            "SELECT c.id, c.customer_code, c.name, c.mobile, c.is_active, "
            "COALESCE((SELECT SUM(t.amount_delta) FROM customer_transactions t WHERE t.customer_id = c.id), 0) AS balance FROM customers c ORDER BY c.name, c.id")

    def customer_before(self, start):
        return self._fetch_all("SELECT customer_id, COALESCE(SUM(amount_delta), 0) AS total FROM customer_transactions WHERE occurred_at < %s GROUP BY customer_id", (start,))

    def customer_period(self, start, end_exclusive):
        """Period movements grouped by customer and by the document kind that wrote them (reference_type): the SAME ledger the statement reads."""
        return self._fetch_all(
            """
            SELECT customer_id, COALESCE(reference_type, 'UNKNOWN') AS reference_type, COUNT(*) AS n,
                   COALESCE(SUM(CASE WHEN amount_delta > 0 THEN amount_delta ELSE 0 END), 0) AS owed_up,
                   COALESCE(SUM(CASE WHEN amount_delta < 0 THEN amount_delta ELSE 0 END), 0) AS owed_down
            FROM customer_transactions WHERE occurred_at >= %s AND occurred_at < %s GROUP BY customer_id, COALESCE(reference_type, 'UNKNOWN')
            """, (start, end_exclusive))

    # ------------------------------------------------------------------ suppliers
    def supplier_rows(self):
        return self._fetch_all(
            "SELECT s.id, s.name, s.phone, s.is_active, s.is_supplier, s.is_intermediary, s.karat_id, k.karat_value FROM suppliers s LEFT JOIN karats k ON k.id = s.karat_id "
            "WHERE s.is_supplier ORDER BY s.name, s.id")

    def supplier_gold_before(self, start):
        return self._fetch_all("SELECT supplier_id, karat_id, COALESCE(SUM(weight_delta_grams), 0) AS grams FROM supplier_gold_movements WHERE occurred_at < %s GROUP BY supplier_id, karat_id", (start,))

    def supplier_gold_movements(self, start, end_exclusive):
        return self._fetch_all(
            """
            SELECT m.id, m.supplier_id, m.karat_id, k.karat_value, t.code AS type_code, t.name AS type_name, m.weight_delta_grams AS grams, m.source_type, m.source_id,
                   m.source_reference, m.description, m.occurred_at
            FROM supplier_gold_movements m JOIN supplier_gold_movement_types t ON t.id = m.movement_type_id JOIN karats k ON k.id = m.karat_id
            WHERE m.occurred_at >= %s AND m.occurred_at < %s ORDER BY m.occurred_at, m.id
            """, (start, end_exclusive))

    def supplier_making_before(self, start):
        return self._fetch_all("SELECT supplier_id, COALESCE(SUM(amount_delta), 0) AS amount FROM supplier_making_movements WHERE occurred_at < %s GROUP BY supplier_id", (start,))

    def supplier_making_movements(self, start, end_exclusive):
        return self._fetch_all(
            """
            SELECT m.id, m.supplier_id, t.code AS type_code, t.name AS type_name, m.amount_delta AS amount, m.source_type, m.source_id, m.source_reference, m.description, m.occurred_at
            FROM supplier_making_movements m JOIN supplier_making_movement_types t ON t.id = m.movement_type_id
            WHERE m.occurred_at >= %s AND m.occurred_at < %s ORDER BY m.occurred_at, m.id
            """, (start, end_exclusive))

    # ------------------------------------------------------------------ treasury (cash and bank accounts)
    def cash_accounts(self):
        return self._fetch_all("SELECT id, code, name, is_active FROM cash_accounts ORDER BY id")

    def bank_accounts(self):
        return self._fetch_all("SELECT id, code, name, bank_name, account_type, is_active FROM bank_accounts ORDER BY id")

    def cash_before(self, start):
        return self._fetch_all("SELECT cash_account_id AS account_id, COALESCE(SUM(amount_delta), 0) AS total FROM cash_movements WHERE occurred_at < %s GROUP BY cash_account_id", (start,))

    def bank_before(self, start):
        return self._fetch_all("SELECT bank_account_id AS account_id, COALESCE(SUM(amount_delta), 0) AS total FROM bank_movements WHERE occurred_at < %s GROUP BY bank_account_id", (start,))

    def cash_movements(self, start, end_exclusive):
        return self._fetch_all(
            """
            SELECT m.id, m.cash_account_id AS account_id, t.code AS type_code, t.name AS type_name, m.amount_delta AS amount, m.transfer_group_id, m.reference_type, m.reference_id,
                   m.reference_code, m.description, m.occurred_at
            FROM cash_movements m JOIN cash_movement_types t ON t.id = m.movement_type_id
            WHERE m.occurred_at >= %s AND m.occurred_at < %s ORDER BY m.occurred_at, m.id
            """, (start, end_exclusive))

    def bank_movements(self, start, end_exclusive):
        return self._fetch_all(
            """
            SELECT m.id, m.bank_account_id AS account_id, t.code AS type_code, t.name AS type_name, m.amount_delta AS amount, m.transfer_group_id, m.reference_type, m.reference_id,
                   m.reference_code, m.description, m.occurred_at
            FROM bank_movements m JOIN bank_movement_types t ON t.id = m.movement_type_id
            WHERE m.occurred_at >= %s AND m.occurred_at < %s ORDER BY m.occurred_at, m.id
            """, (start, end_exclusive))
