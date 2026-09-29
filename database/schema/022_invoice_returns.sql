-- ============================================================
-- masGold v2
-- Migration 022: Return V1 (مرتجع)
--
-- A Return is its own transaction linked to one finalized
-- invoice; the invoice is never edited. Header states:
--   DRAFT / PENDING_APPROVAL / APPROVED / REJECTED   no effect yet
--   FINALIZED                                       every effect, once
--   CANCELED                                        before finalize only
-- Lines are written only at FINALIZE (they ARE the consumption of
-- the original sale line):
--   SELL_INVENTORY  the whole identified piece (UNIQUE per source
--                   line): SOLD → RETURNED_PENDING + inspection review
--   SELL_OLD_GOLD   F7 weight, full or partial, back into the COUNTED
--                   old-gold stock (its own RETURN_OLD_GOLD_IN)
-- Gold is valued at the CURRENT SHOP_BUY of each karat (snapshot kept
-- per line); historical making is refunded only inside 72 hours.
-- Real money leaves the shop only (OUT legs); an explicit part may go
-- to the customer's account (RETURN_CREDIT).
-- Management reviews gain RETURN_APPROVAL (after 72 hours) and
-- inspections that come from a Return line.
--
-- The target database is chosen by the caller; this file never
-- names a database. Re-runnable and additive.
--
-- Rollback: rollback/022_invoice_returns_rollback.sql
-- ============================================================


SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. PREFLIGHT
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 21),
    'SELECT 1 FROM MIGRATION_022_REFUSED_migration_021_missing',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. RETURN TRANSACTIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS invoice_returns (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    return_status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    source_invoice_id BIGINT UNSIGNED NOT NULL,
    source_finalized_at DATETIME NOT NULL,
    customer_id BIGINT UNSIGNED DEFAULT NULL,
    -- The chosen source lines / weights and their fingerprint (an approval binds to it).
    selection_json JSON NOT NULL,
    selection_hash CHAR(64) NOT NULL,
    making_window_open BOOLEAN NOT NULL,
    requires_approval BOOLEAN NOT NULL,
    return_deadline_at DATETIME NOT NULL,
    gold_amount DECIMAL(16,2) NOT NULL DEFAULT 0.00,
    making_refund DECIMAL(16,2) NOT NULL DEFAULT 0.00,
    total_amount DECIMAL(16,2) NOT NULL DEFAULT 0.00,
    money_paid DECIMAL(16,2) NOT NULL DEFAULT 0.00,
    account_amount DECIMAL(16,2) NOT NULL DEFAULT 0.00,
    price_snapshot_json JSON DEFAULT NULL,
    details_json JSON DEFAULT NULL,
    notes VARCHAR(1000) DEFAULT NULL,
    created_by_user_id INT UNSIGNED NOT NULL,
    created_by_employee_id INT UNSIGNED DEFAULT NULL,
    created_at DATETIME NOT NULL,
    finalized_by_user_id INT UNSIGNED DEFAULT NULL,
    finalized_at DATETIME DEFAULT NULL,
    canceled_by_user_id INT UNSIGNED DEFAULT NULL,
    canceled_at DATETIME DEFAULT NULL,
    PRIMARY KEY (id),
    KEY idx_return_source_invoice (source_invoice_id),
    KEY idx_return_status (return_status),
    KEY idx_return_finalized (finalized_at),
    CONSTRAINT fk_return_source_invoice FOREIGN KEY (source_invoice_id) REFERENCES invoices(id),
    CONSTRAINT fk_return_customer FOREIGN KEY (customer_id) REFERENCES customers(id),
    CONSTRAINT fk_return_created_by FOREIGN KEY (created_by_user_id) REFERENCES users(id),
    CONSTRAINT fk_return_employee FOREIGN KEY (created_by_employee_id) REFERENCES employees(id),
    CONSTRAINT fk_return_finalized_by FOREIGN KEY (finalized_by_user_id) REFERENCES users(id),
    CONSTRAINT fk_return_canceled_by FOREIGN KEY (canceled_by_user_id) REFERENCES users(id),
    CONSTRAINT chk_return_status CHECK (
        return_status IN ('DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'FINALIZED', 'CANCELED')
    ),
    CONSTRAINT chk_return_finalized CHECK (
        (return_status = 'FINALIZED') = (finalized_at IS NOT NULL)
        AND (return_status <> 'FINALIZED'
             OR (finalized_by_user_id IS NOT NULL AND price_snapshot_json IS NOT NULL))
    ),
    CONSTRAINT chk_return_canceled CHECK (
        (return_status = 'CANCELED') = (canceled_at IS NOT NULL)
        AND (canceled_at IS NULL OR canceled_by_user_id IS NOT NULL)
    ),
    -- Inside 72 hours: making refund, no approval. After: gold only, manager approval.
    CONSTRAINT chk_return_window CHECK (
        requires_approval <> making_window_open
        AND (making_refund = 0 OR making_window_open)
        AND (return_status NOT IN ('PENDING_APPROVAL', 'APPROVED', 'REJECTED') OR requires_approval)
    ),
    CONSTRAINT chk_return_amounts CHECK (
        gold_amount >= 0 AND making_refund >= 0 AND money_paid >= 0 AND account_amount >= 0
        AND total_amount = gold_amount + making_refund
        AND (return_status <> 'FINALIZED' OR (total_amount > 0 AND money_paid + account_amount = total_amount))
        AND (return_status = 'FINALIZED' OR (money_paid = 0 AND account_amount = 0))
    )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- One row per returned source line, written at FINALIZE. An identified piece is always
-- returned whole and once (UNIQUE); F7 weight may be returned in several parts.
CREATE TABLE IF NOT EXISTS invoice_return_lines (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    return_id BIGINT UNSIGNED NOT NULL,
    line_number SMALLINT UNSIGNED NOT NULL,
    source_invoice_line_id BIGINT UNSIGNED NOT NULL,
    line_type VARCHAR(30) NOT NULL,
    inventory_item_id BIGINT UNSIGNED DEFAULT NULL,
    old_gold_movement_id BIGINT UNSIGNED DEFAULT NULL,
    karat_id TINYINT UNSIGNED NOT NULL,
    karat_value TINYINT UNSIGNED NOT NULL,
    source_weight_grams DECIMAL(10,3) NOT NULL,
    weight_grams DECIMAL(10,3) NOT NULL,
    shop_buy_price_per_gram DECIMAL(14,4) NOT NULL,
    price_quote_id BIGINT UNSIGNED DEFAULT NULL,
    price_provider_timestamp DATETIME DEFAULT NULL,
    gold_amount DECIMAL(16,2) NOT NULL,
    historical_making_total DECIMAL(16,2) DEFAULT NULL,
    making_refund DECIMAL(16,2) NOT NULL DEFAULT 0.00,
    item_name_snapshot VARCHAR(150) DEFAULT NULL,
    barcode_snapshot VARCHAR(100) DEFAULT NULL,
    identified_source_line_id BIGINT UNSIGNED
        GENERATED ALWAYS AS (CASE WHEN inventory_item_id IS NOT NULL THEN source_invoice_line_id ELSE NULL END) STORED,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_return_line_number (return_id, line_number),
    UNIQUE KEY uq_return_identified_source_line (identified_source_line_id),
    UNIQUE KEY uq_return_line_old_gold_movement (old_gold_movement_id),
    KEY idx_return_line_source (source_invoice_line_id),
    KEY idx_return_line_item (inventory_item_id),
    CONSTRAINT fk_return_line_return FOREIGN KEY (return_id) REFERENCES invoice_returns(id),
    CONSTRAINT fk_return_line_source FOREIGN KEY (source_invoice_line_id) REFERENCES invoice_lines(id),
    CONSTRAINT fk_return_line_item FOREIGN KEY (inventory_item_id) REFERENCES inventory_items(id),
    CONSTRAINT fk_return_line_old_gold FOREIGN KEY (old_gold_movement_id) REFERENCES old_gold_movements(id),
    CONSTRAINT fk_return_line_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
    CONSTRAINT chk_return_line_source CHECK (
        (line_type = 'SELL_INVENTORY' AND inventory_item_id IS NOT NULL AND old_gold_movement_id IS NULL
         AND weight_grams = source_weight_grams)
        OR (line_type = 'SELL_OLD_GOLD' AND inventory_item_id IS NULL AND old_gold_movement_id IS NOT NULL)
    ),
    CONSTRAINT chk_return_line_amounts CHECK (
        weight_grams > 0 AND weight_grams <= source_weight_grams
        AND shop_buy_price_per_gram > 0 AND gold_amount >= 0 AND making_refund >= 0
        AND (making_refund = 0 OR historical_making_total IS NOT NULL)
    )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- Real money paid to the customer only (never an offset or a balance).
CREATE TABLE IF NOT EXISTS invoice_return_payments (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    return_id BIGINT UNSIGNED NOT NULL,
    payment_method_id SMALLINT UNSIGNED NOT NULL,
    bank_account_id SMALLINT UNSIGNED DEFAULT NULL,
    direction VARCHAR(10) NOT NULL DEFAULT 'OUT',
    amount DECIMAL(16,2) NOT NULL,
    payment_reference VARCHAR(150) DEFAULT NULL,
    cash_movement_id BIGINT UNSIGNED DEFAULT NULL,
    bank_movement_id BIGINT UNSIGNED DEFAULT NULL,
    occurred_at DATETIME NOT NULL,
    created_by_user_id INT UNSIGNED NOT NULL,
    PRIMARY KEY (id),
    KEY idx_return_payment_return (return_id),
    UNIQUE KEY uq_return_payment_cash (cash_movement_id),
    UNIQUE KEY uq_return_payment_bank (bank_movement_id),
    CONSTRAINT fk_return_payment_return FOREIGN KEY (return_id) REFERENCES invoice_returns(id),
    CONSTRAINT fk_return_payment_method FOREIGN KEY (payment_method_id) REFERENCES payment_methods(id),
    CONSTRAINT fk_return_payment_bank_account FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(id),
    CONSTRAINT fk_return_payment_cash FOREIGN KEY (cash_movement_id) REFERENCES cash_movements(id),
    CONSTRAINT fk_return_payment_bank FOREIGN KEY (bank_movement_id) REFERENCES bank_movements(id),
    CONSTRAINT fk_return_payment_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
    CONSTRAINT chk_return_payment_direction CHECK (direction = 'OUT'),
    CONSTRAINT chk_return_payment_amount CHECK (amount > 0),
    CONSTRAINT chk_return_payment_ledger CHECK (
        (cash_movement_id IS NULL) <> (bank_movement_id IS NULL)
    )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 2. MANAGEMENT REVIEWS: RETURN_APPROVAL and Return inspections
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'management_reviews'
               AND tc.CONSTRAINT_NAME = 'chk_management_review_type'
               AND cc.CHECK_CLAUSE NOT LIKE '%RETURN_APPROVAL%'),
    "ALTER TABLE management_reviews DROP CHECK chk_management_review_type",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND CONSTRAINT_NAME = 'chk_management_review_type'),
    "ALTER TABLE management_reviews ADD CONSTRAINT chk_management_review_type CHECK (
        review_type IN ('MAKING_PRICE', 'CASH_SETTLEMENT', 'OLD_GOLD_DISCOUNT', 'RETURNED_ITEM_INSPECTION',
                        'RETURN_APPROVAL')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND COLUMN_NAME = 'return_id'),
    "ALTER TABLE management_reviews ADD COLUMN return_id BIGINT UNSIGNED NULL AFTER lifecycle_line_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND COLUMN_NAME = 'return_line_id'),
    "ALTER TABLE management_reviews ADD COLUMN return_line_id BIGINT UNSIGNED NULL AFTER return_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND INDEX_NAME = 'uq_management_review_return_line'),
    "ALTER TABLE management_reviews ADD UNIQUE KEY uq_management_review_return_line (return_line_id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND INDEX_NAME = 'idx_management_review_return'),
    "ALTER TABLE management_reviews ADD KEY idx_management_review_return (return_id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND CONSTRAINT_NAME = 'fk_management_review_return'),
    "ALTER TABLE management_reviews ADD CONSTRAINT fk_management_review_return
        FOREIGN KEY (return_id) REFERENCES invoice_returns(id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND CONSTRAINT_NAME = 'fk_management_review_return_line'),
    "ALTER TABLE management_reviews ADD CONSTRAINT fk_management_review_return_line
        FOREIGN KEY (return_line_id) REFERENCES invoice_return_lines(id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- The 021 rule, extended: an inspection comes from exactly one exchange line OR one return
-- line; a RETURN_APPROVAL belongs to one Return and ends APPROVED / REJECTED (or DISMISSED when
-- the Return is canceled); every other review type carries none of these links or codes.
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'management_reviews'
               AND tc.CONSTRAINT_NAME = 'chk_management_review_inspection'
               AND cc.CHECK_CLAUSE NOT LIKE '%RETURN_APPROVAL%'),
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
                  AND resolution_code IN ('RETURN_TO_STOCK', 'CONVERT_TO_OLD_GOLD'))))
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
-- 3. INVOICE EVENT: RETURNED (original invoice timeline)
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'invoice_events'
               AND tc.CONSTRAINT_NAME = 'chk_invoice_event_type' AND cc.CHECK_CLAUSE NOT LIKE '%RETURNED%'),
    "ALTER TABLE invoice_events DROP CHECK chk_invoice_event_type",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_events'
                   AND CONSTRAINT_NAME = 'chk_invoice_event_type'),
    "ALTER TABLE invoice_events ADD CONSTRAINT chk_invoice_event_type CHECK (
        event_type IN ('CREATED', 'FINALIZED', 'PRINTED', 'REPRINTED', 'PAYMENT_ADDED', 'PAYMENT_CORRECTED',
                       'COPIED_TO_NEW_BILL', 'VOIDED', 'MIGRATED', 'EXCHANGED', 'RETURNED')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 4. MOVEMENT AND LEDGER CODES
