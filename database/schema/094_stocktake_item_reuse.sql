-- A piece may be written off by a stocktake MORE THAN ONCE (owner 2026-11-01): it is written off, later restored to the stock (RESTORED movement, never a rewrite of the
-- stocktake document) and then turns out missing again. 073 made stocktake_adjustment_items UNIQUE on inventory_item_id, so the second write-off failed with MySQL 1062.
-- Every write-off keeps its OWN row; the unique key becomes an ordinary index (the foreign key needs one). Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=93),
 'DO 0', 'SELECT * FROM MIGRATION_094_REFUSED_missing_093');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='stocktake_adjustment_items' AND INDEX_NAME='idx_stocktake_item_piece'),
 'ALTER TABLE stocktake_adjustment_items ADD KEY idx_stocktake_item_piece (inventory_item_id)', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='stocktake_adjustment_items' AND INDEX_NAME='uq_stocktake_item_once'),
 'ALTER TABLE stocktake_adjustment_items DROP INDEX uq_stocktake_item_once', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (94,'Stocktake write-off items: a restored piece may be written off again')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
