-- Rollback of 072. REFUSES when any rule exists or a later migration exists: the owner's settings are never deleted to make a rollback pass.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM stock_min_levels)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 72),
 'SELECT * FROM ROLLBACK_072_REFUSED_rules_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS stock_min_levels;
DELETE FROM schema_migrations WHERE version = 72;
