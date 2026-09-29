-- ============================================================
-- masGold v2
-- Rollback of migration 022: Return V1
--
-- Removes ONLY objects created by 022, and only while nothing
-- has used them. Every guard runs before the first change; any
-- failed guard stops the script with
-- "Table '<db>.ROLLBACK_022_REFUSED_<reason>' doesn't exist".
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
    (SELECT COUNT(*) FROM information_schema.TABLES
      WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME IN ('invoice_returns', 'invoice_return_lines', 'invoice_return_payments')) = 3,
    'SELECT (SELECT COUNT(*) FROM invoice_returns)
          + (SELECT COUNT(*) FROM invoice_return_lines)
          + (SELECT COUNT(*) FROM invoice_return_payments)
       INTO @masgold_rows',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0, 'SELECT 1 FROM ROLLBACK_022_REFUSED_return_rows_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_rows := 0;
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
               AND COLUMN_NAME = 'return_line_id'),
    'SELECT COUNT(*) INTO @masgold_rows FROM management_reviews
      WHERE review_type = ''RETURN_APPROVAL'' OR return_id IS NOT NULL OR return_line_id IS NOT NULL',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0, 'SELECT 1 FROM ROLLBACK_022_REFUSED_return_reviews_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM invoice_events WHERE event_type = 'RETURNED'),
    'SELECT 1 FROM ROLLBACK_022_REFUSED_return_events_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM inventory_movements m
              JOIN inventory_movement_types t ON t.id = m.movement_type_id
             WHERE t.code IN ('INVOICE_RETURN_IN'))
    OR EXISTS (SELECT 1 FROM old_gold_movements m
                 JOIN old_gold_movement_types t ON t.id = m.movement_type_id
                WHERE t.code IN ('RETURN_OLD_GOLD_IN'))
    OR EXISTS (SELECT 1 FROM cash_movements m
                 JOIN cash_movement_types t ON t.id = m.movement_type_id
                WHERE t.code IN ('RETURN_PAYMENT_OUT'))
    OR EXISTS (SELECT 1 FROM bank_movements m
                 JOIN bank_movement_types t ON t.id = m.movement_type_id
                WHERE t.code IN ('RETURN_PAYMENT_OUT'))
    OR EXISTS (SELECT 1 FROM customer_transactions ct
                 JOIN customer_transaction_types t ON t.id = ct.transaction_type_id
                WHERE t.code IN ('RETURN_CREDIT'))
    OR EXISTS (SELECT 1 FROM role_permissions rp
                 JOIN permissions p ON p.id = rp.permission_id
                 JOIN roles r ON r.id = rp.role_id
                WHERE p.code IN ('INVOICE_RETURN')
                  AND r.code NOT IN ('OWNER', 'MANAGER', 'SALESPERSON', 'CASHIER')),
    'SELECT 1 FROM ROLLBACK_022_REFUSED_new_codes_in_use', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. PERMISSION AND CODES
-- ============================================================

DELETE FROM role_permissions
 WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('INVOICE_RETURN'));
DELETE FROM permissions WHERE code IN ('INVOICE_RETURN');

DELETE FROM customer_transaction_types WHERE code IN ('RETURN_CREDIT');
DELETE FROM bank_movement_types WHERE code IN ('RETURN_PAYMENT_OUT');
DELETE FROM cash_movement_types WHERE code IN ('RETURN_PAYMENT_OUT');
DELETE FROM old_gold_movement_types WHERE code IN ('RETURN_OLD_GOLD_IN');
DELETE FROM inventory_movement_types WHERE code IN ('INVOICE_RETURN_IN');


-- ============================================================
-- 2. INVOICE EVENT LIST (019 definition)
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'invoice_events'
               AND tc.CONSTRAINT_NAME = 'chk_invoice_event_type' AND cc.CHECK_CLAUSE LIKE '%RETURNED%'),
    "ALTER TABLE invoice_events DROP CHECK chk_invoice_event_type",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_events'
                   AND CONSTRAINT_NAME = 'chk_invoice_event_type'),
    "ALTER TABLE invoice_events ADD CONSTRAINT chk_invoice_event_type CHECK (
        event_type IN ('CREATED', 'FINALIZED', 'PRINTED', 'REPRINTED', 'PAYMENT_ADDED', 'PAYMENT_CORRECTED',
                       'COPIED_TO_NEW_BILL', 'VOIDED', 'MIGRATED', 'EXCHANGED')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 3. MANAGEMENT REVIEWS (021 definitions)
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'management_reviews'
               AND tc.CONSTRAINT_NAME = 'chk_management_review_inspection'
               AND cc.CHECK_CLAUSE LIKE '%RETURN_APPROVAL%'),
    "ALTER TABLE management_reviews DROP CHECK chk_management_review_inspection", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
               AND CONSTRAINT_NAME = 'fk_management_review_return_line'),
    "ALTER TABLE management_reviews DROP FOREIGN KEY fk_management_review_return_line", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
               AND CONSTRAINT_NAME = 'fk_management_review_return'),
    "ALTER TABLE management_reviews DROP FOREIGN KEY fk_management_review_return", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
               AND INDEX_NAME = 'uq_management_review_return_line'),
    "ALTER TABLE management_reviews DROP INDEX uq_management_review_return_line", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
               AND INDEX_NAME = 'idx_management_review_return'),
    "ALTER TABLE management_reviews DROP INDEX idx_management_review_return", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
               AND COLUMN_NAME = 'return_line_id'),
    "ALTER TABLE management_reviews DROP COLUMN return_line_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
               AND COLUMN_NAME = 'return_id'),
    "ALTER TABLE management_reviews DROP COLUMN return_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND CONSTRAINT_NAME = 'chk_management_review_inspection'),
    "ALTER TABLE management_reviews ADD CONSTRAINT chk_management_review_inspection CHECK (
        (review_type = 'RETURNED_ITEM_INSPECTION'
         AND lifecycle_line_id IS NOT NULL
         AND ((review_status = 'OPEN' AND resolution_code IS NULL)
              OR (review_status = 'RESOLVED' AND resolution_code IS NOT NULL
                  AND resolution_code IN ('RETURN_TO_STOCK', 'CONVERT_TO_OLD_GOLD'))))
        OR (review_type <> 'RETURNED_ITEM_INSPECTION'
            AND lifecycle_line_id IS NULL AND resolution_code IS NULL)
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'management_reviews'
               AND tc.CONSTRAINT_NAME = 'chk_management_review_type'
               AND cc.CHECK_CLAUSE LIKE '%RETURN_APPROVAL%'),
    "ALTER TABLE management_reviews DROP CHECK chk_management_review_type", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND CONSTRAINT_NAME = 'chk_management_review_type'),
    "ALTER TABLE management_reviews ADD CONSTRAINT chk_management_review_type CHECK (
        review_type IN ('MAKING_PRICE', 'CASH_SETTLEMENT', 'OLD_GOLD_DISCOUNT', 'RETURNED_ITEM_INSPECTION')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 4. RETURN TABLES
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_return_payments'),
    "DROP TABLE invoice_return_payments",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_return_lines'),
    "DROP TABLE invoice_return_lines",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_returns'),
    "DROP TABLE invoice_returns",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


DELETE FROM schema_migrations WHERE version = 22;

SET SESSION sql_mode = @masgold_saved_sql_mode;
