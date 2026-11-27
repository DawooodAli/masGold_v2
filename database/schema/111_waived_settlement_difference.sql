-- Exchange and Return: the part of the difference the customer / the shop did not settle and the employee waived is stored on the record (owner 2026-11-27: "accept only what was paid").
-- Prepared; NOT applied to any database by the task that wrote it.
--   invoice_lifecycle_transactions.waived_amount   (Exchange)  >= 0, at most |signed_amount|:  money_received - money_paid + account_signed + SIGN(signed_amount) * waived_amount = signed_amount
--   invoice_returns.waived_amount                  (Return)    >= 0:  a FINALIZED Return has money_paid + account_amount + waived_amount = total_amount; any other status keeps waived_amount = 0
-- A waived amount is NOT a payment: it creates no cash / bank movement and no customer-account entry. It is a recorded, attributed (the record's own performer) difference between the authoritative
-- total and what was really settled. Existing rows get 0.00, so every row stays valid. 031's chk_lifecycle_making_model and 033's chk_return_amounts_v2 are replaced by the same checks plus the waived part.
-- Re-run safe.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=110),
 'DO 0', 'SELECT * FROM MIGRATION_111_REFUSED_missing_110');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- ------------------------------------------------------------------ Exchange
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_lifecycle_transactions' AND COLUMN_NAME='waived_amount'),
 "ALTER TABLE invoice_lifecycle_transactions ADD COLUMN waived_amount DECIMAL(16,2) NOT NULL DEFAULT 0.00 AFTER account_signed", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_lifecycle_transactions' AND CONSTRAINT_NAME='chk_lifecycle_making_model'),
 "ALTER TABLE invoice_lifecycle_transactions DROP CHECK chk_lifecycle_making_model", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_lifecycle_transactions' AND CONSTRAINT_NAME='chk_lifecycle_making_model_v2'),
 "ALTER TABLE invoice_lifecycle_transactions ADD CONSTRAINT chk_lifecycle_making_model_v2 CHECK (
   gold_amount >= 0 AND making_charge >= 0 AND making_refund >= 0
   AND money_received >= 0 AND money_paid >= 0
   AND (making_refund = 0 OR making_refund_eligible)
   AND rounding_adjustment > -5 AND rounding_adjustment < 5
   AND signed_amount = (CASE WHEN valued_delta_grams > 0 THEN gold_amount
                             WHEN valued_delta_grams < 0 THEN -gold_amount ELSE 0 END)
                       + making_charge - making_refund + rounding_adjustment
   AND (rounding_adjustment = 0 OR MOD(signed_amount, 5) = 0)
   AND waived_amount >= 0 AND waived_amount <= ABS(signed_amount)
   AND money_received - money_paid + account_signed + SIGN(signed_amount) * waived_amount = signed_amount)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- ------------------------------------------------------------------ Return
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_returns' AND COLUMN_NAME='waived_amount'),
 "ALTER TABLE invoice_returns ADD COLUMN waived_amount DECIMAL(16,2) NOT NULL DEFAULT 0.00 AFTER account_amount", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_returns' AND CONSTRAINT_NAME='chk_return_amounts_v2'),
 "ALTER TABLE invoice_returns DROP CHECK chk_return_amounts_v2", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_returns' AND CONSTRAINT_NAME='chk_return_amounts_v3'),
 "ALTER TABLE invoice_returns ADD CONSTRAINT chk_return_amounts_v3 CHECK (
    gold_amount >= 0 AND making_refund >= 0 AND money_paid >= 0 AND account_amount >= 0 AND waived_amount >= 0 AND refund_basis >= 0
    AND valuation_mode IN ('HISTORICAL', 'CURRENT_BUY')
    AND (valuation_mode = 'HISTORICAL' OR refund_basis = gold_amount + making_refund)
    AND rounding_adjustment > -5 AND rounding_adjustment < 5
    AND total_amount = refund_basis + rounding_adjustment
    AND (rounding_adjustment = 0 OR MOD(total_amount, 5) = 0)
    AND (return_status <> 'FINALIZED' OR (total_amount > 0 AND money_paid + account_amount + waived_amount = total_amount))
    AND (return_status = 'FINALIZED' OR (money_paid = 0 AND account_amount = 0 AND waived_amount = 0)))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (111,'Exchange and Return: a waived settlement difference is stored on the record')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
