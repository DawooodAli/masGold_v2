-- Market-custody gold purchase / sale paid or received through a BANK / wallet account (owner 2026-10-29).
--   payment_method CUSTODY (default, every existing row): the purchase spends / the sale adds CUSTODY CASH exactly as before (054 / 053), no bank movement.
--   payment_method BANK: the gold still enters (purchase) / leaves (sale) the custody, but the custody cash is NOT touched; ONE real bank movement
--   (TRIP_GOLD_BUY_BANK OUT / TRIP_GOLD_SALE_BANK IN) posts on the chosen active account and is reversed by the generic money plan with the entry.
-- No existing row is rewritten. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=90),
 'DO 0', 'SELECT * FROM MIGRATION_091_REFUSED_missing_090');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO bank_movement_types(code,name,expected_direction,description) VALUES
 ('TRIP_GOLD_BUY_BANK','Market Trip Gold Purchase (bank)','OUT','Gold bought for the market custody and paid from a bank / wallet account'),
 ('TRIP_GOLD_SALE_BANK','Market Trip Gold Sale (bank)','IN','Custody gold sold and the money received into a bank / wallet account')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_gold_buys' AND COLUMN_NAME='payment_method'),
 "ALTER TABLE market_trip_gold_buys ADD COLUMN payment_method VARCHAR(8) NOT NULL DEFAULT 'CUSTODY', ADD COLUMN bank_account_id SMALLINT UNSIGNED DEFAULT NULL, ADD COLUMN bank_movement_id BIGINT UNSIGNED DEFAULT NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_gold_sales' AND COLUMN_NAME='payment_method'),
 "ALTER TABLE market_trip_gold_sales ADD COLUMN payment_method VARCHAR(8) NOT NULL DEFAULT 'CUSTODY', ADD COLUMN bank_account_id SMALLINT UNSIGNED DEFAULT NULL, ADD COLUMN bank_movement_id BIGINT UNSIGNED DEFAULT NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_gold_buys' AND CONSTRAINT_NAME='fk_mtgb_bank'),
 'ALTER TABLE market_trip_gold_buys ADD CONSTRAINT fk_mtgb_bank FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(id), ADD CONSTRAINT fk_mtgb_bank_movement FOREIGN KEY (bank_movement_id) REFERENCES bank_movements(id), ADD UNIQUE KEY uq_mtgb_bank_movement (bank_movement_id)', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_gold_sales' AND CONSTRAINT_NAME='fk_mtgs_bank'),
 'ALTER TABLE market_trip_gold_sales ADD CONSTRAINT fk_mtgs_bank FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(id), ADD CONSTRAINT fk_mtgs_bank_movement FOREIGN KEY (bank_movement_id) REFERENCES bank_movements(id), ADD UNIQUE KEY uq_mtgs_bank_movement (bank_movement_id)', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_gold_buys' AND CONSTRAINT_NAME='chk_mtgb_payment'),
 "ALTER TABLE market_trip_gold_buys ADD CONSTRAINT chk_mtgb_payment CHECK ((payment_method = 'CUSTODY' AND bank_account_id IS NULL AND bank_movement_id IS NULL) OR (payment_method = 'BANK' AND bank_account_id IS NOT NULL AND bank_movement_id IS NOT NULL))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_gold_sales' AND CONSTRAINT_NAME='chk_mtgs_payment'),
 "ALTER TABLE market_trip_gold_sales ADD CONSTRAINT chk_mtgs_payment CHECK ((payment_method = 'CUSTODY' AND bank_account_id IS NULL AND bank_movement_id IS NULL) OR (payment_method = 'BANK' AND bank_account_id IS NOT NULL AND bank_movement_id IS NOT NULL))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (91,'Market-custody gold purchase and sale through a bank / wallet account')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
