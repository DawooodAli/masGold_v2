-- ============================================================
-- masGold v2
-- Migration 020: Exchange replacement sources (Billing parity)
--
-- An exchange replacement can now be added the same ways Billing
-- adds a sold piece:
--   SCAN / SEARCH    an identified stock piece (inventory item)
--   MANUAL (F5)      a piece without inventory identity; opens the
--                    same UNIDENTIFIED_SALE reconciliation case as
--                    Billing F5 (manager review), linked to the
--                    lifecycle line
--   OLD_GOLD_STOCK   (F7) weight from the COUNTED old-gold stock,
--                    linked to its own EXCHANGE_REPLACEMENT_OUT
--                    old-gold movement
-- A manual / F7 line never carries an inventory id.
--
-- The target database is chosen by the caller; this file never
-- names a database. Re-runnable and additive.
--
-- Rollback: rollback/020_exchange_replacement_sources_rollback.sql
-- ============================================================


SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. PREFLIGHT
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 19),
    'SELECT 1 FROM MIGRATION_020_REFUSED_migration_019_missing',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. LIFECYCLE LINES: manual and old-gold-stock replacements
-- ============================================================

-- A manual / F7 replacement has no inventory piece.
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND COLUMN_NAME = 'inventory_item_id' AND IS_NULLABLE = 'NO'),
    "ALTER TABLE invoice_lifecycle_lines MODIFY COLUMN inventory_item_id BIGINT UNSIGNED NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND COLUMN_NAME = 'old_gold_movement_id'),
    "ALTER TABLE invoice_lifecycle_lines ADD COLUMN old_gold_movement_id BIGINT UNSIGNED NULL AFTER inventory_item_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND COLUMN_NAME = 'reason_code'),
    "ALTER TABLE invoice_lifecycle_lines ADD COLUMN reason_code VARCHAR(30) NULL AFTER identification_method",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND COLUMN_NAME = 'factory_snapshot'),
    "ALTER TABLE invoice_lifecycle_lines ADD COLUMN factory_snapshot VARCHAR(150) NULL AFTER barcode_snapshot",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND COLUMN_NAME = 'size_snapshot'),
    "ALTER TABLE invoice_lifecycle_lines ADD COLUMN size_snapshot VARCHAR(50) NULL AFTER factory_snapshot",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND COLUMN_NAME = 'notes'),
    "ALTER TABLE invoice_lifecycle_lines ADD COLUMN notes VARCHAR(1000) NULL AFTER reason_code",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND INDEX_NAME = 'uq_lifecycle_line_old_gold_movement'),
    "ALTER TABLE invoice_lifecycle_lines ADD UNIQUE KEY uq_lifecycle_line_old_gold_movement (old_gold_movement_id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND CONSTRAINT_NAME = 'fk_lifecycle_line_old_gold_movement'),
    "ALTER TABLE invoice_lifecycle_lines ADD CONSTRAINT fk_lifecycle_line_old_gold_movement
        FOREIGN KEY (old_gold_movement_id) REFERENCES old_gold_movements(id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Identification list: replaced only while it lacks the new sources.
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'invoice_lifecycle_lines'
               AND tc.CONSTRAINT_NAME = 'chk_lifecycle_line_identification'
               AND cc.CHECK_CLAUSE NOT LIKE '%OLD_GOLD_STOCK%'),
    "ALTER TABLE invoice_lifecycle_lines DROP CHECK chk_lifecycle_line_identification",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND CONSTRAINT_NAME = 'chk_lifecycle_line_identification'),
    "ALTER TABLE invoice_lifecycle_lines ADD CONSTRAINT chk_lifecycle_line_identification CHECK (
        identification_method IS NULL
        OR identification_method IN ('SCAN', 'SEARCH', 'MANUAL', 'OLD_GOLD_STOCK')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Each source carries exactly its own identity (never a fake inventory id).
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND CONSTRAINT_NAME = 'chk_lifecycle_line_source_identity'),
    "ALTER TABLE invoice_lifecycle_lines ADD CONSTRAINT chk_lifecycle_line_source_identity CHECK (
        (line_role <> 'RETURNED' OR (inventory_item_id IS NOT NULL AND old_gold_movement_id IS NULL))
        AND (line_role <> 'REPLACEMENT' OR identification_method IS NOT NULL)
        AND (identification_method IS NULL
             OR (identification_method IN ('SCAN', 'SEARCH')
                 AND inventory_item_id IS NOT NULL AND old_gold_movement_id IS NULL)
             OR (identification_method = 'MANUAL'
                 AND inventory_item_id IS NULL AND old_gold_movement_id IS NULL
                 AND reason_code IS NOT NULL AND item_name_snapshot IS NOT NULL)
             OR (identification_method = 'OLD_GOLD_STOCK'
                 AND inventory_item_id IS NULL AND old_gold_movement_id IS NOT NULL
                 AND item_name_snapshot IS NOT NULL))
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND CONSTRAINT_NAME = 'chk_lifecycle_line_reason_code'),
    "ALTER TABLE invoice_lifecycle_lines ADD CONSTRAINT chk_lifecycle_line_reason_code CHECK (
        reason_code IS NULL OR reason_code IN ('NO_BARCODE', 'BARCODE_LOST', 'NOT_FOUND', 'OTHER')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 2. RECONCILIATION CASE (Billing F5 review) for a manual exchange line
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND COLUMN_NAME = 'lifecycle_line_id'),
    "ALTER TABLE inventory_reconciliation_cases ADD COLUMN lifecycle_line_id BIGINT UNSIGNED NULL AFTER invoice_line_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND INDEX_NAME = 'uq_reconciliation_lifecycle_line'),
    "ALTER TABLE inventory_reconciliation_cases ADD UNIQUE KEY uq_reconciliation_lifecycle_line (lifecycle_line_id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND CONSTRAINT_NAME = 'fk_reconciliation_lifecycle_line'),
    "ALTER TABLE inventory_reconciliation_cases ADD CONSTRAINT fk_reconciliation_lifecycle_line
        FOREIGN KEY (lifecycle_line_id) REFERENCES invoice_lifecycle_lines(id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- An exchange case is an unidentified sale without an invoice line of its own.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND CONSTRAINT_NAME = 'chk_reconciliation_lifecycle_line'),
    "ALTER TABLE inventory_reconciliation_cases ADD CONSTRAINT chk_reconciliation_lifecycle_line CHECK (
        lifecycle_line_id IS NULL
        OR (case_type = 'UNIDENTIFIED_SALE' AND invoice_id IS NULL AND invoice_line_id IS NULL
            AND reason_code IS NOT NULL AND sold_at IS NOT NULL AND observed_weight_grams IS NOT NULL)
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 3. OLD GOLD: replacement taken from the old-gold stock (F7)
-- ============================================================

INSERT INTO old_gold_movement_types (
    code,
    name,
    description,
    expected_direction
)
VALUES
    (
        'EXCHANGE_REPLACEMENT_OUT',
        'Exchange Replacement Out',
        'Old-gold weight given to the customer as an exchange replacement (from old-gold stock)',
        'OUT'
    )
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description),
    expected_direction = VALUES(expected_direction);


-- ============================================================
-- REGISTER MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    20,
    'Exchange replacement sources: manual (F5) and old-gold stock (F7) replacements'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);


SET SESSION sql_mode = @masgold_saved_sql_mode;
