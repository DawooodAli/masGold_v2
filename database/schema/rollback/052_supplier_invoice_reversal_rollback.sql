-- No supplier invoice reversal (correction row or reversal movement) may be erased, and no later migration may exist.
SET @masgold_sql=IF(EXISTS(SELECT 1 FROM transaction_corrections WHERE source_type='SUPPLIER_PURCHASE_INVOICE')
 OR EXISTS(SELECT 1 FROM supplier_gold_movements m JOIN supplier_gold_movement_types t ON t.id=m.movement_type_id WHERE t.code='PURCHASE_INVOICE_REVERSAL')
 OR EXISTS(SELECT 1 FROM supplier_making_movements m JOIN supplier_making_movement_types t ON t.id=m.movement_type_id WHERE t.code='PURCHASE_INVOICE_REVERSAL')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>52),
 'SELECT * FROM ROLLBACK_052_REFUSED_history_or_later_migration','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE transaction_corrections DROP CHECK chk_correction_source;
ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK ((source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL) OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL) OR (source_type IN ('FINANCIAL_OPERATION','CUSTOMER_DEPOSIT','SUPPLIER_MAKING_PAYMENT','MARKET_TRIP_ENTRY','SUPPLIER_GOLD_SETTLEMENT','HOLDING_RECEIPT') AND invoice_id IS NULL AND correction_type='FULL_REVERSAL'));
DELETE rp FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id WHERE p.code='SUPPLIER_INVOICE_REVERSE';
DELETE FROM permissions WHERE code='SUPPLIER_INVOICE_REVERSE';
DELETE FROM supplier_gold_movement_types WHERE code='PURCHASE_INVOICE_REVERSAL';
DELETE FROM supplier_making_movement_types WHERE code='PURCHASE_INVOICE_REVERSAL';
DELETE FROM schema_migrations WHERE version=52;
