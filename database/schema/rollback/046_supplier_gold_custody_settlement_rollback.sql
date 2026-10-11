-- Refuse rollback once any custody gold settlement (link, GOLD_SETTLE entry, CUSTODY_GOLD document or leg) exists, or when a later migration
-- exists. Never erase history. Direct (SHOP_SCRAP) settlements of 045 are untouched.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM market_trip_gold_settlements)
 OR EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind = 'GOLD_SETTLE')
 OR EXISTS(SELECT 1 FROM supplier_gold_settlements WHERE source_kind <> 'SHOP_SCRAP')
 OR EXISTS(SELECT 1 FROM supplier_gold_settlement_legs WHERE source_kind <> 'SHOP_SCRAP')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>46),
 'SELECT * FROM ROLLBACK_046_REFUSED_custody_settlement_data_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE market_trip_gold_settlements;
ALTER TABLE supplier_gold_settlement_legs DROP FOREIGN KEY fk_sgsl_source;
ALTER TABLE supplier_gold_settlement_legs DROP CHECK chk_sgsl_scrap_only;
ALTER TABLE supplier_gold_settlement_legs DROP CHECK chk_sgsl_source;
ALTER TABLE supplier_gold_settlement_legs DROP INDEX idx_sgsl_source;
ALTER TABLE supplier_gold_settlement_legs DROP COLUMN source_kind;
ALTER TABLE supplier_gold_settlements DROP INDEX uq_sgs_link;
ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_source;
ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_source CHECK (source_kind = 'SHOP_SCRAP');
ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind;
ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY'));
DELETE FROM schema_migrations WHERE version=46;
