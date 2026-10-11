-- Whole assayed lot settlement from CURRENT market custody. No master-karat inference or shop-lot movement.
-- All facts and movements remain immutable; reversal excludes the custody consumption and negates stored supplier effects.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=46),
 'DO 0', 'SELECT * FROM MIGRATION_047_REFUSED_missing_046');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND COLUMN_NAME='delivered_karat_id' AND IS_NULLABLE='NO'), "ALTER TABLE supplier_gold_settlements MODIFY COLUMN delivered_karat_id TINYINT UNSIGNED NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND COLUMN_NAME='delivered_karat_value' AND IS_NULLABLE='NO'), "ALTER TABLE supplier_gold_settlements MODIFY COLUMN delivered_karat_value TINYINT UNSIGNED NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND COLUMN_NAME='delivered_lot_id'), "ALTER TABLE supplier_gold_settlements ADD COLUMN delivered_lot_id BIGINT UNSIGNED NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND COLUMN_NAME='delivered_lot_weight_grams'), "ALTER TABLE supplier_gold_settlements ADD COLUMN delivered_lot_weight_grams DECIMAL(12,3) NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND COLUMN_NAME='origin_melt_entry_id'), "ALTER TABLE supplier_gold_settlements ADD COLUMN origin_melt_entry_id BIGINT UNSIGNED NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND COLUMN_NAME='delivered_fineness_permille'), "ALTER TABLE supplier_gold_settlements ADD COLUMN delivered_fineness_permille DECIMAL(5,1) NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='chk_sgs_source'), "ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='chk_sgs_source'), "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_source CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','CUSTODY_LOT'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='chk_sgs_shape'), "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_shape CHECK ((source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD') AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL) OR (source_kind='CUSTODY_LOT' AND delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_lot_id IS NOT NULL AND delivered_lot_weight_grams IS NOT NULL AND delivered_lot_weight_grams=delivered_weight_grams AND origin_melt_entry_id IS NOT NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_lots' AND INDEX_NAME='uq_mtl_settle_facts'), "ALTER TABLE market_trip_lots ADD UNIQUE KEY uq_mtl_settle_facts (id,entry_id,weight_grams,fineness_permille)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='fk_sgs_lot_facts'), "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT fk_sgs_lot_facts FOREIGN KEY (delivered_lot_id,origin_melt_entry_id,delivered_lot_weight_grams,delivered_fineness_permille) REFERENCES market_trip_lots(id,entry_id,weight_grams,fineness_permille)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND INDEX_NAME='uq_sgs_lot_link'), "ALTER TABLE supplier_gold_settlements ADD UNIQUE KEY uq_sgs_lot_link (id,source_kind,supplier_id,delivered_lot_id,origin_melt_entry_id,delivered_weight_grams,delivered_fineness_permille)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlement_legs' AND CONSTRAINT_NAME='chk_sgsl_source'), "ALTER TABLE supplier_gold_settlement_legs DROP CHECK chk_sgsl_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlement_legs' AND CONSTRAINT_NAME='chk_sgsl_source'), "ALTER TABLE supplier_gold_settlement_legs ADD CONSTRAINT chk_sgsl_source CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','CUSTODY_LOT'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'), "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'), "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND INDEX_NAME='uq_mte_id_kind'), "ALTER TABLE market_trip_entries ADD UNIQUE KEY uq_mte_id_kind (id,entry_kind)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_lot_movements' AND CONSTRAINT_NAME='chk_mtlm_type'), "ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_type", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_lot_movements' AND CONSTRAINT_NAME='chk_mtlm_type'), "ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_type CHECK (movement_type IN ('CREATE','SHOP_RETURN','SHOP_ISSUE','SUPPLIER_SETTLE'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_lot_movements' AND CONSTRAINT_NAME='chk_mtlm_delta'), "ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_delta", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_lot_movements' AND CONSTRAINT_NAME='chk_mtlm_delta'), "ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_delta CHECK (weight_delta <> 0 AND (movement_type NOT IN ('CREATE','SHOP_ISSUE') OR weight_delta>0) AND (movement_type NOT IN ('SHOP_RETURN','SUPPLIER_SETTLE') OR weight_delta<0))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_lot_movements' AND INDEX_NAME='uq_mtlm_settle_link'), "ALTER TABLE market_trip_lot_movements ADD UNIQUE KEY uq_mtlm_settle_link (id,lot_id,entry_id,movement_type,weight_delta)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS market_trip_lot_settlements (
 entry_id BIGINT UNSIGNED NOT NULL,
 entry_kind VARCHAR(12) NOT NULL DEFAULT 'LOT_SETTLE',
 settlement_id BIGINT UNSIGNED NOT NULL,
 source_kind VARCHAR(12) NOT NULL DEFAULT 'CUSTODY_LOT',
 supplier_id INT UNSIGNED NOT NULL,
 lot_id BIGINT UNSIGNED NOT NULL,
 origin_melt_entry_id BIGINT UNSIGNED NOT NULL,
 weight_grams DECIMAL(14,3) NOT NULL,
 fineness_permille DECIMAL(5,1) NOT NULL,
 custody_movement_id BIGINT UNSIGNED NOT NULL,
 movement_type VARCHAR(20) NOT NULL DEFAULT 'SUPPLIER_SETTLE',
 weight_delta DECIMAL(12,3) NOT NULL,
 PRIMARY KEY(entry_id),
 UNIQUE KEY uq_mtls_settlement(settlement_id),
 UNIQUE KEY uq_mtls_movement(custody_movement_id),
 KEY idx_mtls_header(settlement_id,source_kind,supplier_id,lot_id,origin_melt_entry_id,weight_grams,fineness_permille),
 KEY idx_mtls_movement(custody_movement_id,lot_id,entry_id,movement_type,weight_delta),
 KEY idx_mtls_lot(lot_id),
 CONSTRAINT fk_mtls_entry FOREIGN KEY(entry_id,entry_kind) REFERENCES market_trip_entries(id,entry_kind),
 CONSTRAINT fk_mtls_header FOREIGN KEY(settlement_id,source_kind,supplier_id,lot_id,origin_melt_entry_id,weight_grams,fineness_permille)
  REFERENCES supplier_gold_settlements(id,source_kind,supplier_id,delivered_lot_id,origin_melt_entry_id,delivered_weight_grams,delivered_fineness_permille),
 CONSTRAINT fk_mtls_movement FOREIGN KEY(custody_movement_id,lot_id,entry_id,movement_type,weight_delta)
  REFERENCES market_trip_lot_movements(id,lot_id,entry_id,movement_type,weight_delta),
 CONSTRAINT chk_mtls_shape CHECK(entry_kind='LOT_SETTLE' AND source_kind='CUSTODY_LOT' AND movement_type='SUPPLIER_SETTLE'
  AND weight_grams>0 AND weight_delta=-weight_grams AND fineness_permille>0 AND fineness_permille<=1000)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
INSERT INTO schema_migrations(version,name) VALUES(47,'Whole assayed lot supplier settlement from market custody')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
