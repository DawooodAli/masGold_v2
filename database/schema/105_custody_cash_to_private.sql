-- Market-custody CASH straight to the owner's private account (owner 2026-11-14). Prepared; NOT applied to any database by the task that wrote it.
--   entry kind ST_CASH_PRIV: the remaining custody cash is taken by the owner into his PRIVATE account: the amount leaves the custody cash, NO cash / bank movement exists (the custody cash is
--   in no ledger account), and ONE private_account_entries row (TO_PRIVATE) is tied to the entry. It shares the immutable settlement document market_trip_settle_cash (no bank, an optional note).
--   private_account_entries gets a second possible source: a bank / wallet operation (operation_id, as before) OR a custody entry (trip_entry_id, exactly one of them). The private balance stays derived:
--   a custody-sourced entry is voided when its custody entry has a full reversal (a correction of source MARKET_TRIP_ENTRY).
-- No existing row is rewritten. LEGACY trips only. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=104),
 'DO 0', 'SELECT * FROM MIGRATION_105_REFUSED_missing_104');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='private_account_entries'),
 'DO 0', 'SELECT * FROM MIGRATION_105_REFUSED_missing_090_private_account_entries');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%ST_CASH_PRIV%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN','ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','ST_EXPENSE','ST_CASH_BANK','ST_CASH_OFF','ST_RET_MKT','ST_LOSS','ST_ADJUST','ST_BAR_FEE','ST_CASH_PRIV'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_mode_kind' AND CHECK_CLAUSE NOT LIKE '%ST_CASH_PRIV%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_mode_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','ST_EXPENSE','ST_CASH_BANK','ST_CASH_OFF','ST_RET_MKT','ST_LOSS','ST_ADJUST','ST_BAR_FEE','ST_CASH_PRIV')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtsc_shape' AND CHECK_CLAUSE NOT LIKE '%ST_CASH_PRIV%'),
 "ALTER TABLE market_trip_settle_cash DROP CHECK chk_mtsc_shape", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_settle_cash' AND CONSTRAINT_NAME='chk_mtsc_shape'),
 "ALTER TABLE market_trip_settle_cash ADD CONSTRAINT chk_mtsc_shape CHECK ((entry_kind IN ('ST_EXPENSE','ST_CASH_OFF') AND description IS NOT NULL AND CHAR_LENGTH(TRIM(description)) > 0 AND bank_account_id IS NULL AND bank_movement_id IS NULL) OR (entry_kind = 'ST_CASH_BANK' AND bank_account_id IS NOT NULL AND bank_movement_id IS NOT NULL) OR (entry_kind = 'ST_CASH_PRIV' AND bank_account_id IS NULL AND bank_movement_id IS NULL))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='private_account_entries' AND COLUMN_NAME='operation_id' AND IS_NULLABLE='NO'),
 "ALTER TABLE private_account_entries MODIFY COLUMN operation_id BIGINT UNSIGNED DEFAULT NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='private_account_entries' AND COLUMN_NAME='trip_entry_id'),
 "ALTER TABLE private_account_entries ADD COLUMN trip_entry_id BIGINT UNSIGNED DEFAULT NULL AFTER operation_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='private_account_entries' AND COLUMN_NAME='trip_entry_kind'),
 "ALTER TABLE private_account_entries ADD COLUMN trip_entry_kind VARCHAR(12) DEFAULT NULL AFTER trip_entry_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='private_account_entries' AND INDEX_NAME='uq_private_entry_trip_entry'),
 "ALTER TABLE private_account_entries ADD UNIQUE KEY uq_private_entry_trip_entry (trip_entry_id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='private_account_entries' AND CONSTRAINT_NAME='fk_private_entry_trip_entry'),
 "ALTER TABLE private_account_entries ADD CONSTRAINT fk_private_entry_trip_entry FOREIGN KEY (trip_entry_id, trip_entry_kind) REFERENCES market_trip_entries(id, entry_kind)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='private_account_entries' AND CONSTRAINT_NAME='chk_private_entry_source'),
 "ALTER TABLE private_account_entries ADD CONSTRAINT chk_private_entry_source CHECK ((operation_id IS NOT NULL AND trip_entry_id IS NULL AND trip_entry_kind IS NULL) OR (operation_id IS NULL AND trip_entry_id IS NOT NULL AND trip_entry_kind = 'ST_CASH_PRIV' AND direction = 'TO_PRIVATE'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (105,'Market-custody cash straight to the private account')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
