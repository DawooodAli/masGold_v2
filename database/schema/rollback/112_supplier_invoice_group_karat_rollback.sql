-- Rollback of 112 (supplier invoice group karat). Refuses when a later migration exists or when any group already differs from its invoice (another karat, or credited grams that are not its own grams):
-- dropping the columns would lose that meaning (history is never deleted silently). Otherwise it drops the check, the foreign key and the two columns.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 112),
 'SELECT * FROM ROLLBACK_112_REFUSED_later_migration_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF((SELECT COUNT(*) FROM supplier_invoice_groups g JOIN supplier_purchase_invoices i ON i.id = g.invoice_id
                       WHERE g.karat_id <> i.karat_id OR g.credited_weight_grams <> g.supplier_weight_grams) > 0,
 'SELECT * FROM ROLLBACK_112_REFUSED_group_karats_in_use', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_invoice_groups' AND CONSTRAINT_NAME='chk_sig_credited'),
 "ALTER TABLE supplier_invoice_groups DROP CHECK chk_sig_credited", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_invoice_groups' AND CONSTRAINT_NAME='fk_sig_karat'),
 "ALTER TABLE supplier_invoice_groups DROP FOREIGN KEY fk_sig_karat", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_invoice_groups' AND COLUMN_NAME='credited_weight_grams'),
 "ALTER TABLE supplier_invoice_groups DROP COLUMN credited_weight_grams", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_invoice_groups' AND COLUMN_NAME='karat_id'),
 "ALTER TABLE supplier_invoice_groups DROP COLUMN karat_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

DELETE FROM schema_migrations WHERE version = 112;
