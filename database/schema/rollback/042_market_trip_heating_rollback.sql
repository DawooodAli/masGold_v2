-- Refuse rollback once any heating document, line or entry exists, or when a later migration exists. Never erase history.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM market_trip_heat_lines)
 OR EXISTS(SELECT 1 FROM market_trip_heats)
 OR EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind = 'HEAT')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>42),
 'SELECT * FROM ROLLBACK_042_REFUSED_heating_data_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE market_trip_heat_lines;
DROP TABLE market_trip_heats;
ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind;
ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE'));
DELETE FROM schema_migrations WHERE version=42;
