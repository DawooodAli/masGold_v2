"""الرئيسية: read-only aggregates of TODAY from the authoritative tables (no writes, no caching).

"Today" is the database's own calendar day (CURDATE()): the invoice service already refuses
to finalize unless the database clock is Cairo time, and invoice_date (the day of the invoice, the same base as the reports) is that clock's NOW() at finalization.
Only FINALIZED invoices count. Old gold uses the COUNTED old_gold_movements ledger, exactly
as the MainWindow strip does (REFERENCE_ONLY history excluded).
"""
from mysql.connector import ProgrammingError

from repositories.base_repository import BaseRepository
from repositories.invoice_amount_reads import InvoiceAmountReads
from repositories.price_series_reads import PriceSeriesReads

SALE_LINE_TYPES = ("SELL_INVENTORY", "SELL_UNIDENTIFIED", "SELL_OLD_GOLD", "SELL_RAFAYEA")
_TODAY = "i.invoice_date >= CURDATE() AND i.invoice_date < CURDATE() + INTERVAL 1 DAY"


class DashboardRepository(InvoiceAmountReads, PriceSeriesReads, BaseRepository):
    def today_invoices(self):
        """Invoice count and commercial totals of today's finalized invoices, plus the manual
        (unidentified) sale lines among them. Owner 2026-11-19: a scrap-only invoice that was closed for less (or more) than its table value by a settlement adjustment counts at
        what was actually settled: buy_total + the adjustment's signed amount (R = N - P is negative when the shop paid less: 11,460 table - 60 = 11,400 paid)."""
        return self._fetch_one(
            f"""
            SELECT COUNT(*) AS invoice_count,
                   COALESCE(SUM(i.sell_total), 0) AS sell_total,
                   COALESCE(SUM(i.buy_total + CASE WHEN i.sell_total = 0 AND i.buy_total > 0 THEN COALESCE(a.amount_signed, 0) ELSE 0 END), 0) AS buy_total,
                   COALESCE(SUM(CASE WHEN i.sell_total > 0 THEN 1 ELSE 0 END), 0) AS sale_invoice_count,
                   COALESCE(SUM(CASE WHEN i.buy_total > 0 THEN 1 ELSE 0 END), 0) AS buy_invoice_count,
                   (SELECT COUNT(*) FROM invoice_lines l JOIN invoices i ON i.id = l.invoice_id
                    WHERE i.invoice_status = 'FINALIZED' AND {_TODAY}
                      AND l.line_type = 'SELL_UNIDENTIFIED') AS unidentified_lines
            FROM invoices i
            LEFT JOIN invoice_settlement_adjustments a ON a.invoice_id = i.id
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
              AND l.line_type IN ('SELL_INVENTORY', 'SELL_UNIDENTIFIED', 'SELL_OLD_GOLD', 'SELL_RAFAYEA')
            GROUP BY k.karat_value
            ORDER BY k.karat_value
            """)

    def month_sold_by_karat(self, previous=False):
        """Grams and pieces SOLD from the first day of the month through today (previous=False) or over the SAME number of days of the SAME month LAST YEAR (previous=True), per karat. Fully reversed
        invoices are left out. "Today" and the month are the database's own calendar (CURDATE())."""
        first = "DATE_FORMAT(CURDATE() - INTERVAL 1 YEAR, '%Y-%m-01')" if previous else "DATE_FORMAT(CURDATE(), '%Y-%m-01')"
        end = f"LEAST({first} + INTERVAL DAY(CURDATE()) DAY, {first} + INTERVAL 1 MONTH)" if previous else "CURDATE() + INTERVAL 1 DAY"
        return self._fetch_all(
            f"""
            SELECT k.karat_value, COALESCE(SUM(l.weight_grams), 0) AS grams, COUNT(*) AS pieces
            FROM invoice_lines l
            JOIN invoices i ON i.id = l.invoice_id
            JOIN karats k ON k.id = l.karat_id
            WHERE i.invoice_status = 'FINALIZED' AND i.invoice_date >= {first} AND i.invoice_date < {end}
              AND l.line_type IN ('SELL_INVENTORY', 'SELL_UNIDENTIFIED', 'SELL_OLD_GOLD', 'SELL_RAFAYEA')
              AND NOT EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type = 'INVOICE' AND c.source_id = i.id AND c.correction_type = 'FULL_REVERSAL')
            GROUP BY k.karat_value
            ORDER BY k.karat_value
            """)

    def sales_by_day(self, days=14):
        """The sale value (EGP, invoice sell_total) of each of the last `days` days including today, fully reversed invoices left out. Days without a sale are simply absent."""
        return self._fetch_all(
            """
            SELECT DATE(i.invoice_date) AS day, COALESCE(SUM(i.sell_total), 0) AS total
            FROM invoices i
            WHERE i.invoice_status = 'FINALIZED' AND i.sell_total > 0
              AND i.invoice_date >= CURDATE() - INTERVAL %s DAY AND i.invoice_date < CURDATE() + INTERVAL 1 DAY
              AND NOT EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type = 'INVOICE' AND c.source_id = i.id AND c.correction_type = 'FULL_REVERSAL')
            GROUP BY DATE(i.invoice_date)
            ORDER BY day
            """, (int(days) - 1,))

    def scrap_sold_by_day(self, days=14):
        """Per day of the last `days` days including today AND per karat: the grams of scrap BOUGHT from customers (BUY_OLD_GOLD lines) and the grams of gold SOLD (the sale line types of the
        dashboard: pieces, manual, scrap and Rafayea) of FINALIZED invoices, fully reversed invoices left out. Same day base as sales_by_day (invoice_date). Days without a line are absent."""
        return self._fetch_all(
            """
            SELECT DATE(i.invoice_date) AS day, k.karat_value,
                   COALESCE(SUM(CASE WHEN l.line_type = 'BUY_OLD_GOLD' THEN l.weight_grams END), 0) AS bought,
                   COALESCE(SUM(CASE WHEN l.line_type IN ('SELL_INVENTORY', 'SELL_UNIDENTIFIED', 'SELL_OLD_GOLD', 'SELL_RAFAYEA') THEN l.weight_grams END), 0) AS sold
            FROM invoice_lines l
            JOIN invoices i ON i.id = l.invoice_id
            JOIN karats k ON k.id = l.karat_id
            WHERE i.invoice_status = 'FINALIZED'
              AND l.line_type IN ('BUY_OLD_GOLD', 'SELL_INVENTORY', 'SELL_UNIDENTIFIED', 'SELL_OLD_GOLD', 'SELL_RAFAYEA')
              AND i.invoice_date >= CURDATE() - INTERVAL %s DAY AND i.invoice_date < CURDATE() + INTERVAL 1 DAY
              AND NOT EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type = 'INVOICE' AND c.source_id = i.id AND c.correction_type = 'FULL_REVERSAL')
            GROUP BY DATE(i.invoice_date), k.karat_value
            ORDER BY day, k.karat_value
            """, (int(days) - 1,))

    def month_start(self):
        return self._fetch_one("SELECT DATE_FORMAT(CURDATE(), '%Y-%m-01') AS first, DAY(CURDATE()) AS day, CURDATE() AS today")

    def below_min_levels(self):
        """The owner's minimum stock rules (migration 072) with what is in stock now for their type and karat, or None below 072."""
        try:
            return self._fetch_all(
                """
                SELECT t.name AS item_type, k.karat_value, l.min_pieces, l.min_grams, COALESCE(s.pieces, 0) AS pieces, COALESCE(s.grams, 0) AS grams
                FROM stock_min_levels l
                JOIN item_types t ON t.id = l.item_type_id
                JOIN karats k ON k.id = l.karat_id
                LEFT JOIN (SELECT item_type_id, karat_id, COUNT(*) AS pieces, SUM(weight_grams) AS grams FROM inventory_items WHERE inventory_status = 'IN_STOCK' GROUP BY item_type_id, karat_id) s
                       ON s.item_type_id = l.item_type_id AND s.karat_id = l.karat_id
                WHERE l.is_active = TRUE ORDER BY t.name, k.karat_value DESC
                """)
        except ProgrammingError as exc:
            if getattr(exc, "errno", None) == 1146:
                return None
            raise

    def today_making_groups(self):
        """Today's sale lines of FINALIZED, not fully reversed invoices in three groups - BULLION (item type سبيكة / جنية), SCRAP (sold from old-gold stock) and PIECES (every other piece):
        total grams, grams whose making is KNOWN and the making charged on them (a line with unknown making never counts as zero)."""
        return self._fetch_all(
            f"""
            SELECT CASE WHEN l.line_type = 'SELL_OLD_GOLD' THEN 'SCRAP'
                        WHEN it.name IN ('سبيكة', 'جنية', 'جنيه') THEN 'BULLION' ELSE 'PIECES' END AS grp,
                   COALESCE(SUM(l.weight_grams), 0) AS grams,
                   COALESCE(SUM(CASE WHEN l.making_total IS NOT NULL THEN l.weight_grams END), 0) AS known_grams,
                   COALESCE(SUM(l.making_total), 0) AS making
            FROM invoice_lines l
            JOIN invoices i ON i.id = l.invoice_id
            LEFT JOIN inventory_items ii ON ii.id = l.inventory_item_id
            LEFT JOIN item_types it ON it.id = ii.item_type_id
            WHERE i.invoice_status = 'FINALIZED' AND {_TODAY}
              AND l.line_type IN ('SELL_INVENTORY', 'SELL_UNIDENTIFIED', 'SELL_OLD_GOLD', 'SELL_RAFAYEA')
              AND NOT EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type = 'INVOICE' AND c.source_id = i.id AND c.correction_type = 'FULL_REVERSAL')
            GROUP BY grp
            """)

    def today_scrap_discount(self):
        """Today's scrap PURCHASE lines (BUY_OLD_GOLD) of FINALIZED, not fully reversed invoices: the gross value (before the discount) and the discount given, so the average discount percent is
        discount / gross. Plain read; a day without a purchase is (0, 0)."""
        return self._fetch_one(
            f"""
            SELECT COALESCE(SUM(l.gross_total), 0) AS gross, COALESCE(SUM(l.discount_amount), 0) AS discount
            FROM invoice_lines l
            JOIN invoices i ON i.id = l.invoice_id
            WHERE i.invoice_status = 'FINALIZED' AND {_TODAY} AND l.line_type = 'BUY_OLD_GOLD'
              AND NOT EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type = 'INVOICE' AND c.source_id = i.id AND c.correction_type = 'FULL_REVERSAL')
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

    def month_top_types(self, limit=3):
        """The item types sold the most so far this month (SELL_INVENTORY lines of FINALIZED, not fully reversed invoices): pieces, grams and line value per type, best first.
        Same window as month_sold_by_karat (invoice_date, from the first day of the month through today)."""
        return self._fetch_all(
            """
            SELECT COALESCE(it.name, 'بدون صنف') AS item_type, COUNT(*) AS pieces,
                   COALESCE(SUM(il.weight_grams), 0) AS grams, COALESCE(SUM(il.line_total), 0) AS value
            FROM invoice_lines il
            JOIN invoices iv ON iv.id = il.invoice_id
            LEFT JOIN inventory_items ii ON ii.id = il.inventory_item_id
            LEFT JOIN item_types it ON it.id = ii.item_type_id
            WHERE il.line_type = 'SELL_INVENTORY' AND iv.invoice_status = 'FINALIZED'
              AND iv.invoice_date >= DATE_FORMAT(CURDATE(), '%Y-%m-01') AND iv.invoice_date < CURDATE() + INTERVAL 1 DAY
              AND NOT EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type = 'INVOICE' AND c.source_id = iv.id AND c.correction_type = 'FULL_REVERSAL')
            GROUP BY COALESCE(it.name, 'بدون صنف')
            ORDER BY value DESC, grams DESC
            LIMIT %s
            """, (int(limit),))

    def today_scrap_purchases(self):
        """Today's scrap bought from customers (BUY_OLD_GOLD lines of FINALIZED, not fully reversed invoices), per karat: grams and the value paid (line total, before the invoice's final EGP-5 rounding)."""
        return self._fetch_all(
            f"""
            SELECT k.karat_value, COALESCE(SUM(l.weight_grams), 0) AS grams, COALESCE(SUM(l.line_total), 0) AS paid
            FROM invoice_lines l
            JOIN invoices i ON i.id = l.invoice_id
            JOIN karats k ON k.id = l.karat_id
            WHERE i.invoice_status = 'FINALIZED' AND {_TODAY} AND l.line_type = 'BUY_OLD_GOLD'
              AND NOT EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type = 'INVOICE' AND c.source_id = i.id AND c.correction_type = 'FULL_REVERSAL')
            GROUP BY k.karat_value
            ORDER BY k.karat_value
            """)

    def dead_stock_by_karat(self, days):
        """DEAD STOCK per karat: pieces IN_STOCK acquired `days` days ago or earlier that were never on a non-draft invoice (the same rule as the inventory-alerts report): pieces, grams, oldest age in days."""
        return self._fetch_all(
            """
            SELECT k.karat_value, COUNT(*) AS pieces, COALESCE(SUM(ii.weight_grams), 0) AS grams, MAX(DATEDIFF(CURDATE(), DATE(ii.acquired_at))) AS oldest_days
            FROM inventory_items ii
            JOIN karats k ON k.id = ii.karat_id
            WHERE ii.inventory_status = 'IN_STOCK' AND ii.acquired_at IS NOT NULL AND DATE(ii.acquired_at) <= CURDATE() - INTERVAL %s DAY
              AND NOT EXISTS (SELECT 1 FROM invoice_lines il JOIN invoices iv ON iv.id = il.invoice_id
                              WHERE il.inventory_item_id = ii.id AND iv.invoice_status <> 'DRAFT')
            GROUP BY k.karat_value
            ORDER BY k.karat_value
            """, (int(days),))

    def latest_shop_buy_prices(self):
        """The latest ACCEPTED stored SHOP_BUY price of every karat (the same source as the scrap-cost report): (karat_value, buy)."""
        return self._fetch_all(
            """
            SELECT k.karat_value, q.shop_buy_price_per_gram AS buy
            FROM gold_price_quotes q
            JOIN karats k ON k.id = q.karat_id
            JOIN (SELECT karat_id, MAX(id) AS last_id FROM gold_price_quotes
                  WHERE validation_status = 'ACCEPTED' AND shop_buy_price_per_gram IS NOT NULL GROUP BY karat_id) m ON m.last_id = q.id
            ORDER BY k.karat_value
            """)

    def db_today(self):
        return self._fetch_one("SELECT CURDATE() AS today")["today"]

    def price_daily_21(self):
        """The last accepted quote of each day for karat 21 (the shared series of the price-indicators report): (day, buy, sell), ascending."""
        return [{"day": r["day"], "buy": r["buy"], "sell": r["sell"]} for r in self.gold_price_daily() if int(r["karat_value"]) == 21]

    def price_history_legacy_21(self):
        """The imported daily 21K prices of the old shop (migration 066), or None below 066."""
        try:
            return [{"day": r["day"], "buy": r["buy"], "sell": r["sell"]} for r in self.gold_price_history_legacy() if int(r["karat_value"]) == 21]
        except ProgrammingError as exc:
            if getattr(exc, "errno", None) == 1146:
                return None
            raise

    def customer_balance_totals(self):
        """What customers owe the shop and what the shop owes customers, over EVERY customer whose ledger-derived balance (vw_customer_balances) is not settled:
        the sum and the count of the positive balances (owed) and of the negative balances (credit, as a positive number)."""
        return self._fetch_one(
            """
            SELECT COALESCE(SUM(CASE WHEN v.balance > 0 THEN v.balance END), 0) AS owed,
                   COALESCE(SUM(CASE WHEN v.balance > 0 THEN 1 END), 0) AS owed_count,
                   COALESCE(-SUM(CASE WHEN v.balance < 0 THEN v.balance END), 0) AS credit,
                   COALESCE(SUM(CASE WHEN v.balance < 0 THEN 1 END), 0) AS credit_count
            FROM vw_customer_balances v
            WHERE v.balance <> 0
            """)

    def customer_balances(self, limit=6):
        """The customers whose account is NOT settled (the true ledger-derived balance of vw_customer_balances) AND who have at least one customer transaction from the FIRST DAY OF
        THE CURRENT MONTH (the owner's provisional recency filter): biggest absolute balance first. A settled customer disappears on his own (balance = 0 never reaches the card)."""
        return self._fetch_all(
            """
            SELECT v.customer_id, v.customer_code, v.name, v.balance
            FROM vw_customer_balances v
            WHERE v.balance <> 0
              AND EXISTS (SELECT 1 FROM customer_transactions ct WHERE ct.customer_id = v.customer_id AND ct.occurred_at >= DATE_FORMAT(CURDATE(), '%Y-%m-01'))
            ORDER BY ABS(v.balance) DESC
            LIMIT %s
            """, (int(limit),))

