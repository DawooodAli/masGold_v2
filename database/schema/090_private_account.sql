-- Private account (owner 2026-10-29): money moved between a bank / wallet account and the OWNER's private account.
-- The money stays on the canonical ledgers: a transfer is a manual operation on the bank account (category PRIVATE_OUT = to the private account,
-- PRIVATE_IN = from it) tied in the SAME transaction to one row of private_account_entries. The private balance is derived (entries whose operation
-- was not reversed), never stored. Only the owner holds the right PRIVATE_ACCOUNT_MANAGE.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=89),
 'DO 0', 'SELECT * FROM MIGRATION_090_REFUSED_missing_089');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO cash_categories(code, name, direction, is_active, is_system, sort_order) VALUES
 ('PRIVATE_OUT', 'تحويل إلى الحساب الخاص', 'OUT', 1, 1, 87),
 ('PRIVATE_IN', 'تحويل من الحساب الخاص', 'IN', 1, 1, 97)
 ON DUPLICATE KEY UPDATE code = code;

CREATE TABLE IF NOT EXISTS private_account_entries (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 direction VARCHAR(12) NOT NULL,
 amount DECIMAL(16,2) NOT NULL,
 operation_id BIGINT UNSIGNED NOT NULL,
 note VARCHAR(500) DEFAULT NULL,
 created_by_user_id INT UNSIGNED DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_private_entry_operation (operation_id),
 CONSTRAINT fk_private_entry_operation FOREIGN KEY (operation_id) REFERENCES financial_operations(id),
 CONSTRAINT fk_private_entry_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_private_entry_amount CHECK (amount > 0),
 CONSTRAINT chk_private_entry_direction CHECK (direction IN ('TO_PRIVATE', 'FROM_PRIVATE'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO permissions(code,name,description) VALUES
 ('PRIVATE_ACCOUNT_MANAGE','Private Account','Transfer between a bank / wallet account and the owner private account, and read its report (owner only)')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code='PRIVATE_ACCOUNT_MANAGE'
 WHERE r.code='OWNER' AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);

INSERT INTO schema_migrations(version,name) VALUES (90,'Private account: entries table, two categories, owner right')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
