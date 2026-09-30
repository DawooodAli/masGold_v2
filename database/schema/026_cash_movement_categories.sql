-- Cash movement categories (prototype CASH_TYPES) on the immutable manual financial operations of 025.
-- مصروفات / مرتبات / إيجار / مرمة are cash OUT, إيراد / مرمة cash IN. A categorised movement's note is
-- optional; an uncategorised manual deposit / withdrawal still requires its reason. No row is rewritten.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=25),
 'DO 0', 'SELECT * FROM MIGRATION_026_REFUSED_missing_025');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='financial_operations' AND COLUMN_NAME='category'),
 "ALTER TABLE financial_operations ADD COLUMN category VARCHAR(20) DEFAULT NULL AFTER operation_type", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='financial_operations' AND CONSTRAINT_NAME='chk_fo_shape'),
 'ALTER TABLE financial_operations DROP CHECK chk_fo_shape', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE financial_operations ADD CONSTRAINT chk_fo_shape CHECK (
 (operation_type='MANUAL_DEPOSIT' AND from_cash_account_id IS NULL AND from_bank_account_id IS NULL
   AND (to_cash_account_id IS NOT NULL OR to_bank_account_id IS NOT NULL)
   AND (category IS NOT NULL OR (reason IS NOT NULL AND CHAR_LENGTH(TRIM(reason))>0)))
 OR (operation_type='MANUAL_WITHDRAWAL' AND to_cash_account_id IS NULL AND to_bank_account_id IS NULL
   AND (from_cash_account_id IS NOT NULL OR from_bank_account_id IS NOT NULL)
   AND (category IS NOT NULL OR (reason IS NOT NULL AND CHAR_LENGTH(TRIM(reason))>0)))
 OR (operation_type='ACCOUNT_TRANSFER'
   AND (from_cash_account_id IS NOT NULL OR from_bank_account_id IS NOT NULL)
   AND (to_cash_account_id IS NOT NULL OR to_bank_account_id IS NOT NULL)
   AND NOT (COALESCE(from_cash_account_id,0)=COALESCE(to_cash_account_id,0)
            AND COALESCE(from_bank_account_id,0)=COALESCE(to_bank_account_id,0))));

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='financial_operations' AND CONSTRAINT_NAME='chk_fo_category'),
 "ALTER TABLE financial_operations ADD CONSTRAINT chk_fo_category CHECK (
   category IS NULL
   OR (operation_type='MANUAL_DEPOSIT' AND category IN ('REVENUE','REPAIR'))
   OR (operation_type='MANUAL_WITHDRAWAL' AND category IN ('EXPENSE','SALARY','RENT','REPAIR')))",
 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (26,'Cash movement categories for manual financial operations')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
