-- Rollback of 063 (settlement step 2d). REFUSES when any TRIP_BAR sale, any GOLD_SALE pool move or a later migration exists: history is never deleted to make it pass.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM market_trip_gold_sales WHERE source_kind = 'TRIP_BAR')
 OR EXISTS(SELECT 1 FROM market_trip_settle_moves WHERE entry_kind = 'GOLD_SALE')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 63),
 'SELECT * FROM ROLLBACK_063_REFUSED_bar_sale_history_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtsm_kind' AND CHECK_CLAUSE LIKE '%GOLD_SALE%'),
 "ALTER TABLE market_trip_settle_moves DROP CHECK chk_mtsm_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_settle_moves' AND CONSTRAINT_NAME='chk_mtsm_kind'),
 "ALTER TABLE market_trip_settle_moves ADD CONSTRAINT chk_mtsm_kind CHECK (entry_kind IN ('ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtgsale_source' AND CHECK_CLAUSE LIKE '%TRIP_BAR%'),
 "ALTER TABLE market_trip_gold_sales DROP CHECK chk_mtgsale_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_gold_sales' AND CONSTRAINT_NAME='chk_mtgsale_source'),
 "ALTER TABLE market_trip_gold_sales ADD CONSTRAINT chk_mtgsale_source CHECK ( (source_kind = 'CUSTODY_GOLD' AND karat_id IS NOT NULL AND designation IN ('MELT','LOGO','UNCLASSIFIED') AND lot_id IS NULL AND fineness_permille IS NULL AND custody_movement_id IS NULL AND movement_type IS NULL AND weight_delta IS NULL) OR (source_kind = 'CUSTODY_LOT' AND lot_id IS NOT NULL AND fineness_permille IS NOT NULL AND fineness_permille > 0 AND fineness_permille <= 1000 AND custody_movement_id IS NOT NULL AND movement_type = 'SALE_OUT' AND weight_delta = -weight_grams AND karat_id IS NULL AND designation IS NULL))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DELETE FROM schema_migrations WHERE version = 63;
