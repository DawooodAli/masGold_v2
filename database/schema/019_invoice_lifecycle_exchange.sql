-- ============================================================
-- masGold v2
-- Migration 019: invoice lifecycle transactions (Exchange V1)
--
-- An exchange (استبدال) is a lifecycle transaction linked to one
-- immutable finalized invoice; it never creates or edits an
-- invoice row, so it never counts as a sale:
--   1. inventory status RETURNED_PENDING (returned piece waiting
--      for inspection; never sellable)
--   2. inventory movement types EXCHANGE_RETURN_IN /
--      EXCHANGE_REPLACEMENT_OUT
--   3. invoice event EXCHANGED on the original invoice timeline
--   4. generic lifecycle transactions, lines (RETURNED /
--      REPLACEMENT; many-to-many) and real-money legs
--   5. cash / bank movement types EXCHANGE_PAYMENT_IN / _OUT and
--      customer ledger types EXCHANGE_BALANCE_DUE / EXCHANGE_CREDIT
--   6. permission INVOICE_EXCHANGE (OWNER)
--
-- The target database is chosen by the caller; this file never
-- names a database. Re-runnable and additive.
--
-- Rollback: rollback/019_invoice_lifecycle_exchange_rollback.sql
-- ============================================================


SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. PREFLIGHT
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 18),
    'SELECT 1 FROM MIGRATION_019_REFUSED_migration_018_missing',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. INVENTORY STATUS: RETURNED_PENDING
-- Each list is replaced only while it lacks the new status.
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'inventory_items'
               AND tc.CONSTRAINT_NAME = 'chk_inventory_status' AND cc.CHECK_CLAUSE NOT LIKE '%RETURNED_PENDING%'),
    "ALTER TABLE inventory_items DROP CHECK chk_inventory_status",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_items'
                   AND CONSTRAINT_NAME = 'chk_inventory_status'),
    "ALTER TABLE inventory_items ADD CONSTRAINT chk_inventory_status CHECK (
        inventory_status IN ('IN_STOCK', 'SOLD', 'SCRAPPED', 'REMOVED', 'RETURNED_PENDING')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'inventory_movements'
               AND tc.CONSTRAINT_NAME = 'chk_inventory_move_from_status'
               AND cc.CHECK_CLAUSE NOT LIKE '%RETURNED_PENDING%'),
    "ALTER TABLE inventory_movements DROP CHECK chk_inventory_move_from_status",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_movements'
                   AND CONSTRAINT_NAME = 'chk_inventory_move_from_status'),
    "ALTER TABLE inventory_movements ADD CONSTRAINT chk_inventory_move_from_status CHECK (
        from_inventory_status IS NULL
        OR from_inventory_status IN ('IN_STOCK', 'SOLD', 'SCRAPPED', 'REMOVED', 'RETURNED_PENDING')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'inventory_movements'
               AND tc.CONSTRAINT_NAME = 'chk_inventory_move_to_status'
               AND cc.CHECK_CLAUSE NOT LIKE '%RETURNED_PENDING%'),
    "ALTER TABLE inventory_movements DROP CHECK chk_inventory_move_to_status",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_movements'
                   AND CONSTRAINT_NAME = 'chk_inventory_move_to_status'),
    "ALTER TABLE inventory_movements ADD CONSTRAINT chk_inventory_move_to_status CHECK (
        to_inventory_status IS NULL
        OR to_inventory_status IN ('IN_STOCK', 'SOLD', 'SCRAPPED', 'REMOVED', 'RETURNED_PENDING')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 2. INVOICE EVENT: EXCHANGED (original invoice timeline)
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'invoice_events'
               AND tc.CONSTRAINT_NAME = 'chk_invoice_event_type' AND cc.CHECK_CLAUSE NOT LIKE '%EXCHANGED%'),
    "ALTER TABLE invoice_events DROP CHECK chk_invoice_event_type",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_events'
                   AND CONSTRAINT_NAME = 'chk_invoice_event_type'),
    "ALTER TABLE invoice_events ADD CONSTRAINT chk_invoice_event_type CHECK (
        event_type IN ('CREATED', 'FINALIZED', 'PRINTED', 'REPRINTED', 'PAYMENT_ADDED', 'PAYMENT_CORRECTED',
                       'COPIED_TO_NEW_BILL', 'VOIDED', 'MIGRATED', 'EXCHANGED')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 3. LIFECYCLE TRANSACTIONS
