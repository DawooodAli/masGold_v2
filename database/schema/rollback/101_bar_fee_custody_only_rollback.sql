-- Rollback of 101 (bar fee custody only). Refuses when a later migration exists or any SUPPLIER fee has no making movement (the restored rule could not hold it).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 101),
 'SELECT * FROM ROLLBACK_101_REFUSED_later_migration_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'market_trip_bar_fees')
   AND (SELECT COUNT(*) FROM market_trip_bar_fees WHERE party_kind = 'SUPPLIER' AND making_movement_id IS NULL) > 0,
 'SELECT * FROM ROLLBACK_101_REFUSED_supplier_fee_without_making_movement', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE market_trip_bar_fees DROP CHECK chk_mtbf_party;
ALTER TABLE market_trip_bar_fees ADD CONSTRAINT chk_mtbf_party CHECK ((party_kind = 'SUPPLIER' AND making_movement_id IS NOT NULL) OR (party_kind = 'INTERMEDIARY' AND making_movement_id IS NULL));
DELETE FROM schema_migrations WHERE version = 101;
