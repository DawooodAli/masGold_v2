-- Supplier gold settlement from shop-owned gold held by a market intermediary (مسوقجي): SG sources HOLDING_LOGO (049 pooled LOGO grams of one master
-- karat) and HOLDING_LOT (048 known-assay lot pool). ONE holding source, ONE supplier and ONE positive quantity (at most three decimals) per SG
-- document; partial and repeated allocations are allowed. The SG header keeps the ORIGINAL full-lot facts (delivered_lot_weight_grams) apart from
-- the delivered grams, and the immutable physical_declaration of a lot settlement (WHOLE_ORIGINAL_BAR / GRAM_ENTITLEMENT / PHYSICALLY_DIVIDED).
-- Exactly one GOLD leg is posted; no Scrap, custody, cash, making, inventory or shop-lot movement exists for these sources (the database already
-- refuses a SCRAP leg on any source but SHOP_SCRAP). The holding ledgers gain explicit signed SETTLEMENT / SETTLEMENT_REVERSAL movements; the
-- original movement is never excluded (a pool balance is the SUM of every movement). No new permission, no new table.
-- Service-enforced, NOT expressible as a CHECK: WHOLE_ORIGINAL_BAR needs the full original quantity AND no prior allocation history of the pool
-- (any GRAM_ENTITLEMENT / PHYSICALLY_DIVIDED SG, reversed ones included), decided under the pool lock.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=49),
 'DO 0', 'SELECT * FROM MIGRATION_050_REFUSED_missing_049');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND COLUMN_NAME='holding_party_id'), "ALTER TABLE supplier_gold_settlements ADD COLUMN holding_party_id INT UNSIGNED NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND COLUMN_NAME='lot_pool_id'), "ALTER TABLE supplier_gold_settlements ADD COLUMN lot_pool_id BIGINT UNSIGNED NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND COLUMN_NAME='logo_pool_id'), "ALTER TABLE supplier_gold_settlements ADD COLUMN logo_pool_id BIGINT UNSIGNED NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND COLUMN_NAME='physical_declaration'), "ALTER TABLE supplier_gold_settlements ADD COLUMN physical_declaration VARCHAR(24) NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND COLUMN_NAME='holding_weight_grams'), "ALTER TABLE supplier_gold_settlements ADD COLUMN holding_weight_grams DECIMAL(12,3) NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND COLUMN_NAME='self_settlement_confirmed'), "ALTER TABLE supplier_gold_settlements ADD COLUMN self_settlement_confirmed TINYINT(1) NOT NULL DEFAULT 0", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='logo_holding_pools' AND INDEX_NAME='uq_lhp_source'), "ALTER TABLE logo_holding_pools ADD UNIQUE KEY uq_lhp_source (id,party_id,karat_id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.TABLE_SCHEMA=DATABASE() AND tc.TABLE_NAME='supplier_gold_settlements' AND tc.CONSTRAINT_NAME='chk_sgs_source' AND cc.CHECK_CLAUSE NOT LIKE '%HOLDING_LOT%'), "ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='chk_sgs_source'), "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_source CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','CUSTODY_LOT','HOLDING_LOT','HOLDING_LOGO'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.TABLE_SCHEMA=DATABASE() AND tc.TABLE_NAME='supplier_gold_settlements' AND tc.CONSTRAINT_NAME='chk_sgs_shape' AND cc.CHECK_CLAUSE NOT LIKE '%HOLDING_LOT%'), "ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_shape", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='chk_sgs_shape'), "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_shape CHECK ((source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD') AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL AND holding_party_id IS NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NULL) OR (source_kind='CUSTODY_LOT' AND delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_lot_id IS NOT NULL AND delivered_lot_weight_grams IS NOT NULL AND delivered_lot_weight_grams=delivered_weight_grams AND origin_melt_entry_id IS NOT NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000 AND holding_party_id IS NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NULL) OR (source_kind='HOLDING_LOGO' AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL AND holding_party_id IS NOT NULL AND logo_pool_id IS NOT NULL AND lot_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NOT NULL AND holding_weight_grams=delivered_weight_grams) OR (source_kind='HOLDING_LOT' AND delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_lot_id IS NOT NULL AND delivered_lot_weight_grams IS NOT NULL AND delivered_weight_grams<=delivered_lot_weight_grams AND origin_melt_entry_id IS NOT NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000 AND holding_party_id IS NOT NULL AND lot_pool_id IS NOT NULL AND logo_pool_id IS NULL AND physical_declaration IS NOT NULL AND physical_declaration IN ('WHOLE_ORIGINAL_BAR','GRAM_ENTITLEMENT','PHYSICALLY_DIVIDED') AND (physical_declaration<>'WHOLE_ORIGINAL_BAR' OR delivered_weight_grams=delivered_lot_weight_grams) AND holding_weight_grams IS NOT NULL AND holding_weight_grams=delivered_weight_grams))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='chk_sgs_self'), "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_self CHECK ((holding_party_id IS NULL AND self_settlement_confirmed=0) OR (holding_party_id IS NOT NULL AND (supplier_id<>holding_party_id OR self_settlement_confirmed=1)))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND INDEX_NAME='idx_sgs_holding_party'), "ALTER TABLE supplier_gold_settlements ADD KEY idx_sgs_holding_party (holding_party_id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND INDEX_NAME='idx_sgs_lot_pool'), "ALTER TABLE supplier_gold_settlements ADD KEY idx_sgs_lot_pool (lot_pool_id,holding_party_id,delivered_lot_id,delivered_lot_weight_grams,delivered_fineness_permille)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND INDEX_NAME='idx_sgs_logo_pool'), "ALTER TABLE supplier_gold_settlements ADD KEY idx_sgs_logo_pool (logo_pool_id,holding_party_id,delivered_karat_id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND INDEX_NAME='uq_sgs_lot_holding'), "ALTER TABLE supplier_gold_settlements ADD UNIQUE KEY uq_sgs_lot_holding (id,lot_pool_id,holding_weight_grams)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND INDEX_NAME='uq_sgs_logo_holding'), "ALTER TABLE supplier_gold_settlements ADD UNIQUE KEY uq_sgs_logo_holding (id,logo_pool_id,holding_weight_grams)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='fk_sgs_holding_party'), "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT fk_sgs_holding_party FOREIGN KEY (holding_party_id) REFERENCES suppliers(id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='fk_sgs_lot_pool'), "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT fk_sgs_lot_pool FOREIGN KEY (lot_pool_id,holding_party_id,delivered_lot_id,delivered_lot_weight_grams,delivered_fineness_permille) REFERENCES intermediary_holding_pools(id,party_id,lot_id,weight_grams,fineness_permille)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='fk_sgs_logo_pool'), "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT fk_sgs_logo_pool FOREIGN KEY (logo_pool_id,holding_party_id,delivered_karat_id) REFERENCES logo_holding_pools(id,party_id,karat_id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.TABLE_SCHEMA=DATABASE() AND tc.TABLE_NAME='supplier_gold_settlement_legs' AND tc.CONSTRAINT_NAME='chk_sgsl_source' AND cc.CHECK_CLAUSE NOT LIKE '%HOLDING_LOT%'), "ALTER TABLE supplier_gold_settlement_legs DROP CHECK chk_sgsl_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlement_legs' AND CONSTRAINT_NAME='chk_sgsl_source'), "ALTER TABLE supplier_gold_settlement_legs ADD CONSTRAINT chk_sgsl_source CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','CUSTODY_LOT','HOLDING_LOT','HOLDING_LOGO'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='intermediary_holding_movements' AND COLUMN_NAME='document_id' AND IS_NULLABLE='NO'), "ALTER TABLE intermediary_holding_movements MODIFY COLUMN document_id BIGINT UNSIGNED NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='intermediary_holding_movements' AND COLUMN_NAME='doc_kind' AND IS_NULLABLE='NO'), "ALTER TABLE intermediary_holding_movements MODIFY COLUMN doc_kind VARCHAR(8) NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='intermediary_holding_movements' AND COLUMN_NAME='settlement_id'), "ALTER TABLE intermediary_holding_movements ADD COLUMN settlement_id BIGINT UNSIGNED NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.TABLE_SCHEMA=DATABASE() AND tc.TABLE_NAME='intermediary_holding_movements' AND tc.CONSTRAINT_NAME='chk_ihm_shape' AND cc.CHECK_CLAUSE NOT LIKE '%SETTLEMENT_REVERSAL%'), "ALTER TABLE intermediary_holding_movements DROP CHECK chk_ihm_shape", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='intermediary_holding_movements' AND CONSTRAINT_NAME='chk_ihm_shape'), "ALTER TABLE intermediary_holding_movements ADD CONSTRAINT chk_ihm_shape CHECK (weight_grams>0 AND ((document_id IS NOT NULL AND settlement_id IS NULL AND movement_type='HANDOVER' AND doc_kind='HANDOVER' AND weight_delta=weight_grams AND correction_id IS NULL) OR (document_id IS NOT NULL AND settlement_id IS NULL AND movement_type='RETURN' AND doc_kind='RETURN' AND weight_delta=-weight_grams AND correction_id IS NULL) OR (document_id IS NOT NULL AND settlement_id IS NULL AND movement_type='HANDOVER_REVERSAL' AND doc_kind='HANDOVER' AND weight_delta=-weight_grams AND correction_id IS NOT NULL) OR (document_id IS NOT NULL AND settlement_id IS NULL AND movement_type='RETURN_REVERSAL' AND doc_kind='RETURN' AND weight_delta=weight_grams AND correction_id IS NOT NULL) OR (movement_type='SETTLEMENT' AND document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NOT NULL AND weight_delta=-weight_grams AND correction_id IS NULL) OR (movement_type='SETTLEMENT_REVERSAL' AND document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NOT NULL AND weight_delta=weight_grams AND correction_id IS NOT NULL)))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='intermediary_holding_movements' AND INDEX_NAME='uq_ihm_settlement_type'), "ALTER TABLE intermediary_holding_movements ADD UNIQUE KEY uq_ihm_settlement_type (settlement_id,movement_type)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='intermediary_holding_movements' AND INDEX_NAME='idx_ihm_settlement'), "ALTER TABLE intermediary_holding_movements ADD KEY idx_ihm_settlement (settlement_id,pool_id,weight_grams)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='intermediary_holding_movements' AND CONSTRAINT_NAME='fk_ihm_settlement'), "ALTER TABLE intermediary_holding_movements ADD CONSTRAINT fk_ihm_settlement FOREIGN KEY (settlement_id,pool_id,weight_grams) REFERENCES supplier_gold_settlements(id,lot_pool_id,holding_weight_grams)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='logo_holding_movements' AND COLUMN_NAME='document_id' AND IS_NULLABLE='NO'), "ALTER TABLE logo_holding_movements MODIFY COLUMN document_id BIGINT UNSIGNED NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='logo_holding_movements' AND COLUMN_NAME='doc_kind' AND IS_NULLABLE='NO'), "ALTER TABLE logo_holding_movements MODIFY COLUMN doc_kind VARCHAR(8) NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='logo_holding_movements' AND COLUMN_NAME='settlement_id'), "ALTER TABLE logo_holding_movements ADD COLUMN settlement_id BIGINT UNSIGNED NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.TABLE_SCHEMA=DATABASE() AND tc.TABLE_NAME='logo_holding_movements' AND tc.CONSTRAINT_NAME='chk_lhm_shape' AND cc.CHECK_CLAUSE NOT LIKE '%SETTLEMENT_REVERSAL%'), "ALTER TABLE logo_holding_movements DROP CHECK chk_lhm_shape", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='logo_holding_movements' AND CONSTRAINT_NAME='chk_lhm_shape'), "ALTER TABLE logo_holding_movements ADD CONSTRAINT chk_lhm_shape CHECK (weight_grams>0 AND ((document_id IS NOT NULL AND settlement_id IS NULL AND movement_type='HANDOVER' AND doc_kind='HANDOVER' AND weight_delta=weight_grams AND correction_id IS NULL) OR (document_id IS NOT NULL AND settlement_id IS NULL AND movement_type='RETURN' AND doc_kind='RETURN' AND weight_delta=-weight_grams AND correction_id IS NULL) OR (document_id IS NOT NULL AND settlement_id IS NULL AND movement_type='HANDOVER_REVERSAL' AND doc_kind='HANDOVER' AND weight_delta=-weight_grams AND correction_id IS NOT NULL) OR (document_id IS NOT NULL AND settlement_id IS NULL AND movement_type='RETURN_REVERSAL' AND doc_kind='RETURN' AND weight_delta=weight_grams AND correction_id IS NOT NULL) OR (movement_type='SETTLEMENT' AND document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NOT NULL AND weight_delta=-weight_grams AND correction_id IS NULL) OR (movement_type='SETTLEMENT_REVERSAL' AND document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NOT NULL AND weight_delta=weight_grams AND correction_id IS NOT NULL)))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='logo_holding_movements' AND INDEX_NAME='uq_lhm_settlement_type'), "ALTER TABLE logo_holding_movements ADD UNIQUE KEY uq_lhm_settlement_type (settlement_id,movement_type)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='logo_holding_movements' AND INDEX_NAME='idx_lhm_settlement'), "ALTER TABLE logo_holding_movements ADD KEY idx_lhm_settlement (settlement_id,pool_id,weight_grams)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='logo_holding_movements' AND CONSTRAINT_NAME='fk_lhm_settlement'), "ALTER TABLE logo_holding_movements ADD CONSTRAINT fk_lhm_settlement FOREIGN KEY (settlement_id,pool_id,weight_grams) REFERENCES supplier_gold_settlements(id,logo_pool_id,holding_weight_grams)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (50,'Supplier gold settlement from intermediary holdings: HOLDING_LOT / HOLDING_LOGO SG sources and settlement movements')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
