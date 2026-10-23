-- Temporary cash advances / custody (سلف الموظفين وعهد العملاء المؤقتة). Prepared; NOT applied to any database by the task that wrote it.
--   cash_advances: one row per advance (code SA + 6 digits) tied to ONE employee or ONE customer and to the manual cash withdrawal (financial_operations) that paid it out.
--   cash_advance_repayments: one row per repayment, tied to the manual deposit (financial_operations) that took the money back. Partial repayments are allowed.
--   The money itself stays on the canonical cash / bank ledgers; the outstanding amount is derived (amount - effective repayments), never stored. A reversed withdrawal / deposit (RV) stops counting.
--   Two system categories: CUSTOMER_CUSTODY (OUT) and ADVANCE_REPAY (IN). The customer ledger (customer_transactions) is never touched.
-- Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=73),
 'DO 0', 'SELECT * FROM MIGRATION_074_REFUSED_missing_073');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO cash_categories(code, name, direction, is_active, is_system, sort_order) VALUES
 ('CUSTOMER_CUSTODY', 'عهدة مؤقتة لعميل', 'OUT', 1, 1, 85),
 ('ADVANCE_REPAY', 'سداد سلفة / عهدة', 'IN', 1, 1, 95)
 ON DUPLICATE KEY UPDATE code = code;

CREATE TABLE IF NOT EXISTS cash_advances (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 kind VARCHAR(10) NOT NULL,
 employee_id INT UNSIGNED DEFAULT NULL,
 customer_id BIGINT UNSIGNED DEFAULT NULL,
 amount DECIMAL(16,2) NOT NULL,
 operation_id BIGINT UNSIGNED NOT NULL,
 note VARCHAR(500) DEFAULT NULL,
 created_by_user_id INT UNSIGNED NOT NULL,
 created_at DATETIME NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_cash_advance_operation (operation_id),
 KEY idx_cash_advance_employee (employee_id),
 KEY idx_cash_advance_customer (customer_id),
 CONSTRAINT fk_cash_advance_employee FOREIGN KEY (employee_id) REFERENCES employees(id),
 CONSTRAINT fk_cash_advance_customer FOREIGN KEY (customer_id) REFERENCES customers(id),
 CONSTRAINT fk_cash_advance_operation FOREIGN KEY (operation_id) REFERENCES financial_operations(id),
 CONSTRAINT fk_cash_advance_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_cash_advance_amount CHECK (amount > 0),
 CONSTRAINT chk_cash_advance_party CHECK (
  (kind = 'EMPLOYEE' AND employee_id IS NOT NULL AND customer_id IS NULL)
  OR (kind = 'CUSTOMER' AND customer_id IS NOT NULL AND employee_id IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS cash_advance_repayments (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 advance_id BIGINT UNSIGNED NOT NULL,
 amount DECIMAL(16,2) NOT NULL,
 operation_id BIGINT UNSIGNED NOT NULL,
 created_by_user_id INT UNSIGNED NOT NULL,
 created_at DATETIME NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_cash_advance_repay_operation (operation_id),
 KEY idx_cash_advance_repay_advance (advance_id),
 CONSTRAINT fk_cash_advance_repay_advance FOREIGN KEY (advance_id) REFERENCES cash_advances(id),
 CONSTRAINT fk_cash_advance_repay_operation FOREIGN KEY (operation_id) REFERENCES financial_operations(id),
 CONSTRAINT fk_cash_advance_repay_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_cash_advance_repay_amount CHECK (amount > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (74,'Temporary cash advances for employees and customers')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
