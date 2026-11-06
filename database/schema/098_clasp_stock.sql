-- Clasp stock «رصيد القفل» (owner 2026-11-06): the clasp (قفل) is a loose part that cannot carry a barcode, so it is kept as a WEIGHT ONLY ledger per karat, like Scrap
-- but separate from it and from the piece inventory. Insert-only signed grams: ENTRY / ADJUST_IN add, ADJUST_OUT removes, SALE_OUT (invoice, a later slice) removes,
-- SALE_RETURN / SALE_REVERSAL give it back. The balance is SUM(delta_grams), never stored, and a removal can never take it below zero (the service decides under a lock).
-- Right CLASP_STOCK_MANAGE (OWNER, MANAGER) enters or corrects the weight with a mandatory reason. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=97),
 'DO 0', 'SELECT * FROM MIGRATION_098_REFUSED_missing_097');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS clasp_stock_movements (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 karat_id TINYINT UNSIGNED NOT NULL,
 movement_code VARCHAR(16) NOT NULL,
 delta_grams DECIMAL(12,3) NOT NULL,
 reason VARCHAR(500) NOT NULL,
 reference_type VARCHAR(30) DEFAULT NULL,
 reference_id BIGINT UNSIGNED DEFAULT NULL,
 request_key VARCHAR(64) NOT NULL,
 created_by_user_id INT UNSIGNED NOT NULL,
 created_at DATETIME NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_clasp_request (request_key),
 KEY idx_clasp_karat (karat_id, id),
 CONSTRAINT fk_clasp_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT fk_clasp_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_clasp_reason CHECK (CHAR_LENGTH(TRIM(reason)) > 0),
 CONSTRAINT chk_clasp_code CHECK (movement_code IN ('ENTRY','ADJUST_IN','ADJUST_OUT','SALE_OUT','SALE_RETURN','SALE_REVERSAL')),
 CONSTRAINT chk_clasp_sign CHECK (
  (movement_code IN ('ENTRY','ADJUST_IN','SALE_RETURN','SALE_REVERSAL') AND delta_grams > 0)
  OR (movement_code IN ('ADJUST_OUT','SALE_OUT') AND delta_grams < 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO permissions(code,name,description) VALUES
 ('CLASP_STOCK_MANAGE','Manage Clasp Stock','Enter or correct the clasp (قفل) weight stock per karat with a reason')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code='CLASP_STOCK_MANAGE'
 WHERE r.code IN ('OWNER','MANAGER') AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);

INSERT INTO schema_migrations(version,name) VALUES (98,'Clasp stock: weight-only ledger per karat and the CLASP_STOCK_MANAGE right')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
