USE masgold_v2;

-- ============================================================
-- masGold v2
-- Migration 007: Physical Cash + Bank Ledgers
--
-- Physical cash and bank balances are completely separate.
--
-- JRD later uses physical cash only.
--
-- Current balances are calculated from permanent movements.
-- We never overwrite a "current balance" field.
-- ============================================================


-- ============================================================
-- PHYSICAL CASH
-- ============================================================


-- ------------------------------------------------------------
-- 1. Physical cash accounts
--
-- Supports one safe today and multiple drawers/safes later.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cash_accounts (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(50) NOT NULL,
    name VARCHAR(150) NOT NULL,

    -- Normally TRUE for every real physical safe/drawer.
    include_in_jrd BOOLEAN NOT NULL DEFAULT TRUE,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    notes VARCHAR(500) DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_cash_accounts_code (code)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 2. Physical cash movement types
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cash_movement_types (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(60) NOT NULL,
    name VARCHAR(150) NOT NULL,

    expected_direction VARCHAR(10) NOT NULL,

    description VARCHAR(500) DEFAULT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    PRIMARY KEY (id),

    UNIQUE KEY uq_cash_movement_type_code (code),

    CONSTRAINT chk_cash_movement_direction
        CHECK (
            expected_direction IN (
                'IN',
                'OUT',
                'EITHER'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 3. Physical cash movements
--
-- Positive = physical cash increases
-- Negative = physical cash decreases
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cash_movements (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    cash_account_id SMALLINT UNSIGNED NOT NULL,

    movement_type_id SMALLINT UNSIGNED NOT NULL,

    amount_delta DECIMAL(16,2) NOT NULL,

    -- When movement comes directly from invoice settlement.
    invoice_payment_id BIGINT UNSIGNED DEFAULT NULL,

    -- When movement settles customer balance outside invoice.
    customer_transaction_id BIGINT UNSIGNED DEFAULT NULL,

    -- Allows cash <-> bank movements to be tied together.
    transfer_group_id CHAR(36) DEFAULT NULL,

    reference_type VARCHAR(50) DEFAULT NULL,
    reference_id BIGINT UNSIGNED DEFAULT NULL,
    reference_code VARCHAR(100) DEFAULT NULL,

    description VARCHAR(500) DEFAULT NULL,
    notes VARCHAR(1000) DEFAULT NULL,

    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    created_by_user_id INT UNSIGNED DEFAULT NULL,

    legacy_source_table VARCHAR(50) DEFAULT NULL,
    legacy_source_id BIGINT UNSIGNED DEFAULT NULL,

    metadata_json JSON DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_cash_invoice_payment (
        invoice_payment_id
    ),

    KEY idx_cash_account (
        cash_account_id
    ),

    KEY idx_cash_date (
        occurred_at
    ),

    KEY idx_cash_type (
        movement_type_id
    ),

    KEY idx_cash_customer_transaction (
        customer_transaction_id
    ),

    KEY idx_cash_transfer_group (
        transfer_group_id
    ),

    KEY idx_cash_reference (
        reference_type,
        reference_id
    ),

    KEY idx_cash_legacy (
        legacy_source_table,
        legacy_source_id
    ),

    CONSTRAINT fk_cash_movement_account
        FOREIGN KEY (cash_account_id)
        REFERENCES cash_accounts(id),

    CONSTRAINT fk_cash_movement_type
        FOREIGN KEY (movement_type_id)
        REFERENCES cash_movement_types(id),

    CONSTRAINT fk_cash_invoice_payment
        FOREIGN KEY (invoice_payment_id)
        REFERENCES invoice_payments(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_cash_customer_transaction
        FOREIGN KEY (customer_transaction_id)
        REFERENCES customer_transactions(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_cash_movement_user
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_cash_amount_nonzero
        CHECK (amount_delta <> 0)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 4. Current physical cash balances
-- ------------------------------------------------------------

CREATE OR REPLACE VIEW vw_cash_balances AS

SELECT
    ca.id AS cash_account_id,
    ca.code,
    ca.name,
    ca.include_in_jrd,
    ca.is_active,

    CAST(
        COALESCE(
            SUM(cm.amount_delta),
            0
        )
        AS DECIMAL(18,2)
    ) AS balance

FROM cash_accounts ca

LEFT JOIN cash_movements cm
    ON cm.cash_account_id = ca.id

GROUP BY
    ca.id,
    ca.code,
    ca.name,
    ca.include_in_jrd,
    ca.is_active;


-- ============================================================
-- BANK
-- ============================================================


-- ------------------------------------------------------------
-- 5. Bank movement types
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bank_movement_types (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(60) NOT NULL,
    name VARCHAR(150) NOT NULL,

    expected_direction VARCHAR(10) NOT NULL,

    description VARCHAR(500) DEFAULT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    PRIMARY KEY (id),

    UNIQUE KEY uq_bank_movement_type_code (code),

    CONSTRAINT chk_bank_movement_direction
        CHECK (
            expected_direction IN (
                'IN',
                'OUT',
                'EITHER'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 6. Bank movements
--
-- Positive = bank account increases
-- Negative = bank account decreases
--
-- Bank balance does NOT enter JRD.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bank_movements (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    bank_account_id SMALLINT UNSIGNED NOT NULL,

    movement_type_id SMALLINT UNSIGNED NOT NULL,

    amount_delta DECIMAL(16,2) NOT NULL,

    invoice_payment_id BIGINT UNSIGNED DEFAULT NULL,

    customer_transaction_id BIGINT UNSIGNED DEFAULT NULL,

    transfer_group_id CHAR(36) DEFAULT NULL,

    payment_reference VARCHAR(150) DEFAULT NULL,

    reference_type VARCHAR(50) DEFAULT NULL,
    reference_id BIGINT UNSIGNED DEFAULT NULL,
    reference_code VARCHAR(100) DEFAULT NULL,

    description VARCHAR(500) DEFAULT NULL,
    notes VARCHAR(1000) DEFAULT NULL,

    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    created_by_user_id INT UNSIGNED DEFAULT NULL,

    legacy_source_table VARCHAR(50) DEFAULT NULL,
    legacy_source_id BIGINT UNSIGNED DEFAULT NULL,

    metadata_json JSON DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_bank_invoice_payment (
        invoice_payment_id
    ),

    KEY idx_bank_account (
        bank_account_id
    ),

    KEY idx_bank_date (
        occurred_at
    ),

    KEY idx_bank_type (
        movement_type_id
    ),

    KEY idx_bank_customer_transaction (
        customer_transaction_id
    ),

    KEY idx_bank_transfer_group (
        transfer_group_id
    ),

    KEY idx_bank_reference (
        reference_type,
        reference_id
    ),

    KEY idx_bank_legacy (
        legacy_source_table,
        legacy_source_id
    ),

    CONSTRAINT fk_bank_movement_account
        FOREIGN KEY (bank_account_id)
        REFERENCES bank_accounts(id),

    CONSTRAINT fk_bank_movement_type
        FOREIGN KEY (movement_type_id)
        REFERENCES bank_movement_types(id),

    CONSTRAINT fk_bank_invoice_payment
        FOREIGN KEY (invoice_payment_id)
        REFERENCES invoice_payments(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_bank_customer_transaction
        FOREIGN KEY (customer_transaction_id)
        REFERENCES customer_transactions(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_bank_movement_user
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_bank_amount_nonzero
        CHECK (amount_delta <> 0)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 7. Current bank balances
-- ------------------------------------------------------------

CREATE OR REPLACE VIEW vw_bank_balances AS

SELECT
    ba.id AS bank_account_id,
    ba.code,
    ba.name,
    ba.bank_name,
    ba.account_label,
    ba.is_active,

    CAST(
        COALESCE(
            SUM(bm.amount_delta),
            0
        )
        AS DECIMAL(18,2)
    ) AS balance

FROM bank_accounts ba

LEFT JOIN bank_movements bm
    ON bm.bank_account_id = ba.id

GROUP BY
    ba.id,
    ba.code,
    ba.name,
    ba.bank_name,
    ba.account_label,
    ba.is_active;


-- ============================================================
-- INITIAL PHYSICAL CASH ACCOUNT
-- ============================================================

INSERT INTO cash_accounts (
    code,
    name,
    include_in_jrd,
    is_active
)
VALUES (
    'MAIN_SAFE',
    'Main Safe',
    TRUE,
    TRUE
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    include_in_jrd = VALUES(include_in_jrd),
    is_active = VALUES(is_active);


-- ============================================================
-- CASH MOVEMENT TYPES
-- ============================================================

INSERT INTO cash_movement_types (
    code,
    name,
    expected_direction,
    description
)
VALUES

    (
        'INVOICE_PAYMENT_IN',
        'Invoice Cash Received',
        'IN',
        'Physical cash received as invoice settlement'
    ),

    (
        'INVOICE_PAYMENT_OUT',
        'Invoice Cash Paid',
        'OUT',
        'Physical cash paid as invoice settlement'
    ),

    (
        'CUSTOMER_PAYMENT_IN',
        'Customer Balance Payment',
        'IN',
        'Cash received against existing customer balance'
    ),

    (
        'SHOP_PAYMENT_OUT',
        'Payment to Customer',
        'OUT',
        'Cash paid against money owed to customer'
    ),

    (
        'MARKET_IN',
        'Market Cash In',
        'IN',
        'Physical cash received from market operation'
    ),

    (
        'MARKET_OUT',
        'Market Cash Out',
        'OUT',
        'Physical cash paid for market operation'
    ),

    (
        'BANK_WITHDRAWAL_IN',
        'Bank Withdrawal to Cash',
        'IN',
        'Money withdrawn from bank and placed into physical cash'
    ),

    (
        'BANK_DEPOSIT_OUT',
        'Cash Deposit to Bank',
        'OUT',
        'Physical cash deposited into a bank account'
    ),

    (
        'EXPENSE_OUT',
        'Expense',
        'OUT',
        'Operating expense paid with physical cash'
    ),

    (
        'CAPITAL_IN',
        'Owner Capital In',
        'IN',
        'Owner adds physical cash to business'
    ),

    (
        'CAPITAL_OUT',
        'Owner Capital Out',
        'OUT',
        'Owner removes physical cash from business'
    ),

    (
        'ADJUSTMENT_IN',
        'Positive Cash Adjustment',
        'IN',
        'Authorized physical cash increase'
    ),

    (
        'ADJUSTMENT_OUT',
        'Negative Cash Adjustment',
        'OUT',
        'Authorized physical cash decrease'
    ),

    (
        'LEGACY_IN',
        'Legacy Cash In',
        'IN',
        'Historical incoming cash migrated from production'
    ),

    (
        'LEGACY_OUT',
        'Legacy Cash Out',
        'OUT',
        'Historical outgoing cash migrated from production'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    expected_direction = VALUES(expected_direction),
    description = VALUES(description);


-- ============================================================
-- BANK MOVEMENT TYPES
-- ============================================================

INSERT INTO bank_movement_types (
    code,
    name,
    expected_direction,
    description
)
VALUES

    (
        'INVOICE_PAYMENT_IN',
        'Invoice Bank Payment Received',
        'IN',
        'Bank payment received as invoice settlement'
    ),

    (
        'INVOICE_PAYMENT_OUT',
        'Invoice Bank Payment Out',
        'OUT',
        'Bank payment made as invoice settlement'
    ),

    (
        'CUSTOMER_PAYMENT_IN',
        'Customer Balance Bank Payment',
        'IN',
        'Bank transfer received against customer balance'
    ),

    (
        'SHOP_PAYMENT_OUT',
        'Bank Payment to Customer',
        'OUT',
        'Bank payment against money owed to customer'
    ),

    (
        'CASH_DEPOSIT_IN',
        'Physical Cash Deposited',
        'IN',
        'Physical cash deposited into this bank account'
    ),

    (
        'CASH_WITHDRAWAL_OUT',
        'Bank Withdrawal',
        'OUT',
        'Money withdrawn from this bank account into physical cash'
    ),

    (
        'BANK_FEE_OUT',
        'Bank Fee',
        'OUT',
        'Bank charge or fee'
    ),

    (
        'CAPITAL_IN',
        'Owner Bank Capital In',
        'IN',
        'Owner adds money directly to bank'
    ),

    (
        'CAPITAL_OUT',
        'Owner Bank Capital Out',
        'OUT',
        'Owner removes money directly from bank'
    ),

    (
        'ADJUSTMENT_IN',
        'Positive Bank Adjustment',
        'IN',
        'Authorized bank balance increase'
    ),

    (
        'ADJUSTMENT_OUT',
        'Negative Bank Adjustment',
        'OUT',
        'Authorized bank balance decrease'
    ),

    (
        'LEGACY_IN',
        'Legacy Bank In',
        'IN',
        'Historical incoming bank movement'
    ),

    (
        'LEGACY_OUT',
        'Legacy Bank Out',
        'OUT',
        'Historical outgoing bank movement'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    expected_direction = VALUES(expected_direction),
    description = VALUES(description);


-- ============================================================
-- PERMISSIONS
-- ============================================================

INSERT INTO permissions (
    code,
    name,
    description
)
VALUES

    (
        'CASH_VIEW',
        'View Physical Cash',
        'Allows viewing physical cash movements and balance'
    ),

    (
        'CASH_MANAGE',
        'Manage Physical Cash',
        'Allows normal physical cash operations'
    ),

    (
        'CASH_ADJUST',
        'Adjust Physical Cash',
        'Allows physical cash reconciliation adjustments'
    ),

    (
        'BANK_VIEW',
        'View Bank',
        'Allows viewing bank transactions and balances'
    ),

    (
        'BANK_MANAGE',
        'Manage Bank',
        'Allows bank transactions and transfers'
    ),

    (
        'BANK_ADJUST',
        'Adjust Bank',
        'Allows authorized bank balance adjustments'
    ),

    (
        'CASH_BANK_TRANSFER',
        'Transfer Cash and Bank',
        'Allows physical cash deposits to bank and bank withdrawals'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);


-- Owner receives only missing permissions.

INSERT INTO role_permissions (
    role_id,
    permission_id
)
SELECT
    r.id,
    p.id
FROM roles r
CROSS JOIN permissions p

LEFT JOIN role_permissions rp
    ON rp.role_id = r.id
   AND rp.permission_id = p.id

WHERE r.code = 'OWNER'
  AND rp.role_id IS NULL;


-- ============================================================
-- REGISTER MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    7,
    'Physical cash and bank movement ledgers'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);