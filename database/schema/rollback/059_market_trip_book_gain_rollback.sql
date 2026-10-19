-- Rollback of 059 (BK_GAIN). REFUSES when any BK_GAIN entry, any gain record, any v3 reconciliation record or a later migration exists: history is never deleted to make it pass.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind = 'BK_GAIN')
 OR (EXISTS(SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_gains') AND EXISTS(SELECT 1 FROM market_trip_gains))
 OR EXISTS(SELECT 1 FROM market_trip_reconciliations WHERE source_algorithm = 'book-recon-source-v3' OR snapshot_format = 'book-recon-snapshot-v3')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 59),
 'SELECT * FROM ROLLBACK_059_REFUSED_gain_history_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtr_head' AND CHECK_CLAUSE LIKE '%source-v3%'),
 "ALTER TABLE market_trip_reconciliations DROP CHECK chk_mtr_head", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_reconciliations' AND CONSTRAINT_NAME='chk_mtr_head'),
 "ALTER TABLE market_trip_reconciliations ADD CONSTRAINT chk_mtr_head CHECK (entry_kind = 'BK_RECON' AND trip_mode = 'BOOK' AND generation >= 1 AND algorithm = 'book-recon-v1' AND source_algorithm IN ('book-recon-source-v1','book-recon-source-v2') AND snapshot_format IN ('book-recon-snapshot-v1','book-recon-snapshot-v2') AND unit_denominator = 26250000 AND JSON_TYPE(snapshot) = 'OBJECT')", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS market_trip_gains;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtbl_leg' AND CHECK_CLAUSE LIKE '%BK_GAIN%'),
 "ALTER TABLE market_trip_book_lines DROP CHECK chk_mtbl_leg", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_book_lines' AND CONSTRAINT_NAME='chk_mtbl_leg'),
 "ALTER TABLE market_trip_book_lines ADD CONSTRAINT chk_mtbl_leg CHECK ((entry_kind = 'BK_ISSUE' AND basis = 'MASTER_KARAT' AND scrap_movement_id IS NOT NULL AND line_price IS NULL) OR (entry_kind = 'BK_RETURN' AND basis = 'MASTER_KARAT' AND scrap_movement_id IS NOT NULL AND line_price IS NULL) OR (entry_kind = 'BK_RETURN' AND basis = 'FINENESS' AND scrap_movement_id IS NULL AND line_price IS NULL) OR (entry_kind IN ('BK_BUY','BK_SELL') AND scrap_movement_id IS NULL AND line_price IS NOT NULL AND line_price > 0) OR (entry_kind IN ('BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE') AND scrap_movement_id IS NULL AND line_price IS NULL))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtbl_kind' AND CHECK_CLAUSE LIKE '%BK_GAIN%'),
 "ALTER TABLE market_trip_book_lines DROP CHECK chk_mtbl_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_book_lines' AND CONSTRAINT_NAME='chk_mtbl_kind'),
 "ALTER TABLE market_trip_book_lines ADD CONSTRAINT chk_mtbl_kind CHECK ((entry_kind IN ('BK_ISSUE','BK_BUY','BK_ISS_STK','BK_RECEIVE') AND direction = 'IN') OR (entry_kind IN ('BK_SELL','BK_RETURN','BK_DLV_SUP','BK_DLV_MKT') AND direction = 'OUT'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_mode_kind' AND CHECK_CLAUSE LIKE '%BK_GAIN%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_mode_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_kind' AND CHECK_CLAUSE LIKE '%BK_GAIN%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DELETE FROM schema_migrations WHERE version = 59;
