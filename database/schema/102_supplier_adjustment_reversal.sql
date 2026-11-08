-- Reversal of a supplier ACCOUNT adjustment «عكس تسوية فروق» (owner 2026-11-08): the gold / making difference rows of migration 096 had no reversal button (a mistake was fixed by a further
-- opposite adjustment typed by hand). A reversal is now one more immutable adjustment row that is the EXACT opposite of the original and names it (reverses_id), posting the same
-- kind of ledger movement. One original has at most one reversal (unique reverses_id); a reversal is never reversed again. Nothing is deleted or rewritten. Every step is guarded.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=101),
 'DO 0', 'SELECT * FROM MIGRATION_102_REFUSED_missing_101');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_account_adjustments' AND COLUMN_NAME='reverses_id'),
 "ALTER TABLE supplier_account_adjustments ADD COLUMN reverses_id BIGINT UNSIGNED DEFAULT NULL AFTER reason", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_account_adjustments' AND INDEX_NAME='uq_saa_reverses'),
 "ALTER TABLE supplier_account_adjustments ADD UNIQUE KEY uq_saa_reverses (reverses_id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_account_adjustments' AND CONSTRAINT_NAME='fk_saa_reverses'),
 "ALTER TABLE supplier_account_adjustments ADD CONSTRAINT fk_saa_reverses FOREIGN KEY (reverses_id) REFERENCES supplier_account_adjustments(id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_account_adjustments' AND CONSTRAINT_NAME='chk_saa_reverses'),
 "ALTER TABLE supplier_account_adjustments ADD CONSTRAINT chk_saa_reverses CHECK (reverses_id IS NULL OR reverses_id <> id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (102,'Supplier account adjustment reversal: reverses_id links an exact opposite adjustment to its original')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
