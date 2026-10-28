-- Rollback of 085. Refuses when a later migration exists or an adjustment above 500 is stored.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 85)
   OR EXISTS(SELECT 1 FROM invoice_settlement_adjustments WHERE ABS(amount_signed) > 500),
 'SELECT * FROM ROLLBACK_085_REFUSED_later_migration_or_large_adjustment', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE invoice_settlement_adjustments DROP CHECK chk_settlement_amount;
ALTER TABLE invoice_settlement_adjustments ADD CONSTRAINT chk_settlement_amount CHECK (
    amount_signed <> 0
    AND residual_signed <> 0
    AND SIGN(amount_signed) = SIGN(residual_signed)
    AND ABS(amount_signed) <= ABS(residual_signed)
    AND ABS(amount_signed) <= 500
);
DELETE FROM schema_migrations WHERE version = 85;
