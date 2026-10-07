-- Exchange making model (owner manual review 2026-10-03).
-- Gold difference and making difference are independent:
--   raw    = (+gold_amount when the customer receives more gold, −gold_amount when less, 0 when equal)
--            + making_charge (replacement making: Σ weight × making/gram)
--            − making_refund (the returned pieces' ORIGINAL invoice making, inside the refund window)
--   signed_amount = raw + rounding_adjustment, rounded to the nearest EGP 5 (|rounding| < 5).
-- Replaces 019's chk_lifecycle_amounts, which tied making to the gold delta. Existing rows satisfy the
-- new rule with rounding_adjustment = 0 (checked first; nothing is rewritten).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=30),
 'DO 0', 'SELECT * FROM MIGRATION_031_REFUSED_missing_030');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_lifecycle_transactions' AND COLUMN_NAME='rounding_adjustment'),
 "ALTER TABLE invoice_lifecycle_transactions ADD COLUMN rounding_adjustment DECIMAL(16,2) NOT NULL DEFAULT 0.00
   AFTER making_refund_eligible", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Every stored exchange must already satisfy the new rule (signed = ±gold + charge − refund).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM invoice_lifecycle_transactions
 WHERE signed_amount <> (CASE WHEN valued_delta_grams > 0 THEN gold_amount
                              WHEN valued_delta_grams < 0 THEN -gold_amount ELSE 0 END)
                       + making_charge - making_refund + rounding_adjustment),
 'SELECT * FROM MIGRATION_031_REFUSED_existing_exchange_violates_model', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_lifecycle_transactions' AND CONSTRAINT_NAME='chk_lifecycle_amounts'),
 "ALTER TABLE invoice_lifecycle_transactions DROP CHECK chk_lifecycle_amounts", 'DO 0');
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

INSERT INTO schema_migrations(version,name) VALUES (31,'Exchange: independent making difference and EGP-5 rounding')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
