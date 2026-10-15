-- Full reversal of a posted supplier purchase invoice (V1). The invoice, its groups and its original ledger movements are never touched: the correction
-- (source SUPPLIER_PURCHASE_INVOICE, full reversal only, OWNER only) posts the exact opposite GOLD and MAKING movements under new movement types, and the
-- reversed state is derived from the correction record. No new table and no column change. A posted invoice with any intaken piece is refused by the service.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=51),
 'DO 0', 'SELECT * FROM MIGRATION_052_REFUSED_missing_051');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.TABLE_SCHEMA=DATABASE() AND tc.TABLE_NAME='transaction_corrections' AND tc.CONSTRAINT_NAME='chk_correction_source' AND cc.CHECK_CLAUSE NOT LIKE '%SUPPLIER_PURCHASE_INVOICE%'), "ALTER TABLE transaction_corrections DROP CHECK chk_correction_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='transaction_corrections' AND CONSTRAINT_NAME='chk_correction_source'), "ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK ((source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL) OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL) OR (source_type IN ('FINANCIAL_OPERATION','CUSTOMER_DEPOSIT','SUPPLIER_MAKING_PAYMENT','MARKET_TRIP_ENTRY','SUPPLIER_GOLD_SETTLEMENT','HOLDING_RECEIPT','SUPPLIER_PURCHASE_INVOICE') AND invoice_id IS NULL AND correction_type='FULL_REVERSAL'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO supplier_gold_movement_types(code,name,description) VALUES
 ('PURCHASE_INVOICE_REVERSAL','Supplier Purchase Invoice Reversal','A reversed supplier purchase invoice; the gold owed to the supplier is restored to its value before the invoice')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO supplier_making_movement_types(code,name,description) VALUES
 ('PURCHASE_INVOICE_REVERSAL','Supplier Purchase Invoice Making Reversal','A reversed supplier purchase invoice; the making owed to the supplier is restored to its value before the invoice')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);

INSERT INTO permissions(code,name,description) VALUES
 ('SUPPLIER_INVOICE_REVERSE','Reverse Supplier Purchase Invoice','Reverse a posted supplier purchase invoice that has no intaken pieces (owner only)')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code='SUPPLIER_INVOICE_REVERSE'
 WHERE r.code='OWNER' AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);

INSERT INTO schema_migrations(version,name) VALUES (52,'Full reversal of a posted supplier purchase invoice: correction source, reversal movement types and owner right')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