--
-- pure = weight x karat / 24 (6 decimals: exact for 18/21/24K); pure_delta =
-- replacement_pure - returned_pure. valued_delta is what is priced:
-- the actual weight difference (same karat) or the pure delta (mixed).
-- signed_amount > 0: the customer pays; < 0: the shop pays; 0: nothing.
-- money_received - money_paid + account_signed = signed_amount:
-- real money and the explicit customer-account part only.
-- ============================================================

CREATE TABLE IF NOT EXISTS invoice_lifecycle_transactions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    transaction_type VARCHAR(20) NOT NULL,
    transaction_status VARCHAR(20) NOT NULL DEFAULT 'FINALIZED',
    source_invoice_id BIGINT UNSIGNED NOT NULL,
    source_finalized_at DATETIME NOT NULL,
    customer_id BIGINT UNSIGNED DEFAULT NULL,
    returned_pure_grams DECIMAL(16,6) NOT NULL,
    replacement_pure_grams DECIMAL(16,6) NOT NULL,
    pure_delta_grams DECIMAL(16,6) NOT NULL,
    valuation_basis VARCHAR(20) NOT NULL,
    valued_delta_grams DECIMAL(16,6) NOT NULL,
    price_basis VARCHAR(20) DEFAULT NULL,
    price_karat_value TINYINT UNSIGNED DEFAULT NULL,
    price_per_gram DECIMAL(14,4) DEFAULT NULL,
    gold_amount DECIMAL(16,2) NOT NULL DEFAULT 0.00,
    making_rate_per_gram DECIMAL(14,4) DEFAULT NULL,
    making_charge DECIMAL(16,2) NOT NULL DEFAULT 0.00,
    making_refund DECIMAL(16,2) NOT NULL DEFAULT 0.00,
    making_refund_eligible BOOLEAN NOT NULL DEFAULT FALSE,
    signed_amount DECIMAL(16,2) NOT NULL,
    money_received DECIMAL(16,2) NOT NULL DEFAULT 0.00,
    money_paid DECIMAL(16,2) NOT NULL DEFAULT 0.00,
    account_signed DECIMAL(16,2) NOT NULL DEFAULT 0.00,
    price_snapshot_json JSON NOT NULL,
    details_json JSON DEFAULT NULL,
    notes VARCHAR(1000) DEFAULT NULL,
    performed_by_user_id INT UNSIGNED NOT NULL,
    performed_by_employee_id INT UNSIGNED DEFAULT NULL,
    occurred_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_lifecycle_source_invoice (source_invoice_id),
    KEY idx_lifecycle_customer (customer_id),
    KEY idx_lifecycle_date (occurred_at),
    CONSTRAINT fk_lifecycle_source_invoice FOREIGN KEY (source_invoice_id) REFERENCES invoices(id),
    CONSTRAINT fk_lifecycle_customer FOREIGN KEY (customer_id) REFERENCES customers(id),
    CONSTRAINT fk_lifecycle_user FOREIGN KEY (performed_by_user_id) REFERENCES users(id),
    CONSTRAINT fk_lifecycle_employee FOREIGN KEY (performed_by_employee_id) REFERENCES employees(id),
    CONSTRAINT chk_lifecycle_type CHECK (transaction_type IN ('EXCHANGE')),
    CONSTRAINT chk_lifecycle_status CHECK (transaction_status IN ('FINALIZED')),
    CONSTRAINT chk_lifecycle_pure CHECK (
        returned_pure_grams > 0
        AND replacement_pure_grams > 0
        AND pure_delta_grams = replacement_pure_grams - returned_pure_grams
    ),
    CONSTRAINT chk_lifecycle_amounts CHECK (
        gold_amount >= 0 AND making_charge >= 0 AND making_refund >= 0
        AND money_received >= 0 AND money_paid >= 0
        AND (making_charge = 0 OR valued_delta_grams > 0)
        AND (making_refund = 0 OR (valued_delta_grams < 0 AND making_refund_eligible))
        AND signed_amount = CASE
            WHEN valued_delta_grams > 0 THEN gold_amount + making_charge
            WHEN valued_delta_grams < 0 THEN -(gold_amount + making_refund)
            ELSE 0 END
        AND money_received - money_paid + account_signed = signed_amount
    ),
    -- SAME_KARAT: every piece has one karat; the actual weight difference is valued at that
    -- karat's own quote. PURE_24K: mixed karats; the pure delta is valued at the real 24K quote.
    CONSTRAINT chk_lifecycle_valuation CHECK (
        valuation_basis IN ('SAME_KARAT', 'PURE_24K')
        AND (valuation_basis <> 'PURE_24K' OR valued_delta_grams = pure_delta_grams)
    ),
    CONSTRAINT chk_lifecycle_price CHECK (
        (valued_delta_grams = 0 AND price_basis IS NULL AND gold_amount = 0)
        OR (valued_delta_grams > 0 AND price_basis = 'SHOP_SELL' AND price_per_gram > 0
            AND price_karat_value IS NOT NULL)
        OR (valued_delta_grams < 0 AND price_basis = 'SHOP_BUY' AND price_per_gram > 0
            AND price_karat_value IS NOT NULL)
    ),
    CONSTRAINT chk_lifecycle_price_karat CHECK (
        valuation_basis <> 'PURE_24K' OR price_karat_value IS NULL OR price_karat_value = 24
    )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- One row per physical piece. RETURNED consumes one original sale
