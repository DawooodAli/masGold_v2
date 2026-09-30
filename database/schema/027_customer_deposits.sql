-- عربون / استرداد عربون: a registered customer's cash deposit held by the shop, and its refund.
-- A deposit (DPxxxxxx) is real cash IN and moves the canonical customer balance toward للعميل; it may create
-- customer credit from zero (unlike CS, which only settles an existing balance toward zero). A refund
-- (DRxxxxxx) is cash OUT, limited to the customer's outstanding deposits and never crossing zero.
-- Finalized rows are immutable; a mistake is a full reversal (RV) only.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=26),
 'DO 0', 'SELECT * FROM MIGRATION_027_REFUSED_missing_026');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS customer_deposits (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 customer_id BIGINT UNSIGNED NOT NULL,
 direction VARCHAR(3) NOT NULL,
 amount DECIMAL(16,2) NOT NULL,
 request_key CHAR(36) NOT NULL,
 request_hash CHAR(64) NOT NULL,
 cash_account_id SMALLINT UNSIGNED NOT NULL,
 balance_before DECIMAL(16,2) NOT NULL,
 balance_after DECIMAL(16,2) NOT NULL,
 notes VARCHAR(1000) DEFAULT NULL,
 performed_by_user_id INT UNSIGNED NOT NULL,
 performed_by_employee_id INT UNSIGNED DEFAULT NULL,
 occurred_at DATETIME NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_cd_request (request_key),
 KEY idx_cd_customer (customer_id, occurred_at, id),
 KEY idx_cd_cash_account (cash_account_id),
 CONSTRAINT fk_cd_customer FOREIGN KEY (customer_id) REFERENCES customers(id),
 CONSTRAINT fk_cd_cash_account FOREIGN KEY (cash_account_id) REFERENCES cash_accounts(id),
 CONSTRAINT fk_cd_user FOREIGN KEY (performed_by_user_id) REFERENCES users(id),
 CONSTRAINT fk_cd_employee FOREIGN KEY (performed_by_employee_id) REFERENCES employees(id),
 CONSTRAINT chk_cd_amount CHECK (amount > 0),
 CONSTRAINT chk_cd_direction CHECK (
  (direction='IN' AND balance_after=balance_before-amount)
  OR (direction='OUT' AND balance_before<0 AND balance_after=balance_before+amount AND balance_after<=0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Standalone correction source (no fabricated invoice); full reversal only.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='transaction_corrections' AND CONSTRAINT_NAME='chk_correction_source'),
 'ALTER TABLE transaction_corrections DROP CHECK chk_correction_source','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK (
 (source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL)
 OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL)
 OR (source_type IN ('FINANCIAL_OPERATION','CUSTOMER_DEPOSIT') AND invoice_id IS NULL
     AND correction_type='FULL_REVERSAL'));

INSERT INTO cash_movement_types(code,name,expected_direction,description) VALUES
 ('CUSTOMER_DEPOSIT','Customer Deposit','IN','Cash deposit (عربون) held for a registered customer'),
 ('CUSTOMER_DEPOSIT_REFUND','Customer Deposit Refund','OUT','Refund of a customer''s outstanding deposit')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
INSERT INTO customer_transaction_types(code,name,expected_direction,description) VALUES
 ('CUSTOMER_DEPOSIT','Customer Deposit','DECREASE','Deposit held for the customer (toward للعميل)'),
 ('CUSTOMER_DEPOSIT_REFUND','Customer Deposit Refund','INCREASE','Refund of the customer''s deposit')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
INSERT INTO schema_migrations(version,name) VALUES (27,'Customer deposits (عربون) and deposit refunds')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
