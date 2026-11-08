-- Rollback of 104 (payroll). Refuses when a later migration exists, when any payroll row exists, or when one operation repays several advances (the restored unique key could not hold it).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 104),
 'SELECT * FROM ROLLBACK_104_REFUSED_later_migration_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'payroll_slips')
   AND ((SELECT COUNT(*) FROM payroll_slips) + (SELECT COUNT(*) FROM payroll_adjustments) + (SELECT COUNT(*) FROM employee_salary_rates)) > 0,
 'SELECT * FROM ROLLBACK_104_REFUSED_payroll_rows_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF((SELECT COUNT(*) FROM (SELECT operation_id FROM cash_advance_repayments GROUP BY operation_id HAVING COUNT(*) > 1) t) > 0,
 'SELECT * FROM ROLLBACK_104_REFUSED_operation_repays_several_advances', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS payroll_adjustments;
DROP TABLE IF EXISTS payroll_slips;
DROP TABLE IF EXISTS employee_salary_rates;
ALTER TABLE cash_advance_repayments DROP INDEX uq_cash_advance_repay_pair;
ALTER TABLE cash_advance_repayments ADD UNIQUE KEY uq_cash_advance_repay_operation (operation_id);
ALTER TABLE cash_advance_repayments DROP INDEX idx_cash_advance_repay_operation;
DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'PAYROLL_MANAGE');
DELETE FROM permissions WHERE code = 'PAYROLL_MANAGE';
DELETE FROM schema_migrations WHERE version = 104;
