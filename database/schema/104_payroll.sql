-- Payroll «المرتبات» (owner 2026-11-08), a section of its own under مراجعات المدير, outside the expenses. Per employee: a monthly salary (history of rates by effective month), bonuses and
-- penalties of a month (each with a reason; a bonus / penalty not yet paid can be voided), and one monthly slip. Paying a slip posts ONE real cash / bank withdrawal (category SALARY) of the
-- NET = salary + bonuses - penalties - advances deducted, ties the slip to that operation and, for the advances deducted, inserts repayment rows tied to the SAME operation (so reversing the
-- salary operation brings the advance back). The slip is never rewritten: a reversal of its operation voids it (derived, like the advances). Owner-only right PAYROLL_MANAGE.
-- cash_advance_repayments: one operation may now repay several advances (unique on advance + operation instead of operation alone). Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=103),
 'DO 0', 'SELECT * FROM MIGRATION_104_REFUSED_missing_103');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS employee_salary_rates (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 employee_id INT UNSIGNED NOT NULL,
 monthly_amount DECIMAL(16,2) NOT NULL,
 effective_month DATE NOT NULL,
 created_by_user_id INT UNSIGNED NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_salary_rate_month (employee_id, effective_month),
 CONSTRAINT fk_salary_rate_employee FOREIGN KEY (employee_id) REFERENCES employees(id),
 CONSTRAINT fk_salary_rate_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_salary_rate_amount CHECK (monthly_amount >= 0),
 CONSTRAINT chk_salary_rate_month CHECK (DAYOFMONTH(effective_month) = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS payroll_slips (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 employee_id INT UNSIGNED NOT NULL,
 period_month DATE NOT NULL,
 base_salary DECIMAL(16,2) NOT NULL,
 bonuses DECIMAL(16,2) NOT NULL DEFAULT 0,
 penalties DECIMAL(16,2) NOT NULL DEFAULT 0,
 advances_deducted DECIMAL(16,2) NOT NULL DEFAULT 0,
 net_amount DECIMAL(16,2) NOT NULL,
 operation_id BIGINT UNSIGNED NOT NULL,
 note VARCHAR(500) DEFAULT NULL,
 created_by_user_id INT UNSIGNED NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_payroll_slip_operation (operation_id),
 KEY idx_payroll_slip_employee_month (employee_id, period_month),
 CONSTRAINT fk_payroll_slip_employee FOREIGN KEY (employee_id) REFERENCES employees(id),
 CONSTRAINT fk_payroll_slip_operation FOREIGN KEY (operation_id) REFERENCES financial_operations(id),
 CONSTRAINT fk_payroll_slip_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_payroll_slip_month CHECK (DAYOFMONTH(period_month) = 1),
 CONSTRAINT chk_payroll_slip_amounts CHECK (base_salary >= 0 AND bonuses >= 0 AND penalties >= 0 AND advances_deducted >= 0 AND net_amount > 0
   AND net_amount = base_salary + bonuses - penalties - advances_deducted)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS payroll_adjustments (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 employee_id INT UNSIGNED NOT NULL,
 period_month DATE NOT NULL,
 kind VARCHAR(10) NOT NULL,
 amount DECIMAL(16,2) NOT NULL,
 reason VARCHAR(300) NOT NULL,
 slip_id BIGINT UNSIGNED DEFAULT NULL,
 created_by_user_id INT UNSIGNED NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 voided_at DATETIME DEFAULT NULL,
 voided_by_user_id INT UNSIGNED DEFAULT NULL,
 void_reason VARCHAR(300) DEFAULT NULL,
 PRIMARY KEY (id),
 KEY idx_payroll_adj_employee_month (employee_id, period_month),
 KEY idx_payroll_adj_slip (slip_id),
 CONSTRAINT fk_payroll_adj_employee FOREIGN KEY (employee_id) REFERENCES employees(id),
 CONSTRAINT fk_payroll_adj_slip FOREIGN KEY (slip_id) REFERENCES payroll_slips(id),
 CONSTRAINT fk_payroll_adj_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT fk_payroll_adj_void_user FOREIGN KEY (voided_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_payroll_adj_kind CHECK (kind IN ('BONUS', 'PENALTY')),
 CONSTRAINT chk_payroll_adj_amount CHECK (amount > 0),
 CONSTRAINT chk_payroll_adj_month CHECK (DAYOFMONTH(period_month) = 1),
 CONSTRAINT chk_payroll_adj_void CHECK ((voided_at IS NULL AND voided_by_user_id IS NULL AND void_reason IS NULL) OR (voided_at IS NOT NULL AND voided_by_user_id IS NOT NULL AND void_reason IS NOT NULL AND slip_id IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- one operation may repay several advances (the payroll deduction): keep an index for the foreign key first, then replace the unique key
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='cash_advance_repayments' AND INDEX_NAME='idx_cash_advance_repay_operation'),
 "ALTER TABLE cash_advance_repayments ADD KEY idx_cash_advance_repay_operation (operation_id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='cash_advance_repayments' AND INDEX_NAME='uq_cash_advance_repay_operation'),
 "ALTER TABLE cash_advance_repayments DROP INDEX uq_cash_advance_repay_operation", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='cash_advance_repayments' AND INDEX_NAME='uq_cash_advance_repay_pair'),
 "ALTER TABLE cash_advance_repayments ADD UNIQUE KEY uq_cash_advance_repay_pair (advance_id, operation_id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO permissions(code,name,description) VALUES
 ('PAYROLL_MANAGE','Payroll','Monthly salaries, bonuses, penalties and paying the net salary from a cash / bank account (owner only)')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code='PAYROLL_MANAGE'
 WHERE r.code='OWNER' AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);

INSERT INTO schema_migrations(version,name) VALUES (104,'Payroll: salary rates, adjustments, slips, owner right, repayments per advance')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
