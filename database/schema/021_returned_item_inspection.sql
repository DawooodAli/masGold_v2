-- ============================================================
-- masGold v2
-- Migration 021: Returned Item Inspection (manager review)
--
-- Every identified piece returned in an exchange (RETURNED_PENDING)
-- gets exactly one RETURNED_ITEM_INSPECTION manager review, linked
-- to its lifecycle RETURNED line (UNIQUE). The manager resolves it
-- with one of two outcomes:
--   RETURN_TO_STOCK       RETURNED_PENDING → IN_STOCK, same item,
--                         same stored barcode (RETURNED_ITEM_RESTOCKED)
--   CONVERT_TO_OLD_GOLD   RETURNED_PENDING → CONVERTED_TO_OLD_GOLD
--                         (terminal, never sellable; the historical
--                         barcode is kept) + a COUNTED
--                         RETURNED_ITEM_TO_OLD_GOLD old-gold IN of the
--                         exact karat and weight
--
-- Reviews for pieces returned before this migration are created by
-- the application (idempotent backfill), never by this file.
--
-- The target database is chosen by the caller; this file never
-- names a database. Re-runnable and additive.
--
-- Rollback: rollback/021_returned_item_inspection_rollback.sql
-- ============================================================


SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. PREFLIGHT
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 20),
    'SELECT 1 FROM MIGRATION_021_REFUSED_migration_020_missing',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. INVENTORY STATUS: CONVERTED_TO_OLD_GOLD (terminal)
-- The status columns are widened from VARCHAR(20) first
-- (CONVERTED_TO_OLD_GOLD is 21 characters); each list is
-- replaced only while it lacks the new status.
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_items'
               AND COLUMN_NAME = 'inventory_status' AND COLUMN_TYPE = 'varchar(20)'),
    "ALTER TABLE inventory_items MODIFY COLUMN inventory_status VARCHAR(30) NOT NULL DEFAULT 'IN_STOCK'",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_movements'
               AND COLUMN_NAME = 'from_inventory_status' AND COLUMN_TYPE = 'varchar(20)'),
    "ALTER TABLE inventory_movements MODIFY COLUMN from_inventory_status VARCHAR(30) DEFAULT NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_movements'
               AND COLUMN_NAME = 'to_inventory_status' AND COLUMN_TYPE = 'varchar(20)'),
    "ALTER TABLE inventory_movements MODIFY COLUMN to_inventory_status VARCHAR(30) DEFAULT NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'inventory_items'
               AND tc.CONSTRAINT_NAME = 'chk_inventory_status'
               AND cc.CHECK_CLAUSE NOT LIKE '%CONVERTED_TO_OLD_GOLD%'),
    "ALTER TABLE inventory_items DROP CHECK chk_inventory_status",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_items'
                   AND CONSTRAINT_NAME = 'chk_inventory_status'),
    "ALTER TABLE inventory_items ADD CONSTRAINT chk_inventory_status CHECK (
        inventory_status IN ('IN_STOCK', 'SOLD', 'SCRAPPED', 'REMOVED', 'RETURNED_PENDING',
                             'CONVERTED_TO_OLD_GOLD')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'inventory_movements'
               AND tc.CONSTRAINT_NAME = 'chk_inventory_move_from_status'
               AND cc.CHECK_CLAUSE NOT LIKE '%CONVERTED_TO_OLD_GOLD%'),
    "ALTER TABLE inventory_movements DROP CHECK chk_inventory_move_from_status",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_movements'
                   AND CONSTRAINT_NAME = 'chk_inventory_move_from_status'),
    "ALTER TABLE inventory_movements ADD CONSTRAINT chk_inventory_move_from_status CHECK (
        from_inventory_status IS NULL
        OR from_inventory_status IN ('IN_STOCK', 'SOLD', 'SCRAPPED', 'REMOVED', 'RETURNED_PENDING',
                                     'CONVERTED_TO_OLD_GOLD')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'inventory_movements'
               AND tc.CONSTRAINT_NAME = 'chk_inventory_move_to_status'
               AND cc.CHECK_CLAUSE NOT LIKE '%CONVERTED_TO_OLD_GOLD%'),
    "ALTER TABLE inventory_movements DROP CHECK chk_inventory_move_to_status",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_movements'
                   AND CONSTRAINT_NAME = 'chk_inventory_move_to_status'),
    "ALTER TABLE inventory_movements ADD CONSTRAINT chk_inventory_move_to_status CHECK (
        to_inventory_status IS NULL
        OR to_inventory_status IN ('IN_STOCK', 'SOLD', 'SCRAPPED', 'REMOVED', 'RETURNED_PENDING',
                                   'CONVERTED_TO_OLD_GOLD')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 2. MANAGEMENT REVIEW: RETURNED_ITEM_INSPECTION
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'management_reviews'
               AND tc.CONSTRAINT_NAME = 'chk_management_review_type'
               AND cc.CHECK_CLAUSE NOT LIKE '%RETURNED_ITEM_INSPECTION%'),
    "ALTER TABLE management_reviews DROP CHECK chk_management_review_type",
    'DO 0');
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

