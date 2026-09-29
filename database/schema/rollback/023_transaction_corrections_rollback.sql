-- ============================================================
-- masGold v2
-- Rollback of migration 023: Reversal / Correction V1
--
-- Removes ONLY objects created by 023, and only while nothing
-- has used them. Every guard runs before the first change; any
-- failed guard stops the script with
-- "Table '<db>.ROLLBACK_023_REFUSED_<reason>' doesn't exist".
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
        AND TABLE_NAME IN ('transaction_corrections', 'transaction_correction_legs')) = 2,
    'SELECT (SELECT COUNT(*) FROM transaction_corrections) + (SELECT COUNT(*) FROM transaction_correction_legs)
       INTO @masgold_rows',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0, 'SELECT 1 FROM ROLLBACK_023_REFUSED_corrections_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_rows := 0;
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_return_lines'
               AND COLUMN_NAME = 'reversed_by_correction_id')
    AND EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND COLUMN_NAME = 'reversed_by_correction_id'),
    'SELECT (SELECT COUNT(*) FROM invoice_lifecycle_lines WHERE reversed_by_correction_id IS NOT NULL)
          + (SELECT COUNT(*) FROM invoice_return_lines WHERE reversed_by_correction_id IS NOT NULL)
          + (SELECT COUNT(*) FROM management_reviews
              WHERE review_type = ''RETURNED_ITEM_INSPECTION'' AND review_status = ''DISMISSED'')
       INTO @masgold_rows',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0, 'SELECT 1 FROM ROLLBACK_023_REFUSED_reversed_lines_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM invoice_events WHERE event_type IN ('REVERSED', 'SETTLEMENT_CORRECTED')),
    'SELECT 1 FROM ROLLBACK_023_REFUSED_correction_events_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM inventory_movements m
              JOIN inventory_movement_types t ON t.id = m.movement_type_id
             WHERE t.code IN ('REVERSAL_SALE_RESTOCK', 'REVERSAL_RETURN_TO_SOLD',
                              'REVERSAL_EXCHANGE_REPLACEMENT_RESTOCK', 'REVERSAL_EXCHANGE_RETURN_TO_SOLD'))
    OR EXISTS (SELECT 1 FROM old_gold_movements m
                 JOIN old_gold_movement_types t ON t.id = m.movement_type_id
                WHERE t.code IN ('REVERSAL_OLD_GOLD_SALE_IN', 'REVERSAL_OLD_GOLD_PURCHASE_OUT',
                                 'REVERSAL_RETURN_OLD_GOLD_OUT', 'REVERSAL_EXCHANGE_OLD_GOLD_IN'))
    OR EXISTS (SELECT 1 FROM cash_movements m
                 JOIN cash_movement_types t ON t.id = m.movement_type_id
                WHERE t.code IN ('REVERSAL_PAYMENT', 'SETTLEMENT_CORRECTION'))
    OR EXISTS (SELECT 1 FROM bank_movements m
                 JOIN bank_movement_types t ON t.id = m.movement_type_id
                WHERE t.code IN ('REVERSAL_PAYMENT', 'SETTLEMENT_CORRECTION'))
    OR EXISTS (SELECT 1 FROM customer_transactions ct
                 JOIN customer_transaction_types t ON t.id = ct.transaction_type_id
                WHERE t.code IN ('REVERSAL_ACCOUNT', 'SETTLEMENT_CORRECTION'))
    OR EXISTS (SELECT 1 FROM role_permissions rp
                 JOIN permissions p ON p.id = rp.permission_id
                 JOIN roles r ON r.id = rp.role_id
                WHERE p.code IN ('TRANSACTION_CORRECT')
                  AND r.code NOT IN ('OWNER', 'MANAGER')),
    'SELECT 1 FROM ROLLBACK_023_REFUSED_new_codes_in_use', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. PERMISSION AND CODES
-- ============================================================

DELETE FROM role_permissions
 WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('TRANSACTION_CORRECT'));
DELETE FROM permissions WHERE code IN ('TRANSACTION_CORRECT');

DELETE FROM customer_transaction_types WHERE code IN ('REVERSAL_ACCOUNT', 'SETTLEMENT_CORRECTION');
DELETE FROM bank_movement_types WHERE code IN ('REVERSAL_PAYMENT', 'SETTLEMENT_CORRECTION');
DELETE FROM cash_movement_types WHERE code IN ('REVERSAL_PAYMENT', 'SETTLEMENT_CORRECTION');
DELETE FROM old_gold_movement_types WHERE code IN ('REVERSAL_OLD_GOLD_SALE_IN', 'REVERSAL_OLD_GOLD_PURCHASE_OUT',
                                                   'REVERSAL_RETURN_OLD_GOLD_OUT', 'REVERSAL_EXCHANGE_OLD_GOLD_IN');
DELETE FROM inventory_movement_types WHERE code IN ('REVERSAL_SALE_RESTOCK', 'REVERSAL_RETURN_TO_SOLD',
                                                    'REVERSAL_EXCHANGE_REPLACEMENT_RESTOCK',
                                                    'REVERSAL_EXCHANGE_RETURN_TO_SOLD');


