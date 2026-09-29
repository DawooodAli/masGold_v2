-- ============================================================
-- masGold v2
-- Rollback of migration 021: Returned Item Inspection
--
-- Removes ONLY objects created by 021, and only while nothing
-- has used them. Every guard runs before the first change; any
-- failed guard stops the script with
-- "Table '<db>.ROLLBACK_021_REFUSED_<reason>' doesn't exist".
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
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
               AND COLUMN_NAME = 'lifecycle_line_id'),
    'SELECT COUNT(*) INTO @masgold_rows FROM management_reviews
      WHERE review_type = ''RETURNED_ITEM_INSPECTION'' OR lifecycle_line_id IS NOT NULL
         OR resolution_code IS NOT NULL',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0, 'SELECT 1 FROM ROLLBACK_021_REFUSED_inspection_reviews_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM inventory_items WHERE inventory_status = 'CONVERTED_TO_OLD_GOLD')
    OR EXISTS (SELECT 1 FROM inventory_movements
                WHERE from_inventory_status = 'CONVERTED_TO_OLD_GOLD'
                   OR to_inventory_status = 'CONVERTED_TO_OLD_GOLD'),
    'SELECT 1 FROM ROLLBACK_021_REFUSED_converted_items_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM inventory_movements m
              JOIN inventory_movement_types t ON t.id = m.movement_type_id
             WHERE t.code IN ('RETURNED_ITEM_RESTOCKED', 'RETURNED_ITEM_TO_OLD_GOLD'))
    OR EXISTS (SELECT 1 FROM old_gold_movements m
                 JOIN old_gold_movement_types t ON t.id = m.movement_type_id
                WHERE t.code IN ('RETURNED_ITEM_TO_OLD_GOLD')),
    'SELECT 1 FROM ROLLBACK_021_REFUSED_new_codes_in_use', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. MOVEMENT CODES
-- ============================================================

DELETE FROM old_gold_movement_types WHERE code IN ('RETURNED_ITEM_TO_OLD_GOLD');

DELETE FROM inventory_movement_types WHERE code IN ('RETURNED_ITEM_RESTOCKED', 'RETURNED_ITEM_TO_OLD_GOLD');


-- ============================================================
-- 2. MANAGEMENT REVIEW: restore the 016 definitions
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
               AND CONSTRAINT_NAME = 'chk_management_review_inspection'),
    "ALTER TABLE management_reviews DROP CHECK chk_management_review_inspection", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
               AND CONSTRAINT_NAME = 'fk_management_review_lifecycle_line'),
    "ALTER TABLE management_reviews DROP FOREIGN KEY fk_management_review_lifecycle_line", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
               AND INDEX_NAME = 'uq_management_review_lifecycle_line'),
    "ALTER TABLE management_reviews DROP INDEX uq_management_review_lifecycle_line", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
               AND COLUMN_NAME = 'resolution_code'),
    "ALTER TABLE management_reviews DROP COLUMN resolution_code", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
               AND COLUMN_NAME = 'lifecycle_line_id'),
    "ALTER TABLE management_reviews DROP COLUMN lifecycle_line_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'management_reviews'
               AND tc.CONSTRAINT_NAME = 'chk_management_review_type'
               AND cc.CHECK_CLAUSE LIKE '%RETURNED_ITEM_INSPECTION%'),
    "ALTER TABLE management_reviews DROP CHECK chk_management_review_type", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND CONSTRAINT_NAME = 'chk_management_review_type'),
    "ALTER TABLE management_reviews ADD CONSTRAINT chk_management_review_type CHECK (
        review_type IN ('MAKING_PRICE', 'CASH_SETTLEMENT', 'OLD_GOLD_DISCOUNT')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 3. INVENTORY STATUS: restore the 019 definitions
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'inventory_movements'
               AND tc.CONSTRAINT_NAME = 'chk_inventory_move_to_status'
               AND cc.CHECK_CLAUSE LIKE '%CONVERTED_TO_OLD_GOLD%'),
    "ALTER TABLE inventory_movements DROP CHECK chk_inventory_move_to_status", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_movements'
                   AND CONSTRAINT_NAME = 'chk_inventory_move_to_status'),
    "ALTER TABLE inventory_movements ADD CONSTRAINT chk_inventory_move_to_status CHECK (
        to_inventory_status IS NULL
        OR to_inventory_status IN ('IN_STOCK', 'SOLD', 'SCRAPPED', 'REMOVED', 'RETURNED_PENDING')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'inventory_movements'
               AND tc.CONSTRAINT_NAME = 'chk_inventory_move_from_status'
               AND cc.CHECK_CLAUSE LIKE '%CONVERTED_TO_OLD_GOLD%'),
    "ALTER TABLE inventory_movements DROP CHECK chk_inventory_move_from_status", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_movements'
                   AND CONSTRAINT_NAME = 'chk_inventory_move_from_status'),
    "ALTER TABLE inventory_movements ADD CONSTRAINT chk_inventory_move_from_status CHECK (
        from_inventory_status IS NULL
        OR from_inventory_status IN ('IN_STOCK', 'SOLD', 'SCRAPPED', 'REMOVED', 'RETURNED_PENDING')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'inventory_items'
               AND tc.CONSTRAINT_NAME = 'chk_inventory_status' AND cc.CHECK_CLAUSE LIKE '%CONVERTED_TO_OLD_GOLD%'),
    "ALTER TABLE inventory_items DROP CHECK chk_inventory_status", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_items'
                   AND CONSTRAINT_NAME = 'chk_inventory_status'),
    "ALTER TABLE inventory_items ADD CONSTRAINT chk_inventory_status CHECK (
        inventory_status IN ('IN_STOCK', 'SOLD', 'SCRAPPED', 'REMOVED', 'RETURNED_PENDING')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- No stored status is longer than 20 characters (guarded above): the 003 widths again.
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_items'
               AND COLUMN_NAME = 'inventory_status' AND COLUMN_TYPE = 'varchar(30)'),
    "ALTER TABLE inventory_items MODIFY COLUMN inventory_status VARCHAR(20) NOT NULL DEFAULT 'IN_STOCK'",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_movements'
               AND COLUMN_NAME = 'from_inventory_status' AND COLUMN_TYPE = 'varchar(30)'),
    "ALTER TABLE inventory_movements MODIFY COLUMN from_inventory_status VARCHAR(20) DEFAULT NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_movements'
               AND COLUMN_NAME = 'to_inventory_status' AND COLUMN_TYPE = 'varchar(30)'),
    "ALTER TABLE inventory_movements MODIFY COLUMN to_inventory_status VARCHAR(20) DEFAULT NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


DELETE FROM schema_migrations WHERE version = 21;

SET SESSION sql_mode = @masgold_saved_sql_mode;
