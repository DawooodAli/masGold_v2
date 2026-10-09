-- Refuse rollback once any melt, lot or designated (non-UNCLASSIFIED) gold line exists, or when a later migration exists.
-- Never erase history: designations, melt documents, lots and their analysis are permanent facts.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM market_trip_melts)
 OR EXISTS(SELECT 1 FROM market_trip_melt_inputs)
 OR EXISTS(SELECT 1 FROM market_trip_lots)
 OR EXISTS(SELECT 1 FROM market_trip_lot_movements)
 OR EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind = 'MELT')
 OR EXISTS(SELECT 1 FROM market_trip_entry_gold WHERE designation <> 'UNCLASSIFIED')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>39),
 'SELECT * FROM ROLLBACK_039_REFUSED_melting_data_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE market_trip_lot_movements;
DROP TABLE market_trip_lots;
DROP TABLE market_trip_melt_inputs;
DROP TABLE market_trip_melts;
ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind;
ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN'));
ALTER TABLE market_trip_entry_gold ADD UNIQUE KEY uq_mteg_entry_karat (entry_id, karat_id);
ALTER TABLE market_trip_entry_gold DROP INDEX uq_mteg_entry_karat_des;
ALTER TABLE market_trip_entry_gold DROP CHECK chk_mteg_designation;
ALTER TABLE market_trip_entry_gold DROP COLUMN designation;
DELETE FROM schema_migrations WHERE version=39;
