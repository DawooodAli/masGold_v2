-- Refuse rollback once any lot issue, its movement or entry exists, or when a later migration exists. Never erase history.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM market_trip_lot_issues)
 OR EXISTS(SELECT 1 FROM market_trip_lot_shop_movements WHERE movement_type IN ('TRIP_ISSUE','ISSUE_REVERSAL'))
 OR EXISTS(SELECT 1 FROM market_trip_lot_movements WHERE movement_type = 'SHOP_ISSUE')
 OR EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind = 'LOT_ISSUE')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>41),
 'SELECT * FROM ROLLBACK_041_REFUSED_lot_issue_data_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE market_trip_lot_issues;
ALTER TABLE market_trip_lot_shop_movements DROP CHECK chk_mtlsm_shape;
ALTER TABLE market_trip_lot_shop_movements ADD CONSTRAINT chk_mtlsm_shape CHECK (
 (movement_type = 'TRIP_RETURN' AND weight_delta > 0 AND correction_id IS NULL)
 OR (movement_type = 'RETURN_REVERSAL' AND weight_delta < 0 AND correction_id IS NOT NULL));
ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_delta;
ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_delta CHECK (weight_delta <> 0
 AND (movement_type <> 'CREATE' OR weight_delta > 0) AND (movement_type <> 'SHOP_RETURN' OR weight_delta < 0));
ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_type;
ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_type CHECK (movement_type IN ('CREATE','SHOP_RETURN'));
ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind;
ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN'));
DELETE FROM schema_migrations WHERE version=41;
