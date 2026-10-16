-- No marketer document, ledger row or marketer settlement may be erased, and no later migration may exist.
SET @masgold_sql=IF(EXISTS(SELECT 1 FROM marketer_documents)
 OR EXISTS(SELECT 1 FROM marketer_cash_movements)
 OR EXISTS(SELECT 1 FROM marketer_gold_movements)
 OR EXISTS(SELECT 1 FROM supplier_gold_settlements WHERE source_kind='HOLDING_MKT')
 OR EXISTS(SELECT 1 FROM supplier_gold_settlement_legs WHERE source_kind='HOLDING_MKT')
 OR EXISTS(SELECT 1 FROM transaction_corrections WHERE source_type='MARKETER_DOCUMENT')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>55),
 'SELECT * FROM ROLLBACK_055_REFUSED_history_or_later_migration','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS marketer_gold_movements;
DROP TABLE IF EXISTS marketer_cash_movements;
DROP TABLE IF EXISTS marketer_purchase_lines;
DROP TABLE IF EXISTS marketer_shop_links;
DROP TABLE IF EXISTS marketer_documents;
ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_shape;
ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_shape CHECK ((source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD') AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL AND holding_party_id IS NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NULL) OR (source_kind='CUSTODY_LOT' AND delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_lot_id IS NOT NULL AND delivered_lot_weight_grams IS NOT NULL AND delivered_lot_weight_grams=delivered_weight_grams AND origin_melt_entry_id IS NOT NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000 AND holding_party_id IS NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NULL) OR (source_kind='HOLDING_LOGO' AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL AND holding_party_id IS NOT NULL AND logo_pool_id IS NOT NULL AND lot_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NOT NULL AND holding_weight_grams=delivered_weight_grams) OR (source_kind='HOLDING_LOT' AND delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_lot_id IS NOT NULL AND delivered_lot_weight_grams IS NOT NULL AND delivered_weight_grams<=delivered_lot_weight_grams AND origin_melt_entry_id IS NOT NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000 AND holding_party_id IS NOT NULL AND lot_pool_id IS NOT NULL AND logo_pool_id IS NULL AND physical_declaration IS NOT NULL AND physical_declaration IN ('WHOLE_ORIGINAL_BAR','GRAM_ENTITLEMENT','PHYSICALLY_DIVIDED') AND (physical_declaration<>'WHOLE_ORIGINAL_BAR' OR delivered_weight_grams=delivered_lot_weight_grams) AND holding_weight_grams IS NOT NULL AND holding_weight_grams=delivered_weight_grams));
ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_source;
ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_source CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','CUSTODY_LOT','HOLDING_LOT','HOLDING_LOGO'));
ALTER TABLE supplier_gold_settlements DROP INDEX uq_sgs_marketer;
ALTER TABLE supplier_gold_settlement_legs DROP CHECK chk_sgsl_source;
ALTER TABLE supplier_gold_settlement_legs ADD CONSTRAINT chk_sgsl_source CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','CUSTODY_LOT','HOLDING_LOT','HOLDING_LOGO'));
ALTER TABLE transaction_corrections DROP CHECK chk_correction_source;
ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK ((source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL) OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL) OR (source_type IN ('FINANCIAL_OPERATION','CUSTOMER_DEPOSIT','SUPPLIER_MAKING_PAYMENT','MARKET_TRIP_ENTRY','SUPPLIER_GOLD_SETTLEMENT','HOLDING_RECEIPT','SUPPLIER_PURCHASE_INVOICE') AND invoice_id IS NULL AND correction_type='FULL_REVERSAL'));
DELETE FROM schema_migrations WHERE version=55;