-- The returned lifecycle line under inspection (one review per line).
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND COLUMN_NAME = 'lifecycle_line_id'),
    "ALTER TABLE management_reviews ADD COLUMN lifecycle_line_id BIGINT UNSIGNED NULL AFTER invoice_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND COLUMN_NAME = 'resolution_code'),
    "ALTER TABLE management_reviews ADD COLUMN resolution_code VARCHAR(30) NULL AFTER resolved_at",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND INDEX_NAME = 'uq_management_review_lifecycle_line'),
    "ALTER TABLE management_reviews ADD UNIQUE KEY uq_management_review_lifecycle_line (lifecycle_line_id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND CONSTRAINT_NAME = 'fk_management_review_lifecycle_line'),
    "ALTER TABLE management_reviews ADD CONSTRAINT fk_management_review_lifecycle_line
        FOREIGN KEY (lifecycle_line_id) REFERENCES invoice_lifecycle_lines(id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- An inspection always has its line and resolves only with one of the two outcomes.
-- Other review types keep their 016 lifecycle and never carry a line or a code.
-- resolution_code IS NOT NULL is explicit: a NULL makes IN (...) UNKNOWN, which a CHECK accepts.
-- Add → drop an earlier draft that lacked it → add again: a fresh database adds it once,
-- a database that ran the draft gets it replaced.
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
               AND tc.CONSTRAINT_NAME = 'chk_management_review_inspection'
               AND cc.CHECK_CLAUSE NOT LIKE '%resolution_code` is not null%'),
    "ALTER TABLE management_reviews DROP CHECK chk_management_review_inspection",
    'DO 0');
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


-- ============================================================
-- 3. MOVEMENT CODES
-- ============================================================

INSERT INTO inventory_movement_types (
    code,
    name,
    description
)
VALUES
    (
        'RETURNED_ITEM_RESTOCKED',
        'Returned Item Restocked',
        'Manager inspection: a returned piece goes back to stock with the same item and stored barcode'
    ),
    (
        'RETURNED_ITEM_TO_OLD_GOLD',
        'Returned Item To Old Gold',
        'Manager inspection: a returned piece is converted to old gold; terminal, never sellable'
    )
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);

INSERT INTO old_gold_movement_types (
    code,
    name,
    description,
    expected_direction
)
VALUES
    (
        'RETURNED_ITEM_TO_OLD_GOLD',
        'Returned Item To Old Gold',
        'Weight of a returned exchange piece converted to old gold by manager inspection',
        'IN'
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
    21,
    'Returned item inspection: manager review, return to stock or convert to old gold'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);


SET SESSION sql_mode = @masgold_saved_sql_mode;
