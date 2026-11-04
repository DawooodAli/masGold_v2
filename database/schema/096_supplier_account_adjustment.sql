-- Supplier ACCOUNT adjustments (owner 2026-11-04, «تسوية فروق الذهب» / «تسوية فروق المصنعيات»): the gold or the making (EGP) a supplier holds for the shop is
-- sometimes more or less than the real invoices say. The owner corrects the ACCOUNT ONLY: no Scrap, cash, bank, inventory or invoice movement. One adjustment =
-- one immutable row here + ONE ledger movement (type ACCOUNT_ADJUSTMENT): GOLD in the supplier's own dedicated karat (physical grams) or MAKING in EGP.
-- delta > 0 = the shop owes the supplier MORE; delta < 0 = LESS (the same sign convention as the ledgers). A mistake is corrected by a further opposite
-- adjustment (nothing is deleted). Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=95),
 'DO 0', 'SELECT * FROM MIGRATION_096_REFUSED_missing_095');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO supplier_gold_movement_types(code,name,description) VALUES
 ('ACCOUNT_ADJUSTMENT','Supplier Gold Account Adjustment','Gold added to (more owed to the supplier) or deducted from (less owed) the supplier gold account, with no stock movement')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO supplier_making_movement_types(code,name,description) VALUES
 ('ACCOUNT_ADJUSTMENT','Supplier Making Account Adjustment','Making (EGP) added to (more owed to the supplier) or deducted from (less owed) the supplier making account, with no money movement')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);

CREATE TABLE IF NOT EXISTS supplier_account_adjustments (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 supplier_id INT UNSIGNED NOT NULL,
 kind VARCHAR(6) NOT NULL,
 karat_id TINYINT UNSIGNED DEFAULT NULL,
 delta DECIMAL(16,3) NOT NULL,
 reason VARCHAR(500) NOT NULL,
 request_key VARCHAR(64) NOT NULL,
 created_by_user_id INT UNSIGNED NOT NULL,
 created_at DATETIME NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_saa_request (request_key),
 KEY idx_saa_supplier (supplier_id, id),
 CONSTRAINT fk_saa_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
 CONSTRAINT fk_saa_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT fk_saa_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_saa_delta CHECK (delta <> 0),
 CONSTRAINT chk_saa_reason CHECK (CHAR_LENGTH(TRIM(reason)) > 0),
 CONSTRAINT chk_saa_kind CHECK (
  (kind = 'GOLD' AND karat_id IS NOT NULL)
  OR (kind = 'MAKING' AND karat_id IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO permissions(code,name,description) VALUES
 ('SUPPLIER_ACCOUNT_ADJUST','Adjust Supplier Account Differences','Add or deduct gold / making on a supplier account without any stock or money movement (owner only)')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code='SUPPLIER_ACCOUNT_ADJUST'
 WHERE r.code = 'OWNER' AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);

INSERT INTO schema_migrations(version,name) VALUES (96,'Supplier account adjustments: gold / making differences, immutable rows, movement types and owner right')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