-- ============================================================
-- 2. INVOICE EVENT LIST (022 definition)
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'invoice_events'
               AND tc.CONSTRAINT_NAME = 'chk_invoice_event_type'
               AND cc.CHECK_CLAUSE LIKE '%SETTLEMENT_CORRECTED%'),
    "ALTER TABLE invoice_events DROP CHECK chk_invoice_event_type",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_events'
                   AND CONSTRAINT_NAME = 'chk_invoice_event_type'),
    "ALTER TABLE invoice_events ADD CONSTRAINT chk_invoice_event_type CHECK (
        event_type IN ('CREATED', 'FINALIZED', 'PRINTED', 'REPRINTED', 'PAYMENT_ADDED', 'PAYMENT_CORRECTED',
                       'COPIED_TO_NEW_BILL', 'VOIDED', 'MIGRATED', 'EXCHANGED', 'RETURNED')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 3. REVIEW CHECK (022 definition)
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'management_reviews'
               AND tc.CONSTRAINT_NAME = 'chk_management_review_inspection'
               AND LOCATE('DISMISSED', cc.CHECK_CLAUSE) < LOCATE('RETURN_APPROVAL', cc.CHECK_CLAUSE)),
    "ALTER TABLE management_reviews DROP CHECK chk_management_review_inspection",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND CONSTRAINT_NAME = 'chk_management_review_inspection'),
    "ALTER TABLE management_reviews ADD CONSTRAINT chk_management_review_inspection CHECK (
        (review_type = 'RETURNED_ITEM_INSPECTION'
         AND return_id IS NULL
         AND ((lifecycle_line_id IS NOT NULL AND return_line_id IS NULL)
              OR (lifecycle_line_id IS NULL AND return_line_id IS NOT NULL))
         AND ((review_status = 'OPEN' AND resolution_code IS NULL)
              OR (review_status = 'RESOLVED' AND resolution_code IS NOT NULL
                  AND resolution_code IN ('RETURN_TO_STOCK', 'CONVERT_TO_OLD_GOLD'))))
        OR (review_type = 'RETURN_APPROVAL'
            AND return_id IS NOT NULL AND lifecycle_line_id IS NULL AND return_line_id IS NULL
            AND ((review_status = 'OPEN' AND resolution_code IS NULL)
                 OR (review_status = 'RESOLVED' AND resolution_code IS NOT NULL
                     AND resolution_code IN ('APPROVED', 'REJECTED'))
                 OR (review_status = 'DISMISSED' AND resolution_code IS NULL)))
        OR (review_type NOT IN ('RETURNED_ITEM_INSPECTION', 'RETURN_APPROVAL')
            AND lifecycle_line_id IS NULL AND return_id IS NULL AND return_line_id IS NULL
            AND resolution_code IS NULL)
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 4. ONE-USE KEYS (019 / 022 definitions) and the reversal links
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_return_lines'
                   AND INDEX_NAME = 'uq_return_identified_source_line'),
    "ALTER TABLE invoice_return_lines ADD UNIQUE KEY uq_return_identified_source_line (identified_source_line_id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_return_lines'
               AND INDEX_NAME = 'uq_return_active_identified_source_line'),
    "ALTER TABLE invoice_return_lines DROP INDEX uq_return_active_identified_source_line",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_return_lines'
               AND COLUMN_NAME = 'active_identified_source_line_id'),
    "ALTER TABLE invoice_return_lines DROP COLUMN active_identified_source_line_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_return_lines'
               AND CONSTRAINT_NAME = 'fk_return_line_reversed_by'),
    "ALTER TABLE invoice_return_lines DROP FOREIGN KEY fk_return_line_reversed_by",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_return_lines'
               AND COLUMN_NAME = 'reversed_by_correction_id'),
    "ALTER TABLE invoice_return_lines DROP COLUMN reversed_by_correction_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND INDEX_NAME = 'uq_lifecycle_source_line'),
    "ALTER TABLE invoice_lifecycle_lines ADD UNIQUE KEY uq_lifecycle_source_line (source_invoice_line_id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND INDEX_NAME = 'uq_lifecycle_active_source_line'),
    "ALTER TABLE invoice_lifecycle_lines DROP INDEX uq_lifecycle_active_source_line",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND INDEX_NAME = 'idx_lifecycle_line_source'),
    "ALTER TABLE invoice_lifecycle_lines DROP INDEX idx_lifecycle_line_source",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND COLUMN_NAME = 'active_source_line_id'),
    "ALTER TABLE invoice_lifecycle_lines DROP COLUMN active_source_line_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND CONSTRAINT_NAME = 'fk_lifecycle_line_reversed_by'),
    "ALTER TABLE invoice_lifecycle_lines DROP FOREIGN KEY fk_lifecycle_line_reversed_by",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND COLUMN_NAME = 'reversed_by_correction_id'),
    "ALTER TABLE invoice_lifecycle_lines DROP COLUMN reversed_by_correction_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 5. CORRECTION TABLES
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'transaction_correction_legs'),
    "DROP TABLE transaction_correction_legs",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'transaction_corrections'),
    "DROP TABLE transaction_corrections",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


DELETE FROM schema_migrations WHERE version = 23;

SET SESSION sql_mode = @masgold_saved_sql_mode;
