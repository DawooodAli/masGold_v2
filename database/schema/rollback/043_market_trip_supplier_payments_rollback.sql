-- Refuse rollback once any custody supplier payment (link, entry or CUSTODY payment) exists, or when a later migration exists.
-- Never erase history.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM market_trip_supplier_payments)
 OR EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind = 'SUPPLIER_PAY')
 OR EXISTS(SELECT 1 FROM supplier_making_payments WHERE payment_method = 'CUSTODY')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>43),
 'SELECT * FROM ROLLBACK_043_REFUSED_custody_payment_data_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE market_trip_supplier_payments;
ALTER TABLE supplier_making_payments DROP CHECK chk_smp_method;
ALTER TABLE supplier_making_payments ADD CONSTRAINT chk_smp_method CHECK (
 (payment_method = 'CASH' AND cash_account_id IS NOT NULL AND bank_account_id IS NULL)
 OR (payment_method = 'BANK' AND bank_account_id IS NOT NULL AND cash_account_id IS NULL));
ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind;
ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT'));
DELETE FROM schema_migrations WHERE version=43;
