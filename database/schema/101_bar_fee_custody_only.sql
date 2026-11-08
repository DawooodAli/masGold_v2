-- Bar fee «أجر السبيكة» goes to the custody cash ONLY (owner 2026-11-08): it no longer deducts anything from the supplier's making account. Migration 080 required a SUPPLIER fee to carry a
-- making movement (chk_mtbf_party); the new rule lets a fee of either party kind exist without one. Fees already posted keep their stored supplier deduction (and their reversal restores it);
-- nothing is rewritten. Only this CHECK changes. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=100),
 'DO 0', 'SELECT * FROM MIGRATION_101_REFUSED_missing_100');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtbf_party' AND CHECK_CLAUSE LIKE '%is not null%'),
 "ALTER TABLE market_trip_bar_fees DROP CHECK chk_mtbf_party", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_bar_fees' AND CONSTRAINT_NAME='chk_mtbf_party'),
 "ALTER TABLE market_trip_bar_fees ADD CONSTRAINT chk_mtbf_party CHECK (party_kind IN ('SUPPLIER','INTERMEDIARY') AND (party_kind = 'SUPPLIER' OR making_movement_id IS NULL))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (101,'Bar fee: custody cash only, a supplier fee needs no supplier making movement')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
