-- Refuse rollback once any supplier gold settlement (document, leg or movement) exists, or when a later migration exists. Never erase history.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM supplier_gold_settlements)
 OR EXISTS(SELECT 1 FROM supplier_gold_settlement_legs)
 OR EXISTS(SELECT 1 FROM supplier_gold_movements m JOIN supplier_gold_movement_types t ON t.id = m.movement_type_id
           WHERE t.code IN ('SETTLEMENT','SETTLEMENT_REVERSAL'))
 OR EXISTS(SELECT 1 FROM old_gold_movements m JOIN old_gold_movement_types t ON t.id = m.movement_type_id
           WHERE t.code IN ('SUPPLIER_GOLD_SETTLEMENT_OUT','REVERSAL_SUPPLIER_GOLD_SETTLEMENT_IN'))
 OR EXISTS(SELECT 1 FROM transaction_corrections WHERE source_type = 'SUPPLIER_GOLD_SETTLEMENT')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>45),
 'SELECT * FROM ROLLBACK_045_REFUSED_settlement_data_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE supplier_gold_settlement_legs;
DROP TABLE supplier_gold_settlements;
ALTER TABLE transaction_corrections DROP CHECK chk_correction_source;
ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK (
 (source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL)
 OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL)
 OR (source_type IN ('FINANCIAL_OPERATION','CUSTOMER_DEPOSIT','SUPPLIER_MAKING_PAYMENT','MARKET_TRIP_ENTRY')
     AND invoice_id IS NULL AND correction_type='FULL_REVERSAL'));
DELETE rp FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
 WHERE p.code IN ('SUPPLIER_GOLD_SETTLE','SUPPLIER_GOLD_SETTLE_REVERSE');
DELETE FROM permissions WHERE code IN ('SUPPLIER_GOLD_SETTLE','SUPPLIER_GOLD_SETTLE_REVERSE');
DELETE FROM supplier_gold_movement_types WHERE code IN ('SETTLEMENT','SETTLEMENT_REVERSAL');
DELETE FROM old_gold_movement_types WHERE code IN ('SUPPLIER_GOLD_SETTLEMENT_OUT','REVERSAL_SUPPLIER_GOLD_SETTLEMENT_IN');
DELETE FROM schema_migrations WHERE version=45;
