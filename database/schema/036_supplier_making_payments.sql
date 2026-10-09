-- Purchasing & Suppliers Phase 2A: supplier MAKING payments (EGP) and their reversal.
--
-- A making payment (SMxxxxxx) is an immutable document: one real money OUT movement (cash from MAIN_SAFE, or a bank /
-- wallet account) and one NEGATIVE supplier-making movement, posted atomically. It is NOT allocated to an invoice: it
-- moves the supplier's net making account (delta < 0 = less owed to the supplier; the balance may cross zero into credit).
-- The supplier GOLD account is never touched. Money stays on the canonical cash_movements / bank_movements ledgers.
--
-- The only correction is a full reversal (RV) through transaction_corrections (standalone source, no fabricated invoice):
-- the opposite money movement on the ORIGINAL account plus a positive supplier-making movement. Nothing is rewritten.
--
-- Every step is guarded, so a re-run changes nothing.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=35),
 'DO 0', 'SELECT * FROM MIGRATION_036_REFUSED_missing_035');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS supplier_making_payments (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 supplier_id INT UNSIGNED NOT NULL,
 payment_method VARCHAR(10) NOT NULL,
 cash_account_id SMALLINT UNSIGNED DEFAULT NULL,
 bank_account_id SMALLINT UNSIGNED DEFAULT NULL,
 amount DECIMAL(16,2) NOT NULL,
 payment_date DATE NOT NULL,
 reference VARCHAR(100) DEFAULT NULL,
 notes VARCHAR(1000) DEFAULT NULL,
 balance_before DECIMAL(16,2) NOT NULL,
 balance_after DECIMAL(16,2) NOT NULL,
 request_key CHAR(36) NOT NULL,
 request_hash CHAR(64) NOT NULL,
 performed_by_user_id INT UNSIGNED NOT NULL,
 performed_by_employee_id INT UNSIGNED DEFAULT NULL,
 occurred_at DATETIME NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_smp_request (request_key),
 KEY idx_smp_supplier (supplier_id, payment_date, id),
 KEY idx_smp_cash_account (cash_account_id),
 KEY idx_smp_bank_account (bank_account_id),
 CONSTRAINT fk_smp_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
 CONSTRAINT fk_smp_cash_account FOREIGN KEY (cash_account_id) REFERENCES cash_accounts(id),
 CONSTRAINT fk_smp_bank_account FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(id),
 CONSTRAINT fk_smp_user FOREIGN KEY (performed_by_user_id) REFERENCES users(id),
 CONSTRAINT fk_smp_employee FOREIGN KEY (performed_by_employee_id) REFERENCES employees(id),
 CONSTRAINT chk_smp_amount CHECK (amount > 0),
 CONSTRAINT chk_smp_method CHECK (
  (payment_method = 'CASH' AND cash_account_id IS NOT NULL AND bank_account_id IS NULL)
  OR (payment_method = 'BANK' AND bank_account_id IS NOT NULL AND cash_account_id IS NULL)),
 CONSTRAINT chk_smp_balance CHECK (balance_after = balance_before - amount)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Insert-only links from a payment to the two ledger movements it posted (one real money movement, one making movement).
CREATE TABLE IF NOT EXISTS supplier_making_payment_legs (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 payment_id BIGINT UNSIGNED NOT NULL,
 leg_kind VARCHAR(10) NOT NULL,
 cash_movement_id BIGINT UNSIGNED DEFAULT NULL,
 bank_movement_id BIGINT UNSIGNED DEFAULT NULL,
 making_movement_id BIGINT UNSIGNED DEFAULT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_smpl_kind (payment_id, leg_kind),
 UNIQUE KEY uq_smpl_cash_movement (cash_movement_id),
 UNIQUE KEY uq_smpl_bank_movement (bank_movement_id),
 UNIQUE KEY uq_smpl_making_movement (making_movement_id),
 CONSTRAINT fk_smpl_payment FOREIGN KEY (payment_id) REFERENCES supplier_making_payments(id),
 CONSTRAINT fk_smpl_cash_movement FOREIGN KEY (cash_movement_id) REFERENCES cash_movements(id),
 CONSTRAINT fk_smpl_bank_movement FOREIGN KEY (bank_movement_id) REFERENCES bank_movements(id),
 CONSTRAINT fk_smpl_making_movement FOREIGN KEY (making_movement_id) REFERENCES supplier_making_movements(id),
 CONSTRAINT chk_smpl_leg CHECK (
  (leg_kind = 'MONEY' AND making_movement_id IS NULL AND ((cash_movement_id IS NULL) <> (bank_movement_id IS NULL)))
  OR (leg_kind = 'MAKING' AND making_movement_id IS NOT NULL AND cash_movement_id IS NULL AND bank_movement_id IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Standalone correction source (no fabricated invoice); full reversal only. The whole current rule is restated.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='transaction_corrections' AND CONSTRAINT_NAME='chk_correction_source'),
 'ALTER TABLE transaction_corrections DROP CHECK chk_correction_source','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK (
 (source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL)
 OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL)
 OR (source_type IN ('FINANCIAL_OPERATION','CUSTOMER_DEPOSIT','SUPPLIER_MAKING_PAYMENT') AND invoice_id IS NULL
     AND correction_type='FULL_REVERSAL'));

INSERT INTO cash_movement_types(code,name,expected_direction,description) VALUES
 ('SUPPLIER_MAKING_PAYMENT','Supplier Making Payment','OUT','Cash paid to a supplier against making (EGP)')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO bank_movement_types(code,name,expected_direction,description) VALUES
 ('SUPPLIER_MAKING_PAYMENT','Supplier Making Payment','OUT','Bank / wallet payment to a supplier against making (EGP)')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO supplier_making_movement_types(code,name,description) VALUES
 ('PAYMENT','Supplier Making Payment','Making (EGP) paid to the supplier; the amount owed to the supplier decreases'),
 ('PAYMENT_REVERSAL','Supplier Making Payment Reversal','A reversed making payment; the amount owed to the supplier is restored')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);

INSERT INTO permissions(code,name,description) VALUES
 ('SUPPLIER_MAKING_PAY','Pay Supplier Making','Record a cash or bank payment against a supplier making account'),
 ('SUPPLIER_MAKING_PAY_REVERSE','Reverse Supplier Making Payment','Reverse a posted supplier making payment (owner only)')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code='SUPPLIER_MAKING_PAY'
 WHERE r.code IN ('OWNER','MANAGER') AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code='SUPPLIER_MAKING_PAY_REVERSE'
 WHERE r.code = 'OWNER' AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);

INSERT INTO schema_migrations(version,name) VALUES (36,'Supplier making payments (cash / bank) and their reversal')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
