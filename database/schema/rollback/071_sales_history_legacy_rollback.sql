-- Rollback of 071. REFUSES when the history holds any row or a later migration exists: imported evidence is never deleted to make a rollback pass.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM sales_history_legacy)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 71),
 'SELECT * FROM ROLLBACK_071_REFUSED_sales_history_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS sales_history_legacy;
DELETE FROM schema_migrations WHERE version = 71;