-- line (at most once, whatever the lifecycle type); REPLACEMENT is a
-- stock piece leaving the shop.
CREATE TABLE IF NOT EXISTS invoice_lifecycle_lines (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    lifecycle_transaction_id BIGINT UNSIGNED NOT NULL,
    line_role VARCHAR(20) NOT NULL,
    line_number SMALLINT UNSIGNED NOT NULL,
    source_invoice_line_id BIGINT UNSIGNED DEFAULT NULL,
    inventory_item_id BIGINT UNSIGNED NOT NULL,
    karat_id TINYINT UNSIGNED NOT NULL,
    karat_value TINYINT UNSIGNED NOT NULL,
    weight_grams DECIMAL(10,3) NOT NULL,
    pure_weight_grams DECIMAL(14,6) NOT NULL,
    making_per_gram DECIMAL(12,2) DEFAULT NULL,
    making_per_pure_gram DECIMAL(14,4) DEFAULT NULL,
    making_total DECIMAL(16,2) DEFAULT NULL,
    item_name_snapshot VARCHAR(150) DEFAULT NULL,
    barcode_snapshot VARCHAR(50) DEFAULT NULL,
    identification_method VARCHAR(20) DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_lifecycle_line_number (lifecycle_transaction_id, line_number),
    UNIQUE KEY uq_lifecycle_source_line (source_invoice_line_id),
    KEY idx_lifecycle_line_item (inventory_item_id),
    CONSTRAINT fk_lifecycle_line_transaction FOREIGN KEY (lifecycle_transaction_id)
        REFERENCES invoice_lifecycle_transactions(id),
    CONSTRAINT fk_lifecycle_line_source FOREIGN KEY (source_invoice_line_id) REFERENCES invoice_lines(id),
    CONSTRAINT fk_lifecycle_line_item FOREIGN KEY (inventory_item_id) REFERENCES inventory_items(id),
    CONSTRAINT fk_lifecycle_line_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
    CONSTRAINT chk_lifecycle_line_role CHECK (line_role IN ('RETURNED', 'REPLACEMENT')),
    CONSTRAINT chk_lifecycle_line_source CHECK (
        (line_role = 'RETURNED') = (source_invoice_line_id IS NOT NULL)
    ),
    CONSTRAINT chk_lifecycle_line_weights CHECK (
        weight_grams > 0
        AND pure_weight_grams = ROUND(weight_grams * karat_value / 24, 6)
    ),
    CONSTRAINT chk_lifecycle_line_identification CHECK (
        identification_method IS NULL OR identification_method IN ('SCAN', 'SEARCH')
    )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- Real money only (never an offset or a balance), each leg posted in
-- the same transaction to its real ledger and linked here.
CREATE TABLE IF NOT EXISTS invoice_lifecycle_payments (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    lifecycle_transaction_id BIGINT UNSIGNED NOT NULL,
    payment_method_id SMALLINT UNSIGNED NOT NULL,
    bank_account_id SMALLINT UNSIGNED DEFAULT NULL,
    direction VARCHAR(10) NOT NULL,
    amount DECIMAL(16,2) NOT NULL,
    payment_reference VARCHAR(150) DEFAULT NULL,
    cash_movement_id BIGINT UNSIGNED DEFAULT NULL,
    bank_movement_id BIGINT UNSIGNED DEFAULT NULL,
    occurred_at DATETIME NOT NULL,
    created_by_user_id INT UNSIGNED NOT NULL,
    PRIMARY KEY (id),
    KEY idx_lifecycle_payment_transaction (lifecycle_transaction_id),
    UNIQUE KEY uq_lifecycle_payment_cash (cash_movement_id),
    UNIQUE KEY uq_lifecycle_payment_bank (bank_movement_id),
    CONSTRAINT fk_lifecycle_payment_transaction FOREIGN KEY (lifecycle_transaction_id)
        REFERENCES invoice_lifecycle_transactions(id),
    CONSTRAINT fk_lifecycle_payment_method FOREIGN KEY (payment_method_id) REFERENCES payment_methods(id),
    CONSTRAINT fk_lifecycle_payment_bank_account FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(id),
    CONSTRAINT fk_lifecycle_payment_cash FOREIGN KEY (cash_movement_id) REFERENCES cash_movements(id),
    CONSTRAINT fk_lifecycle_payment_bank FOREIGN KEY (bank_movement_id) REFERENCES bank_movements(id),
    CONSTRAINT fk_lifecycle_payment_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
    CONSTRAINT chk_lifecycle_payment_direction CHECK (direction IN ('IN', 'OUT')),
    CONSTRAINT chk_lifecycle_payment_amount CHECK (amount > 0),
    CONSTRAINT chk_lifecycle_payment_ledger CHECK (
        (cash_movement_id IS NULL) <> (bank_movement_id IS NULL)
    )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


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
        'EXCHANGE_RETURN_IN',
        'Exchange Return In',
        'A sold piece returned by the customer in an exchange; waits for inspection (RETURNED_PENDING)'
    ),
    (
        'EXCHANGE_REPLACEMENT_OUT',
        'Exchange Replacement Out',
        'A stock piece given to the customer in an exchange'
    )
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);

