-- No holding history, including reversed history, may be erased.
SET @masgold_sql=IF(EXISTS(SELECT 1 FROM intermediary_holding_pools)
 OR EXISTS(SELECT 1 FROM intermediary_holding_documents)
 OR EXISTS(SELECT 1 FROM intermediary_holding_movements)
 OR EXISTS(SELECT 1 FROM market_trip_holding_links)
 OR EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind IN ('LOT_HOLD','HOLD_RETURN'))
 OR EXISTS(SELECT 1 FROM market_trip_lot_movements WHERE movement_type IN ('HOLD_OUT','HOLD_IN'))
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>48),
 'SELECT * FROM ROLLBACK_048_REFUSED_history_or_later_migration','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE market_trip_holding_links;
DROP TABLE intermediary_holding_movements;
DROP TABLE intermediary_holding_documents;
DROP TABLE intermediary_holding_pools;
ALTER TABLE market_trip_entries DROP INDEX uq_mte_id_kind_trip;
ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind;
ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK(entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE'));
ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_type;
ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_type CHECK(movement_type IN ('CREATE','SHOP_RETURN','SHOP_ISSUE','SUPPLIER_SETTLE'));
ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_delta;
ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_delta CHECK (weight_delta <> 0 AND (movement_type NOT IN ('CREATE','SHOP_ISSUE') OR weight_delta>0) AND (movement_type NOT IN ('SHOP_RETURN','SUPPLIER_SETTLE') OR weight_delta<0));
DELETE rp FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
 WHERE p.code IN ('INTERMEDIARY_HOLDING_VIEW','INTERMEDIARY_HOLDING_MANAGE','INTERMEDIARY_HOLDING_REVERSE');
DELETE FROM permissions WHERE code IN ('INTERMEDIARY_HOLDING_VIEW','INTERMEDIARY_HOLDING_MANAGE','INTERMEDIARY_HOLDING_REVERSE');
DELETE FROM schema_migrations WHERE version=48;
