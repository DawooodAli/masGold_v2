-- Supplier invoice group MAKING-RATE adjustment (owner 2026-11-21): the making rate (EGP / gram) of a making group of a POSTED invoice was wrong, so the owner enters the
-- correct rate. The difference (new rate - current rate) x the group's contractual supplier weight is posted ONCE to the supplier MAKING ledger (type GROUP_RATE_ADJUSTMENT):
-- a higher rate means the shop owes the supplier MORE, a lower rate LESS. The group row, the invoice header and the original ledger rows are never touched; the effective rate is the
-- newest adjustment's new rate. A mistaken adjustment is corrected by a further adjustment back to the right rate (nothing is deleted). Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=105),
 'DO 0', 'SELECT * FROM MIGRATION_106_REFUSED_missing_105');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO supplier_making_movement_types(code,name,description) VALUES
 ('GROUP_RATE_ADJUSTMENT','Supplier Invoice Group Making Rate Adjustment','Making (EGP) added to (more owed to the supplier) or deducted from (less owed) a making group of a posted supplier invoice because its per-gram rate changed')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);

CREATE TABLE IF NOT EXISTS supplier_invoice_group_rate_adjustments (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 invoice_id BIGINT UNSIGNED NOT NULL,
 group_id BIGINT UNSIGNED NOT NULL,
 old_rate DECIMAL(14,2) NOT NULL,
 new_rate DECIMAL(14,2) NOT NULL,
 delta_amount DECIMAL(16,2) NOT NULL,
 reason VARCHAR(500) NOT NULL,
 request_key VARCHAR(64) NOT NULL,
 created_by_user_id INT UNSIGNED NOT NULL,
 created_at DATETIME NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_sigra_request (request_key),
 KEY idx_sigra_group (group_id, id),
 KEY idx_sigra_invoice (invoice_id),
 CONSTRAINT fk_sigra_invoice FOREIGN KEY (invoice_id) REFERENCES supplier_purchase_invoices(id),
 CONSTRAINT fk_sigra_group FOREIGN KEY (group_id) REFERENCES supplier_invoice_groups(id),
 CONSTRAINT fk_sigra_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_sigra_rates CHECK (old_rate >= 0 AND new_rate >= 0 AND new_rate <> old_rate),
 CONSTRAINT chk_sigra_delta CHECK (delta_amount <> 0),
 CONSTRAINT chk_sigra_reason CHECK (CHAR_LENGTH(TRIM(reason)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (106,'Supplier invoice group making-rate adjustment: immutable rows and the making movement type')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
