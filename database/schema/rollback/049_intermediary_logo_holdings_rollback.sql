-- No LOGO holding history, including reversed history, may be erased.
SET @masgold_sql=IF(EXISTS(SELECT 1 FROM logo_holding_pools)
 OR EXISTS(SELECT 1 FROM logo_holding_documents)
 OR EXISTS(SELECT 1 FROM logo_holding_movements)
 OR EXISTS(SELECT 1 FROM market_trip_logo_hold_links)
 OR EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind IN ('LOGO_HOLD','HOLD_BACK'))
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>49),
 'SELECT * FROM ROLLBACK_049_REFUSED_history_or_later_migration','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE market_trip_logo_hold_links;
DROP TABLE logo_holding_movements;
DROP TABLE logo_holding_documents;
DROP TABLE logo_holding_pools;
ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind;
ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK(entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN'));
DELETE FROM schema_migrations WHERE version=49;
