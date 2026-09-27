-- ============================================================
-- masGold v2
-- Rollback of migration 016: Billing shop workflow
--
-- Removes ONLY objects created by 016, and only while nothing
-- has used them. Every guard runs before the first change; any
-- failed guard stops the script with
-- "Table '<db>.ROLLBACK_016_REFUSED_<reason>' doesn't exist".
--
-- Re-runnable: each step runs only while its object still exists.
-- ============================================================


SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. GUARDS
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM invoice_lines WHERE line_type IN ('SELL_OLD_GOLD', 'SELL_UNIDENTIFIED')),
    'SELECT 1 FROM ROLLBACK_016_REFUSED_new_line_kinds_used', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_rows := 0;
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND COLUMN_NAME = 'identification_method'),
    'SELECT COUNT(*) INTO @masgold_rows FROM invoice_lines WHERE identification_method IS NOT NULL',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0, 'SELECT 1 FROM ROLLBACK_016_REFUSED_identification_recorded', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_rows := 0;
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
               AND COLUMN_NAME = 'invoice_id'),
    'SELECT COUNT(*) INTO @masgold_rows FROM inventory_reconciliation_cases
      WHERE invoice_id IS NOT NULL OR invoice_line_id IS NOT NULL OR reason_code IS NOT NULL
         OR observed_size IS NOT NULL OR salesperson_employee_id IS NOT NULL OR sold_at IS NOT NULL',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0, 'SELECT 1 FROM ROLLBACK_016_REFUSED_invoice_cases_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_rows := 0;
SET @masgold_sql := IF(
    (SELECT COUNT(*) FROM information_schema.TABLES
      WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME IN ('inventory_reconciliation_case_events', 'invoice_settlement_adjustments',
                           'management_reviews')) = 3,
    'SELECT (SELECT COUNT(*) FROM inventory_reconciliation_case_events)
          + (SELECT COUNT(*) FROM invoice_settlement_adjustments)
          + (SELECT COUNT(*) FROM management_reviews)
       INTO @masgold_rows',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0, 'SELECT 1 FROM ROLLBACK_016_REFUSED_workflow_rows_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM old_gold_movements m
              JOIN old_gold_movement_types t ON t.id = m.movement_type_id
             WHERE t.code IN ('INVOICE_SALE_OUT'))
    OR EXISTS (SELECT 1 FROM role_permissions rp
                 JOIN permissions p ON p.id = rp.permission_id
                 JOIN roles r ON r.id = rp.role_id
                WHERE p.code IN ('INVOICE_SETTLEMENT_APPROVE', 'MANAGEMENT_REVIEW', 'INVENTORY_RECONCILE')
                  AND r.code NOT IN ('OWNER', 'MANAGER')),
    'SELECT 1 FROM ROLLBACK_016_REFUSED_new_codes_in_use', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. PERMISSIONS, SETTINGS, REVIEWS, SETTLEMENT, CASE EVENTS
-- ============================================================

DELETE FROM role_permissions
 WHERE permission_id IN (SELECT id FROM permissions
                          WHERE code IN ('INVOICE_SETTLEMENT_APPROVE', 'MANAGEMENT_REVIEW', 'INVENTORY_RECONCILE'));
DELETE FROM permissions WHERE code IN ('INVOICE_SETTLEMENT_APPROVE', 'MANAGEMENT_REVIEW', 'INVENTORY_RECONCILE');

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'billing_settings'),
    "DROP TABLE billing_settings",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'),
    "DROP TABLE management_reviews",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_settlement_adjustments'),
    "DROP TABLE invoice_settlement_adjustments",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_case_events'),
    "DROP TABLE inventory_reconciliation_case_events",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

DELETE FROM old_gold_movement_types WHERE code IN ('INVOICE_SALE_OUT');


