-- ============================================================
-- masGold v2
-- Migration 023: Reversal / Correction V1
--
-- A finalized Invoice, Exchange or Return is never edited or
-- deleted. A manager (TRANSACTION_CORRECT) corrects it with a new,
-- FINALIZED, immutable correction linked to it:
--   FULL_REVERSAL          RV + id: negates every posted effect of
--                          the source exactly (money, customer
--                          ledger, inventory, old gold), once per
--                          source (UNIQUE)
--   SETTLEMENT_CORRECTION  FC + id: re-routes the money / customer-
--                          account allocation only; a chain per
--                          source (sequence 1..n, UNIQUE)
-- A reversed Exchange / Return line is linked to its reversal so the
-- original sale line becomes lifecycle-eligible again (the one-use
-- keys now count active lines only).
--
-- The target database is chosen by the caller; this file never
-- names a database. Re-runnable and additive.
--
-- Rollback: rollback/023_transaction_corrections_rollback.sql
-- ============================================================


SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. PREFLIGHT
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 22),
    'SELECT 1 FROM MIGRATION_023_REFUSED_migration_022_missing',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. CORRECTIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS transaction_corrections (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    correction_type VARCHAR(30) NOT NULL,
    correction_status VARCHAR(20) NOT NULL DEFAULT 'FINALIZED',
    source_type VARCHAR(20) NOT NULL,
    source_id BIGINT UNSIGNED NOT NULL,
    source_reference VARCHAR(30) NOT NULL,
    -- The original invoice (itself, or the one the Exchange / Return belongs to): History anchor.
    invoice_id BIGINT UNSIGNED NOT NULL,
    chain_sequence INT UNSIGNED DEFAULT NULL,
    previous_correction_id BIGINT UNSIGNED DEFAULT NULL,
    money_direction VARCHAR(3) DEFAULT NULL,
    settled_total DECIMAL(16,2) DEFAULT NULL,
    reason VARCHAR(1000) NOT NULL,
    before_json JSON DEFAULT NULL,
    after_json JSON DEFAULT NULL,
    effects_json JSON DEFAULT NULL,
    performed_by_user_id INT UNSIGNED NOT NULL,
    performed_by_employee_id INT UNSIGNED DEFAULT NULL,
    occurred_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reversal_key VARCHAR(40)
        GENERATED ALWAYS AS (CASE WHEN correction_type = 'FULL_REVERSAL'
                                  THEN CONCAT(source_type, ':', source_id) ELSE NULL END) STORED,
    settlement_key VARCHAR(60)
        GENERATED ALWAYS AS (CASE WHEN correction_type = 'SETTLEMENT_CORRECTION'
                                  THEN CONCAT(source_type, ':', source_id, ':', chain_sequence) ELSE NULL END) STORED,
    PRIMARY KEY (id),
    UNIQUE KEY uq_correction_one_reversal (reversal_key),
    UNIQUE KEY uq_correction_settlement_chain (settlement_key),
    KEY idx_correction_source (source_type, source_id),
    KEY idx_correction_invoice (invoice_id),
    KEY idx_correction_date (occurred_at),
    CONSTRAINT fk_correction_invoice FOREIGN KEY (invoice_id) REFERENCES invoices(id),
    CONSTRAINT fk_correction_previous FOREIGN KEY (previous_correction_id) REFERENCES transaction_corrections(id),
    CONSTRAINT fk_correction_user FOREIGN KEY (performed_by_user_id) REFERENCES users(id),
    CONSTRAINT fk_correction_employee FOREIGN KEY (performed_by_employee_id) REFERENCES employees(id),
    CONSTRAINT chk_correction_type CHECK (correction_type IN ('FULL_REVERSAL', 'SETTLEMENT_CORRECTION')),
    CONSTRAINT chk_correction_status CHECK (correction_status IN ('FINALIZED')),
    CONSTRAINT chk_correction_source CHECK (source_type IN ('INVOICE', 'EXCHANGE', 'RETURN')),
    -- A correction always carries its reason (never blank).
    CONSTRAINT chk_correction_reason CHECK (CHAR_LENGTH(TRIM(reason)) > 0),
    CONSTRAINT chk_correction_shape CHECK (
        (correction_type = 'FULL_REVERSAL' AND chain_sequence IS NULL AND after_json IS NULL)
        OR (correction_type = 'SETTLEMENT_CORRECTION' AND chain_sequence IS NOT NULL AND chain_sequence > 0
            AND after_json IS NOT NULL AND money_direction IN ('IN', 'OUT') AND settled_total > 0
            AND ((chain_sequence = 1) = (previous_correction_id IS NULL)))
    )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- Money / account legs of a correction:
--   EFFECTIVE  (settlement) the allocation in force after this correction (positive amounts)
--   POSTED     the signed ledger rows this correction wrote (cash / bank / customer)
CREATE TABLE IF NOT EXISTS transaction_correction_legs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    correction_id BIGINT UNSIGNED NOT NULL,
    leg_role VARCHAR(10) NOT NULL,
    leg_kind VARCHAR(10) NOT NULL,
    payment_method_code VARCHAR(30) DEFAULT NULL,
    bank_account_id SMALLINT UNSIGNED DEFAULT NULL,
    amount DECIMAL(16,2) NOT NULL,
    cash_movement_id BIGINT UNSIGNED DEFAULT NULL,
    bank_movement_id BIGINT UNSIGNED DEFAULT NULL,
    customer_transaction_id BIGINT UNSIGNED DEFAULT NULL,
    PRIMARY KEY (id),
    KEY idx_correction_leg_correction (correction_id),
    UNIQUE KEY uq_correction_leg_cash (cash_movement_id),
    UNIQUE KEY uq_correction_leg_bank (bank_movement_id),
    UNIQUE KEY uq_correction_leg_customer (customer_transaction_id),
    CONSTRAINT fk_correction_leg_correction FOREIGN KEY (correction_id) REFERENCES transaction_corrections(id),
    CONSTRAINT fk_correction_leg_bank_account FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(id),
    CONSTRAINT fk_correction_leg_cash FOREIGN KEY (cash_movement_id) REFERENCES cash_movements(id),
    CONSTRAINT fk_correction_leg_bank FOREIGN KEY (bank_movement_id) REFERENCES bank_movements(id),
    CONSTRAINT fk_correction_leg_customer FOREIGN KEY (customer_transaction_id) REFERENCES customer_transactions(id),
    CONSTRAINT chk_correction_leg_role CHECK (leg_role IN ('EFFECTIVE', 'POSTED') AND leg_kind IN ('MONEY', 'ACCOUNT')),
    CONSTRAINT chk_correction_leg_shape CHECK (
        (leg_role = 'EFFECTIVE' AND amount > 0 AND cash_movement_id IS NULL AND bank_movement_id IS NULL
         AND customer_transaction_id IS NULL
         AND ((leg_kind = 'MONEY') = (payment_method_code IS NOT NULL)))
        OR (leg_role = 'POSTED' AND amount <> 0
            AND ((leg_kind = 'MONEY' AND customer_transaction_id IS NULL
                  AND ((cash_movement_id IS NULL) <> (bank_movement_id IS NULL)))
                 OR (leg_kind = 'ACCOUNT' AND customer_transaction_id IS NOT NULL
                     AND cash_movement_id IS NULL AND bank_movement_id IS NULL)))
    )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 2. REVERSED LIFECYCLE LINES: the one-use keys count active lines only
-- ============================================================

-- Exchange lines (019): keep an ordinary index for the FK, then replace the one-use key.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND COLUMN_NAME = 'reversed_by_correction_id'),
    "ALTER TABLE invoice_lifecycle_lines ADD COLUMN reversed_by_correction_id BIGINT UNSIGNED NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND CONSTRAINT_NAME = 'fk_lifecycle_line_reversed_by'),
    "ALTER TABLE invoice_lifecycle_lines ADD CONSTRAINT fk_lifecycle_line_reversed_by
        FOREIGN KEY (reversed_by_correction_id) REFERENCES transaction_corrections(id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND COLUMN_NAME = 'active_source_line_id'),
    "ALTER TABLE invoice_lifecycle_lines ADD COLUMN active_source_line_id BIGINT UNSIGNED
        GENERATED ALWAYS AS (CASE WHEN reversed_by_correction_id IS NULL THEN source_invoice_line_id ELSE NULL END) STORED",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND INDEX_NAME = 'idx_lifecycle_line_source'),
    "ALTER TABLE invoice_lifecycle_lines ADD KEY idx_lifecycle_line_source (source_invoice_line_id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
                   AND INDEX_NAME = 'uq_lifecycle_active_source_line'),
    "ALTER TABLE invoice_lifecycle_lines ADD UNIQUE KEY uq_lifecycle_active_source_line (active_source_line_id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lifecycle_lines'
               AND INDEX_NAME = 'uq_lifecycle_source_line'),
    "ALTER TABLE invoice_lifecycle_lines DROP INDEX uq_lifecycle_source_line",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Return lines (022): the same, for the whole-identified-piece key.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_return_lines'
                   AND COLUMN_NAME = 'reversed_by_correction_id'),
    "ALTER TABLE invoice_return_lines ADD COLUMN reversed_by_correction_id BIGINT UNSIGNED NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_return_lines'
                   AND CONSTRAINT_NAME = 'fk_return_line_reversed_by'),
    "ALTER TABLE invoice_return_lines ADD CONSTRAINT fk_return_line_reversed_by
        FOREIGN KEY (reversed_by_correction_id) REFERENCES transaction_corrections(id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_return_lines'
                   AND COLUMN_NAME = 'active_identified_source_line_id'),
    "ALTER TABLE invoice_return_lines ADD COLUMN active_identified_source_line_id BIGINT UNSIGNED
        GENERATED ALWAYS AS (CASE WHEN inventory_item_id IS NOT NULL AND reversed_by_correction_id IS NULL
                                  THEN source_invoice_line_id ELSE NULL END) STORED",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_return_lines'
                   AND INDEX_NAME = 'uq_return_active_identified_source_line'),
    "ALTER TABLE invoice_return_lines ADD UNIQUE KEY uq_return_active_identified_source_line
        (active_identified_source_line_id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_return_lines'
               AND INDEX_NAME = 'uq_return_identified_source_line'),
    "ALTER TABLE invoice_return_lines DROP INDEX uq_return_identified_source_line",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 3. REVIEWS: an open inspection of a reversed Exchange / Return is DISMISSED
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'management_reviews'
               AND tc.CONSTRAINT_NAME = 'chk_management_review_inspection'
               -- 022's clause names RETURN_APPROVAL before its only DISMISSED; 023's inspection branch
               -- already has DISMISSED (first occurrence before RETURN_APPROVAL).
               AND LOCATE('DISMISSED', cc.CHECK_CLAUSE) > LOCATE('RETURN_APPROVAL', cc.CHECK_CLAUSE)),
    "ALTER TABLE management_reviews DROP CHECK chk_management_review_inspection",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND CONSTRAINT_NAME = 'chk_management_review_inspection'),
    "ALTER TABLE management_reviews ADD CONSTRAINT chk_management_review_inspection CHECK (
        (review_type = 'RETURNED_ITEM_INSPECTION'
         AND return_id IS NULL
         AND ((lifecycle_line_id IS NOT NULL AND return_line_id IS NULL)
              OR (lifecycle_line_id IS NULL AND return_line_id IS NOT NULL))
         AND ((review_status = 'OPEN' AND resolution_code IS NULL)
              OR (review_status = 'RESOLVED' AND resolution_code IS NOT NULL
                  AND resolution_code IN ('RETURN_TO_STOCK', 'CONVERT_TO_OLD_GOLD'))
              OR (review_status = 'DISMISSED' AND resolution_code IS NULL)))
        OR (review_type = 'RETURN_APPROVAL'
            AND return_id IS NOT NULL AND lifecycle_line_id IS NULL AND return_line_id IS NULL
            AND ((review_status = 'OPEN' AND resolution_code IS NULL)
                 OR (review_status = 'RESOLVED' AND resolution_code IS NOT NULL
                     AND resolution_code IN ('APPROVED', 'REJECTED'))
                 OR (review_status = 'DISMISSED' AND resolution_code IS NULL)))
        OR (review_type NOT IN ('RETURNED_ITEM_INSPECTION', 'RETURN_APPROVAL')
            AND lifecycle_line_id IS NULL AND return_id IS NULL AND return_line_id IS NULL
            AND resolution_code IS NULL)
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 4. INVOICE EVENTS: REVERSED / SETTLEMENT_CORRECTED (original invoice timeline)
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'invoice_events'
               AND tc.CONSTRAINT_NAME = 'chk_invoice_event_type'
               AND cc.CHECK_CLAUSE NOT LIKE '%SETTLEMENT_CORRECTED%'),
    "ALTER TABLE invoice_events DROP CHECK chk_invoice_event_type",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_events'
                   AND CONSTRAINT_NAME = 'chk_invoice_event_type'),
    "ALTER TABLE invoice_events ADD CONSTRAINT chk_invoice_event_type CHECK (
        event_type IN ('CREATED', 'FINALIZED', 'PRINTED', 'REPRINTED', 'PAYMENT_ADDED', 'PAYMENT_CORRECTED',
                       'COPIED_TO_NEW_BILL', 'VOIDED', 'MIGRATED', 'EXCHANGED', 'RETURNED', 'REVERSED',
                       'SETTLEMENT_CORRECTED')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 5. MOVEMENT AND LEDGER CODES (correction-specific, never ordinary sale / return / exchange codes)
