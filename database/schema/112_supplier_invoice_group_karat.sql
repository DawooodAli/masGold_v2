-- Supplier purchase invoice: a making group may carry its OWN karat (owner 2026-11-27: e.g. a 24K group on an invoice of a supplier whose account karat is 21; the group's grams are converted into the account karat).
-- Prepared; NOT applied to any database by the task that wrote it.
--   supplier_invoice_groups.karat_id               the group's ACTUAL karat (the karat of the pieces that will be entered). supplier_weight_grams stays the ACTUAL grams of that karat, so the making
--                                                  (EGP / gram x actual grams) and the intake tolerance are unchanged.
--   supplier_invoice_groups.credited_weight_grams  the group's grams converted into the supplier's ACCOUNT karat = supplier_weight_grams x group karat / account karat (3 dp, ROUND_HALF_UP, computed ONCE by the
--                                                  service); the posted purchase gold movement is the sum of these, in the account karat. For a group of the account karat it equals supplier_weight_grams.
-- Existing groups are backfilled with their invoice's karat and credited = supplier_weight_grams, so every posted invoice keeps its exact meaning and its ledger rows are never touched. Re-run safe.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=111),
 'DO 0', 'SELECT * FROM MIGRATION_112_REFUSED_missing_111');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_invoice_groups' AND COLUMN_NAME='karat_id'),
 "ALTER TABLE supplier_invoice_groups ADD COLUMN karat_id TINYINT UNSIGNED DEFAULT NULL AFTER supplier_weight_grams", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_invoice_groups' AND COLUMN_NAME='credited_weight_grams'),
 "ALTER TABLE supplier_invoice_groups ADD COLUMN credited_weight_grams DECIMAL(14,3) DEFAULT NULL AFTER karat_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Backfill (the only data statement): a group that has no karat yet takes its invoice's karat and credits exactly its own grams.
UPDATE supplier_invoice_groups g JOIN supplier_purchase_invoices i ON i.id = g.invoice_id
   SET g.karat_id = i.karat_id, g.credited_weight_grams = g.supplier_weight_grams
 WHERE g.karat_id IS NULL OR g.credited_weight_grams IS NULL;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_invoice_groups' AND COLUMN_NAME='karat_id' AND IS_NULLABLE='YES'),
 "ALTER TABLE supplier_invoice_groups MODIFY COLUMN karat_id TINYINT UNSIGNED NOT NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_invoice_groups' AND COLUMN_NAME='credited_weight_grams' AND IS_NULLABLE='YES'),
 "ALTER TABLE supplier_invoice_groups MODIFY COLUMN credited_weight_grams DECIMAL(14,3) NOT NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_invoice_groups' AND CONSTRAINT_NAME='fk_sig_karat'),
 "ALTER TABLE supplier_invoice_groups ADD CONSTRAINT fk_sig_karat FOREIGN KEY (karat_id) REFERENCES karats(id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_invoice_groups' AND CONSTRAINT_NAME='chk_sig_credited'),
 "ALTER TABLE supplier_invoice_groups ADD CONSTRAINT chk_sig_credited CHECK (credited_weight_grams > 0)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (112,'Supplier invoice group karat: the actual karat and the grams converted into the supplier account karat')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
