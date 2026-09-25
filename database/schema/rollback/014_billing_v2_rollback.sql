-- ============================================================
-- masGold v2
-- Rollback of migration 014: Canonical Billing V2
--
-- Removes ONLY objects created by 014, and only while they hold no
-- business data. Every guard runs before the first change; any
-- failed guard stops the script with
-- "Table '<db>.ROLLBACK_014_REFUSED_<reason>' doesn't exist".
--
-- Roll back 015 first. Objects of 013_schema_consolidation are
-- baseline schema and are never touched here.
--
-- Re-runnable: each drop runs only while its object still exists.
-- ============================================================


SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. GUARDS (no change happens before all of them pass)
-- ============================================================

-- 015 must be rolled back first.
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM schema_migrations WHERE version = 15)
    OR EXISTS (SELECT 1 FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'legacy_import_runs')
    OR EXISTS (SELECT 1 FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'old_gold_movements'
                  AND COLUMN_NAME = 'balance_effect'),
    'SELECT 1 FROM ROLLBACK_014_REFUSED_rollback_015_first',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- New invoice columns are dropped, so the invoice family must be empty.
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM invoices)
    OR EXISTS (SELECT 1 FROM invoice_lines)
    OR EXISTS (SELECT 1 FROM invoice_customer_snapshots),
    'SELECT 1 FROM ROLLBACK_014_REFUSED_invoice_family_not_empty',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_rows := 0;
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dead_cash_recoveries'),
    'SELECT COUNT(*) INTO @masgold_rows FROM dead_cash_recoveries',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0,
    'SELECT 1 FROM ROLLBACK_014_REFUSED_dead_cash_recoveries_not_empty', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Only the default direction may be discarded.
SET @masgold_rows := 0;
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dead_cash_writeoffs'
               AND COLUMN_NAME = 'writeoff_direction'),
    "SELECT COUNT(*) INTO @masgold_rows FROM dead_cash_writeoffs
      WHERE writeoff_direction <> 'CUSTOMER_OWES_SHOP'",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0,
    'SELECT 1 FROM ROLLBACK_014_REFUSED_writeoff_direction_in_use', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Only default origin/completeness values may be discarded.
SET @masgold_rows := 0;
SET @masgold_sql := IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customers'
        AND COLUMN_NAME IN ('record_origin', 'data_completeness')) = 2,
    "SELECT COUNT(*) INTO @masgold_rows FROM customers
      WHERE record_origin <> 'NATIVE' OR data_completeness <> 'COMPLETE'",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0,
    'SELECT 1 FROM ROLLBACK_014_REFUSED_legacy_customers_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Codes added by 014 must be unused.
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM customer_transactions ct
              JOIN customer_transaction_types t ON t.id = ct.transaction_type_id
             WHERE t.code IN ('SALE_BALANCE_DUE_REVERSAL', 'BUY_BALANCE_DUE_REVERSAL',
                              'DEAD_CASH_RECOVERY'))
    OR EXISTS (SELECT 1 FROM old_gold_movements m
                 JOIN old_gold_movement_types t ON t.id = m.movement_type_id
                WHERE t.code = 'CUSTOMER_BUY_IN_REVERSAL')
    OR EXISTS (SELECT 1 FROM invoice_payments p
                 JOIN payment_methods pm ON pm.id = p.payment_method_id
                WHERE pm.code = 'MOBILE_WALLET'),
    'SELECT 1 FROM ROLLBACK_014_REFUSED_new_codes_in_use',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- A barcode number allocated beyond what inventory_barcodes proves
