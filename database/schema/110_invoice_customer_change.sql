-- Change the customer of a finalized invoice (owner 2026-11-25: the customer name is sometimes entered wrong). Prepared; NOT applied to any database by the task that wrote it.
--   Adds ONE customer-ledger type, INVOICE_CUSTOMER_CHANGE (direction EITHER). The service moves what the invoice wrote on the old customer's account to the new customer's account with two NEW
--   ledger rows (the reverse on the old customer, the same amount on the new one), both tagged with the invoice reference; the original ledger row is never rewritten. No table or column changes.
--   Re-run safe.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=109),
 'DO 0', 'SELECT * FROM MIGRATION_110_REFUSED_missing_109');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO customer_transaction_types(code, name, expected_direction, description) VALUES
 ('INVOICE_CUSTOMER_CHANGE', 'Invoice Customer Change', 'EITHER', 'An invoice moved to another customer: the reverse of its account effect on the old customer, the same effect on the new customer')
 ON DUPLICATE KEY UPDATE name = VALUES(name);

INSERT INTO schema_migrations(version,name) VALUES (110,'Customer ledger type for changing the customer of a finalized invoice')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
