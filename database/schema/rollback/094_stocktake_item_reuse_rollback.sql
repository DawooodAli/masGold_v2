-- Rollback of 094. Refuses when a later migration exists or any piece was written off by a stocktake more than once (the unique key could not be restored).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 94)
   OR EXISTS(SELECT 1 FROM stocktake_adjustment_items GROUP BY inventory_item_id HAVING COUNT(*) > 1),
 'SELECT * FROM ROLLBACK_094_REFUSED_later_migration_or_repeated_write_off_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE stocktake_adjustment_items ADD UNIQUE KEY uq_stocktake_item_once (inventory_item_id);
ALTER TABLE stocktake_adjustment_items DROP INDEX idx_stocktake_item_piece;
DELETE FROM schema_migrations WHERE version = 94;
