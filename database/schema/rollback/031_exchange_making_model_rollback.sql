-- The 019 check can only come back while every stored exchange still fits the OLD rule
-- (making tied to the gold delta, no rounding). Refuse once a new-model exchange exists.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM invoice_lifecycle_transactions
        WHERE rounding_adjustment <> 0
           OR (making_charge <> 0 AND valued_delta_grams <= 0)
           OR (making_refund <> 0 AND valued_delta_grams >= 0)
           OR signed_amount <> CASE WHEN valued_delta_grams > 0 THEN gold_amount + making_charge
                                    WHEN valued_delta_grams < 0 THEN -(gold_amount + making_refund) ELSE 0 END)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>31),
 'SELECT * FROM ROLLBACK_031_REFUSED_new_model_exchanges_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE invoice_lifecycle_transactions DROP CHECK chk_lifecycle_making_model;
ALTER TABLE invoice_lifecycle_transactions ADD CONSTRAINT chk_lifecycle_amounts CHECK (
    gold_amount >= 0 AND making_charge >= 0 AND making_refund >= 0
    AND money_received >= 0 AND money_paid >= 0
    AND (making_charge = 0 OR valued_delta_grams > 0)
    AND (making_refund = 0 OR (valued_delta_grams < 0 AND making_refund_eligible))
    AND signed_amount = CASE
        WHEN valued_delta_grams > 0 THEN gold_amount + making_charge
        WHEN valued_delta_grams < 0 THEN -(gold_amount + making_refund)
        ELSE 0 END
    AND money_received - money_paid + account_signed = signed_amount);
ALTER TABLE invoice_lifecycle_transactions DROP COLUMN rounding_adjustment;
DELETE FROM schema_migrations WHERE version=31;
