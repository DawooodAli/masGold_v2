-- Bar fee «أجر السبيكة» belongs to the market custody ONLY (owner 2026-11-08): the dialog no longer asks for a supplier / marketer, so a fee may have no party. party_id and party_kind become
-- nullable (a fee without a party carries neither and no making movement); a fee that has a party keeps the 101 rule. Fees already posted keep their party. Only these two columns and the CHECK
-- change. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=102),
 'DO 0', 'SELECT * FROM MIGRATION_103_REFUSED_missing_102');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_bar_fees' AND COLUMN_NAME='party_id' AND IS_NULLABLE='NO'),
 "ALTER TABLE market_trip_bar_fees MODIFY party_id INT UNSIGNED DEFAULT NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_bar_fees' AND COLUMN_NAME='party_kind' AND IS_NULLABLE='NO'),
 "ALTER TABLE market_trip_bar_fees MODIFY party_kind VARCHAR(12) DEFAULT NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtbf_party' AND CHECK_CLAUSE NOT LIKE '%party_id%'),
 "ALTER TABLE market_trip_bar_fees DROP CHECK chk_mtbf_party", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_bar_fees' AND CONSTRAINT_NAME='chk_mtbf_party'),
 "ALTER TABLE market_trip_bar_fees ADD CONSTRAINT chk_mtbf_party CHECK ((party_id IS NULL AND party_kind IS NULL AND making_movement_id IS NULL) OR (party_id IS NOT NULL AND party_kind IN ('SUPPLIER','INTERMEDIARY') AND (party_kind = 'SUPPLIER' OR making_movement_id IS NULL)))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (103,'Bar fee: custody only, no supplier / marketer needed (party columns nullable)')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
