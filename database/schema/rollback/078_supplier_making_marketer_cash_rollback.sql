-- Rollback of 078. Refuse while any marketer-funded supplier payment or later migration exists.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM supplier_making_payments WHERE payment_method = 'MARKETER' OR marketer_party_id IS NOT NULL)
 OR EXISTS(SELECT 1 FROM marketer_documents WHERE doc_kind = 'SUPPLIER_PAY')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 78),
 'SELECT * FROM ROLLBACK_078_REFUSED_marketer_supplier_payments_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

DROP TABLE IF EXISTS supplier_making_marketer_payments;
ALTER TABLE supplier_making_payments DROP CHECK chk_smp_method;
ALTER TABLE supplier_making_payments ADD CONSTRAINT chk_smp_method CHECK (
 (payment_method = 'CASH' AND cash_account_id IS NOT NULL AND bank_account_id IS NULL)
 OR (payment_method = 'BANK' AND bank_account_id IS NOT NULL AND cash_account_id IS NULL)
 OR (payment_method = 'CUSTODY' AND cash_account_id IS NULL AND bank_account_id IS NULL));
ALTER TABLE supplier_making_payments DROP FOREIGN KEY fk_smp_marketer_party;
ALTER TABLE supplier_making_payments DROP INDEX uq_smp_marketer_link;
ALTER TABLE supplier_making_payments DROP INDEX idx_smp_marketer;
ALTER TABLE supplier_making_payments DROP COLUMN self_payment_confirmed;
ALTER TABLE supplier_making_payments DROP COLUMN marketer_party_id;

ALTER TABLE marketer_cash_movements DROP CHECK chk_mkcm_shape;
ALTER TABLE marketer_cash_movements ADD CONSTRAINT chk_mkcm_shape CHECK (cash_amount > 0 AND (
 (movement_type = 'CASH_HANDOVER' AND doc_kind = 'CASH_HANDOVER' AND amount_delta = cash_amount AND correction_id IS NULL)
 OR (movement_type = 'CASH_RETURN' AND doc_kind = 'CASH_RETURN' AND amount_delta = -cash_amount AND correction_id IS NULL)
 OR (movement_type = 'SALE_PROCEEDS' AND doc_kind = 'SALE' AND amount_delta = cash_amount AND correction_id IS NULL)
 OR (movement_type = 'PURCHASE_SPEND' AND doc_kind = 'PURCHASE' AND amount_delta = -cash_amount AND correction_id IS NULL)
 OR (movement_type = 'CASH_HANDOVER_REVERSAL' AND doc_kind = 'CASH_HANDOVER' AND amount_delta = -cash_amount AND correction_id IS NOT NULL)
 OR (movement_type = 'CASH_RETURN_REVERSAL' AND doc_kind = 'CASH_RETURN' AND amount_delta = cash_amount AND correction_id IS NOT NULL)
 OR (movement_type = 'SALE_PROCEEDS_REVERSAL' AND doc_kind = 'SALE' AND amount_delta = -cash_amount AND correction_id IS NOT NULL)
 OR (movement_type = 'PURCHASE_SPEND_REVERSAL' AND doc_kind = 'PURCHASE' AND amount_delta = cash_amount AND correction_id IS NOT NULL)));

ALTER TABLE marketer_documents DROP CHECK chk_mkd_shape;
ALTER TABLE marketer_documents ADD CONSTRAINT chk_mkd_shape CHECK (
 (doc_kind IN ('CASH_HANDOVER','CASH_RETURN') AND cash_amount IS NOT NULL AND cash_amount > 0 AND weight_grams IS NULL AND counterparty_kind IS NULL
  AND counterparty_party_id IS NULL AND counterparty_name IS NULL AND counterparty_key IS NULL AND reference_total IS NULL)
 OR (doc_kind = 'GOLD_RETURN' AND cash_amount IS NULL AND weight_grams IS NOT NULL AND weight_grams > 0 AND counterparty_kind IS NULL
  AND counterparty_party_id IS NULL AND counterparty_name IS NULL AND counterparty_key IS NULL AND reference_total IS NULL)
 OR (doc_kind IN ('PURCHASE','SALE') AND cash_amount IS NOT NULL AND cash_amount > 0 AND weight_grams IS NOT NULL AND weight_grams > 0
  AND ((counterparty_kind = 'REGISTERED_PARTY' AND counterparty_party_id IS NOT NULL) OR (counterparty_kind = 'THIRD_PARTY' AND counterparty_party_id IS NULL))
  AND counterparty_name IS NOT NULL AND CHAR_LENGTH(TRIM(counterparty_name)) > 0 AND counterparty_key IS NOT NULL AND CHAR_LENGTH(TRIM(counterparty_key)) > 0));

DELETE FROM schema_migrations WHERE version = 78;
