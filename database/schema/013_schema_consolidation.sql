-- ============================================================
-- masGold v2
-- Migration 013: Schema consolidation
--
-- Brings the prototype-era schema into the numbered chain, so a
-- fresh database is built from 001-015 alone. The unnumbered files
-- in database/migrations/20260921_*.sql stay as historical
-- artifacts and are NOT part of a fresh build.
--
-- Reproduces what the application uses from those files, taken
-- from the live masgold_v2 definitions (read-only inspection):
--   - barcode print history
--   - OLD_STOCK -> NEW_STOCK conversion audit
--   - old gold -> new stock conversion audit
--   - frozen Billing V1 (billing_*), kept as read-only history
--   - old_gold_movements prototype columns
--   - inventory_items.acquisition_source ENUM
--
-- Corrections (approved):
--   - old_gold_conversion_batches.karat_id is TINYINT UNSIGNED like
--     karats.id (the prototype file declared INT, which cannot be
--     created on an empty database).
--   - The acquisition-source CHECK allows OLD_STOCK_CONVERSION,
--     which the ENUM and the conversion code already use.
--   - old_gold_movements.karat_id stays NOT NULL as in 004; the
--     nullable development drift is not reproduced (or changed).
--
-- The six prototype tables keep their live collation
-- utf8mb4_0900_ai_ci so fresh builds match masgold_v2 exactly.
--
-- Registers 012 (applied but never recorded) and 013.
--
-- The target database is chosen by the caller; this file never
-- names a database. Re-runnable: every change runs only when its
-- object is missing.
--
-- No rollback script: these objects are baseline schema used by
-- the current application. A rehearsal database is rebuilt instead.
-- ============================================================


SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. PREFLIGHT
--
-- A failed check stops the script (mysql client default) with
-- "Table '<db>.MIGRATION_013_REFUSED_<reason>' doesn't exist".
-- ============================================================

-- CHECK constraints are enforced from MySQL 8.0.16.
SET @masgold_sql := IF(
    (SUBSTRING_INDEX(VERSION(), '.', 1) + 0) * 10000
    + (SUBSTRING_INDEX(SUBSTRING_INDEX(VERSION(), '.', 2), '.', -1) + 0) * 100
    + (SUBSTRING_INDEX(SUBSTRING_INDEX(SUBSTRING_INDEX(VERSION(), '-', 1), '.', 3), '.', -1) + 0)
    < 80016,
    'SELECT 1 FROM MIGRATION_013_REFUSED_mysql_older_than_8_0_16',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 11),
    'SELECT 1 FROM MIGRATION_013_REFUSED_migration_011_missing',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 012 must really be applied before it is registered below.
SET @masgold_sql := IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE()
        AND ((TABLE_NAME = 'inventory_items' AND COLUMN_NAME = 'entered_by_employee_id')
          OR (TABLE_NAME = 'inventory_movements' AND COLUMN_NAME = 'performed_by_employee_id')
          OR (TABLE_NAME = 'old_gold_movements' AND COLUMN_NAME = 'received_by_employee_id'))) <> 3,
    'SELECT 1 FROM MIGRATION_013_REFUSED_migration_012_not_applied',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- The ENUM conversion below must not meet a value it cannot hold.
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM inventory_items
             WHERE acquisition_source NOT IN ('MARKET_PURCHASE', 'OLD_GOLD_MANUFACTURE',
                                              'OLD_STOCK_CONVERSION', 'LEGACY_MIGRATION',
                                              'MANUAL_ENTRY')),
    'SELECT 1 FROM MIGRATION_013_REFUSED_unknown_acquisition_source',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. REGISTER 012
--
-- 012_actor_attribution is applied (checked above) but was never
-- recorded in schema_migrations.
-- ============================================================

INSERT INTO schema_migrations (
    version,
    name
)
SELECT
    12,
    'Actor attribution: physical employee on inventory and old gold'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = 12
);