-- (e.g. a printed label) must not become reusable.
SET @masgold_rows := 0;
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'barcode_number_sequences'),
    "SELECT COUNT(*) INTO @masgold_rows
       FROM barcode_number_sequences s
       LEFT JOIN (
           SELECT b.sequence_date, MAX(b.seq) AS max_seq
             FROM (
                 SELECT
                     CAST(LEFT(ib.barcode_value, CHAR_LENGTH(ib.barcode_value) - 6) AS SIGNED) AS seq,
                     RIGHT(ib.barcode_value, 6) AS date_part,
                     MAKEDATE(2000 + CAST(LEFT(RIGHT(ib.barcode_value, 6), 2) AS SIGNED), 1)
                         + INTERVAL (CAST(SUBSTRING(RIGHT(ib.barcode_value, 6), 3, 2) AS SIGNED) - 1) MONTH
                         + INTERVAL (CAST(RIGHT(ib.barcode_value, 2) AS SIGNED) - 1) DAY
                         AS sequence_date
                   FROM inventory_barcodes ib
                  WHERE ib.barcode_value REGEXP '^[0-9]{8,9}$'
             ) b
            WHERE DATE_FORMAT(b.sequence_date, '%y%m%d') = b.date_part
            GROUP BY b.sequence_date
       ) proven ON proven.sequence_date = s.sequence_date
      WHERE proven.max_seq IS NULL OR s.last_seq > proven.max_seq",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0,
    'SELECT 1 FROM ROLLBACK_014_REFUSED_barcode_numbers_allocated', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. DEAD CASH
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dead_cash_recoveries'),
    "DROP TABLE dead_cash_recoveries",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dead_cash_writeoffs'
               AND INDEX_NAME = 'uq_dead_cash_writeoff_customer'),
    "ALTER TABLE dead_cash_writeoffs DROP INDEX uq_dead_cash_writeoff_customer",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'dead_cash_writeoffs'
               AND CONSTRAINT_NAME = 'chk_dead_cash_writeoff_direction'),
    "ALTER TABLE dead_cash_writeoffs DROP CHECK chk_dead_cash_writeoff_direction",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dead_cash_writeoffs'
               AND COLUMN_NAME = 'writeoff_direction'),
    "ALTER TABLE dead_cash_writeoffs DROP COLUMN writeoff_direction",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 2. CUSTOMERS
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'customers'
               AND CONSTRAINT_NAME = 'chk_customer_record_origin'),
    "ALTER TABLE customers DROP CHECK chk_customer_record_origin",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customers'
               AND INDEX_NAME = 'idx_customers_origin'),
    "ALTER TABLE customers DROP INDEX idx_customers_origin",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customers'
               AND COLUMN_NAME = 'data_completeness'),
    "ALTER TABLE customers DROP COLUMN data_completeness",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customers'
               AND COLUMN_NAME = 'record_origin'),
    "ALTER TABLE customers DROP COLUMN record_origin",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 3. CUSTOMER SNAPSHOT
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_customer_snapshots'
               AND CONSTRAINT_NAME = 'chk_invoice_customer_snapshot_balances'),
    "ALTER TABLE invoice_customer_snapshots DROP CHECK chk_invoice_customer_snapshot_balances",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_customer_snapshots'
               AND COLUMN_NAME = 'dead_cash_warning_active'),
    "ALTER TABLE invoice_customer_snapshots DROP COLUMN dead_cash_warning_active",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_customer_snapshots'
               AND COLUMN_NAME = 'balance_after'),
    "ALTER TABLE invoice_customer_snapshots DROP COLUMN balance_after",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_customer_snapshots'
               AND COLUMN_NAME = 'balance_before'),
    "ALTER TABLE invoice_customer_snapshots DROP COLUMN balance_before",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 4. INVOICE LINES
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND CONSTRAINT_NAME = 'chk_invoice_line_native_old_gold'),
    "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_native_old_gold",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND CONSTRAINT_NAME = 'chk_invoice_line_native_sale'),
    "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_native_sale",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND CONSTRAINT_NAME = 'chk_invoice_line_native_values'),
    "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_native_values",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND CONSTRAINT_NAME = 'chk_invoice_line_discount_amount'),
    "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_discount_amount",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND CONSTRAINT_NAME = 'chk_invoice_line_discount_scope'),
    "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_discount_scope",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND CONSTRAINT_NAME = 'chk_invoice_line_discount_type'),
    "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_discount_type",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND CONSTRAINT_NAME = 'chk_invoice_line_gross'),
    "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_gross",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND CONSTRAINT_NAME = 'chk_invoice_line_record_origin'),
    "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_record_origin",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND CONSTRAINT_NAME = 'fk_invoice_line_old_gold_movement'),
    "ALTER TABLE invoice_lines DROP FOREIGN KEY fk_invoice_line_old_gold_movement",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND INDEX_NAME = 'uq_invoice_lines_old_gold_movement'),
    "ALTER TABLE invoice_lines DROP INDEX uq_invoice_lines_old_gold_movement",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND CONSTRAINT_NAME = 'fk_invoice_line_invoice_origin'),
    "ALTER TABLE invoice_lines DROP FOREIGN KEY fk_invoice_line_invoice_origin",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND INDEX_NAME = 'idx_invoice_lines_invoice_origin'),
    "ALTER TABLE invoice_lines DROP INDEX idx_invoice_lines_invoice_origin",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND COLUMN_NAME = 'discount_amount'),
    "ALTER TABLE invoice_lines DROP COLUMN discount_amount",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND COLUMN_NAME = 'discount_value'),
    "ALTER TABLE invoice_lines DROP COLUMN discount_value",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND COLUMN_NAME = 'discount_type'),
    "ALTER TABLE invoice_lines DROP COLUMN discount_type",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND COLUMN_NAME = 'gross_total'),
    "ALTER TABLE invoice_lines DROP COLUMN gross_total",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND COLUMN_NAME = 'old_gold_movement_id'),
    "ALTER TABLE invoice_lines DROP COLUMN old_gold_movement_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND COLUMN_NAME = 'record_origin'),
    "ALTER TABLE invoice_lines DROP COLUMN record_origin",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 5. INVOICES
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
               AND CONSTRAINT_NAME = 'chk_invoice_native_type'),
    "ALTER TABLE invoices DROP CHECK chk_invoice_native_type",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
               AND CONSTRAINT_NAME = 'chk_invoice_native_totals'),
    "ALTER TABLE invoices DROP CHECK chk_invoice_native_totals",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
               AND CONSTRAINT_NAME = 'chk_invoice_native_status'),
    "ALTER TABLE invoices DROP CHECK chk_invoice_native_status",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
               AND CONSTRAINT_NAME = 'chk_invoice_native_number'),
    "ALTER TABLE invoices DROP CHECK chk_invoice_native_number",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
               AND CONSTRAINT_NAME = 'chk_invoice_record_origin'),
    "ALTER TABLE invoices DROP CHECK chk_invoice_record_origin",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
               AND INDEX_NAME = 'uq_invoices_id_origin'),
    "ALTER TABLE invoices DROP INDEX uq_invoices_id_origin",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
               AND COLUMN_NAME = 'record_origin'),
    "ALTER TABLE invoices DROP COLUMN record_origin",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 6. SEQUENCES
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'barcode_number_sequences'),
    "DROP TABLE barcode_number_sequences",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_number_sequences'),
    "DROP TABLE invoice_number_sequences",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 7. CODES (unused, checked above) AND REGISTRATION
-- ============================================================

DELETE FROM customer_transaction_types
WHERE code IN (
    'SALE_BALANCE_DUE_REVERSAL',
    'BUY_BALANCE_DUE_REVERSAL',
    'DEAD_CASH_RECOVERY'
);

DELETE FROM old_gold_movement_types
WHERE code = 'CUSTOMER_BUY_IN_REVERSAL';

DELETE FROM payment_methods
WHERE code = 'MOBILE_WALLET';

DELETE FROM schema_migrations
WHERE version = 14;


SET SESSION sql_mode = @masgold_saved_sql_mode;
