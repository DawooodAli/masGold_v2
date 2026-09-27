"""الرئيسية: read-only aggregates of TODAY from the authoritative tables (no writes, no caching).

"Today" is the database's own calendar day (CURDATE()): the invoice service already refuses
to finalize unless the database clock is Cairo time, and finalized_at is that clock's NOW().
Only FINALIZED invoices count. Old gold uses the COUNTED old_gold_movements ledger, exactly
as the MainWindow strip does (REFERENCE_ONLY history excluded).
"""
from repositories.base_repository import BaseRepository

SALE_LINE_TYPES = ("SELL_INVENTORY", "SELL_UNIDENTIFIED", "SELL_OLD_GOLD")
_TODAY = "i.finalized_at >= CURDATE() AND i.finalized_at < CURDATE() + INTERVAL 1 DAY"


class DashboardRepository(BaseRepository):
    def today_invoices(self):
        """Invoice count and commercial totals of today's finalized invoices, plus the manual
        (unidentified) sale lines among them."""
        return self._fetch_one(
            f"""
            SELECT COUNT(*) AS invoice_count,
                   COALESCE(SUM(i.sell_total), 0) AS sell_total,
                   COALESCE(SUM(i.buy_total), 0) AS buy_total,
                   COALESCE(SUM(CASE WHEN i.sell_total > 0 THEN 1 ELSE 0 END), 0) AS sale_invoice_count,
                   COALESCE(SUM(CASE WHEN i.buy_total > 0 THEN 1 ELSE 0 END), 0) AS buy_invoice_count,
                   (SELECT COUNT(*) FROM invoice_lines l JOIN invoices i ON i.id = l.invoice_id
                    WHERE i.invoice_status = 'FINALIZED' AND {_TODAY}
                      AND l.line_type = 'SELL_UNIDENTIFIED') AS unidentified_lines
            FROM invoices i
            WHERE i.invoice_status = 'FINALIZED' AND {_TODAY}
            """)

    def today_sold_by_karat(self):
        """Grams and pieces SOLD today per karat (all sale line types, incl. sales from old-gold stock)."""
        return self._fetch_all(
            f"""
            SELECT k.karat_value, COALESCE(SUM(l.weight_grams), 0) AS grams, COUNT(*) AS pieces,
                   COALESCE(SUM(CASE WHEN l.line_type = 'SELL_OLD_GOLD' THEN l.weight_grams END), 0)
                       AS from_old_gold_grams
            FROM invoice_lines l
            JOIN invoices i ON i.id = l.invoice_id
            JOIN karats k ON k.id = l.karat_id
            WHERE i.invoice_status = 'FINALIZED' AND {_TODAY}
              AND l.line_type IN ('SELL_INVENTORY', 'SELL_UNIDENTIFIED', 'SELL_OLD_GOLD')
            GROUP BY k.karat_value
            ORDER BY k.karat_value
            """)

    def today_old_gold_flow(self):
        """COUNTED old-gold grams that came IN (+) and went OUT (−) today, per karat."""
        return self._fetch_all(
            """
            SELECT k.karat_value,
                   COALESCE(SUM(CASE WHEN og.weight_delta_grams > 0 THEN og.weight_delta_grams END), 0) AS grams_in,
                   COALESCE(-SUM(CASE WHEN og.weight_delta_grams < 0 THEN og.weight_delta_grams END), 0) AS grams_out
            FROM old_gold_movements og
            JOIN karats k ON k.id = og.karat_id
            WHERE og.balance_effect = 'COUNTED' AND og.weight_delta_grams IS NOT NULL
              AND og.occurred_at >= CURDATE() AND og.occurred_at < CURDATE() + INTERVAL 1 DAY
            GROUP BY k.karat_value
            ORDER BY k.karat_value
            """)

    def recent_invoices(self, limit=5):
        """The latest finalized invoices (a glance, not Invoice History)."""
        return self._fetch_all(
            """
            SELECT i.invoice_number, i.finalized_at, i.net_amount, i.net_direction,
                   s.customer_name_snapshot, e.name AS employee_name
            FROM invoices i
            LEFT JOIN invoice_customer_snapshots s ON s.invoice_id = i.id
            LEFT JOIN employees e ON e.id = i.employee_id
            WHERE i.invoice_status = 'FINALIZED'
            ORDER BY i.finalized_at DESC, i.id DESC
            LIMIT %s
            """, (int(limit),))
