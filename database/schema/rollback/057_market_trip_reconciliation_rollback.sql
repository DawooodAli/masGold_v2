-- Rollback of 057 (market-trip BOOK reconciliation document). It REFUSES, and changes nothing, when ANY reconciliation history exists (a reconciliation row, or a BK_RECON
-- entry - a reversed one included, since its entry and correction record remain history) or when a later migration was applied: recorded history is never dropped, rewritten or
-- deleted to make a rollback pass. With no such record it removes only what 057 added and restores the 056 definitions of chk_mte_kind and chk_mte_mode_kind.
SET @masgold_sql=IF(EXISTS(SELECT 1 FROM market_trip_reconciliations)
 OR EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind='BK_RECON')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>57),
 'SELECT * FROM ROLLBACK_057_REFUSED_reconciliation_history_or_later_migration','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS market_trip_reconciliations;
ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind;
ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind;
ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN'));
ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN')));
DELETE FROM schema_migrations WHERE version=57;