INSERT INTO cash_movement_types (
    code,
    name,
    expected_direction,
    description
)
VALUES
    ('EXCHANGE_PAYMENT_IN', 'Exchange Payment In', 'IN', 'Physical cash received for an exchange difference'),
    ('EXCHANGE_PAYMENT_OUT', 'Exchange Payment Out', 'OUT', 'Physical cash paid for an exchange difference')
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
    ('EXCHANGE_PAYMENT_IN', 'Exchange Payment In', 'IN', 'Bank / wallet money received for an exchange difference'),
    ('EXCHANGE_PAYMENT_OUT', 'Exchange Payment Out', 'OUT', 'Bank / wallet money paid for an exchange difference')
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
    ('EXCHANGE_BALANCE_DUE', 'Exchange Remainder', 'INCREASE',
     'Exchange difference the customer still owes the shop (explicit account choice)'),
    ('EXCHANGE_CREDIT', 'Exchange Credit', 'DECREASE',
     'Exchange difference the shop owes the customer (explicit account choice)')
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    expected_direction = VALUES(expected_direction),
    description = VALUES(description);


-- ============================================================
-- 5. PERMISSION (viewing invoice history never grants it)
-- ============================================================

INSERT INTO permissions (
    code,
    name,
    description
)
VALUES
    (
        'INVOICE_EXCHANGE',
        'Exchange Invoice Items',
        'Exchange sold pieces of a finalized invoice for stock pieces'
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
    ON p.code IN ('INVOICE_EXCHANGE')
WHERE NOT EXISTS (SELECT 1 FROM role_permissions rp
                   WHERE rp.role_id = r.id AND rp.permission_id = p.id)
  AND r.code IN ('OWNER');


-- ============================================================
-- REGISTER MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    19,
    'Invoice lifecycle: Exchange V1 (lifecycle transactions, RETURNED_PENDING, exchange codes)'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);


SET SESSION sql_mode = @masgold_saved_sql_mode;
