-- Supplier MAKING payments from market custody (دفع مصنعيات من العهدة): the OWNER pays a supplier's making (EGP) with cash that is
-- already in an OPEN market custody, so no second Main Safe / bank movement exists.
--   SUPPLIER_PAY  a new entry kind (MTxxxxxx-n). It consumes outstanding custody cash: custody cash = issued - returned - paid to
--                 suppliers over the entries that were not reversed.
--   payment       the existing supplier making document (SMxxxxxx, supplier_making_payments) with the new method CUSTODY: no cash or
--                 bank account, no MONEY leg; its one MAKING leg and its negative supplier-making movement are unchanged.
--   link          market_trip_supplier_payments: entry -> payment -> supplier, with the amount (insert-only, one row per entry and
--                 one per payment). The reversed state of the entry and of the SM document derive from the SAME correction record
--                 (the full reversal of the custody entry); the payment's own reversal path refuses a CUSTODY payment.
-- The supplier GOLD account is never touched. Every step is guarded, so a re-run changes nothing.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=42),
 'DO 0', 'SELECT * FROM MIGRATION_043_REFUSED_missing_042');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 1. the SUPPLIER_PAY entry kind
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%SUPPLIER_PAY%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entries' AND CONSTRAINT_NAME = 'chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 2. the CUSTODY payment method of a supplier making payment (no cash / bank account)
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_smp_method' AND CHECK_CLAUSE NOT LIKE '%CUSTODY%'),
 "ALTER TABLE supplier_making_payments DROP CHECK chk_smp_method", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'supplier_making_payments' AND CONSTRAINT_NAME = 'chk_smp_method'),
 "ALTER TABLE supplier_making_payments ADD CONSTRAINT chk_smp_method CHECK (
  (payment_method = 'CASH' AND cash_account_id IS NOT NULL AND bank_account_id IS NULL)
  OR (payment_method = 'BANK' AND bank_account_id IS NOT NULL AND cash_account_id IS NULL)
  OR (payment_method = 'CUSTODY' AND cash_account_id IS NULL AND bank_account_id IS NULL))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 3. the immutable link between the custody entry and the supplier payment (the service keeps amount = the payment's amount)
CREATE TABLE IF NOT EXISTS market_trip_supplier_payments (
 entry_id BIGINT UNSIGNED NOT NULL,
 payment_id BIGINT UNSIGNED NOT NULL,
 supplier_id INT UNSIGNED NOT NULL,
 amount DECIMAL(16,2) NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (entry_id),
 UNIQUE KEY uq_mtsp_payment (payment_id),
 KEY idx_mtsp_supplier (supplier_id),
 CONSTRAINT fk_mtsp_entry FOREIGN KEY (entry_id) REFERENCES market_trip_entries(id),
 CONSTRAINT fk_mtsp_payment FOREIGN KEY (payment_id) REFERENCES supplier_making_payments(id),
 CONSTRAINT fk_mtsp_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
 CONSTRAINT chk_mtsp_amount CHECK (amount > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (43,'Market-trip supplier making payments: SUPPLIER_PAY entries, CUSTODY method and the entry-payment link')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
