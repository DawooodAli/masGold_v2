-- Refuse rollback once any supplier carries a karat (backfilled or chosen: it is business information that invoices
-- inherited), or when a later migration exists. Never erase it.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM suppliers WHERE karat_id IS NOT NULL)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>35),
 'SELECT * FROM ROLLBACK_035_REFUSED_supplier_karats_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE suppliers DROP FOREIGN KEY fk_suppliers_karat;
ALTER TABLE suppliers DROP KEY idx_suppliers_karat;
ALTER TABLE suppliers DROP COLUMN karat_id;
DELETE FROM schema_migrations WHERE version=35;
