-- Refuse rollback once any making payment or reversal exists, or when a later migration exists. Never erase money history.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM supplier_making_payments)
 OR EXISTS(SELECT 1 FROM supplier_making_payment_legs)
 OR EXISTS(SELECT 1 FROM transaction_corrections WHERE source_type='SUPPLIER_MAKING_PAYMENT')
 OR EXISTS(SELECT 1 FROM supplier_making_movements m JOIN supplier_making_movement_types t ON t.id=m.movement_type_id
           WHERE t.code IN ('PAYMENT','PAYMENT_REVERSAL'))
 OR EXISTS(SELECT 1 FROM cash_movements m JOIN cash_movement_types t ON t.id=m.movement_type_id WHERE t.code='SUPPLIER_MAKING_PAYMENT')
 OR EXISTS(SELECT 1 FROM bank_movements m JOIN bank_movement_types t ON t.id=m.movement_type_id WHERE t.code='SUPPLIER_MAKING_PAYMENT')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>36),
 'SELECT * FROM ROLLBACK_036_REFUSED_supplier_payments_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE transaction_corrections DROP CHECK chk_correction_source;
ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK (
 (source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL)
 OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL)
 OR (source_type IN ('FINANCIAL_OPERATION','CUSTOMER_DEPOSIT') AND invoice_id IS NULL
     AND correction_type='FULL_REVERSAL'));
DROP TABLE supplier_making_payment_legs;
DROP TABLE supplier_making_payments;
DELETE FROM cash_movement_types WHERE code='SUPPLIER_MAKING_PAYMENT';
DELETE FROM bank_movement_types WHERE code='SUPPLIER_MAKING_PAYMENT';
DELETE FROM supplier_making_movement_types WHERE code IN ('PAYMENT','PAYMENT_REVERSAL');
DELETE rp FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id
 WHERE p.code IN ('SUPPLIER_MAKING_PAY','SUPPLIER_MAKING_PAY_REVERSE');
DELETE FROM permissions WHERE code IN ('SUPPLIER_MAKING_PAY','SUPPLIER_MAKING_PAY_REVERSE');
DELETE FROM schema_migrations WHERE version=36;
