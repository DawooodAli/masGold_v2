-- Market-custody SETTLEMENT, step 2b: DISTRIBUTION of the owner's gold to a SUPPLIER after he returns from the market. Prepared; NOT applied to any database by the task that wrote it.
--   unmelted gold (UNCLASSIFIED custody, in its OWN karat): the existing GOLD_SETTLE flow (046) now accepts the designation UNCLASSIFIED (chk_mtgs_designation); no new table.
--   the melted bar (BAR pool, 21K-equivalent grams): entry kind ST_BAR_SUP (LEGACY trips only), SG source kind TRIP_BAR (the delivered amount is N grams of the 21K master karat,
--   i.e. Eq21; the credit is N x 21 / supplier karat, computed once like 045/046), no Scrap leg, and the link table market_trip_bar_supplier_links (entry <-> SG, tied by composite keys).
--   market_trip_settle_moves may now carry ST_BAR_SUP moves (a negative BAR move).
-- No existing row is rewritten. The CHECK expressions have NOT been executed on a MySQL server before the rehearsal gate. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=60),
 'DO 0', 'SELECT * FROM MIGRATION_061_REFUSED_missing_060');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 1. unmelted (UNCLASSIFIED) gold may be delivered to a supplier from custody (046 accepted LOGO only)
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtgs_designation' AND CHECK_CLAUSE NOT LIKE '%UNCLASSIFIED%'),
 "ALTER TABLE market_trip_gold_settlements DROP CHECK chk_mtgs_designation", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_gold_settlements' AND CONSTRAINT_NAME='chk_mtgs_designation'),
 "ALTER TABLE market_trip_gold_settlements ADD CONSTRAINT chk_mtgs_designation CHECK (designation IN ('LOGO','UNCLASSIFIED'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 2. the entry kind (LEGACY only) and the pool-move kind
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%ST_BAR_SUP%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN','ST_MELT','ST_BAR_SUP'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_mode_kind' AND CHECK_CLAUSE NOT LIKE '%ST_BAR_SUP%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_mode_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','ST_MELT','ST_BAR_SUP')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtsm_kind' AND CHECK_CLAUSE NOT LIKE '%ST_BAR_SUP%'),
 "ALTER TABLE market_trip_settle_moves DROP CHECK chk_mtsm_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_settle_moves' AND CONSTRAINT_NAME='chk_mtsm_kind'),
 "ALTER TABLE market_trip_settle_moves ADD CONSTRAINT chk_mtsm_kind CHECK (entry_kind IN ('ST_MELT','ST_BAR_SUP'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 3. the SG document of a bar delivery (source TRIP_BAR: delivered 21K master karat + weight, never a Scrap leg)
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_sgs_source' AND CHECK_CLAUSE NOT LIKE '%TRIP_BAR%'),
 "ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='chk_sgs_source'),
 "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_source CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','CUSTODY_LOT','HOLDING_LOT','HOLDING_LOGO','HOLDING_MKT','TRIP_BOOK','TRIP_BAR'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_sgs_shape' AND CHECK_CLAUSE NOT LIKE '%TRIP_BAR%'),
 "ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_shape", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='chk_sgs_shape'),
 "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_shape CHECK ((source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','TRIP_BAR') AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL AND holding_party_id IS NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NULL) OR (source_kind='CUSTODY_LOT' AND delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_lot_id IS NOT NULL AND delivered_lot_weight_grams IS NOT NULL AND delivered_lot_weight_grams=delivered_weight_grams AND origin_melt_entry_id IS NOT NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000 AND holding_party_id IS NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NULL) OR (source_kind='HOLDING_LOGO' AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL AND holding_party_id IS NOT NULL AND logo_pool_id IS NOT NULL AND lot_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NOT NULL AND holding_weight_grams=delivered_weight_grams) OR (source_kind='HOLDING_LOT' AND delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_lot_id IS NOT NULL AND delivered_lot_weight_grams IS NOT NULL AND delivered_weight_grams<=delivered_lot_weight_grams AND origin_melt_entry_id IS NOT NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000 AND holding_party_id IS NOT NULL AND lot_pool_id IS NOT NULL AND logo_pool_id IS NULL AND physical_declaration IS NOT NULL AND physical_declaration IN ('WHOLE_ORIGINAL_BAR','GRAM_ENTITLEMENT','PHYSICALLY_DIVIDED') AND (physical_declaration<>'WHOLE_ORIGINAL_BAR' OR delivered_weight_grams=delivered_lot_weight_grams) AND holding_weight_grams IS NOT NULL AND holding_weight_grams=delivered_weight_grams) OR (source_kind='HOLDING_MKT' AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_karat_value=21 AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL AND holding_party_id IS NOT NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NOT NULL AND holding_weight_grams=delivered_weight_grams) OR (source_kind='TRIP_BOOK' AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND holding_party_id IS NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NULL AND ((delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_fineness_permille IS NULL) OR (delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000))))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_sgsl_source' AND CHECK_CLAUSE NOT LIKE '%TRIP_BAR%'),
 "ALTER TABLE supplier_gold_settlement_legs DROP CHECK chk_sgsl_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlement_legs' AND CONSTRAINT_NAME='chk_sgsl_source'),
 "ALTER TABLE supplier_gold_settlement_legs ADD CONSTRAINT chk_sgsl_source CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','CUSTODY_LOT','HOLDING_LOT','HOLDING_LOGO','HOLDING_MKT','TRIP_BOOK','TRIP_BAR'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 4. the link entry <-> SG (the database keeps the link equal to the document in source, supplier and weight)
CREATE TABLE IF NOT EXISTS market_trip_bar_supplier_links (
 entry_id BIGINT UNSIGNED NOT NULL,
 entry_kind VARCHAR(12) NOT NULL DEFAULT 'ST_BAR_SUP',
 trip_id BIGINT UNSIGNED NOT NULL,
 settlement_id BIGINT UNSIGNED NOT NULL,
 source_kind VARCHAR(12) NOT NULL DEFAULT 'TRIP_BAR',
 supplier_id INT UNSIGNED NOT NULL,
 weight_grams DECIMAL(14,3) NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (entry_id),
 UNIQUE KEY uq_mtbarl_settlement (settlement_id),
 KEY idx_mtbarl_supplier (supplier_id),
 CONSTRAINT fk_mtbarl_entry FOREIGN KEY (entry_id, entry_kind, trip_id) REFERENCES market_trip_entries(id, entry_kind, trip_id),
 CONSTRAINT fk_mtbarl_settlement FOREIGN KEY (settlement_id, source_kind, supplier_id, weight_grams) REFERENCES supplier_gold_settlements(id, source_kind, supplier_id, delivered_weight_grams),
 CONSTRAINT chk_mtbarl_kind CHECK (entry_kind = 'ST_BAR_SUP' AND source_kind = 'TRIP_BAR' AND weight_grams > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (61,'Market-custody settlement step 2b: supplier delivery of the melted bar (ST_BAR_SUP, TRIP_BAR) and of unmelted gold (UNCLASSIFIED)')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
