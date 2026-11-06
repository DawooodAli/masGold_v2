-- Rollback of 098 (clasp stock). Refuses when a later migration exists or the ledger holds any row (history is never deleted silently).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 98),
 'SELECT * FROM ROLLBACK_098_REFUSED_later_migration_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'clasp_stock_movements')
   AND (SELECT COUNT(*) FROM clasp_stock_movements) > 0,
 'SELECT * FROM ROLLBACK_098_REFUSED_clasp_movements_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'CLASP_STOCK_MANAGE');
DELETE FROM permissions WHERE code = 'CLASP_STOCK_MANAGE';
DROP TABLE IF EXISTS clasp_stock_movements;
DELETE FROM schema_migrations WHERE version = 98;
