-- Supplier MAKING receipt (owner 2026-10-31, «لنا عند المورد»): the supplier owes the shop making (EGP) and sends the money back, in CASH
-- (into a cash account, normally the Main Safe) or through a BANK / wallet account. It is the same SMxxxxxx document as a making payment with the
-- opposite direction: ONE real money IN movement and ONE POSITIVE supplier-making movement (type RECEIPT: the supplier's debt to the shop falls).
-- The supplier GOLD account is never touched. The only correction is the existing full reversal (RV), restoring the stored figures.
-- Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=92),
 'DO 0', 'SELECT * FROM MIGRATION_093_REFUSED_missing_092');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_making_payments' AND COLUMN_NAME='direction'),
 "ALTER TABLE supplier_making_payments ADD COLUMN direction VARCHAR(7) NOT NULL DEFAULT 'PAY' AFTER payment_method", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='chk_smp_balance' AND CHECK_CLAUSE NOT LIKE '%RECEIVE%'),
 'ALTER TABLE supplier_making_payments DROP CHECK chk_smp_balance', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_making_payments' AND CONSTRAINT_NAME='chk_smp_balance'),
 "ALTER TABLE supplier_making_payments ADD CONSTRAINT chk_smp_balance CHECK (
  (direction = 'PAY' AND balance_after = balance_before - amount)
  OR (direction = 'RECEIVE' AND balance_after = balance_before + amount))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_making_payments' AND CONSTRAINT_NAME='chk_smp_direction'),
 "ALTER TABLE supplier_making_payments ADD CONSTRAINT chk_smp_direction CHECK (
  direction = 'PAY' OR (direction = 'RECEIVE' AND payment_method IN ('CASH','BANK')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO cash_movement_types(code,name,expected_direction,description) VALUES
 ('SUPPLIER_MAKING_RECEIPT','Supplier Making Receipt','IN','Cash received from a supplier against making (EGP)')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO bank_movement_types(code,name,expected_direction,description) VALUES
 ('SUPPLIER_MAKING_RECEIPT','Supplier Making Receipt','IN','Bank / wallet receipt from a supplier against making (EGP)')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO supplier_making_movement_types(code,name,description) VALUES
 ('RECEIPT','Supplier Making Receipt','Making (EGP) received from the supplier; the amount the supplier owes the shop decreases'),
 ('RECEIPT_REVERSAL','Supplier Making Receipt Reversal','A reversed making receipt; the amount the supplier owes the shop is restored')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);

INSERT INTO schema_migrations(version,name) VALUES (93,'Supplier making receipt (money received from a supplier against making)')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
