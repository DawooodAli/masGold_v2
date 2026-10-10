-- Refuse rollback when any party is an intermediary (alone or as a second role) - the role would lose its meaning - or when a later
-- migration exists. A supplier-only table rolls back to exactly the 043 shape.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM suppliers WHERE is_intermediary = 1 OR is_supplier = 0)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>44),
 'SELECT * FROM ROLLBACK_044_REFUSED_intermediary_roles_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE suppliers DROP CHECK chk_suppliers_intermediary_karat;
ALTER TABLE suppliers DROP CHECK chk_suppliers_role;
ALTER TABLE suppliers DROP COLUMN is_intermediary;
ALTER TABLE suppliers DROP COLUMN is_supplier;
DELETE FROM schema_migrations WHERE version=44;
