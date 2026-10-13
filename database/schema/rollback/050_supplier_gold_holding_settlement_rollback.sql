-- No holding-backed settlement or holding settlement movement, reversed ones included, may be erased.
SET @masgold_sql=IF(EXISTS(SELECT 1 FROM supplier_gold_settlements WHERE source_kind IN ('HOLDING_LOT','HOLDING_LOGO'))
 OR EXISTS(SELECT 1 FROM supplier_gold_settlement_legs WHERE source_kind IN ('HOLDING_LOT','HOLDING_LOGO'))
 OR EXISTS(SELECT 1 FROM intermediary_holding_movements WHERE settlement_id IS NOT NULL)
 OR EXISTS(SELECT 1 FROM logo_holding_movements WHERE settlement_id IS NOT NULL)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>50),
 'SELECT * FROM ROLLBACK_050_REFUSED_history_or_later_migration','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE intermediary_holding_movements DROP FOREIGN KEY fk_ihm_settlement;
ALTER TABLE intermediary_holding_movements DROP INDEX idx_ihm_settlement;
ALTER TABLE intermediary_holding_movements DROP INDEX uq_ihm_settlement_type;
ALTER TABLE intermediary_holding_movements DROP CHECK chk_ihm_shape;
ALTER TABLE intermediary_holding_movements DROP COLUMN settlement_id;
ALTER TABLE intermediary_holding_movements MODIFY COLUMN document_id BIGINT UNSIGNED NOT NULL;
ALTER TABLE intermediary_holding_movements MODIFY COLUMN doc_kind VARCHAR(8) NOT NULL;
ALTER TABLE intermediary_holding_movements ADD CONSTRAINT chk_ihm_shape CHECK (weight_grams>0 AND ((movement_type='HANDOVER' AND doc_kind='HANDOVER' AND weight_delta=weight_grams AND correction_id IS NULL) OR (movement_type='RETURN' AND doc_kind='RETURN' AND weight_delta=-weight_grams AND correction_id IS NULL) OR (movement_type='HANDOVER_REVERSAL' AND doc_kind='HANDOVER' AND weight_delta=-weight_grams AND correction_id IS NOT NULL) OR (movement_type='RETURN_REVERSAL' AND doc_kind='RETURN' AND weight_delta=weight_grams AND correction_id IS NOT NULL)));
ALTER TABLE logo_holding_movements DROP FOREIGN KEY fk_lhm_settlement;
ALTER TABLE logo_holding_movements DROP INDEX idx_lhm_settlement;
ALTER TABLE logo_holding_movements DROP INDEX uq_lhm_settlement_type;
ALTER TABLE logo_holding_movements DROP CHECK chk_lhm_shape;
ALTER TABLE logo_holding_movements DROP COLUMN settlement_id;
ALTER TABLE logo_holding_movements MODIFY COLUMN document_id BIGINT UNSIGNED NOT NULL;
ALTER TABLE logo_holding_movements MODIFY COLUMN doc_kind VARCHAR(8) NOT NULL;
ALTER TABLE logo_holding_movements ADD CONSTRAINT chk_lhm_shape CHECK (weight_grams>0 AND ((movement_type='HANDOVER' AND doc_kind='HANDOVER' AND weight_delta=weight_grams AND correction_id IS NULL) OR (movement_type='RETURN' AND doc_kind='RETURN' AND weight_delta=-weight_grams AND correction_id IS NULL) OR (movement_type='HANDOVER_REVERSAL' AND doc_kind='HANDOVER' AND weight_delta=-weight_grams AND correction_id IS NOT NULL) OR (movement_type='RETURN_REVERSAL' AND doc_kind='RETURN' AND weight_delta=weight_grams AND correction_id IS NOT NULL)));
ALTER TABLE supplier_gold_settlement_legs DROP CHECK chk_sgsl_source;
ALTER TABLE supplier_gold_settlement_legs ADD CONSTRAINT chk_sgsl_source CHECK(source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','CUSTODY_LOT'));
ALTER TABLE supplier_gold_settlements DROP FOREIGN KEY fk_sgs_logo_pool;
ALTER TABLE supplier_gold_settlements DROP FOREIGN KEY fk_sgs_lot_pool;
ALTER TABLE supplier_gold_settlements DROP FOREIGN KEY fk_sgs_holding_party;
ALTER TABLE supplier_gold_settlements DROP INDEX uq_sgs_logo_holding;
ALTER TABLE supplier_gold_settlements DROP INDEX uq_sgs_lot_holding;
ALTER TABLE supplier_gold_settlements DROP INDEX idx_sgs_logo_pool;
ALTER TABLE supplier_gold_settlements DROP INDEX idx_sgs_lot_pool;
ALTER TABLE supplier_gold_settlements DROP INDEX idx_sgs_holding_party;
ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_self;
ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_shape;
ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_source;
ALTER TABLE supplier_gold_settlements DROP COLUMN self_settlement_confirmed, DROP COLUMN holding_weight_grams, DROP COLUMN physical_declaration, DROP COLUMN logo_pool_id, DROP COLUMN lot_pool_id, DROP COLUMN holding_party_id;
ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_source CHECK(source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','CUSTODY_LOT'));
ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_shape CHECK ((source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD') AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL) OR (source_kind='CUSTODY_LOT' AND delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_lot_id IS NOT NULL AND delivered_lot_weight_grams IS NOT NULL AND delivered_lot_weight_grams=delivered_weight_grams AND origin_melt_entry_id IS NOT NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000));
ALTER TABLE logo_holding_pools DROP INDEX uq_lhp_source;
DELETE FROM schema_migrations WHERE version=50;