-- ============================================================

INSERT INTO inventory_movement_types (
    code,
    name,
    description
)
VALUES
    ('REVERSAL_SALE_RESTOCK', 'Reversal Sale Restock',
     'A reversed invoice: the sold piece is back in stock (same item, same barcode)'),
    ('REVERSAL_RETURN_TO_SOLD', 'Reversal Return To Sold',
     'A reversed Return: the returned piece is sold again (RETURNED_PENDING -> SOLD)'),
    ('REVERSAL_EXCHANGE_REPLACEMENT_RESTOCK', 'Reversal Exchange Replacement Restock',
     'A reversed Exchange: the replacement piece is back in stock'),
    ('REVERSAL_EXCHANGE_RETURN_TO_SOLD', 'Reversal Exchange Return To Sold',
     'A reversed Exchange: the returned original piece is sold again (RETURNED_PENDING -> SOLD)')
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);

INSERT INTO old_gold_movement_types (
    code,
    name,
    description,
    expected_direction
)
VALUES
    ('REVERSAL_OLD_GOLD_SALE_IN', 'Reversal Old Gold Sale In',
     'A reversed invoice: weight sold from the old-gold stock (F7) comes back', 'IN'),
    ('REVERSAL_OLD_GOLD_PURCHASE_OUT', 'Reversal Old Gold Purchase Out',
     'A reversed invoice: old gold bought from the customer leaves the stock again', 'OUT'),
    ('REVERSAL_RETURN_OLD_GOLD_OUT', 'Reversal Return Old Gold Out',
     'A reversed Return: F7 weight the Return put back leaves the stock again', 'OUT'),
    ('REVERSAL_EXCHANGE_OLD_GOLD_IN', 'Reversal Exchange Old Gold In',
     'A reversed Exchange: F7 weight given as a replacement comes back', 'IN')
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description),
    expected_direction = VALUES(expected_direction);

