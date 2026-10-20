-- Rollback of 061 (settlement step 2b). REFUSES when any ST_BAR_SUP entry, any bar supplier link, any UNCLASSIFIED custody settlement or a later migration exists: history is never deleted to make it pass.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind = 'ST_BAR_SUP')
 OR (EXISTS(SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_bar_supplier_links') AND EXISTS(SELECT 1 FROM market_trip_bar_supplier_links))
 OR EXISTS(SELECT 1 FROM market_trip_gold_settlements WHERE designation = 'UNCLASSIFIED')
 OR EXISTS(SELECT 1 FROM supplier_gold_settlements WHERE source_kind = 'TRIP_BAR')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 61),
 'SELECT * FROM ROLLBACK_061_REFUSED_settlement_distribution_history_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS market_trip_bar_supplier_links;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_sgsl_source' AND CHECK_CLAUSE LIKE '%TRIP_BAR%'),
 "ALTER TABLE supplier_gold_settlement_legs DROP CHECK chk_sgsl_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlement_legs' AND CONSTRAINT_NAME='chk_sgsl_source'),
 "ALTER TABLE supplier_gold_settlement_legs ADD CONSTRAINT chk_sgsl_source CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','CUSTODY_LOT','HOLDING_LOT','HOLDING_LOGO','HOLDING_MKT','TRIP_BOOK'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_sgs_shape' AND CHECK_CLAUSE LIKE '%TRIP_BAR%'),
 "ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_shape", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='chk_sgs_shape'),
 "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_shape CHECK ((source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD') AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL AND holding_party_id IS NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NULL) OR (source_kind='CUSTODY_LOT' AND delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_lot_id IS NOT NULL AND delivered_lot_weight_grams IS NOT NULL AND delivered_lot_weight_grams=delivered_weight_grams AND origin_melt_entry_id IS NOT NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000 AND holding_party_id IS NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NULL) OR (source_kind='HOLDING_LOGO' AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL AND holding_party_id IS NOT NULL AND logo_pool_id IS NOT NULL AND lot_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NOT NULL AND holding_weight_grams=delivered_weight_grams) OR (source_kind='HOLDING_LOT' AND delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_lot_id IS NOT NULL AND delivered_lot_weight_grams IS NOT NULL AND delivered_weight_grams<=delivered_lot_weight_grams AND origin_melt_entry_id IS NOT NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000 AND holding_party_id IS NOT NULL AND lot_pool_id IS NOT NULL AND logo_pool_id IS NULL AND physical_declaration IS NOT NULL AND physical_declaration IN ('WHOLE_ORIGINAL_BAR','GRAM_ENTITLEMENT','PHYSICALLY_DIVIDED') AND (physical_declaration<>'WHOLE_ORIGINAL_BAR' OR delivered_weight_grams=delivered_lot_weight_grams) AND holding_weight_grams IS NOT NULL AND holding_weight_grams=delivered_weight_grams) OR (source_kind='HOLDING_MKT' AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_karat_value=21 AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL AND holding_party_id IS NOT NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NOT NULL AND holding_weight_grams=delivered_weight_grams) OR (source_kind='TRIP_BOOK' AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND holding_party_id IS NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NULL AND ((delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_fineness_permille IS NULL) OR (delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000))))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_sgs_source' AND CHECK_CLAUSE LIKE '%TRIP_BAR%'),
 "ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='chk_sgs_source'),
 "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_source CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','CUSTODY_LOT','HOLDING_LOT','HOLDING_LOGO','HOLDING_MKT','TRIP_BOOK'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtsm_kind' AND CHECK_CLAUSE LIKE '%ST_BAR_SUP%'),
 "ALTER TABLE market_trip_settle_moves DROP CHECK chk_mtsm_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_settle_moves' AND CONSTRAINT_NAME='chk_mtsm_kind'),
 "ALTER TABLE market_trip_settle_moves ADD CONSTRAINT chk_mtsm_kind CHECK (entry_kind IN ('ST_MELT'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_mode_kind' AND CHECK_CLAUSE LIKE '%ST_BAR_SUP%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_mode_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','ST_MELT')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_kind' AND CHECK_CLAUSE LIKE '%ST_BAR_SUP%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN','ST_MELT'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtgs_designation' AND CHECK_CLAUSE LIKE '%UNCLASSIFIED%'),
 "ALTER TABLE market_trip_gold_settlements DROP CHECK chk_mtgs_designation", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_gold_settlements' AND CONSTRAINT_NAME='chk_mtgs_designation'),
 "ALTER TABLE market_trip_gold_settlements ADD CONSTRAINT chk_mtgs_designation CHECK (designation IN ('LOGO'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DELETE FROM schema_migrations WHERE version = 61;
