-- Customer Account V2: immutable settlements; canonical customer ledger remains authoritative.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=23),
 'DO 0', 'SELECT * FROM MIGRATION_024_REFUSED_missing_023');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS customer_account_settlements (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 customer_id BIGINT UNSIGNED NOT NULL,
 request_key CHAR(36) NOT NULL,
 request_hash CHAR(64) NOT NULL,
 direction VARCHAR(3) NOT NULL,
 amount DECIMAL(16,2) NOT NULL,
 balance_before DECIMAL(16,2) NOT NULL,
 balance_after DECIMAL(16,2) NOT NULL,
 notes VARCHAR(1000) DEFAULT NULL,
 performed_by_user_id INT UNSIGNED NOT NULL,
 performed_by_employee_id INT UNSIGNED DEFAULT NULL,
 occurred_at DATETIME NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_cs_request (request_key),
 KEY idx_cs_customer (customer_id, occurred_at, id),
 CONSTRAINT fk_cs_customer FOREIGN KEY (customer_id) REFERENCES customers(id),
 CONSTRAINT fk_cs_user FOREIGN KEY (performed_by_user_id) REFERENCES users(id),
 CONSTRAINT fk_cs_employee FOREIGN KEY (performed_by_employee_id) REFERENCES employees(id),
 CONSTRAINT chk_cs_amount CHECK (amount > 0 AND amount <= ABS(balance_before)),
 CONSTRAINT chk_cs_direction CHECK (
  (direction='IN' AND balance_before>0 AND balance_after=balance_before-amount AND balance_after>=0)
  OR (direction='OUT' AND balance_before<0 AND balance_after=balance_before+amount AND balance_after<=0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS customer_account_settlement_payments (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 settlement_id BIGINT UNSIGNED NOT NULL,
 payment_method_code VARCHAR(40) NOT NULL,
 bank_account_id SMALLINT UNSIGNED DEFAULT NULL,
 amount DECIMAL(16,2) NOT NULL,
 cash_movement_id BIGINT UNSIGNED DEFAULT NULL,
 bank_movement_id BIGINT UNSIGNED DEFAULT NULL,
 PRIMARY KEY (id),
 KEY idx_cs_payment (settlement_id),
 CONSTRAINT fk_csp_header FOREIGN KEY (settlement_id) REFERENCES customer_account_settlements(id),
 CONSTRAINT fk_csp_bank FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(id),
 CONSTRAINT fk_csp_cash_movement FOREIGN KEY (cash_movement_id) REFERENCES cash_movements(id),
 CONSTRAINT fk_csp_bank_movement FOREIGN KEY (bank_movement_id) REFERENCES bank_movements(id),
 CONSTRAINT chk_csp_amount CHECK (amount>0),
 CONSTRAINT chk_csp_route CHECK (
  (payment_method_code='PHYSICAL_CASH' AND bank_account_id IS NULL AND cash_movement_id IS NOT NULL AND bank_movement_id IS NULL)
  OR (payment_method_code IN ('MOBILE_WALLET','INSTAPAY','BANK_TRANSFER') AND bank_account_id IS NOT NULL AND cash_movement_id IS NULL AND bank_movement_id IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Keep invoice-backed corrections anchored, while CS has no fabricated invoice.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='transaction_corrections' AND CONSTRAINT_NAME='chk_correction_source'),
 'ALTER TABLE transaction_corrections DROP CHECK chk_correction_source','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE transaction_corrections MODIFY COLUMN source_type VARCHAR(40) NOT NULL,
 MODIFY COLUMN invoice_id BIGINT UNSIGNED NULL,
 MODIFY COLUMN reversal_key VARCHAR(80) GENERATED ALWAYS AS
  (CASE WHEN correction_type='FULL_REVERSAL' THEN CONCAT(source_type, ':', source_id) ELSE NULL END) STORED,
 MODIFY COLUMN settlement_key VARCHAR(100) GENERATED ALWAYS AS
  (CASE WHEN correction_type='SETTLEMENT_CORRECTION' THEN CONCAT(source_type, ':', source_id, ':', chain_sequence) ELSE NULL END) STORED;
ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK (
 (source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL)
 OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL));

INSERT INTO cash_movement_types(code,name,expected_direction,description)
 VALUES ('CUSTOMER_ACCOUNT_SETTLEMENT','Customer Account Settlement','EITHER','Real money against existing customer balance')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
INSERT INTO bank_movement_types(code,name,expected_direction,description)
 VALUES ('CUSTOMER_ACCOUNT_SETTLEMENT','Customer Account Settlement','EITHER','Real money against existing customer balance')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
INSERT INTO customer_transaction_types(code,name,expected_direction,description)
 VALUES ('CUSTOMER_ACCOUNT_SETTLEMENT','Customer Account Settlement','EITHER','Opposite signed real-money settlement of existing balance')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
INSERT INTO permissions(code,name,description)
 VALUES ('CUSTOMER_ACCOUNT_SETTLE','Settle Customer Account','Receive/pay an existing customer balance')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code='CUSTOMER_ACCOUNT_SETTLE'
 WHERE r.code IN ('OWNER','MANAGER','CASHIER') AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);
INSERT INTO schema_migrations(version,name) VALUES (24,'Customer Account V2 and standalone CS corrections')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
