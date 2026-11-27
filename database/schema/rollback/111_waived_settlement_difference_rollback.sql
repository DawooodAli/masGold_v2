-- Rollback of 111 (waived settlement difference on Exchange and Return). Refuses when a later migration exists or any Exchange / Return already waived an amount (history is never deleted silently);
-- otherwise it restores 031's chk_lifecycle_making_model and 033's chk_return_amounts_v2 and drops the two columns.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 111),
 'SELECT * FROM ROLLBACK_111_REFUSED_later_migration_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF((SELECT COUNT(*) FROM invoice_lifecycle_transactions WHERE waived_amount <> 0) + (SELECT COUNT(*) FROM invoice_returns WHERE waived_amount <> 0) > 0,
 'SELECT * FROM ROLLBACK_111_REFUSED_waived_amounts_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_lifecycle_transactions' AND CONSTRAINT_NAME='chk_lifecycle_making_model_v2'),
 "ALTER TABLE invoice_lifecycle_transactions DROP CHECK chk_lifecycle_making_model_v2", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_lifecycle_transactions' AND CONSTRAINT_NAME='chk_lifecycle_making_model'),
 "ALTER TABLE invoice_lifecycle_transactions ADD CONSTRAINT chk_lifecycle_making_model CHECK (
   gold_amount >= 0 AND making_charge >= 0 AND making_refund >= 0
   AND money_received >= 0 AND money_paid >= 0
   AND (making_refund = 0 OR making_refund_eligible)
   AND rounding_adjustment > -5 AND rounding_adjustment < 5
   AND signed_amount = (CASE WHEN valued_delta_grams > 0 THEN gold_amount
                             WHEN valued_delta_grams < 0 THEN -gold_amount ELSE 0 END)
                       + making_charge - making_refund + rounding_adjustment
   AND (rounding_adjustment = 0 OR MOD(signed_amount, 5) = 0)
   AND money_received - money_paid + account_signed = signed_amount)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_lifecycle_transactions' AND COLUMN_NAME='waived_amount'),
 "ALTER TABLE invoice_lifecycle_transactions DROP COLUMN waived_amount", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_returns' AND CONSTRAINT_NAME='chk_return_amounts_v3'),
 "ALTER TABLE invoice_returns DROP CHECK chk_return_amounts_v3", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_returns' AND CONSTRAINT_NAME='chk_return_amounts_v2'),
 "ALTER TABLE invoice_returns ADD CONSTRAINT chk_return_amounts_v2 CHECK (
    gold_amount >= 0 AND making_refund >= 0 AND money_paid >= 0 AND account_amount >= 0 AND refund_basis >= 0
    AND valuation_mode IN ('HISTORICAL', 'CURRENT_BUY')
    AND (valuation_mode = 'HISTORICAL' OR refund_basis = gold_amount + making_refund)
    AND rounding_adjustment > -5 AND rounding_adjustment < 5
    AND total_amount = refund_basis + rounding_adjustment
    AND (rounding_adjustment = 0 OR MOD(total_amount, 5) = 0)
    AND (return_status <> 'FINALIZED' OR (total_amount > 0 AND money_paid + account_amount = total_amount))
    AND (return_status = 'FINALIZED' OR (money_paid = 0 AND account_amount = 0)))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_returns' AND COLUMN_NAME='waived_amount'),
 "ALTER TABLE invoice_returns DROP COLUMN waived_amount", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

DELETE FROM schema_migrations WHERE version = 111;