INSERT INTO cash_movement_types (
    code,
    name,
    expected_direction,
    description
)
VALUES
    ('REVERSAL_PAYMENT', 'Reversal Payment', 'EITHER', 'Exact opposite of the money a reversed transaction moved'),
    ('SETTLEMENT_CORRECTION', 'Settlement Correction', 'EITHER', 'Money moved to the correct account / method')
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    expected_direction = VALUES(expected_direction),
    description = VALUES(description);

INSERT INTO bank_movement_types (
    code,
    name,
    expected_direction,
    description
)
VALUES
    ('REVERSAL_PAYMENT', 'Reversal Payment', 'EITHER', 'Exact opposite of the money a reversed transaction moved'),
    ('SETTLEMENT_CORRECTION', 'Settlement Correction', 'EITHER', 'Money moved to the correct account / method')
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    expected_direction = VALUES(expected_direction),
    description = VALUES(description);

INSERT INTO customer_transaction_types (
    code,
    name,
    expected_direction,
    description
)
VALUES
    ('REVERSAL_ACCOUNT', 'Reversal Account', 'EITHER',
     'Exact opposite of the customer-account effect of a reversed transaction'),
    ('SETTLEMENT_CORRECTION', 'Settlement Correction', 'EITHER',
     'Customer-account part re-allocated by a settlement correction')
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    expected_direction = VALUES(expected_direction),
    description = VALUES(description);


-- ============================================================
-- 6. PERMISSION (managers only; never SALESPERSON / CASHIER)
-- ============================================================

INSERT INTO permissions (
    code,
    name,
    description
)
VALUES
    (
        'TRANSACTION_CORRECT',
        'Correct Finalized Transactions',
        'Reverse or correct the settlement of a finalized invoice, exchange or return'
    )
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);

INSERT INTO role_permissions (
    role_id,
    permission_id
)
SELECT
    r.id,
    p.id
FROM roles r
JOIN permissions p
    ON p.code IN ('TRANSACTION_CORRECT')
WHERE NOT EXISTS (SELECT 1 FROM role_permissions rp
                   WHERE rp.role_id = r.id AND rp.permission_id = p.id)
  AND r.code IN ('OWNER', 'MANAGER');


-- ============================================================
-- REGISTER MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    23,
    'Reversal / Correction V1: full reversal, settlement correction, reversed lifecycle lines'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);


SET SESSION sql_mode = @masgold_saved_sql_mode;