-- ============================================================
-- 2. OLD GOLD MOVEMENTS: prototype-era columns
--
-- Used by the old-gold conversion and Billing V1 writers.
-- karat_id is deliberately left as defined in 004 (NOT NULL).
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'old_gold_movements'
                   AND COLUMN_NAME = 'movement_code'),
    "ALTER TABLE old_gold_movements ADD COLUMN movement_code VARCHAR(40) NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'old_gold_movements'
                   AND COLUMN_NAME = 'customer_id'),
    "ALTER TABLE old_gold_movements ADD COLUMN customer_id BIGINT UNSIGNED NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'old_gold_movements'
                   AND COLUMN_NAME = 'invoice_id'),
    "ALTER TABLE old_gold_movements ADD COLUMN invoice_id BIGINT UNSIGNED NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'old_gold_movements'
                   AND COLUMN_NAME = 'factory_id'),
    "ALTER TABLE old_gold_movements ADD COLUMN factory_id INT UNSIGNED NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'old_gold_movements'
                   AND COLUMN_NAME = 'performed_by_user_id'),
    "ALTER TABLE old_gold_movements ADD COLUMN performed_by_user_id INT UNSIGNED NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 3. INVENTORY ACQUISITION SOURCE
--
-- 003 declared VARCHAR(30) DEFAULT 'MARKET_PURCHASE'; the application
-- runs on the prototype ENUM (no default). The CHECK is widened to
-- the same five values: OLD_STOCK -> NEW_STOCK conversion inserts
-- OLD_STOCK_CONVERSION and was always rejected before.
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_items'
                   AND COLUMN_NAME = 'acquisition_source'
                   AND COLUMN_TYPE = "enum('MARKET_PURCHASE','OLD_GOLD_MANUFACTURE','OLD_STOCK_CONVERSION','LEGACY_MIGRATION','MANUAL_ENTRY')"
                   AND IS_NULLABLE = 'NO'),
    "ALTER TABLE inventory_items MODIFY COLUMN acquisition_source ENUM(
        'MARKET_PURCHASE',
        'OLD_GOLD_MANUFACTURE',
        'OLD_STOCK_CONVERSION',
        'LEGACY_MIGRATION',
        'MANUAL_ENTRY'
    ) NOT NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- One atomic ALTER swaps the old CHECK for the corrected one.
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_items'
               AND CONSTRAINT_NAME = 'chk_inventory_source')
    AND NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                     WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_items'
                       AND CONSTRAINT_NAME = 'chk_inventory_acquisition_source'),
    "ALTER TABLE inventory_items
        DROP CHECK chk_inventory_source,
        ADD CONSTRAINT chk_inventory_acquisition_source CHECK (
            acquisition_source IN (
                'MARKET_PURCHASE',
                'OLD_GOLD_MANUFACTURE',
                'OLD_STOCK_CONVERSION',
                'LEGACY_MIGRATION',
                'MANUAL_ENTRY'
            )
        )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 4. BARCODE PRINT HISTORY
-- ============================================================

