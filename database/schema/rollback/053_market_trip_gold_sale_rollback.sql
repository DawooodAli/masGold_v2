-- No sale of custody gold (document, entry or SALE_OUT movement) may be erased, and no later migration may exist.
SET @masgold_sql=IF(EXISTS(SELECT 1 FROM market_trip_gold_sales)
 OR EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind='GOLD_SALE')
 OR EXISTS(SELECT 1 FROM market_trip_lot_movements WHERE movement_type='SALE_OUT')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>53),
 'SELECT * FROM ROLLBACK_053_REFUSED_history_or_later_migration','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS market_trip_gold_sales;
ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind;
ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK'));
ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_type;
ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_type CHECK (movement_type IN ('CREATE','SHOP_RETURN','SHOP_ISSUE','SUPPLIER_SETTLE','HOLD_OUT','HOLD_IN'));
ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_delta;
ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_delta CHECK (weight_delta <> 0 AND (movement_type NOT IN ('CREATE','SHOP_ISSUE','HOLD_IN') OR weight_delta>0) AND (movement_type NOT IN ('SHOP_RETURN','SUPPLIER_SETTLE','HOLD_OUT') OR weight_delta<0));
DELETE FROM schema_migrations WHERE version=53;
