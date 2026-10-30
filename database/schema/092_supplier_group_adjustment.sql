-- Supplier invoice group GOLD adjustment (owner 2026-10-30): the supplier's weight of a making group of a POSTED invoice was wrong, so the owner adds
-- gold to the group (the shop owes the supplier MORE) or deducts gold (the shop owes LESS). Gold only: the group's making, the invoice header and
-- the original ledger rows are never touched. One adjustment = one immutable row here + ONE supplier gold movement (type GROUP_ADJUSTMENT) in the
-- invoice's own karat. A mistaken adjustment is corrected by a further opposite adjustment (nothing is deleted).
-- Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=91),
 'DO 0', 'SELECT * FROM MIGRATION_092_REFUSED_missing_091');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO supplier_gold_movement_types(code,name,description) VALUES
 ('GROUP_ADJUSTMENT','Supplier Invoice Group Adjustment','Gold added to (more owed to the supplier) or deducted from (less owed) a making group of a posted supplier invoice')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);

CREATE TABLE IF NOT EXISTS supplier_invoice_group_adjustments (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 invoice_id BIGINT UNSIGNED NOT NULL,
 group_id BIGINT UNSIGNED NOT NULL,
 delta_grams DECIMAL(14,3) NOT NULL,
 reason VARCHAR(500) NOT NULL,
 request_key VARCHAR(64) NOT NULL,
 created_by_user_id INT UNSIGNED NOT NULL,
 created_at DATETIME NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_siga_request (request_key),
 KEY idx_siga_group (group_id),
 KEY idx_siga_invoice (invoice_id),
 CONSTRAINT fk_siga_invoice FOREIGN KEY (invoice_id) REFERENCES supplier_purchase_invoices(id),
 CONSTRAINT fk_siga_group FOREIGN KEY (group_id) REFERENCES supplier_invoice_groups(id),
 CONSTRAINT fk_siga_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_siga_delta CHECK (delta_grams <> 0),
 CONSTRAINT chk_siga_reason CHECK (CHAR_LENGTH(TRIM(reason)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO permissions(code,name,description) VALUES
 ('SUPPLIER_INVOICE_ADJUST','Adjust Supplier Invoice Group Gold','Add or deduct gold on a making group of a posted supplier invoice (owner / manager)')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code='SUPPLIER_INVOICE_ADJUST'
 WHERE r.code IN ('OWNER','MANAGER') AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);

INSERT INTO schema_migrations(version,name) VALUES (92,'Supplier invoice group gold adjustment: immutable adjustment rows, movement type and right')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