-- ============================================================
-- 2. RECONCILIATION CASE COLUMNS
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND CONSTRAINT_NAME = 'fk_reconciliation_invoice'),
    "ALTER TABLE inventory_reconciliation_cases DROP FOREIGN KEY fk_reconciliation_invoice",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND CONSTRAINT_NAME = 'fk_reconciliation_invoice_line'),
    "ALTER TABLE inventory_reconciliation_cases DROP FOREIGN KEY fk_reconciliation_invoice_line",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND CONSTRAINT_NAME = 'fk_reconciliation_salesperson'),
    "ALTER TABLE inventory_reconciliation_cases DROP FOREIGN KEY fk_reconciliation_salesperson",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND CONSTRAINT_NAME = 'chk_reconciliation_reason_code'),
    "ALTER TABLE inventory_reconciliation_cases DROP CHECK chk_reconciliation_reason_code",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND CONSTRAINT_NAME = 'chk_reconciliation_unidentified_sale'),
    "ALTER TABLE inventory_reconciliation_cases DROP CHECK chk_reconciliation_unidentified_sale",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND INDEX_NAME = 'uq_reconciliation_invoice_line'),
    "ALTER TABLE inventory_reconciliation_cases DROP INDEX uq_reconciliation_invoice_line",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND INDEX_NAME = 'idx_reconciliation_invoice'),
    "ALTER TABLE inventory_reconciliation_cases DROP INDEX idx_reconciliation_invoice",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND INDEX_NAME = 'fk_reconciliation_salesperson'),
    "ALTER TABLE inventory_reconciliation_cases DROP INDEX fk_reconciliation_salesperson",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 003's FK on matched_inventory_item_id used the unique key while 016 existed;
-- give it back its original index before dropping the unique key.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND INDEX_NAME = 'fk_reconciliation_matched_item'),
    "ALTER TABLE inventory_reconciliation_cases ADD KEY fk_reconciliation_matched_item (matched_inventory_item_id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND INDEX_NAME = 'uq_reconciliation_matched_item'),
    "ALTER TABLE inventory_reconciliation_cases DROP INDEX uq_reconciliation_matched_item",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND COLUMN_NAME = 'invoice_id'),
    "ALTER TABLE inventory_reconciliation_cases DROP COLUMN invoice_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND COLUMN_NAME = 'invoice_line_id'),
    "ALTER TABLE inventory_reconciliation_cases DROP COLUMN invoice_line_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND COLUMN_NAME = 'observed_size'),
    "ALTER TABLE inventory_reconciliation_cases DROP COLUMN observed_size",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND COLUMN_NAME = 'reason_code'),
    "ALTER TABLE inventory_reconciliation_cases DROP COLUMN reason_code",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND COLUMN_NAME = 'salesperson_employee_id'),
    "ALTER TABLE inventory_reconciliation_cases DROP COLUMN salesperson_employee_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND COLUMN_NAME = 'sold_at'),
    "ALTER TABLE inventory_reconciliation_cases DROP COLUMN sold_at",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 3. INVOICE LINES: restore the 014 rules
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND CONSTRAINT_NAME = 'chk_invoice_line_native_old_gold_sale'),
    "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_native_old_gold_sale", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND CONSTRAINT_NAME = 'chk_invoice_line_native_unidentified'),
    "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_native_unidentified", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'invoice_lines'
               AND tc.CONSTRAINT_NAME = 'chk_invoice_line_native_values' AND cc.CHECK_CLAUSE LIKE '%SELL_OLD_GOLD%'),
    "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_native_values",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

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
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'invoice_lines'
               AND tc.CONSTRAINT_NAME = 'chk_invoice_line_type' AND cc.CHECK_CLAUSE LIKE '%SELL_OLD_GOLD%'),
    "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_type",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND CONSTRAINT_NAME = 'chk_invoice_line_type'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_type CHECK (
        line_type IN ('SELL_INVENTORY', 'BUY_OLD_GOLD', 'OTHER')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND CONSTRAINT_NAME = 'chk_invoice_line_identification'),
    "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_identification", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND COLUMN_NAME = 'identification_method'),
    "ALTER TABLE invoice_lines DROP COLUMN identification_method", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


DELETE FROM schema_migrations WHERE version = 16;

SET SESSION sql_mode = @masgold_saved_sql_mode;
