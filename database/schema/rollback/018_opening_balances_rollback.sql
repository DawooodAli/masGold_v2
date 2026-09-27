-- ============================================================
-- masGold v2
-- Rollback of migration 018: opening balances
--
-- Refused while any opening balance was recorded. Every guard
-- runs before the first change; a failed guard stops the script
-- with "Table '<db>.ROLLBACK_018_REFUSED_<reason>' doesn't exist".
-- Re-runnable.
-- ============================================================


SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. GUARDS
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM cash_movements m
              JOIN cash_movement_types t ON t.id = m.movement_type_id
             WHERE t.code IN ('OPENING_BALANCE'))
    OR EXISTS (SELECT 1 FROM bank_movements m
                 JOIN bank_movement_types t ON t.id = m.movement_type_id
                WHERE t.code IN ('OPENING_BALANCE')),
    'SELECT 1 FROM ROLLBACK_018_REFUSED_new_codes_in_use', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. UNDO
-- ============================================================

DELETE FROM cash_movement_types WHERE code IN ('OPENING_BALANCE');
DELETE FROM bank_movement_types WHERE code IN ('OPENING_BALANCE');

DELETE FROM schema_migrations WHERE version = 18;

SET SESSION sql_mode = @masgold_saved_sql_mode;
