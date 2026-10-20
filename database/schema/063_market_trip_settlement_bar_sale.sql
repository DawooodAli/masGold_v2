-- Market-custody SETTLEMENT, step 2d: SALE of the melted BAR (بيع السبيكة بعد الرجوع). Prepared; NOT applied to any database by the task that wrote it.
--   The existing GOLD_SALE entry (053) gains a third source, TRIP_BAR: N grams (21K-equivalent) of the BAR pool are sold for an agreed total that ENTERS the custody CASH
--   (never Main Safe, no cash/bank movement); the sale document keeps karat / lot / movement NULL, and ONE negative BAR move (entry_kind GOLD_SALE) is posted in market_trip_settle_moves.
--   No Scrap, supplier, making, lot or inventory movement. The reversal is the ordinary full reversal: the entry stops counting, so the grams are back in the BAR pool and the proceeds leave the custody cash.
-- No existing row is rewritten. The CHECK expressions have NOT been executed on a MySQL server before the rehearsal gate. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=62),
 'DO 0', 'SELECT * FROM MIGRATION_063_REFUSED_missing_062');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtgsale_source' AND CHECK_CLAUSE NOT LIKE '%TRIP_BAR%'),
 "ALTER TABLE market_trip_gold_sales DROP CHECK chk_mtgsale_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_gold_sales' AND CONSTRAINT_NAME='chk_mtgsale_source'),
 "ALTER TABLE market_trip_gold_sales ADD CONSTRAINT chk_mtgsale_source CHECK ( (source_kind = 'CUSTODY_GOLD' AND karat_id IS NOT NULL AND designation IN ('MELT','LOGO','UNCLASSIFIED') AND lot_id IS NULL AND fineness_permille IS NULL AND custody_movement_id IS NULL AND movement_type IS NULL AND weight_delta IS NULL) OR (source_kind = 'CUSTODY_LOT' AND lot_id IS NOT NULL AND fineness_permille IS NOT NULL AND fineness_permille > 0 AND fineness_permille <= 1000 AND custody_movement_id IS NOT NULL AND movement_type = 'SALE_OUT' AND weight_delta = -weight_grams AND karat_id IS NULL AND designation IS NULL) OR (source_kind = 'TRIP_BAR' AND karat_id IS NULL AND designation IS NULL AND lot_id IS NULL AND fineness_permille IS NULL AND custody_movement_id IS NULL AND movement_type IS NULL AND weight_delta IS NULL))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtsm_kind' AND CHECK_CLAUSE NOT LIKE '%GOLD_SALE%'),
 "ALTER TABLE market_trip_settle_moves DROP CHECK chk_mtsm_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_settle_moves' AND CONSTRAINT_NAME='chk_mtsm_kind'),
 "ALTER TABLE market_trip_settle_moves ADD CONSTRAINT chk_mtsm_kind CHECK (entry_kind IN ('ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','GOLD_SALE'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (63,'Market-custody settlement step 2d: sale of the melted bar (GOLD_SALE source TRIP_BAR, BAR pool move)')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
