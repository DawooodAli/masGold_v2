-- ============================================================
-- masGold v2
-- Rollback of migration 015: Legacy provenance
--
-- Removes ONLY objects created by 015, and only while no legacy
-- data exists. Every guard runs before the first change; any
-- failed guard stops the script with
-- "Table '<db>.ROLLBACK_015_REFUSED_<reason>' doesn't exist".
--
-- Re-runnable: each step runs only while its object still exists.
-- ============================================================


SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. GUARDS (no change happens before all of them pass)
-- ============================================================

-- Provenance tables must be empty (record map and exceptions
-- reference runs, so an empty runs table implies both are empty;
-- all three are still checked).
SET @masgold_rows := 0;
SET @masgold_sql := IF(
    (SELECT COUNT(*) FROM information_schema.TABLES
      WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME IN ('legacy_import_runs', 'legacy_record_map', 'legacy_exceptions')) = 3,
    'SELECT (SELECT COUNT(*) FROM legacy_import_runs)
          + (SELECT COUNT(*) FROM legacy_record_map)
          + (SELECT COUNT(*) FROM legacy_exceptions)
       INTO @masgold_rows',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0,
    'SELECT 1 FROM ROLLBACK_015_REFUSED_provenance_not_empty', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- No legacy invoices or lines; NULL amounts exist only on those.
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM invoices WHERE record_origin <> 'NATIVE'
                                      OR sell_total IS NULL OR buy_total IS NULL)
    OR EXISTS (SELECT 1 FROM invoice_lines WHERE record_origin <> 'NATIVE'
                                             OR gold_price_per_gram IS NULL
                                             OR line_total IS NULL),
    'SELECT 1 FROM ROLLBACK_015_REFUSED_legacy_invoices_exist',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_rows := 0;
SET @masgold_sql := IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('invoices', 'invoice_lines')
        AND COLUMN_NAME = 'provenance_json') = 2,
    'SELECT (SELECT COUNT(*) FROM invoices WHERE provenance_json IS NOT NULL)
          + (SELECT COUNT(*) FROM invoice_lines WHERE provenance_json IS NOT NULL)
       INTO @masgold_rows',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0,
    'SELECT 1 FROM ROLLBACK_015_REFUSED_provenance_json_in_use', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_rows := 0;
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'old_gold_movements'
               AND COLUMN_NAME = 'balance_effect'),
    "SELECT COUNT(*) INTO @masgold_rows FROM old_gold_movements
      WHERE balance_effect <> 'COUNTED'",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0,
    'SELECT 1 FROM ROLLBACK_015_REFUSED_reference_only_old_gold_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Codes added by 015 must be unused.
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM customer_transactions ct
              JOIN customer_transaction_types t ON t.id = ct.transaction_type_id
             WHERE t.code IN ('LEGACY_REMAINDER', 'LEGACY_PAYMENT',
                              'LEGACY_DEPOSIT', 'LEGACY_DEPOSIT_REFUND'))
    OR EXISTS (SELECT 1 FROM old_gold_movements m
                 JOIN old_gold_movement_types t ON t.id = m.movement_type_id
                WHERE t.code = 'OPENING_BALANCE'),
    'SELECT 1 FROM ROLLBACK_015_REFUSED_new_codes_in_use',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. OLD GOLD: restore the 004 view, then remove balance_effect
-- ============================================================

CREATE OR REPLACE VIEW vw_old_gold_balances AS

SELECT
    k.id AS karat_id,
    k.karat_value,
    k.name AS karat_name,

    CAST(
        COALESCE(
            SUM(m.weight_delta_grams),
            0
        )
        AS DECIMAL(14,3)
    ) AS balance_grams,

    CAST(
        COALESCE(
            SUM(
                CASE
                    WHEN m.weight_delta_grams > 0
                    THEN m.weight_delta_grams
                    ELSE 0
                END
            ),
            0
        )
        AS DECIMAL(14,3)
    ) AS total_in_grams,

    CAST(
        COALESCE(
            SUM(
                CASE
                    WHEN m.weight_delta_grams < 0
                    THEN ABS(m.weight_delta_grams)
                    ELSE 0
                END
            ),
            0
        )
        AS DECIMAL(14,3)
    ) AS total_out_grams

FROM karats k

LEFT JOIN old_gold_movements m
    ON m.karat_id = k.id

GROUP BY
    k.id,
    k.karat_value,
    k.name;


SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'old_gold_movements'
               AND CONSTRAINT_NAME = 'chk_old_gold_balance_effect'),
    "ALTER TABLE old_gold_movements DROP CHECK chk_old_gold_balance_effect",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'old_gold_movements'
               AND INDEX_NAME = 'idx_old_gold_balance_effect'),
    "ALTER TABLE old_gold_movements DROP INDEX idx_old_gold_balance_effect",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'old_gold_movements'
               AND COLUMN_NAME = 'balance_effect'),
    "ALTER TABLE old_gold_movements DROP COLUMN balance_effect",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 2. PROVENANCE TABLES (empty, checked above)
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'legacy_exceptions'),
    "DROP TABLE legacy_exceptions",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'legacy_record_map'),
    "DROP TABLE legacy_record_map",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'legacy_import_runs'),
    "DROP TABLE legacy_import_runs",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 3. INVOICE LINES: drop provenance, restore NOT NULL
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND COLUMN_NAME = 'provenance_json'),
    "ALTER TABLE invoice_lines DROP COLUMN provenance_json",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND COLUMN_NAME = 'line_total' AND IS_NULLABLE = 'YES'),
    "ALTER TABLE invoice_lines MODIFY COLUMN line_total DECIMAL(16,2) NOT NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND COLUMN_NAME = 'gold_price_per_gram' AND IS_NULLABLE = 'YES'),
    "ALTER TABLE invoice_lines MODIFY COLUMN gold_price_per_gram DECIMAL(14,4) NOT NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 4. INVOICES: drop provenance, restore NOT NULL DEFAULT 0.00
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
               AND COLUMN_NAME = 'provenance_json'),
    "ALTER TABLE invoices DROP COLUMN provenance_json",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
               AND COLUMN_NAME = 'buy_total' AND IS_NULLABLE = 'YES'),
    "ALTER TABLE invoices MODIFY COLUMN buy_total DECIMAL(16,2) NOT NULL DEFAULT 0.00",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
               AND COLUMN_NAME = 'sell_total' AND IS_NULLABLE = 'YES'),
    "ALTER TABLE invoices MODIFY COLUMN sell_total DECIMAL(16,2) NOT NULL DEFAULT 0.00",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 5. CODES (unused, checked above) AND REGISTRATION
-- ============================================================

DELETE FROM customer_transaction_types
WHERE code IN (
    'LEGACY_REMAINDER',
    'LEGACY_PAYMENT',
    'LEGACY_DEPOSIT',
    'LEGACY_DEPOSIT_REFUND'
);

DELETE FROM old_gold_movement_types
WHERE code = 'OPENING_BALANCE';

DELETE FROM schema_migrations
WHERE version = 15;


SET SESSION sql_mode = @masgold_saved_sql_mode;
