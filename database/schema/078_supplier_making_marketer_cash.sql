-- Supplier making payments from marketer-held cash: the payment and the marketer cash outflow are immutable and linked.
-- The existing supplier making payment remains the canonical SM document; a SUPPLIER_PAY marketer document and its
-- cash-ledger movement explain the source. A single canonical RV reverses both ledgers atomically.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version = 77),
 'DO 0', 'SELECT * FROM MIGRATION_078_REFUSED_missing_077');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_making_payments' AND COLUMN_NAME='marketer_party_id'),
 'ALTER TABLE supplier_making_payments ADD COLUMN marketer_party_id INT UNSIGNED DEFAULT NULL AFTER bank_account_id', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_making_payments' AND COLUMN_NAME='self_payment_confirmed'),
 'ALTER TABLE supplier_making_payments ADD COLUMN self_payment_confirmed TINYINT(1) NOT NULL DEFAULT 0 AFTER marketer_party_id', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_making_payments' AND INDEX_NAME='idx_smp_marketer'),
 'ALTER TABLE supplier_making_payments ADD KEY idx_smp_marketer (marketer_party_id)', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_making_payments' AND CONSTRAINT_NAME='fk_smp_marketer_party'),
 'ALTER TABLE supplier_making_payments ADD CONSTRAINT fk_smp_marketer_party FOREIGN KEY (marketer_party_id) REFERENCES suppliers(id)', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_making_payments' AND INDEX_NAME='uq_smp_marketer_link'),
 'ALTER TABLE supplier_making_payments ADD UNIQUE KEY uq_smp_marketer_link (id, marketer_party_id, amount)', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='chk_smp_method' AND CHECK_CLAUSE NOT LIKE '%MARKETER%'),
 'ALTER TABLE supplier_making_payments DROP CHECK chk_smp_method', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='supplier_making_payments' AND CONSTRAINT_NAME='chk_smp_method'),
 "ALTER TABLE supplier_making_payments ADD CONSTRAINT chk_smp_method CHECK (
  (payment_method = 'CASH' AND cash_account_id IS NOT NULL AND bank_account_id IS NULL AND marketer_party_id IS NULL AND self_payment_confirmed = 0)
  OR (payment_method = 'BANK' AND bank_account_id IS NOT NULL AND cash_account_id IS NULL AND marketer_party_id IS NULL AND self_payment_confirmed = 0)
  OR (payment_method = 'CUSTODY' AND cash_account_id IS NULL AND bank_account_id IS NULL AND marketer_party_id IS NULL AND self_payment_confirmed = 0)
  OR (payment_method = 'MARKETER' AND cash_account_id IS NULL AND bank_account_id IS NULL AND marketer_party_id IS NOT NULL
      AND self_payment_confirmed IN (0,1)))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='chk_mkd_shape' AND CHECK_CLAUSE NOT LIKE '%SUPPLIER_PAY%'),
 'ALTER TABLE marketer_documents DROP CHECK chk_mkd_shape', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='marketer_documents' AND CONSTRAINT_NAME='chk_mkd_shape'),
 "ALTER TABLE marketer_documents ADD CONSTRAINT chk_mkd_shape CHECK (
  (doc_kind IN ('CASH_HANDOVER','CASH_RETURN') AND cash_amount IS NOT NULL AND cash_amount > 0 AND weight_grams IS NULL AND counterparty_kind IS NULL
   AND counterparty_party_id IS NULL AND counterparty_name IS NULL AND counterparty_key IS NULL AND reference_total IS NULL)
  OR (doc_kind = 'GOLD_RETURN' AND cash_amount IS NULL AND weight_grams IS NOT NULL AND weight_grams > 0 AND counterparty_kind IS NULL
   AND counterparty_party_id IS NULL AND counterparty_name IS NULL AND counterparty_key IS NULL AND reference_total IS NULL)
  OR (doc_kind IN ('PURCHASE','SALE') AND cash_amount IS NOT NULL AND cash_amount > 0 AND weight_grams IS NOT NULL AND weight_grams > 0
   AND ((counterparty_kind = 'REGISTERED_PARTY' AND counterparty_party_id IS NOT NULL) OR (counterparty_kind = 'THIRD_PARTY' AND counterparty_party_id IS NULL))
   AND counterparty_name IS NOT NULL AND CHAR_LENGTH(TRIM(counterparty_name)) > 0 AND counterparty_key IS NOT NULL AND CHAR_LENGTH(TRIM(counterparty_key)) > 0)
  OR (doc_kind = 'SUPPLIER_PAY' AND cash_amount IS NOT NULL AND cash_amount > 0 AND weight_grams IS NULL AND counterparty_kind IS NULL
   AND counterparty_party_id IS NULL AND counterparty_name IS NULL AND counterparty_key IS NULL AND reference_total IS NULL))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='chk_mkcm_shape' AND CHECK_CLAUSE NOT LIKE '%SUPPLIER_PAY%'),
 'ALTER TABLE marketer_cash_movements DROP CHECK chk_mkcm_shape', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='marketer_cash_movements' AND CONSTRAINT_NAME='chk_mkcm_shape'),
 "ALTER TABLE marketer_cash_movements ADD CONSTRAINT chk_mkcm_shape CHECK (cash_amount > 0 AND (
  (movement_type = 'CASH_HANDOVER' AND doc_kind = 'CASH_HANDOVER' AND amount_delta = cash_amount AND correction_id IS NULL)
  OR (movement_type = 'CASH_RETURN' AND doc_kind = 'CASH_RETURN' AND amount_delta = -cash_amount AND correction_id IS NULL)
  OR (movement_type = 'SALE_PROCEEDS' AND doc_kind = 'SALE' AND amount_delta = cash_amount AND correction_id IS NULL)
  OR (movement_type = 'PURCHASE_SPEND' AND doc_kind = 'PURCHASE' AND amount_delta = -cash_amount AND correction_id IS NULL)
  OR (movement_type = 'CASH_HANDOVER_REVERSAL' AND doc_kind = 'CASH_HANDOVER' AND amount_delta = -cash_amount AND correction_id IS NOT NULL)
  OR (movement_type = 'CASH_RETURN_REVERSAL' AND doc_kind = 'CASH_RETURN' AND amount_delta = cash_amount AND correction_id IS NOT NULL)
  OR (movement_type = 'SALE_PROCEEDS_REVERSAL' AND doc_kind = 'SALE' AND amount_delta = -cash_amount AND correction_id IS NOT NULL)
  OR (movement_type = 'PURCHASE_SPEND_REVERSAL' AND doc_kind = 'PURCHASE' AND amount_delta = cash_amount AND correction_id IS NOT NULL)
  OR (movement_type = 'SUPPLIER_PAY' AND doc_kind = 'SUPPLIER_PAY' AND amount_delta = -cash_amount AND correction_id IS NULL)
  OR (movement_type = 'SUPPLIER_PAY_REVERSAL' AND doc_kind = 'SUPPLIER_PAY' AND amount_delta = cash_amount AND correction_id IS NOT NULL)))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS supplier_making_marketer_payments (
 payment_id BIGINT UNSIGNED NOT NULL,
 marketer_document_id BIGINT UNSIGNED NOT NULL,
 doc_kind VARCHAR(14) NOT NULL DEFAULT 'SUPPLIER_PAY',
 marketer_party_id INT UNSIGNED NOT NULL,
 amount DECIMAL(16,2) NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (payment_id),
 UNIQUE KEY uq_smmp_document (marketer_document_id),
 KEY idx_smmp_marketer (marketer_party_id, payment_id),
 CONSTRAINT fk_smmp_payment FOREIGN KEY (payment_id, marketer_party_id, amount)
  REFERENCES supplier_making_payments(id, marketer_party_id, amount),
 CONSTRAINT fk_smmp_document FOREIGN KEY (marketer_document_id, doc_kind, marketer_party_id, amount)
  REFERENCES marketer_documents(id, doc_kind, party_id, cash_amount),
 CONSTRAINT chk_smmp_shape CHECK (doc_kind = 'SUPPLIER_PAY' AND amount > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version, name) VALUES
 (78, 'Supplier making payments from marketer-held cash with linked ledger and canonical reversal')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