-- ============================================================

INSERT INTO inventory_movement_types (
    code,
    name,
    description
)
VALUES
    (
        'INVOICE_RETURN_IN',
        'Invoice Return In',
        'A sold piece returned by the customer (Return); waits for inspection (RETURNED_PENDING)'
    )
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
    (
        'RETURN_OLD_GOLD_IN',
        'Return Old Gold In',
        'Weight sold from the old-gold stock (F7) and returned by the customer (Return)',
        'IN'
    )
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
    ('RETURN_PAYMENT_OUT', 'Return Payment Out', 'OUT', 'Physical cash paid to the customer for a Return')
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
    ('RETURN_PAYMENT_OUT', 'Return Payment Out', 'OUT', 'Bank / wallet money paid to the customer for a Return')
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
    ('RETURN_CREDIT', 'Return Credit', 'DECREASE',
     'Return amount the shop owes the customer, kept on the customer account (explicit choice)')
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    expected_direction = VALUES(expected_direction),
    description = VALUES(description);


-- ============================================================
-- 5. PERMISSION (an employee Return; approval stays MANAGEMENT_REVIEW)
-- ============================================================

INSERT INTO permissions (
    code,
    name,
    description
)
VALUES
    (
        'INVOICE_RETURN',
        'Return Invoice Items',
        'Return sold pieces / old-gold weight of a finalized invoice'
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
    ON p.code IN ('INVOICE_RETURN')
WHERE NOT EXISTS (SELECT 1 FROM role_permissions rp
                   WHERE rp.role_id = r.id AND rp.permission_id = p.id)
  -- The operational sales roles execute a Return; approving one after 72 hours stays
  -- MANAGEMENT_REVIEW (OWNER / MANAGER, 016), which this migration never grants.
  AND r.code IN ('OWNER', 'MANAGER', 'SALESPERSON', 'CASHIER');


-- ============================================================
-- REGISTER MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    22,
    'Return V1: return transactions, lines, payments, approval and return inspections'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);


SET SESSION sql_mode = @masgold_saved_sql_mode;
