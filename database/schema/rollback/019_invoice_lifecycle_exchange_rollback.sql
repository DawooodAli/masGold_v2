-- ============================================================
-- masGold v2
-- Rollback of migration 019: invoice lifecycle (Exchange V1)
--
-- Removes ONLY objects created by 019, and only while nothing
-- has used them. Every guard runs before the first change; any
-- failed guard stops the script with
-- "Table '<db>.ROLLBACK_019_REFUSED_<reason>' doesn't exist".
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
        AND TABLE_NAME IN ('invoice_lifecycle_transactions', 'invoice_lifecycle_lines',
                           'invoice_lifecycle_payments')) = 3,
    'SELECT (SELECT COUNT(*) FROM invoice_lifecycle_transactions)
          + (SELECT COUNT(*) FROM invoice_lifecycle_lines)
          + (SELECT COUNT(*) FROM invoice_lifecycle_payments)
       INTO @masgold_rows',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0, 'SELECT 1 FROM ROLLBACK_019_REFUSED_lifecycle_rows_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM inventory_items WHERE inventory_status = 'RETURNED_PENDING')
    OR EXISTS (SELECT 1 FROM inventory_movements
                WHERE from_inventory_status = 'RETURNED_PENDING' OR to_inventory_status = 'RETURNED_PENDING'),
    'SELECT 1 FROM ROLLBACK_019_REFUSED_returned_pending_in_use', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM invoice_events WHERE event_type = 'EXCHANGED'),
    'SELECT 1 FROM ROLLBACK_019_REFUSED_exchange_events_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM inventory_movements m
              JOIN inventory_movement_types t ON t.id = m.movement_type_id
             WHERE t.code IN ('EXCHANGE_RETURN_IN', 'EXCHANGE_REPLACEMENT_OUT'))
    OR EXISTS (SELECT 1 FROM cash_movements m
                 JOIN cash_movement_types t ON t.id = m.movement_type_id
                WHERE t.code IN ('EXCHANGE_PAYMENT_IN', 'EXCHANGE_PAYMENT_OUT'))
    OR EXISTS (SELECT 1 FROM bank_movements m
                 JOIN bank_movement_types t ON t.id = m.movement_type_id
                WHERE t.code IN ('EXCHANGE_PAYMENT_IN', 'EXCHANGE_PAYMENT_OUT'))
    OR EXISTS (SELECT 1 FROM customer_transactions ct
                 JOIN customer_transaction_types t ON t.id = ct.transaction_type_id
                WHERE t.code IN ('EXCHANGE_BALANCE_DUE', 'EXCHANGE_CREDIT'))
    OR EXISTS (SELECT 1 FROM role_permissions rp
                 JOIN permissions p ON p.id = rp.permission_id
                 JOIN roles r ON r.id = rp.role_id
                WHERE p.code IN ('INVOICE_EXCHANGE')
                  AND r.code NOT IN ('OWNER')),
    'SELECT 1 FROM ROLLBACK_019_REFUSED_new_codes_in_use', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. PERMISSION, CODES, LIFECYCLE TABLES
-- ============================================================

DELETE FROM role_permissions
 WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('INVOICE_EXCHANGE'));
DELETE FROM permissions WHERE code IN ('INVOICE_EXCHANGE');

DELETE FROM customer_transaction_types WHERE code IN ('EXCHANGE_BALANCE_DUE', 'EXCHANGE_CREDIT');
DELETE FROM bank_movement_types WHERE code IN ('EXCHANGE_PAYMENT_IN', 'EXCHANGE_PAYMENT_OUT');
DELETE FROM cash_movement_types WHERE code IN ('EXCHANGE_PAYMENT_IN', 'EXCHANGE_PAYMENT_OUT');
DELETE FROM inventory_movement_types WHERE code IN ('EXCHANGE_RETURN_IN', 'EXCHANGE_REPLACEMENT_OUT');

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_payments'),
    "DROP TABLE invoice_lifecycle_payments",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'),
    "DROP TABLE invoice_lifecycle_lines",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_transactions'),
    "DROP TABLE invoice_lifecycle_transactions",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 2. RESTORE THE ORIGINAL STATUS / EVENT LISTS (003, 006)
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'invoice_events'
               AND tc.CONSTRAINT_NAME = 'chk_invoice_event_type' AND cc.CHECK_CLAUSE LIKE '%EXCHANGED%'),
    "ALTER TABLE invoice_events DROP CHECK chk_invoice_event_type",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_events'
                   AND CONSTRAINT_NAME = 'chk_invoice_event_type'),
    "ALTER TABLE invoice_events ADD CONSTRAINT chk_invoice_event_type CHECK (
        event_type IN ('CREATED', 'FINALIZED', 'PRINTED', 'REPRINTED', 'PAYMENT_ADDED', 'PAYMENT_CORRECTED',
                       'COPIED_TO_NEW_BILL', 'VOIDED', 'MIGRATED')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'inventory_movements'
               AND tc.CONSTRAINT_NAME = 'chk_inventory_move_to_status'
               AND cc.CHECK_CLAUSE LIKE '%RETURNED_PENDING%'),
    "ALTER TABLE inventory_movements DROP CHECK chk_inventory_move_to_status",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_movements'
                   AND CONSTRAINT_NAME = 'chk_inventory_move_to_status'),
    "ALTER TABLE inventory_movements ADD CONSTRAINT chk_inventory_move_to_status CHECK (
        to_inventory_status IS NULL
        OR to_inventory_status IN ('IN_STOCK', 'SOLD', 'SCRAPPED', 'REMOVED')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'inventory_movements'
               AND tc.CONSTRAINT_NAME = 'chk_inventory_move_from_status'
               AND cc.CHECK_CLAUSE LIKE '%RETURNED_PENDING%'),
    "ALTER TABLE inventory_movements DROP CHECK chk_inventory_move_from_status",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_movements'
                   AND CONSTRAINT_NAME = 'chk_inventory_move_from_status'),
    "ALTER TABLE inventory_movements ADD CONSTRAINT chk_inventory_move_from_status CHECK (
        from_inventory_status IS NULL
        OR from_inventory_status IN ('IN_STOCK', 'SOLD', 'SCRAPPED', 'REMOVED')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'inventory_items'
               AND tc.CONSTRAINT_NAME = 'chk_inventory_status' AND cc.CHECK_CLAUSE LIKE '%RETURNED_PENDING%'),
    "ALTER TABLE inventory_items DROP CHECK chk_inventory_status",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_items'
                   AND CONSTRAINT_NAME = 'chk_inventory_status'),
    "ALTER TABLE inventory_items ADD CONSTRAINT chk_inventory_status CHECK (
        inventory_status IN ('IN_STOCK', 'SOLD', 'SCRAPPED', 'REMOVED')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


DELETE FROM schema_migrations WHERE version = 19;

SET SESSION sql_mode = @masgold_saved_sql_mode;
