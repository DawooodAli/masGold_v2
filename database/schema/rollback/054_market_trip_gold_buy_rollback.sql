-- No cash gold purchase (document, line or entry) may be erased, and no later migration may exist.
SET @masgold_sql=IF(EXISTS(SELECT 1 FROM market_trip_gold_buys)
 OR EXISTS(SELECT 1 FROM market_trip_gold_buy_lines)
 OR EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind='GOLD_BUY')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>54),
 'SELECT * FROM ROLLBACK_054_REFUSED_history_or_later_migration','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS market_trip_gold_buy_lines;
DROP TABLE IF EXISTS market_trip_gold_buys;
ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind;
ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE'));
DELETE FROM schema_migrations WHERE version=54;
