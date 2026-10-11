-- No historical record, including reversed consumption, may be erased.
SET @masgold_sql=IF(EXISTS(SELECT 1 FROM market_trip_lot_settlements)
 OR EXISTS(SELECT 1 FROM supplier_gold_settlements WHERE source_kind='CUSTODY_LOT')
 OR EXISTS(SELECT 1 FROM supplier_gold_settlement_legs WHERE source_kind='CUSTODY_LOT')
 OR EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind='LOT_SETTLE')
 OR EXISTS(SELECT 1 FROM market_trip_lot_movements WHERE movement_type='SUPPLIER_SETTLE')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>47),
 'SELECT * FROM ROLLBACK_047_REFUSED_history_or_later_migration','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE market_trip_lot_settlements;
ALTER TABLE supplier_gold_settlements DROP FOREIGN KEY fk_sgs_lot_facts;
ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_shape;
ALTER TABLE supplier_gold_settlements DROP INDEX uq_sgs_lot_link;
ALTER TABLE supplier_gold_settlements DROP COLUMN delivered_lot_id, DROP COLUMN delivered_lot_weight_grams, DROP COLUMN origin_melt_entry_id, DROP COLUMN delivered_fineness_permille;
ALTER TABLE supplier_gold_settlements MODIFY COLUMN delivered_karat_id TINYINT UNSIGNED NOT NULL, MODIFY COLUMN delivered_karat_value TINYINT UNSIGNED NOT NULL;
ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_source;
ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_source CHECK(source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD'));
ALTER TABLE supplier_gold_settlement_legs DROP CHECK chk_sgsl_source;
ALTER TABLE supplier_gold_settlement_legs ADD CONSTRAINT chk_sgsl_source CHECK(source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD'));
ALTER TABLE market_trip_entries DROP INDEX uq_mte_id_kind;
ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind;
ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK(entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE'));
ALTER TABLE market_trip_lot_movements DROP INDEX uq_mtlm_settle_link;
ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_type;
ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_type CHECK(movement_type IN ('CREATE','SHOP_RETURN','SHOP_ISSUE'));
ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_delta;
ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_delta CHECK(weight_delta<>0 AND (movement_type<>'CREATE' OR weight_delta>0) AND (movement_type<>'SHOP_RETURN' OR weight_delta<0) AND (movement_type<>'SHOP_ISSUE' OR weight_delta>0));
ALTER TABLE market_trip_lots DROP INDEX uq_mtl_settle_facts;
DELETE FROM schema_migrations WHERE version=47;
