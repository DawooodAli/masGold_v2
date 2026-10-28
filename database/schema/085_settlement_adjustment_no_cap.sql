-- Old-gold-only invoices: the paid amount settles the invoice and a large difference is only a warning (owner 2026-10-28), so the
-- settlement adjustment row no longer has a 500 EGP cap.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=84),
 'DO 0', 'SELECT * FROM MIGRATION_085_REFUSED_missing_084');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

ALTER TABLE invoice_settlement_adjustments DROP CHECK chk_settlement_amount;
ALTER TABLE invoice_settlement_adjustments ADD CONSTRAINT chk_settlement_amount CHECK (
    amount_signed <> 0
    AND residual_signed <> 0
    AND SIGN(amount_signed) = SIGN(residual_signed)
    AND ABS(amount_signed) <= ABS(residual_signed)
);

INSERT INTO schema_migrations(version,name) VALUES (85,'Settlement adjustment without 500 cap')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
