-- Refuse rollback once any lot return, shop-side lot movement or SHOP_RETURN custody movement exists, or when a later migration
-- exists. Never erase history: returned lots and their shop ledger are permanent facts.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM market_trip_lot_returns)
 OR EXISTS(SELECT 1 FROM market_trip_lot_shop_movements)
 OR EXISTS(SELECT 1 FROM market_trip_lot_movements WHERE movement_type <> 'CREATE')
 OR EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind = 'LOT_RETURN')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>40),
 'SELECT * FROM ROLLBACK_040_REFUSED_lot_return_data_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE market_trip_lot_returns;
DROP TABLE market_trip_lot_shop_movements;
ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_delta;
ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_delta CHECK (weight_delta <> 0 AND (movement_type <> 'CREATE' OR weight_delta > 0));
ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_type;
ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_type CHECK (movement_type IN ('CREATE'));
ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind;
ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT'));
ALTER TABLE market_trip_entries MODIFY COLUMN entry_kind VARCHAR(6) NOT NULL;
DELETE FROM schema_migrations WHERE version=40;
