-- Refuse rollback once a categorised movement exists (its optional note would violate 025). Never erase history.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM financial_operations WHERE category IS NOT NULL)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>26),
 'SELECT * FROM ROLLBACK_026_REFUSED_history_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE financial_operations DROP CHECK chk_fo_category;
ALTER TABLE financial_operations DROP CHECK chk_fo_shape;
ALTER TABLE financial_operations ADD CONSTRAINT chk_fo_shape CHECK (
 (operation_type='MANUAL_DEPOSIT' AND from_cash_account_id IS NULL AND from_bank_account_id IS NULL
   AND (to_cash_account_id IS NOT NULL OR to_bank_account_id IS NOT NULL)
   AND reason IS NOT NULL AND CHAR_LENGTH(TRIM(reason))>0)
 OR (operation_type='MANUAL_WITHDRAWAL' AND to_cash_account_id IS NULL AND to_bank_account_id IS NULL
   AND (from_cash_account_id IS NOT NULL OR from_bank_account_id IS NOT NULL)
   AND reason IS NOT NULL AND CHAR_LENGTH(TRIM(reason))>0)
 OR (operation_type='ACCOUNT_TRANSFER'
   AND (from_cash_account_id IS NOT NULL OR from_bank_account_id IS NOT NULL)
   AND (to_cash_account_id IS NOT NULL OR to_bank_account_id IS NOT NULL)
   AND NOT (COALESCE(from_cash_account_id,0)=COALESCE(to_cash_account_id,0)
            AND COALESCE(from_bank_account_id,0)=COALESCE(to_bank_account_id,0))));
ALTER TABLE financial_operations DROP COLUMN category;
DELETE FROM schema_migrations WHERE version=26;
