-- Rollback of 090. Refuses when a later migration exists or any private-account entry was recorded.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 90)
   OR EXISTS(SELECT 1 FROM private_account_entries),
 'SELECT * FROM ROLLBACK_090_REFUSED_later_migration_or_entries_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS private_account_entries;
DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'PRIVATE_ACCOUNT_MANAGE');
DELETE FROM permissions WHERE code = 'PRIVATE_ACCOUNT_MANAGE';
DELETE FROM cash_categories WHERE code IN ('PRIVATE_OUT', 'PRIVATE_IN')
  AND NOT EXISTS (SELECT 1 FROM financial_operations WHERE category IN ('PRIVATE_OUT', 'PRIVATE_IN'));
DELETE FROM schema_migrations WHERE version = 90;
