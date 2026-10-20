-- Market-custody SETTLEMENT, step 2e: settling the custody CASH after the owner returns. Prepared; NOT applied to any database by the task that wrote it.
--   entry kind ST_EXPENSE   a free-text expense paid from the custody cash (description mandatory): the amount leaves the custody cash, NO cash / bank movement.
--   entry kind ST_CASH_BANK the remaining custody cash deposited into an active bank / wallet account: the amount leaves the custody cash and ONE bank movement (type TRIP_CUSTODY_BANK_IN) enters the account.
--   entry kind ST_CASH_OFF  the remaining custody cash written off: the amount leaves the custody cash, a mandatory reason, NO other movement.
--   All three are LEGACY-only and share the immutable document market_trip_settle_cash (one row per entry). Custody cash = issued - returned - paid + proceeds - purchases - settled (defined once in custody_rules).
--   The gold purchase (054) lines may now be UNCLASSIFIED: what was bought in the market has no melt / logo decision until the settlement.
-- No existing row is rewritten. The CHECK expressions have NOT been executed on a MySQL server before the rehearsal gate. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=63),
 'DO 0', 'SELECT * FROM MIGRATION_064_REFUSED_missing_063');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%ST_EXPENSE%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN','ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','ST_EXPENSE','ST_CASH_BANK','ST_CASH_OFF'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_mode_kind' AND CHECK_CLAUSE NOT LIKE '%ST_EXPENSE%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_mode_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','ST_EXPENSE','ST_CASH_BANK','ST_CASH_OFF')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtgbl_designation' AND CHECK_CLAUSE NOT LIKE '%UNCLASSIFIED%'),
 "ALTER TABLE market_trip_gold_buy_lines DROP CHECK chk_mtgbl_designation", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_gold_buy_lines' AND CONSTRAINT_NAME='chk_mtgbl_designation'),
 "ALTER TABLE market_trip_gold_buy_lines ADD CONSTRAINT chk_mtgbl_designation CHECK (designation IN ('MELT','LOGO','UNCLASSIFIED'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO bank_movement_types(code,name,expected_direction,description) VALUES
 ('TRIP_CUSTODY_BANK_IN','Market Trip Custody Cash Deposit','IN','Remaining market-trip custody cash deposited into a bank / wallet account')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);

CREATE TABLE IF NOT EXISTS market_trip_settle_cash (
 entry_id BIGINT UNSIGNED NOT NULL,
 entry_kind VARCHAR(12) NOT NULL,
 amount DECIMAL(16,2) NOT NULL,
 description VARCHAR(300) DEFAULT NULL,
 reference VARCHAR(100) DEFAULT NULL,
 bank_account_id SMALLINT UNSIGNED DEFAULT NULL,
 bank_movement_id BIGINT UNSIGNED DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (entry_id),
 UNIQUE KEY uq_mtsc_movement (bank_movement_id),
 CONSTRAINT fk_mtsc_entry FOREIGN KEY (entry_id, entry_kind) REFERENCES market_trip_entries(id, entry_kind),
 CONSTRAINT fk_mtsc_bank FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(id),
 CONSTRAINT fk_mtsc_movement FOREIGN KEY (bank_movement_id) REFERENCES bank_movements(id),
 CONSTRAINT chk_mtsc_amount CHECK (amount > 0),
 CONSTRAINT chk_mtsc_shape CHECK (
  (entry_kind IN ('ST_EXPENSE','ST_CASH_OFF') AND description IS NOT NULL AND CHAR_LENGTH(TRIM(description)) > 0 AND bank_account_id IS NULL AND bank_movement_id IS NULL)
  OR (entry_kind = 'ST_CASH_BANK' AND bank_account_id IS NOT NULL AND bank_movement_id IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (64,'Market-custody settlement step 2e: custody cash expense, bank deposit and write-off, gold purchase without destination')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
