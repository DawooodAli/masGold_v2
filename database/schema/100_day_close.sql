-- Day close «تسليم وإقفال اليوم» (owner 2026-11-08): at the end of the day the employee counts the physical cash, the program shows the expected cash balance (the sum of the cash
-- accounts' ledger), and the saved minutes keep both figures, the difference with its mandatory explanation, the number of unfinished (DRAFT) invoices and a snapshot of every cash /
-- bank balance. Insert-only: a close is never edited or deleted (a second close of the same day is a new row). It moves no money and corrects nothing; it is a record.
-- Right DAY_CLOSE (OWNER, MANAGER, CASHIER). Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=99),
 'DO 0', 'SELECT * FROM MIGRATION_100_REFUSED_missing_099');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS day_closes (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 business_date DATE NOT NULL,
 expected_cash DECIMAL(14,2) NOT NULL,
 counted_cash DECIMAL(14,2) NOT NULL,
 difference DECIMAL(14,2) NOT NULL,
 difference_reason VARCHAR(500) DEFAULT NULL,
 draft_invoices INT UNSIGNED NOT NULL DEFAULT 0,
 balances_json JSON NOT NULL,
 note VARCHAR(500) DEFAULT NULL,
 request_key VARCHAR(64) NOT NULL,
 closed_by_user_id INT UNSIGNED NOT NULL,
 closed_at DATETIME NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_day_close_request (request_key),
 KEY idx_day_close_date (business_date, id),
 CONSTRAINT fk_day_close_user FOREIGN KEY (closed_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_day_close_counted CHECK (counted_cash >= 0),
 CONSTRAINT chk_day_close_difference CHECK (difference = counted_cash - expected_cash),
 CONSTRAINT chk_day_close_reason CHECK (difference = 0 OR (difference_reason IS NOT NULL AND CHAR_LENGTH(TRIM(difference_reason)) > 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO permissions(code,name,description) VALUES
 ('DAY_CLOSE','Close the day','Count the cash and record the end-of-day close with its difference')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code='DAY_CLOSE'
 WHERE r.code IN ('OWNER','MANAGER','CASHIER') AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);

INSERT INTO schema_migrations(version,name) VALUES (100,'Day close: insert-only end-of-day cash count record and the DAY_CLOSE right')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
