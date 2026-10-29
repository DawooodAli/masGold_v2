-- Supplier groups in the "new inventory piece" entry (owner 2026-10-29): the owner can take a group off the entry list when its barcodes were
-- already printed elsewhere. One insert-only row per hidden group; it only hides the group from that list and never changes weights, the
-- invoice, a ledger or an intake limit.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=87),
 'DO 0', 'SELECT * FROM MIGRATION_088_REFUSED_missing_087');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS supplier_group_intake_closures (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 group_id BIGINT UNSIGNED NOT NULL,
 closed_by_user_id INT UNSIGNED DEFAULT NULL,
 closed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_sgic_group (group_id),
 CONSTRAINT fk_sgic_group FOREIGN KEY (group_id) REFERENCES supplier_invoice_groups(id),
 CONSTRAINT fk_sgic_user FOREIGN KEY (closed_by_user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (88,'Supplier group intake closures')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
