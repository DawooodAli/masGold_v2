-- ============================================================
-- masGold v2
-- Migration 014: Canonical Billing V2
--
-- Forward schema only. No legacy data.
--
-- The target database is chosen by the caller, e.g.
--     mysql ... <database> < 014_billing_v2.sql
-- This file never names a database.
--
-- Re-runnable: every ALTER runs only when its object is missing
-- (checked in information_schema), tables use IF NOT EXISTS and
-- codes use ON DUPLICATE KEY UPDATE.
--
-- Additive: nothing is dropped, renamed or deleted.
--
-- Native invoice rows are fully constrained by CHECKs guarded on
-- record_origin = 'NATIVE'. Legacy relaxations live in 015.
--
-- Rollback: rollback/014_billing_v2_rollback.sql
-- ============================================================


-- The DDL below is quoted with double quotes; pin a known sql_mode
-- (no ANSI_QUOTES) for this session and restore it at the end.
SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. PREFLIGHT
--
-- A failed check stops the script (mysql client default) with
-- "Table '<db>.MIGRATION_014_REFUSED_<reason>' doesn't exist".
-- ============================================================

-- CHECK constraints are enforced from MySQL 8.0.16.
SET @masgold_sql := IF(
    (SUBSTRING_INDEX(VERSION(), '.', 1) + 0) * 10000
    + (SUBSTRING_INDEX(SUBSTRING_INDEX(VERSION(), '.', 2), '.', -1) + 0) * 100
    + (SUBSTRING_INDEX(SUBSTRING_INDEX(SUBSTRING_INDEX(VERSION(), '-', 1), '.', 3), '.', -1) + 0)
    < 80016,
    'SELECT 1 FROM MIGRATION_014_REFUSED_mysql_older_than_8_0_16',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 013_schema_consolidation (which also registers 012) must be applied.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 13),
    'SELECT 1 FROM MIGRATION_014_REFUSED_migration_013_missing',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- First application requires an empty canonical invoice family, so no
-- existing row can make a new CHECK fail half-way through the script.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
                   AND COLUMN_NAME = 'record_origin')
    AND (EXISTS (SELECT 1 FROM invoices) OR EXISTS (SELECT 1 FROM invoice_lines)),
    'SELECT 1 FROM MIGRATION_014_REFUSED_invoice_family_not_empty',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. INVOICE NUMBER SEQUENCE
--
-- Production format: YYMMDDNN (e.g. 26092512).
-- NN is 01-99 per business day; past 99 finalization is refused.
-- Allocated inside the finalize transaction; uq_invoices_number
-- remains the final guard against duplicates.
-- ============================================================

