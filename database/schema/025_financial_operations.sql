-- Financial Accounts V1: manual deposit / withdrawal / internal transfer as immutable operations.
-- Money stays on the canonical cash_movements / bank_movements ledgers (balance = SUM, never stored).
-- An operation is a header plus its posted legs; a full reversal (RV) is its only correction.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=24),
 'DO 0', 'SELECT * FROM MIGRATION_025_REFUSED_missing_024');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS financial_operations (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 operation_type VARCHAR(30) NOT NULL,
 request_key CHAR(36) NOT NULL,
 request_hash CHAR(64) NOT NULL,
 amount DECIMAL(16,2) NOT NULL,
 from_cash_account_id SMALLINT UNSIGNED DEFAULT NULL,
 from_bank_account_id SMALLINT UNSIGNED DEFAULT NULL,
 to_cash_account_id SMALLINT UNSIGNED DEFAULT NULL,
 to_bank_account_id SMALLINT UNSIGNED DEFAULT NULL,
 reason VARCHAR(1000) DEFAULT NULL,
 performed_by_user_id INT UNSIGNED NOT NULL,
 performed_by_employee_id INT UNSIGNED DEFAULT NULL,
 occurred_at DATETIME NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_fo_request (request_key),
 KEY idx_fo_type (operation_type, occurred_at, id),
 KEY idx_fo_from_cash (from_cash_account_id),
 KEY idx_fo_from_bank (from_bank_account_id),
 KEY idx_fo_to_cash (to_cash_account_id),
 KEY idx_fo_to_bank (to_bank_account_id),
 CONSTRAINT fk_fo_from_cash FOREIGN KEY (from_cash_account_id) REFERENCES cash_accounts(id),
 CONSTRAINT fk_fo_from_bank FOREIGN KEY (from_bank_account_id) REFERENCES bank_accounts(id),
 CONSTRAINT fk_fo_to_cash FOREIGN KEY (to_cash_account_id) REFERENCES cash_accounts(id),
 CONSTRAINT fk_fo_to_bank FOREIGN KEY (to_bank_account_id) REFERENCES bank_accounts(id),
 CONSTRAINT fk_fo_user FOREIGN KEY (performed_by_user_id) REFERENCES users(id),
 CONSTRAINT fk_fo_employee FOREIGN KEY (performed_by_employee_id) REFERENCES employees(id),
 CONSTRAINT chk_fo_amount CHECK (amount > 0),
 CONSTRAINT chk_fo_one_ledger CHECK (
  (from_cash_account_id IS NULL OR from_bank_account_id IS NULL)
  AND (to_cash_account_id IS NULL OR to_bank_account_id IS NULL)),
 CONSTRAINT chk_fo_shape CHECK (
  (operation_type='MANUAL_DEPOSIT' AND from_cash_account_id IS NULL AND from_bank_account_id IS NULL
    AND (to_cash_account_id IS NOT NULL OR to_bank_account_id IS NOT NULL)
    AND reason IS NOT NULL AND CHAR_LENGTH(TRIM(reason))>0)
  OR (operation_type='MANUAL_WITHDRAWAL' AND to_cash_account_id IS NULL AND to_bank_account_id IS NULL
    AND (from_cash_account_id IS NOT NULL OR from_bank_account_id IS NOT NULL)
    AND reason IS NOT NULL AND CHAR_LENGTH(TRIM(reason))>0)
  OR (operation_type='ACCOUNT_TRANSFER'
    AND (from_cash_account_id IS NOT NULL OR from_bank_account_id IS NOT NULL)
    AND (to_cash_account_id IS NOT NULL OR to_bank_account_id IS NOT NULL)
    AND NOT (COALESCE(from_cash_account_id,0)=COALESCE(to_cash_account_id,0)
             AND COALESCE(from_bank_account_id,0)=COALESCE(to_bank_account_id,0))))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- One OUT and/or one IN leg per operation, each bound to exactly one ledger movement.
CREATE TABLE IF NOT EXISTS financial_operation_legs (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 operation_id BIGINT UNSIGNED NOT NULL,
 leg_role VARCHAR(3) NOT NULL,
 amount DECIMAL(16,2) NOT NULL,
 cash_movement_id BIGINT UNSIGNED DEFAULT NULL,
 bank_movement_id BIGINT UNSIGNED DEFAULT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_fol_role (operation_id, leg_role),
 UNIQUE KEY uq_fol_cash_movement (cash_movement_id),
 UNIQUE KEY uq_fol_bank_movement (bank_movement_id),
 CONSTRAINT fk_fol_operation FOREIGN KEY (operation_id) REFERENCES financial_operations(id),
 CONSTRAINT fk_fol_cash_movement FOREIGN KEY (cash_movement_id) REFERENCES cash_movements(id),
 CONSTRAINT fk_fol_bank_movement FOREIGN KEY (bank_movement_id) REFERENCES bank_movements(id),
 CONSTRAINT chk_fol_role CHECK ((leg_role='IN' AND amount>0) OR (leg_role='OUT' AND amount<0)),
 CONSTRAINT chk_fol_movement CHECK ((cash_movement_id IS NULL) <> (bank_movement_id IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Standalone correction source (no fabricated invoice); full reversal only, never FC.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='transaction_corrections' AND CONSTRAINT_NAME='chk_correction_source'),
 'ALTER TABLE transaction_corrections DROP CHECK chk_correction_source','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK (
 (source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL)
 OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL)
 OR (source_type='FINANCIAL_OPERATION' AND invoice_id IS NULL AND correction_type='FULL_REVERSAL'));

INSERT INTO cash_movement_types(code,name,expected_direction,description) VALUES
 ('MANUAL_DEPOSIT','Manual Deposit','IN','External money deposited by an authorized operator'),
 ('MANUAL_WITHDRAWAL','Manual Withdrawal','OUT','External money withdrawn by an authorized operator'),
 ('ACCOUNT_TRANSFER_IN','Account Transfer In','IN','Money received from another tracked shop account'),
 ('ACCOUNT_TRANSFER_OUT','Account Transfer Out','OUT','Money sent to another tracked shop account')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
INSERT INTO bank_movement_types(code,name,expected_direction,description) VALUES
 ('MANUAL_DEPOSIT','Manual Deposit','IN','External money deposited by an authorized operator'),
 ('MANUAL_WITHDRAWAL','Manual Withdrawal','OUT','External money withdrawn by an authorized operator'),
 ('ACCOUNT_TRANSFER_IN','Account Transfer In','IN','Money received from another tracked shop account'),
 ('ACCOUNT_TRANSFER_OUT','Account Transfer Out','OUT','Money sent to another tracked shop account')
 ON DUPLICATE KEY UPDATE name=VALUES(name);

INSERT INTO permissions(code,name,description)
 VALUES ('FINANCIAL_ACCOUNT_OPERATE','Operate Financial Accounts','Manual deposit, manual withdrawal and internal account transfer')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code='FINANCIAL_ACCOUNT_OPERATE'
 WHERE r.code IN ('OWNER','MANAGER','CASHIER') AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);
INSERT INTO schema_migrations(version,name) VALUES (25,'Financial Accounts V1: manual operations and transfers')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