CREATE TABLE IF NOT EXISTS barcode_print_jobs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    batch_code VARCHAR(50) NOT NULL,
    printer_name VARCHAR(150) NOT NULL,
    item_count INT UNSIGNED NOT NULL,
    physical_label_count INT UNSIGNED NOT NULL,
    reprint_of_job_id BIGINT UNSIGNED NULL,
    requested_by_user_id INT UNSIGNED NULL,
    requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_barcode_print_jobs_batch_code (batch_code),
    KEY ix_barcode_print_jobs_requested_at (requested_at),
    KEY ix_barcode_print_jobs_reprint_of (reprint_of_job_id),

    CONSTRAINT fk_barcode_print_jobs_reprint_of
        FOREIGN KEY (reprint_of_job_id)
        REFERENCES barcode_print_jobs(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;


CREATE TABLE IF NOT EXISTS barcode_print_job_items (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    print_job_id BIGINT UNSIGNED NOT NULL,
    position_order INT UNSIGNED NOT NULL,
    inventory_item_id BIGINT UNSIGNED NOT NULL,
    barcode_value VARCHAR(100) NOT NULL,
    was_new_barcode TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_barcode_print_job_position (print_job_id, position_order),
    KEY ix_barcode_print_job_items_inventory (inventory_item_id),
    KEY ix_barcode_print_job_items_barcode (barcode_value),

    CONSTRAINT fk_barcode_print_job_items_job
        FOREIGN KEY (print_job_id)
        REFERENCES barcode_print_jobs(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_barcode_print_job_items_inventory
        FOREIGN KEY (inventory_item_id)
        REFERENCES inventory_items(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;


CREATE TABLE IF NOT EXISTS barcode_print_job_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    print_job_id BIGINT UNSIGNED NOT NULL,
    event_type VARCHAR(20) NOT NULL,
    message VARCHAR(1000) NULL,
    event_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY ix_barcode_print_job_events_job (print_job_id, id),
    KEY ix_barcode_print_job_events_at (event_at),

    CONSTRAINT fk_barcode_print_job_events_job
        FOREIGN KEY (print_job_id)
        REFERENCES barcode_print_jobs(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;


-- ============================================================
-- 5. OLD_STOCK -> NEW_STOCK CONVERSION AUDIT
-- ============================================================

CREATE TABLE IF NOT EXISTS inventory_conversion_batches (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    conversion_code VARCHAR(50) NOT NULL,
    result_inventory_item_id BIGINT UNSIGNED NOT NULL,
    source_total_weight_grams DECIMAL(12,3) NOT NULL,
    result_weight_grams DECIMAL(12,3) NOT NULL,
    weight_difference_grams DECIMAL(12,3) NOT NULL,
    performed_by_employee_id INT UNSIGNED NOT NULL,
    performed_by_user_id INT UNSIGNED NULL,
    notes VARCHAR(1000) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_inventory_conversion_code (conversion_code),
    UNIQUE KEY uq_inventory_conversion_result (result_inventory_item_id),
    KEY ix_inventory_conversion_created_at (created_at),
    KEY ix_inventory_conversion_employee (performed_by_employee_id),

    CONSTRAINT fk_inventory_conversion_result
        FOREIGN KEY (result_inventory_item_id)
        REFERENCES inventory_items(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_inventory_conversion_employee
        FOREIGN KEY (performed_by_employee_id)
        REFERENCES employees(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;


CREATE TABLE IF NOT EXISTS inventory_conversion_sources (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    conversion_batch_id BIGINT UNSIGNED NOT NULL,
    source_inventory_item_id BIGINT UNSIGNED NOT NULL,
    source_weight_grams DECIMAL(12,3) NOT NULL,
    source_barcode_value VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    -- One physical source piece can be consumed only once.
    UNIQUE KEY uq_inventory_conversion_source_item (source_inventory_item_id),
    KEY ix_inventory_conversion_sources_batch (conversion_batch_id),

    CONSTRAINT fk_inventory_conversion_sources_batch
        FOREIGN KEY (conversion_batch_id)
        REFERENCES inventory_conversion_batches(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_inventory_conversion_sources_item
        FOREIGN KEY (source_inventory_item_id)
        REFERENCES inventory_items(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;


-- ============================================================
-- 6. OLD GOLD -> NEW STOCK CONVERSION AUDIT
--
-- karat_id is TINYINT UNSIGNED to match karats.id (see header).
-- ============================================================

CREATE TABLE IF NOT EXISTS old_gold_conversion_batches (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    conversion_code VARCHAR(50) NOT NULL,

    old_gold_movement_id BIGINT UNSIGNED NOT NULL,
    result_inventory_item_id BIGINT UNSIGNED NOT NULL,

    karat_id TINYINT UNSIGNED NOT NULL,

    consumed_weight_grams DECIMAL(12,3) NOT NULL,
    result_weight_grams DECIMAL(12,3) NOT NULL,
    weight_difference_grams DECIMAL(12,3) NOT NULL,

    performed_by_employee_id INT UNSIGNED NOT NULL,
    performed_by_user_id INT UNSIGNED NULL,

    notes VARCHAR(1000) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_old_gold_conversion_code (conversion_code),
    UNIQUE KEY uq_old_gold_conversion_movement (old_gold_movement_id),
    UNIQUE KEY uq_old_gold_conversion_result (result_inventory_item_id),

    KEY ix_old_gold_conversion_karat (karat_id),
    KEY ix_old_gold_conversion_created_at (created_at),

    CONSTRAINT fk_old_gold_conversion_movement
        FOREIGN KEY (old_gold_movement_id)
        REFERENCES old_gold_movements(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_old_gold_conversion_result
        FOREIGN KEY (result_inventory_item_id)
        REFERENCES inventory_items(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_old_gold_conversion_karat
        FOREIGN KEY (karat_id)
        REFERENCES karats(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_old_gold_conversion_employee
        FOREIGN KEY (performed_by_employee_id)
        REFERENCES employees(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;


-- ============================================================
-- 7. FROZEN BILLING V1 (billing_*)
--
-- Temporary V1 persistence, kept only as read-only history after
-- the canonical invoices family (014) takes over.
-- ============================================================

CREATE TABLE IF NOT EXISTS billing_invoice_sequences (
    sequence_date DATE NOT NULL,
    seq INT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY (sequence_date)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS billing_invoices (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    invoice_code VARCHAR(30) NOT NULL,

    -- Nullable and not FK-bound in V1; snapshots carry the name.
    customer_id BIGINT UNSIGNED NULL,
    customer_name_snapshot VARCHAR(150) NOT NULL,

    employee_id INT UNSIGNED NOT NULL,

    sell_price_21_per_gram DECIMAL(14,4) NOT NULL DEFAULT 0,
    buy_price_21_per_gram DECIMAL(14,4) NOT NULL DEFAULT 0,

    sale_total DECIMAL(16,2) NOT NULL DEFAULT 0,
    old_gold_total DECIMAL(16,2) NOT NULL DEFAULT 0,
    net_customer_due DECIMAL(16,2) NOT NULL DEFAULT 0,

    sale_weight_total_grams DECIMAL(12,3) NOT NULL DEFAULT 0,
    old_gold_weight_total_grams DECIMAL(12,3) NOT NULL DEFAULT 0,

    status VARCHAR(20) NOT NULL DEFAULT 'FINALIZED',
    notes VARCHAR(1000) NULL,

    finalized_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_billing_invoice_code (invoice_code),
    KEY ix_billing_invoice_customer (customer_id),
    KEY ix_billing_invoice_employee (employee_id),
    KEY ix_billing_invoice_finalized (finalized_at),

    CONSTRAINT fk_billing_invoice_employee
        FOREIGN KEY (employee_id)
        REFERENCES employees(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT chk_billing_invoice_status
        CHECK (status IN ('FINALIZED', 'REVERSED'))
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS billing_sale_lines (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    invoice_id BIGINT UNSIGNED NOT NULL,
    inventory_item_id BIGINT UNSIGNED NOT NULL,

    barcode_snapshot VARCHAR(100) NULL,
    item_type_snapshot VARCHAR(150) NOT NULL,
    karat_value_snapshot INT NOT NULL,

    weight_grams DECIMAL(12,3) NOT NULL,
    gold_price_per_gram DECIMAL(14,4) NOT NULL,
    making_per_gram DECIMAL(14,4) NOT NULL DEFAULT 0,
    line_total DECIMAL(16,2) NOT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY ix_billing_sale_inventory_item (inventory_item_id),
    KEY ix_billing_sale_invoice (invoice_id),

    CONSTRAINT fk_billing_sale_invoice
        FOREIGN KEY (invoice_id)
        REFERENCES billing_invoices(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_billing_sale_inventory_item
        FOREIGN KEY (inventory_item_id)
        REFERENCES inventory_items(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT chk_billing_sale_weight
        CHECK (weight_grams > 0),

    CONSTRAINT chk_billing_sale_price
        CHECK (gold_price_per_gram > 0),

    CONSTRAINT chk_billing_sale_making
        CHECK (making_per_gram >= 0),

    CONSTRAINT chk_billing_sale_total
        CHECK (line_total >= 0)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS billing_old_gold_lines (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    invoice_id BIGINT UNSIGNED NOT NULL,
    old_gold_movement_id BIGINT UNSIGNED NOT NULL,

    karat_id TINYINT UNSIGNED NOT NULL,
    karat_value_snapshot INT NOT NULL,

    weight_grams DECIMAL(12,3) NOT NULL,
    buy_price_per_gram DECIMAL(14,4) NOT NULL,
    line_total DECIMAL(16,2) NOT NULL,
    notes VARCHAR(1000) NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_billing_old_gold_movement (old_gold_movement_id),
    KEY ix_billing_old_gold_invoice (invoice_id),
    KEY ix_billing_old_gold_karat (karat_id),

    CONSTRAINT fk_billing_old_gold_invoice
        FOREIGN KEY (invoice_id)
        REFERENCES billing_invoices(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_billing_old_gold_movement
        FOREIGN KEY (old_gold_movement_id)
        REFERENCES old_gold_movements(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_billing_old_gold_karat
        FOREIGN KEY (karat_id)
        REFERENCES karats(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT chk_billing_old_gold_weight
        CHECK (weight_grams > 0),

    CONSTRAINT chk_billing_old_gold_price
        CHECK (buy_price_per_gram > 0),

    CONSTRAINT chk_billing_old_gold_total
        CHECK (line_total >= 0)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- REGISTER MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    13,
    'Schema consolidation: prototype-era tables and columns, acquisition source fix'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);


SET SESSION sql_mode = @masgold_saved_sql_mode;