CREATE TABLE IF NOT EXISTS invoice_number_sequences (
    sequence_date DATE NOT NULL,

    last_seq TINYINT UNSIGNED NOT NULL,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (sequence_date),

    CONSTRAINT chk_invoice_number_seq_range
        CHECK (last_seq BETWEEN 0 AND 99)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- Never allocate a number already used by an existing invoice.
-- Only valid YYMMDDNN numbers count; the date round-trip filters
-- impossible dates without raising strict-mode errors.
INSERT INTO invoice_number_sequences (
    sequence_date,
    last_seq
)
SELECT
    seeded.sequence_date,
    seeded.max_seq
FROM (
    SELECT
        n.sequence_date,
        MAX(n.seq) AS max_seq
    FROM (
        SELECT
            CAST(RIGHT(i.invoice_number, 2) AS SIGNED) AS seq,
            LEFT(i.invoice_number, 6) AS date_part,
            MAKEDATE(2000 + CAST(LEFT(i.invoice_number, 2) AS SIGNED), 1)
                + INTERVAL (CAST(SUBSTRING(i.invoice_number, 3, 2) AS SIGNED) - 1) MONTH
                + INTERVAL (CAST(SUBSTRING(i.invoice_number, 5, 2) AS SIGNED) - 1) DAY
                AS sequence_date
        FROM invoices i
        WHERE i.invoice_number REGEXP '^[0-9]{8}$'
    ) n
    WHERE DATE_FORMAT(n.sequence_date, '%y%m%d') = n.date_part
      AND n.seq BETWEEN 1 AND 99
    GROUP BY n.sequence_date
) seeded
ON DUPLICATE KEY UPDATE
    last_seq = GREATEST(invoice_number_sequences.last_seq, VALUES(last_seq));


-- ============================================================
-- 2. BARCODE NUMBER SEQUENCE
--
-- Barcode format stays NNYYMMDD (e.g. 01260921, 02260921 ...);
-- NN is 01-999 per day, so a barcode has 8 or 9 digits (legacy
-- golddb reached 141 in one day).
-- Replaces the "max over ACTIVE barcodes" lookup, which would
-- re-propose numbers of sold/retired items.
-- ============================================================

CREATE TABLE IF NOT EXISTS barcode_number_sequences (
    sequence_date DATE NOT NULL,

    last_seq SMALLINT UNSIGNED NOT NULL,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (sequence_date),

    CONSTRAINT chk_barcode_number_seq_range
        CHECK (last_seq BETWEEN 0 AND 999)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- Seed from every existing barcode, whatever its status.
INSERT INTO barcode_number_sequences (
    sequence_date,
    last_seq
)
SELECT
    seeded.sequence_date,
    seeded.max_seq
FROM (
    SELECT
        b.sequence_date,
        MAX(b.seq) AS max_seq
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
      AND b.seq > 0
    GROUP BY b.sequence_date
) seeded
ON DUPLICATE KEY UPDATE
    last_seq = GREATEST(barcode_number_sequences.last_seq, VALUES(last_seq));


-- ============================================================
-- 3. INVOICES: record origin and native integrity
--
-- record_origin: NATIVE = written by Billing V2
--                LEGACY = imported historical bill (015 / ETL)
--
-- UNIQUE (id, record_origin) lets invoice_lines prove that a
-- line has the same origin as its invoice.
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
                   AND COLUMN_NAME = 'record_origin'),
    "ALTER TABLE invoices ADD COLUMN record_origin VARCHAR(10) NOT NULL DEFAULT 'NATIVE' AFTER legacy_completeness",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
                   AND INDEX_NAME = 'uq_invoices_id_origin'),
    "ALTER TABLE invoices ADD UNIQUE KEY uq_invoices_id_origin (id, record_origin)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
                   AND CONSTRAINT_NAME = 'chk_invoice_record_origin'),
    "ALTER TABLE invoices ADD CONSTRAINT chk_invoice_record_origin CHECK (
        (record_origin = 'NATIVE'
            AND legacy_source_system IS NULL
            AND legacy_bill_number IS NULL
            AND legacy_completeness IS NULL)
        OR
        (record_origin = 'LEGACY'
            AND legacy_source_system IS NOT NULL
            AND legacy_completeness IS NOT NULL)
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Native numbers are YYMMDDNN with a real month/day and NN 01-99.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
                   AND CONSTRAINT_NAME = 'chk_invoice_native_number'),
    "ALTER TABLE invoices ADD CONSTRAINT chk_invoice_native_number CHECK (
        record_origin <> 'NATIVE'
        OR invoice_number REGEXP '^[0-9]{2}(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])(0[1-9]|[1-9][0-9])$'
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Drafts are UI/in-memory only; a native row is written on finalize.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
                   AND CONSTRAINT_NAME = 'chk_invoice_native_status'),
    "ALTER TABLE invoices ADD CONSTRAINT chk_invoice_native_status CHECK (
        record_origin <> 'NATIVE'
        OR (
            invoice_status IN ('FINALIZED', 'VOIDED')
            AND finalized_at IS NOT NULL
            AND (invoice_status <> 'VOIDED'
                 OR (voided_at IS NOT NULL AND void_reason IS NOT NULL))
        )
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- N = sell_total - buy_total; net_amount = |N|; direction from the
-- sign of N. Overpayment is never stored on a native invoice.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
                   AND CONSTRAINT_NAME = 'chk_invoice_native_totals'),
    "ALTER TABLE invoices ADD CONSTRAINT chk_invoice_native_totals CHECK (
        record_origin <> 'NATIVE'
        OR (
            sell_total IS NOT NULL
            AND buy_total IS NOT NULL
            AND net_amount = ABS(sell_total - buy_total)
            AND net_direction = (
                CASE
                    WHEN sell_total > buy_total THEN 'CUSTOMER_PAYS_SHOP'
                    WHEN sell_total < buy_total THEN 'SHOP_PAYS_CUSTOMER'
                    ELSE 'SETTLED'
                END
            )
            AND settlement_status IN ('UNSETTLED', 'PARTIAL', 'SETTLED')
        )
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
                   AND CONSTRAINT_NAME = 'chk_invoice_native_type'),
    "ALTER TABLE invoices ADD CONSTRAINT chk_invoice_native_type CHECK (
        record_origin <> 'NATIVE'
        OR (
            (invoice_type <> 'SELL' OR buy_total = 0)
            AND (invoice_type <> 'BUY' OR sell_total = 0)
        )
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 4. INVOICE LINES: origin, gross/discount, old-gold link
--
-- gross_total      = ROUND(weight_grams * gold_price_per_gram, 2)
-- SELL_INVENTORY   : line_total = gross_total + making_total
-- BUY_OLD_GOLD     : line_total = gross_total - discount_amount
--
-- gold_price_per_gram is the snapshot market price before any
-- discount. discount_value holds the percent (0-100) or the amount,
-- both with 2 decimals (InvoiceCalculator precision).
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND COLUMN_NAME = 'record_origin'),
    "ALTER TABLE invoice_lines ADD COLUMN record_origin VARCHAR(10) NOT NULL DEFAULT 'NATIVE' AFTER invoice_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND COLUMN_NAME = 'old_gold_movement_id'),
    "ALTER TABLE invoice_lines ADD COLUMN old_gold_movement_id BIGINT UNSIGNED NULL AFTER inventory_item_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND COLUMN_NAME = 'gross_total'),
    "ALTER TABLE invoice_lines ADD COLUMN gross_total DECIMAL(16,2) NULL AFTER making_total",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND COLUMN_NAME = 'discount_type'),
    "ALTER TABLE invoice_lines ADD COLUMN discount_type VARCHAR(10) NULL AFTER gross_total",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND COLUMN_NAME = 'discount_value'),
    "ALTER TABLE invoice_lines ADD COLUMN discount_value DECIMAL(16,2) NULL AFTER discount_type",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND COLUMN_NAME = 'discount_amount'),
    "ALTER TABLE invoice_lines ADD COLUMN discount_amount DECIMAL(16,2) NULL AFTER discount_value",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- Line origin must equal invoice origin.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND INDEX_NAME = 'idx_invoice_lines_invoice_origin'),
    "ALTER TABLE invoice_lines ADD KEY idx_invoice_lines_invoice_origin (invoice_id, record_origin)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND CONSTRAINT_NAME = 'fk_invoice_line_invoice_origin'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT fk_invoice_line_invoice_origin
        FOREIGN KEY (invoice_id, record_origin)
        REFERENCES invoices(id, record_origin)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- One ledger movement per old-gold line, never shared.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND INDEX_NAME = 'uq_invoice_lines_old_gold_movement'),
    "ALTER TABLE invoice_lines ADD UNIQUE KEY uq_invoice_lines_old_gold_movement (old_gold_movement_id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND CONSTRAINT_NAME = 'fk_invoice_line_old_gold_movement'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT fk_invoice_line_old_gold_movement
        FOREIGN KEY (old_gold_movement_id)
        REFERENCES old_gold_movements(id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND CONSTRAINT_NAME = 'chk_invoice_line_record_origin'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_record_origin CHECK (
        record_origin IN ('NATIVE', 'LEGACY')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND CONSTRAINT_NAME = 'chk_invoice_line_gross'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_gross CHECK (
        gross_total IS NULL OR gross_total >= 0
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND CONSTRAINT_NAME = 'chk_invoice_line_discount_type'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_discount_type CHECK (
        (discount_type IS NULL AND discount_value IS NULL)
        OR (discount_type IS NOT NULL
            AND discount_type IN ('PERCENT', 'AMOUNT')
            AND discount_value IS NOT NULL
            AND discount_amount IS NOT NULL)
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Discounts exist only on old-gold purchase lines.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND CONSTRAINT_NAME = 'chk_invoice_line_discount_scope'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_discount_scope CHECK (
        line_type = 'BUY_OLD_GOLD'
        OR (discount_type IS NULL AND discount_amount IS NULL)
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 0 <= discount <= gross; PERCENT 0-100 with the exact rounded amount
-- (no rounding to 5); AMOUNT equals the entered value.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND CONSTRAINT_NAME = 'chk_invoice_line_discount_amount'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_discount_amount CHECK (
        (discount_amount IS NULL
            OR (discount_amount >= 0
                AND (gross_total IS NULL OR discount_amount <= gross_total)))
        AND (discount_value IS NULL OR discount_value >= 0)
        AND (discount_type IS NULL OR discount_type <> 'PERCENT'
            OR (discount_value <= 100
                AND (gross_total IS NULL
                     OR discount_amount = ROUND(gross_total * discount_value / 100, 2))))
        AND (discount_type IS NULL OR discount_type <> 'AMOUNT'
            OR discount_amount = discount_value)
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Every native line is a priced inventory sale or old-gold purchase
-- whose gross value is exactly weight x snapshot price.
-- ("sale without an inventory item" is deferred from V2.)
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND CONSTRAINT_NAME = 'chk_invoice_line_native_values'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_native_values CHECK (
        record_origin <> 'NATIVE'
        OR (
            line_type IN ('SELL_INVENTORY', 'BUY_OLD_GOLD')
            AND quantity = 1
            AND gold_price_per_gram IS NOT NULL
            AND line_total IS NOT NULL
            AND gross_total IS NOT NULL
            AND gross_total = ROUND(weight_grams * gold_price_per_gram, 2)
        )
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND CONSTRAINT_NAME = 'chk_invoice_line_native_sale'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_native_sale CHECK (
        record_origin <> 'NATIVE'
        OR line_type <> 'SELL_INVENTORY'
        OR (
            making_total IS NOT NULL
            AND (making_per_gram IS NULL
                 OR making_total = ROUND(weight_grams * making_per_gram, 2))
            AND line_total = gross_total + making_total
            AND discount_type IS NULL
            AND discount_amount IS NULL
            AND old_gold_movement_id IS NULL
        )
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND CONSTRAINT_NAME = 'chk_invoice_line_native_old_gold'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_native_old_gold CHECK (
        record_origin <> 'NATIVE'
        OR line_type <> 'BUY_OLD_GOLD'
        OR (
            making_per_gram IS NULL
            AND making_total IS NULL
            AND old_gold_movement_id IS NOT NULL
            AND discount_amount IS NOT NULL
            AND line_total = gross_total - discount_amount
        )
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 5. CUSTOMER SNAPSHOT: balance and Dead Cash warning at finalize
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_customer_snapshots'
                   AND COLUMN_NAME = 'balance_before'),
    "ALTER TABLE invoice_customer_snapshots ADD COLUMN balance_before DECIMAL(16,2) NULL AFTER customer_type_snapshot",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_customer_snapshots'
                   AND COLUMN_NAME = 'balance_after'),
    "ALTER TABLE invoice_customer_snapshots ADD COLUMN balance_after DECIMAL(16,2) NULL AFTER balance_before",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_customer_snapshots'
                   AND COLUMN_NAME = 'dead_cash_warning_active'),
    "ALTER TABLE invoice_customer_snapshots ADD COLUMN dead_cash_warning_active BOOLEAN NOT NULL DEFAULT FALSE AFTER balance_after",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_customer_snapshots'
                   AND CONSTRAINT_NAME = 'chk_invoice_customer_snapshot_balances'),
    "ALTER TABLE invoice_customer_snapshots ADD CONSTRAINT chk_invoice_customer_snapshot_balances CHECK (
        (balance_before IS NULL AND balance_after IS NULL)
        OR (balance_before IS NOT NULL AND balance_after IS NOT NULL)
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 6. CUSTOMERS: record origin
--
-- NATIVE             normal customer, COMPLETE
-- LEGACY             imported from golddb (any completeness)
-- LEGACY_PLACEHOLDER missing legacy id, UNKNOWN; active only to
--                    settle its historical balance, hidden from
--                    pickers, never selectable for new invoices
--                    (enforced by the service layer).
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customers'
                   AND COLUMN_NAME = 'record_origin'),
    "ALTER TABLE customers ADD COLUMN record_origin VARCHAR(20) NOT NULL DEFAULT 'NATIVE' AFTER is_active",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customers'
                   AND COLUMN_NAME = 'data_completeness'),
    "ALTER TABLE customers ADD COLUMN data_completeness VARCHAR(20) NOT NULL DEFAULT 'COMPLETE' AFTER record_origin",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customers'
                   AND INDEX_NAME = 'idx_customers_origin'),
    "ALTER TABLE customers ADD KEY idx_customers_origin (record_origin, is_active)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'customers'
                   AND CONSTRAINT_NAME = 'chk_customer_record_origin'),
    "ALTER TABLE customers ADD CONSTRAINT chk_customer_record_origin CHECK (
        (record_origin = 'NATIVE' AND data_completeness = 'COMPLETE')
        OR (record_origin = 'LEGACY'
            AND data_completeness IN ('COMPLETE', 'PARTIAL', 'UNKNOWN'))
        OR (record_origin = 'LEGACY_PLACEHOLDER' AND data_completeness = 'UNKNOWN')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 7. DEAD CASH: write-off direction and recoveries
--
-- Only CUSTOMER_OWES_SHOP is used by V2; SHOP_OWES_CUSTOMER is
-- reserved so credit write-off can be added without redesign.
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dead_cash_writeoffs'
                   AND COLUMN_NAME = 'writeoff_direction'),
    "ALTER TABLE dead_cash_writeoffs ADD COLUMN writeoff_direction VARCHAR(30) NOT NULL DEFAULT 'CUSTOMER_OWES_SHOP' AFTER customer_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'dead_cash_writeoffs'
                   AND CONSTRAINT_NAME = 'chk_dead_cash_writeoff_direction'),
    "ALTER TABLE dead_cash_writeoffs ADD CONSTRAINT chk_dead_cash_writeoff_direction CHECK (
        writeoff_direction IN ('CUSTOMER_OWES_SHOP', 'SHOP_OWES_CUSTOMER')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Lets a recovery prove it belongs to the write-off's customer.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dead_cash_writeoffs'
                   AND INDEX_NAME = 'uq_dead_cash_writeoff_customer'),
    "ALTER TABLE dead_cash_writeoffs ADD UNIQUE KEY uq_dead_cash_writeoff_customer (id, customer_id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- A recovery restores written-off debt the customer now pays:
