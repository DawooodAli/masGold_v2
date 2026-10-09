-- Refuse rollback once any trip, trip movement or trip correction exists, or when a later migration exists. Never erase history.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM market_trips)
 OR EXISTS(SELECT 1 FROM market_trip_entries)
 OR EXISTS(SELECT 1 FROM transaction_corrections WHERE source_type='MARKET_TRIP_ENTRY')
 OR EXISTS(SELECT 1 FROM old_gold_movements m JOIN old_gold_movement_types t ON t.id=m.movement_type_id
           WHERE t.code IN ('TRIP_CUSTODY_OUT','TRIP_CUSTODY_RETURN_IN','REVERSAL_TRIP_ISSUE_IN','REVERSAL_TRIP_RETURN_OUT'))
 OR EXISTS(SELECT 1 FROM cash_movements m JOIN cash_movement_types t ON t.id=m.movement_type_id
           WHERE t.code IN ('TRIP_CASH_ISSUE','TRIP_CASH_RETURN'))
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>37),
 'SELECT * FROM ROLLBACK_037_REFUSED_trip_data_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE transaction_corrections DROP CHECK chk_correction_source;
ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK (
 (source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL)
 OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL)
 OR (source_type IN ('FINANCIAL_OPERATION','CUSTOMER_DEPOSIT','SUPPLIER_MAKING_PAYMENT') AND invoice_id IS NULL
     AND correction_type='FULL_REVERSAL'));
DROP TABLE market_trip_entry_gold;
DROP TABLE market_trip_entry_cash;
DROP TABLE market_trip_entries;
DROP TABLE market_trip_status_events;
DROP TABLE market_trips;
DELETE FROM old_gold_movement_types WHERE code IN ('TRIP_CUSTODY_OUT','TRIP_CUSTODY_RETURN_IN','REVERSAL_TRIP_ISSUE_IN','REVERSAL_TRIP_RETURN_OUT');
DELETE FROM cash_movement_types WHERE code IN ('TRIP_CASH_ISSUE','TRIP_CASH_RETURN');
DELETE rp FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id WHERE p.code='MARKET_TRIP_MANAGE';
DELETE FROM permissions WHERE code='MARKET_TRIP_MANAGE';
DELETE FROM schema_migrations WHERE version=37;
