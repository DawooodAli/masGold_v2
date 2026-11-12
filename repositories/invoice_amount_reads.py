"""Read-only reads for the corrected invoice amount (services/invoice_amount_rules.py). Plain SELECTs, no lock, a fixed number of statements: every settlement correction of an
invoice (they are few) and the lines of the invoices whose amount changed."""


class InvoiceAmountReads:
    def invoice_settlement_corrections(self):
        """Every SETTLEMENT_CORRECTION (FC) of source INVOICE with the settlement before it and what it posted (signed money, signed customer-account delta). ONE statement."""
        return self._fetch_all(
            """
            SELECT c.id, c.source_id AS invoice_id, c.chain_sequence, c.money_direction, c.settled_total, c.before_json,
                   (SELECT COALESCE(SUM(l.amount), 0) FROM transaction_correction_legs l
                     WHERE l.correction_id = c.id AND l.leg_role = 'POSTED' AND l.leg_kind = 'MONEY') AS money_posted,
                   (SELECT COALESCE(SUM(l.amount), 0) FROM transaction_correction_legs l
                     WHERE l.correction_id = c.id AND l.leg_role = 'POSTED' AND l.leg_kind = 'ACCOUNT') AS account_posted
            FROM transaction_corrections c
            WHERE c.correction_type = 'SETTLEMENT_CORRECTION' AND c.source_type = 'INVOICE'
            ORDER BY c.source_id, c.chain_sequence
            """)

    def lines_of_invoices(self, invoice_ids):
        """Every line of the given invoices with what the home page groups by (karat, item type, the invoice's day, full reversal). ONE statement."""
        ids = [int(i) for i in invoice_ids or []]
        if not ids:
            return []
        marks = ", ".join(["%s"] * len(ids))
        return self._fetch_all(
            f"""
            SELECT il.id AS line_id, il.invoice_id, il.line_type, il.weight_grams, il.making_total, k.karat_value, it.name AS item_type,
                   iv.invoice_status, iv.finalized_at, iv.invoice_date,
                   EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type = 'INVOICE' AND c.source_id = iv.id
                           AND c.correction_type = 'FULL_REVERSAL') AS reversed
            FROM invoice_lines il
            JOIN invoices iv ON iv.id = il.invoice_id
            LEFT JOIN karats k ON k.id = il.karat_id
            LEFT JOIN inventory_items ii ON ii.id = il.inventory_item_id
            LEFT JOIN item_types it ON it.id = ii.item_type_id
            WHERE il.invoice_id IN ({marks})
            ORDER BY il.invoice_id, il.id
            """, tuple(ids))