-- DEAD_CASH_RECOVERY (+amount) paired with CUSTOMER_PAYMENT (-amount).
-- The cap (sum of recoveries <= write-off amount) is enforced by the
-- service under a row lock.
CREATE TABLE IF NOT EXISTS dead_cash_recoveries (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    dead_cash_writeoff_id BIGINT UNSIGNED NOT NULL,
    customer_id BIGINT UNSIGNED NOT NULL,

    amount DECIMAL(16,2) NOT NULL,

    recovery_transaction_id BIGINT UNSIGNED NOT NULL,
    payment_transaction_id BIGINT UNSIGNED NOT NULL,

    notes VARCHAR(1000) DEFAULT NULL,

    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    created_by_user_id INT UNSIGNED DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_dead_cash_recovery_transaction (
        recovery_transaction_id
    ),

    UNIQUE KEY uq_dead_cash_recovery_payment (
        payment_transaction_id
    ),

    KEY idx_dead_cash_recoveries_writeoff (
        dead_cash_writeoff_id,
        customer_id
    ),

    KEY idx_dead_cash_recoveries_customer (
        customer_id
    ),

    CONSTRAINT fk_dead_cash_recovery_writeoff
        FOREIGN KEY (dead_cash_writeoff_id, customer_id)
        REFERENCES dead_cash_writeoffs(id, customer_id),

    CONSTRAINT fk_dead_cash_recovery_customer
        FOREIGN KEY (customer_id)
        REFERENCES customers(id),

    CONSTRAINT fk_dead_cash_recovery_transaction
        FOREIGN KEY (recovery_transaction_id)
        REFERENCES customer_transactions(id),

    CONSTRAINT fk_dead_cash_recovery_payment
        FOREIGN KEY (payment_transaction_id)
        REFERENCES customer_transactions(id),

    CONSTRAINT fk_dead_cash_recovery_user
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_dead_cash_recovery_amount
        CHECK (amount > 0),

    CONSTRAINT chk_dead_cash_recovery_pair
        CHECK (recovery_transaction_id <> payment_transaction_id)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 8. CODES
-- ============================================================

INSERT INTO customer_transaction_types (
    code,
    name,
    expected_direction,
    description
)
VALUES

    (
        'SALE_BALANCE_DUE_REVERSAL',
        'Sale Remainder Reversal',
        'DECREASE',
        'Void of an invoice that left the customer owing the shop'
    ),

    (
        'BUY_BALANCE_DUE_REVERSAL',
        'Purchase Remainder Reversal',
        'INCREASE',
        'Void of an invoice that left the shop owing the customer'
    ),

    (
        'DEAD_CASH_RECOVERY',
        'Dead Cash Recovery',
        'INCREASE',
        'Restores written-off debt the customer is now paying; always paired with CUSTOMER_PAYMENT'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    expected_direction = VALUES(expected_direction),
    description = VALUES(description);


INSERT INTO old_gold_movement_types (
    code,
    name,
    description,
    expected_direction
)
VALUES

    (
        'CUSTOMER_BUY_IN_REVERSAL',
        'Customer Purchase Reversal',
        'Void of an old-gold purchase from a customer',
        'OUT'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description),
    expected_direction = VALUES(expected_direction);


INSERT INTO payment_methods (
    code,
    name,
    affects_physical_cash,
    requires_bank_account,
    sort_order
)
VALUES

    (
        'MOBILE_WALLET',
        'Mobile Wallet',
        FALSE,
        FALSE,
        5
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    affects_physical_cash = VALUES(affects_physical_cash),
    requires_bank_account = VALUES(requires_bank_account),
    sort_order = VALUES(sort_order);


-- ============================================================
-- REGISTER MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    14,
    'Canonical Billing V2: numbering, native line integrity, Dead Cash recovery'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);


SET SESSION sql_mode = @masgold_saved_sql_mode;
