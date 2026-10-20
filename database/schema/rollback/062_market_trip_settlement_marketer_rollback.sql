-- Rollback of 062 (settlement step 2c). REFUSES when any ST_BAR_MKT entry, any marketer leg of it or a later migration exists: history is never deleted to make it pass.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind = 'ST_BAR_MKT')
 OR EXISTS(SELECT 1 FROM marketer_gold_movements WHERE trip_entry_kind = 'ST_BAR_MKT')
 OR EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind = 'ST_LOGO')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 62),
 'SELECT * FROM ROLLBACK_062_REFUSED_marketer_logo_history_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mkgm_shape' AND CHECK_CLAUSE LIKE '%ST_BAR_MKT%'),
 "ALTER TABLE marketer_gold_movements DROP CHECK chk_mkgm_shape", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='marketer_gold_movements' AND CONSTRAINT_NAME='chk_mkgm_shape'),
 "ALTER TABLE marketer_gold_movements ADD CONSTRAINT chk_mkgm_shape CHECK (weight_grams > 0 AND ( (document_id IS NOT NULL AND settlement_id IS NULL AND trip_entry_id IS NULL AND ( (movement_type = 'PURCHASE_IN' AND doc_kind = 'PURCHASE' AND weight_delta = weight_grams AND correction_id IS NULL) OR (movement_type = 'SALE_OUT' AND doc_kind = 'SALE' AND weight_delta = -weight_grams AND correction_id IS NULL) OR (movement_type = 'GOLD_RETURN' AND doc_kind = 'GOLD_RETURN' AND weight_delta = -weight_grams AND correction_id IS NULL) OR (movement_type = 'PURCHASE_IN_REVERSAL' AND doc_kind = 'PURCHASE' AND weight_delta = -weight_grams AND correction_id IS NOT NULL) OR (movement_type = 'SALE_OUT_REVERSAL' AND doc_kind = 'SALE' AND weight_delta = weight_grams AND correction_id IS NOT NULL) OR (movement_type = 'GOLD_RETURN_REVERSAL' AND doc_kind = 'GOLD_RETURN' AND weight_delta = weight_grams AND correction_id IS NOT NULL))) OR (document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NOT NULL AND trip_entry_id IS NULL AND ( (movement_type = 'SETTLEMENT' AND weight_delta = -weight_grams AND correction_id IS NULL) OR (movement_type = 'SETTLEMENT_REVERSAL' AND weight_delta = weight_grams AND correction_id IS NOT NULL))) OR (document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NULL AND trip_entry_id IS NOT NULL AND ((movement_type = 'TRIP_BOOK_IN' AND trip_entry_kind = 'BK_DLV_MKT' AND weight_delta = weight_grams AND correction_id IS NULL) OR (movement_type = 'TRIP_BOOK_IN_REVERSAL' AND trip_entry_kind = 'BK_DLV_MKT' AND weight_delta = -weight_grams AND correction_id IS NOT NULL) OR (movement_type = 'TRIP_BOOK_OUT' AND trip_entry_kind = 'BK_RECEIVE' AND weight_delta = -weight_grams AND correction_id IS NULL) OR (movement_type = 'TRIP_BOOK_OUT_REVERSAL' AND trip_entry_kind = 'BK_RECEIVE' AND weight_delta = weight_grams AND correction_id IS NOT NULL)))))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtsm_pool' AND CHECK_CLAUSE LIKE '%LOGO%'),
 "ALTER TABLE market_trip_settle_moves DROP CHECK chk_mtsm_pool", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_settle_moves' AND CONSTRAINT_NAME='chk_mtsm_pool'),
 "ALTER TABLE market_trip_settle_moves ADD CONSTRAINT chk_mtsm_pool CHECK ((pool = 'RAW' AND karat_id IS NOT NULL) OR (pool = 'BAR' AND karat_id IS NULL))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtsm_kind' AND CHECK_CLAUSE LIKE '%ST_BAR_MKT%'),
 "ALTER TABLE market_trip_settle_moves DROP CHECK chk_mtsm_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_settle_moves' AND CONSTRAINT_NAME='chk_mtsm_kind'),
 "ALTER TABLE market_trip_settle_moves ADD CONSTRAINT chk_mtsm_kind CHECK (entry_kind IN ('ST_MELT','ST_BAR_SUP'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_mode_kind' AND CHECK_CLAUSE LIKE '%ST_BAR_MKT%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_mode_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','ST_MELT','ST_BAR_SUP')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_kind' AND CHECK_CLAUSE LIKE '%ST_BAR_MKT%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN','ST_MELT','ST_BAR_SUP'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DELETE FROM schema_migrations WHERE version = 62;
