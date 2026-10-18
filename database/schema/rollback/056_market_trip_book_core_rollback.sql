-- Rollback of 056 (market-trip BOOK core). It REFUSES, and changes nothing, when any BOOK business history exists (a BOOK trip, a BOOK entry, a book line, a
-- trade document or an assayed-stock movement) or when a later migration was applied: recorded history is never dropped or rewritten to make a rollback pass.
-- With no BOOK record at all it removes only what 056 added and restores the 055 definition of chk_mte_kind; every trip and entry then keeps its legacy meaning.
SET @masgold_sql=IF(EXISTS(SELECT 1 FROM market_trips WHERE accounting_mode<>'LEGACY')
 OR EXISTS(SELECT 1 FROM market_trip_entries WHERE trip_mode<>'LEGACY' OR entry_kind IN ('BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN'))
 OR EXISTS(SELECT 1 FROM market_trip_book_lines)
 OR EXISTS(SELECT 1 FROM market_trip_book_trades)
 OR EXISTS(SELECT 1 FROM shop_assayed_stock_movements)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>56),
 'SELECT * FROM ROLLBACK_056_REFUSED_book_history_or_later_migration','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS shop_assayed_stock_movements;
DROP TABLE IF EXISTS market_trip_book_trades;
DROP TABLE IF EXISTS market_trip_book_lines;
ALTER TABLE market_trip_entry_gold DROP FOREIGN KEY fk_mteg_mode;
ALTER TABLE market_trip_entry_gold DROP CHECK chk_mteg_mode;
ALTER TABLE market_trip_entry_gold DROP COLUMN trip_mode;
ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind;
ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind;
ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY'));
ALTER TABLE market_trip_entries DROP FOREIGN KEY fk_mte_trip_mode;
ALTER TABLE market_trip_entries DROP INDEX uq_mte_id_mode;
ALTER TABLE market_trip_entries DROP COLUMN trip_mode;
ALTER TABLE market_trips DROP CHECK chk_mt_mode;
ALTER TABLE market_trips DROP INDEX uq_mt_id_mode;
ALTER TABLE market_trips DROP COLUMN accounting_mode;
DELETE FROM schema_migrations WHERE version=56;
