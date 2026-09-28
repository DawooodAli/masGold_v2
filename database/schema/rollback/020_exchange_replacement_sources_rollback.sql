-- ============================================================
-- masGold v2
-- Rollback of migration 020: Exchange replacement sources
--
-- Removes ONLY objects created by 020, and only while nothing
-- has used them. Every guard runs before the first change; any
-- failed guard stops the script with
-- "Table '<db>.ROLLBACK_020_REFUSED_<reason>' doesn't exist".
--
-- Re-runnable: each step runs only while its object still exists.
-- ============================================================


SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. GUARDS
-- ============================================================

SET @masgold_rows := 0;
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND COLUMN_NAME = 'old_gold_movement_id'),
    'SELECT COUNT(*) INTO @masgold_rows FROM invoice_lifecycle_lines
      WHERE inventory_item_id IS NULL OR old_gold_movement_id IS NOT NULL OR reason_code IS NOT NULL
         OR notes IS NOT NULL OR factory_snapshot IS NOT NULL OR size_snapshot IS NOT NULL
         OR identification_method IN (''MANUAL'', ''OLD_GOLD_STOCK'')',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0, 'SELECT 1 FROM ROLLBACK_020_REFUSED_new_replacement_sources_used', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_rows := 0;
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
               AND COLUMN_NAME = 'lifecycle_line_id'),
    'SELECT COUNT(*) INTO @masgold_rows FROM inventory_reconciliation_cases WHERE lifecycle_line_id IS NOT NULL',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0, 'SELECT 1 FROM ROLLBACK_020_REFUSED_exchange_cases_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM old_gold_movements m
              JOIN old_gold_movement_types t ON t.id = m.movement_type_id
             WHERE t.code IN ('EXCHANGE_REPLACEMENT_OUT')),
    'SELECT 1 FROM ROLLBACK_020_REFUSED_new_codes_in_use', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. OLD-GOLD CODE
-- ============================================================

DELETE FROM old_gold_movement_types WHERE code IN ('EXCHANGE_REPLACEMENT_OUT');


-- ============================================================
-- 2. RECONCILIATION CASE LINK
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
               AND CONSTRAINT_NAME = 'chk_reconciliation_lifecycle_line'),
    "ALTER TABLE inventory_reconciliation_cases DROP CHECK chk_reconciliation_lifecycle_line", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
               AND CONSTRAINT_NAME = 'fk_reconciliation_lifecycle_line'),
    "ALTER TABLE inventory_reconciliation_cases DROP FOREIGN KEY fk_reconciliation_lifecycle_line", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
               AND INDEX_NAME = 'uq_reconciliation_lifecycle_line'),
    "ALTER TABLE inventory_reconciliation_cases DROP INDEX uq_reconciliation_lifecycle_line", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
               AND COLUMN_NAME = 'lifecycle_line_id'),
    "ALTER TABLE inventory_reconciliation_cases DROP COLUMN lifecycle_line_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 3. LIFECYCLE LINES: restore the 019 definitions
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND CONSTRAINT_NAME = 'chk_lifecycle_line_reason_code'),
    "ALTER TABLE invoice_lifecycle_lines DROP CHECK chk_lifecycle_line_reason_code", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND CONSTRAINT_NAME = 'chk_lifecycle_line_source_identity'),
    "ALTER TABLE invoice_lifecycle_lines DROP CHECK chk_lifecycle_line_source_identity", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'invoice_lifecycle_lines'
               AND tc.CONSTRAINT_NAME = 'chk_lifecycle_line_identification'
               AND cc.CHECK_CLAUSE LIKE '%OLD_GOLD_STOCK%'),
    "ALTER TABLE invoice_lifecycle_lines DROP CHECK chk_lifecycle_line_identification", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND CONSTRAINT_NAME = 'chk_lifecycle_line_identification'),
    "ALTER TABLE invoice_lifecycle_lines ADD CONSTRAINT chk_lifecycle_line_identification CHECK (
        identification_method IS NULL OR identification_method IN ('SCAN', 'SEARCH')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND CONSTRAINT_NAME = 'fk_lifecycle_line_old_gold_movement'),
    "ALTER TABLE invoice_lifecycle_lines DROP FOREIGN KEY fk_lifecycle_line_old_gold_movement", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND INDEX_NAME = 'uq_lifecycle_line_old_gold_movement'),
    "ALTER TABLE invoice_lifecycle_lines DROP INDEX uq_lifecycle_line_old_gold_movement", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND COLUMN_NAME = 'old_gold_movement_id'),
    "ALTER TABLE invoice_lifecycle_lines DROP COLUMN old_gold_movement_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND COLUMN_NAME = 'reason_code'),
    "ALTER TABLE invoice_lifecycle_lines DROP COLUMN reason_code", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND COLUMN_NAME = 'notes'),
    "ALTER TABLE invoice_lifecycle_lines DROP COLUMN notes", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND COLUMN_NAME = 'factory_snapshot'),
    "ALTER TABLE invoice_lifecycle_lines DROP COLUMN factory_snapshot", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND COLUMN_NAME = 'size_snapshot'),
    "ALTER TABLE invoice_lifecycle_lines DROP COLUMN size_snapshot", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Every remaining line has an inventory piece (guarded above): NOT NULL again.
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND COLUMN_NAME = 'inventory_item_id' AND IS_NULLABLE = 'YES'),
    "ALTER TABLE invoice_lifecycle_lines MODIFY COLUMN inventory_item_id BIGINT UNSIGNED NOT NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


DELETE FROM schema_migrations WHERE version = 20;

SET SESSION sql_mode = @masgold_saved_sql_mode;
