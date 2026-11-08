-- Rollback of 103 (bar fee without a party). Refuses when a later migration exists or any fee has no party (the restored NOT NULL columns could not hold it).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 103),
 'SELECT * FROM ROLLBACK_103_REFUSED_later_migration_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'market_trip_bar_fees')
   AND (SELECT COUNT(*) FROM market_trip_bar_fees WHERE party_id IS NULL) > 0,
 'SELECT * FROM ROLLBACK_103_REFUSED_fee_without_party_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE market_trip_bar_fees DROP CHECK chk_mtbf_party;
ALTER TABLE market_trip_bar_fees MODIFY party_id INT UNSIGNED NOT NULL;
ALTER TABLE market_trip_bar_fees MODIFY party_kind VARCHAR(12) NOT NULL;
ALTER TABLE market_trip_bar_fees ADD CONSTRAINT chk_mtbf_party CHECK (party_kind IN ('SUPPLIER','INTERMEDIARY') AND (party_kind = 'SUPPLIER' OR making_movement_id IS NULL));
DELETE FROM schema_migrations WHERE version = 103;
