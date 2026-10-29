-- Rollback of 091. Refuses when a later migration exists or any purchase / sale was paid or received through a bank account.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 91)
   OR EXISTS(SELECT 1 FROM market_trip_gold_buys WHERE payment_method = 'BANK')
   OR EXISTS(SELECT 1 FROM market_trip_gold_sales WHERE payment_method = 'BANK'),
 'SELECT * FROM ROLLBACK_091_REFUSED_later_migration_or_bank_trade_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE market_trip_gold_buys DROP CHECK chk_mtgb_payment, DROP FOREIGN KEY fk_mtgb_bank_movement, DROP FOREIGN KEY fk_mtgb_bank,
 DROP INDEX uq_mtgb_bank_movement, DROP COLUMN bank_movement_id, DROP COLUMN bank_account_id, DROP COLUMN payment_method;
ALTER TABLE market_trip_gold_sales DROP CHECK chk_mtgs_payment, DROP FOREIGN KEY fk_mtgs_bank_movement, DROP FOREIGN KEY fk_mtgs_bank,
 DROP INDEX uq_mtgs_bank_movement, DROP COLUMN bank_movement_id, DROP COLUMN bank_account_id, DROP COLUMN payment_method;
DELETE FROM bank_movement_types WHERE code IN ('TRIP_GOLD_BUY_BANK','TRIP_GOLD_SALE_BANK');
DELETE FROM schema_migrations WHERE version = 91;
