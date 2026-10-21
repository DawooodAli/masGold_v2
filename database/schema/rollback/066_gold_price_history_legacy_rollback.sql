-- Rollback of 066. REFUSES when the history holds any row or a later migration exists: imported evidence is never deleted to make a rollback pass.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM gold_price_history_legacy)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 66),
 'SELECT * FROM ROLLBACK_066_REFUSED_price_history_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS gold_price_history_legacy;
DELETE FROM schema_migrations WHERE version = 66;
