USE masgold_v2;

-- ============================================================
-- masGold v2
-- Migration 006: Billing Core
--
-- Principles:
--
-- 1. Finalized invoice commercial data is immutable.
-- 2. Historical bills are reproducible from frozen snapshots.
-- 3. Payments are separate from the invoice itself.
-- 4. Cash, bank and mixed payments are supported.
-- 5. Customer remainder is NOT fake cash.
--    It goes to customer_transactions.
-- 6. Payment corrections preserve history.
-- 7. "Sell again" creates a NEW invoice linked to the old one.
-- ============================================================


-- ------------------------------------------------------------
-- 1. Bank accounts
--
-- Bank balances are recorded, but they are NOT part of JRD.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bank_accounts (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(50) DEFAULT NULL,
    name VARCHAR(150) NOT NULL,

    bank_name VARCHAR(150) DEFAULT NULL,
    account_label VARCHAR(150) DEFAULT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    notes VARCHAR(500) DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_bank_accounts_code (code),

    KEY idx_bank_accounts_active (is_active)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 2. Payment methods
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS payment_methods (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,

    affects_physical_cash BOOLEAN NOT NULL DEFAULT FALSE,
    requires_bank_account BOOLEAN NOT NULL DEFAULT FALSE,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    sort_order SMALLINT UNSIGNED NOT NULL DEFAULT 0,

    PRIMARY KEY (id),

    UNIQUE KEY uq_payment_methods_code (code)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 3. Invoices
--
-- invoice_type:
--
-- SELL  = shop sells jewelry
-- BUY   = shop buys old gold / items
-- MIXED = both sides in same bill
--
-- net_direction tells who must pay after sale/buy offset.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS invoices (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    invoice_number VARCHAR(100) NOT NULL,

    invoice_type VARCHAR(20) NOT NULL,

    invoice_status VARCHAR(20)
        NOT NULL DEFAULT 'DRAFT',

    customer_id BIGINT UNSIGNED DEFAULT NULL,
    employee_id INT UNSIGNED DEFAULT NULL,

    invoice_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Total value sold by shop.
    sell_total DECIMAL(16,2) NOT NULL DEFAULT 0.00,

    -- Total value purchased by shop.
    buy_total DECIMAL(16,2) NOT NULL DEFAULT 0.00,

    -- Absolute final settlement amount.
    net_amount DECIMAL(16,2) NOT NULL DEFAULT 0.00,

    net_direction VARCHAR(30)
        NOT NULL DEFAULT 'SETTLED',

    settlement_status VARCHAR(20)
        NOT NULL DEFAULT 'UNSETTLED',

    notes VARCHAR(1000) DEFAULT NULL,

    -- "Use old bill as new bill"
    source_invoice_id BIGINT UNSIGNED DEFAULT NULL,

    -- Historical migration information.
    legacy_source_system VARCHAR(100) DEFAULT NULL,
    legacy_bill_number VARCHAR(100) DEFAULT NULL,

    legacy_completeness VARCHAR(20) DEFAULT NULL,

    created_by_user_id INT UNSIGNED DEFAULT NULL,
    finalized_by_user_id INT UNSIGNED DEFAULT NULL,
    voided_by_user_id INT UNSIGNED DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finalized_at DATETIME DEFAULT NULL,
    voided_at DATETIME DEFAULT NULL,

    void_reason VARCHAR(1000) DEFAULT NULL,

    PRIMARY KEY (id),

    UNIQUE KEY uq_invoices_number (
        invoice_number
    ),

    KEY idx_invoices_date (
        invoice_date
    ),

    KEY idx_invoices_customer (
        customer_id
    ),

    KEY idx_invoices_employee (
        employee_id
    ),

    KEY idx_invoices_type (
        invoice_type
    ),

    KEY idx_invoices_status (
        invoice_status
    ),

    KEY idx_invoices_legacy_bill (
        legacy_bill_number
    ),

    KEY idx_invoices_source_invoice (
        source_invoice_id
    ),

    CONSTRAINT fk_invoice_customer
        FOREIGN KEY (customer_id)
        REFERENCES customers(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_invoice_employee
        FOREIGN KEY (employee_id)
        REFERENCES employees(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_invoice_source_invoice
        FOREIGN KEY (source_invoice_id)
        REFERENCES invoices(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_invoice_created_user
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_invoice_finalized_user
        FOREIGN KEY (finalized_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_invoice_voided_user
        FOREIGN KEY (voided_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_invoice_type
        CHECK (
            invoice_type IN (
                'SELL',
                'BUY',
                'MIXED'
            )
        ),

    CONSTRAINT chk_invoice_status
        CHECK (
            invoice_status IN (
                'DRAFT',
                'FINALIZED',
                'VOIDED'
            )
        ),

    CONSTRAINT chk_invoice_net_direction
        CHECK (
            net_direction IN (
                'CUSTOMER_PAYS_SHOP',
                'SHOP_PAYS_CUSTOMER',
                'SETTLED'
            )
        ),

    CONSTRAINT chk_invoice_settlement_status
        CHECK (
            settlement_status IN (
                'UNSETTLED',
                'PARTIAL',
                'SETTLED',
                'OVERPAID'
            )
        ),

    CONSTRAINT chk_invoice_totals
        CHECK (
            sell_total >= 0
            AND buy_total >= 0
            AND net_amount >= 0
        ),

    CONSTRAINT chk_invoice_legacy_completeness
        CHECK (
            legacy_completeness IS NULL
            OR legacy_completeness IN (
                'COMPLETE',
                'PARTIAL',
                'UNKNOWN'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 4. Invoice lines
--
-- Frozen historical line information.
--
-- We do NOT depend on today's factory/category/item names
-- to reproduce an old bill.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS invoice_lines (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    invoice_id BIGINT UNSIGNED NOT NULL,

    line_number SMALLINT UNSIGNED NOT NULL,

    line_type VARCHAR(30) NOT NULL,

    -- Present when selling a known physical inventory item.
    inventory_item_id BIGINT UNSIGNED DEFAULT NULL,

    karat_id TINYINT UNSIGNED NOT NULL,

    weight_grams DECIMAL(12,3) NOT NULL,

    quantity INT UNSIGNED NOT NULL DEFAULT 1,

    -- Frozen descriptive snapshots
    item_name_snapshot VARCHAR(150) DEFAULT NULL,
    factory_snapshot VARCHAR(150) DEFAULT NULL,
    category_snapshot VARCHAR(50) DEFAULT NULL,
    size_snapshot VARCHAR(50) DEFAULT NULL,
    barcode_snapshot VARCHAR(100) DEFAULT NULL,

    -- Frozen economics
    gold_price_per_gram DECIMAL(14,4) NOT NULL,

    making_per_gram DECIMAL(12,2) DEFAULT NULL,
    making_total DECIMAL(16,2) DEFAULT NULL,

    line_total DECIMAL(16,2) NOT NULL,

    notes VARCHAR(1000) DEFAULT NULL,

    PRIMARY KEY (id),

    UNIQUE KEY uq_invoice_line_number (
        invoice_id,
        line_number
    ),

    KEY idx_invoice_lines_invoice (
        invoice_id
    ),

    KEY idx_invoice_lines_inventory (
        inventory_item_id
    ),

    KEY idx_invoice_lines_karat (
        karat_id
    ),

    KEY idx_invoice_lines_type (
        line_type
    ),

    CONSTRAINT fk_invoice_line_invoice
        FOREIGN KEY (invoice_id)
        REFERENCES invoices(id),

    CONSTRAINT fk_invoice_line_inventory
        FOREIGN KEY (inventory_item_id)
        REFERENCES inventory_items(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_invoice_line_karat
        FOREIGN KEY (karat_id)
        REFERENCES karats(id),

    CONSTRAINT chk_invoice_line_type
        CHECK (
            line_type IN (
                'SELL_INVENTORY',
                'BUY_OLD_GOLD',
                'OTHER'
            )
        ),

    CONSTRAINT chk_invoice_line_weight
        CHECK (weight_grams > 0),

    CONSTRAINT chk_invoice_line_quantity
        CHECK (quantity > 0),

    CONSTRAINT chk_invoice_line_gold_price
        CHECK (gold_price_per_gram > 0),

    CONSTRAINT chk_invoice_line_making
        CHECK (
            making_per_gram IS NULL
            OR making_per_gram >= 0
        ),

    CONSTRAINT chk_invoice_line_making_total
        CHECK (
            making_total IS NULL
            OR making_total >= 0
        ),

    CONSTRAINT chk_invoice_line_total
        CHECK (line_total >= 0)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 5. Frozen customer snapshot
--
-- If customer later changes phone/name/place,
-- historical invoice still reproduces exactly.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS invoice_customer_snapshots (
    invoice_id BIGINT UNSIGNED NOT NULL,

    customer_id BIGINT UNSIGNED DEFAULT NULL,

    customer_code_snapshot VARCHAR(50) DEFAULT NULL,
    customer_name_snapshot VARCHAR(150) DEFAULT NULL,
    mobile_snapshot VARCHAR(50) DEFAULT NULL,
    place_snapshot VARCHAR(150) DEFAULT NULL,
    customer_type_snapshot VARCHAR(100) DEFAULT NULL,

    PRIMARY KEY (invoice_id),

    CONSTRAINT fk_invoice_customer_snapshot_invoice
        FOREIGN KEY (invoice_id)
        REFERENCES invoices(id),

    CONSTRAINT fk_invoice_customer_snapshot_customer
        FOREIGN KEY (customer_id)
        REFERENCES customers(id)
        ON DELETE SET NULL
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 6. Frozen gold-price snapshot
--
-- Keeps exact 18K / 21K / 24K shop prices used/available
-- when the invoice was finalized.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS invoice_price_snapshots (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    invoice_id BIGINT UNSIGNED NOT NULL,
    karat_id TINYINT UNSIGNED NOT NULL,

    shop_buy_price_per_gram DECIMAL(14,4) DEFAULT NULL,
    shop_sell_price_per_gram DECIMAL(14,4) DEFAULT NULL,

    quote_id BIGINT UNSIGNED DEFAULT NULL,

    source_code_snapshot VARCHAR(50) DEFAULT NULL,
    source_name_snapshot VARCHAR(150) DEFAULT NULL,

    provider_timestamp DATETIME DEFAULT NULL,
    captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_invoice_price_karat (
        invoice_id,
        karat_id
    ),

    CONSTRAINT fk_invoice_price_invoice
        FOREIGN KEY (invoice_id)
        REFERENCES invoices(id),

    CONSTRAINT fk_invoice_price_karat
        FOREIGN KEY (karat_id)
        REFERENCES karats(id),

    CONSTRAINT fk_invoice_price_quote
        FOREIGN KEY (quote_id)
        REFERENCES gold_price_quotes(id)
        ON DELETE SET NULL
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 7. Invoice payments
--
-- This records ACTUAL money movement only.
--
-- Customer remainder is NOT inserted here.
-- It belongs in customer_transactions.
--
-- Examples:
--
-- Cash received:
--     direction = IN
--
-- Bank received:
--     direction = IN
--
-- Paying customer:
--     direction = OUT
--
-- Correct cash->bank:
--
-- Original:
--     CASH IN 50000
--
-- Correction:
--     CASH OUT 50000   (reverses original)
--     BANK IN 50000
--
-- Nothing is silently deleted.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS invoice_payments (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    invoice_id BIGINT UNSIGNED NOT NULL,

    payment_method_id SMALLINT UNSIGNED NOT NULL,

    bank_account_id SMALLINT UNSIGNED DEFAULT NULL,

    direction VARCHAR(10) NOT NULL,

    amount DECIMAL(16,2) NOT NULL,

    payment_reference VARCHAR(150) DEFAULT NULL,

    -- Used for correction/reversal history.
    reverses_payment_id BIGINT UNSIGNED DEFAULT NULL,

    correction_group_id CHAR(36) DEFAULT NULL,

    notes VARCHAR(1000) DEFAULT NULL,

    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    created_by_user_id INT UNSIGNED DEFAULT NULL,

    PRIMARY KEY (id),

    KEY idx_invoice_payments_invoice (
        invoice_id
    ),

    KEY idx_invoice_payments_method (
        payment_method_id
    ),

    KEY idx_invoice_payments_bank (
        bank_account_id
    ),

    KEY idx_invoice_payments_date (
        occurred_at
    ),

    KEY idx_invoice_payments_correction (
        correction_group_id
    ),

    CONSTRAINT fk_invoice_payment_invoice
        FOREIGN KEY (invoice_id)
        REFERENCES invoices(id),

    CONSTRAINT fk_invoice_payment_method
        FOREIGN KEY (payment_method_id)
        REFERENCES payment_methods(id),

    CONSTRAINT fk_invoice_payment_bank
        FOREIGN KEY (bank_account_id)
        REFERENCES bank_accounts(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_invoice_payment_reversal
        FOREIGN KEY (reverses_payment_id)
        REFERENCES invoice_payments(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_invoice_payment_user
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_invoice_payment_direction
        CHECK (
            direction IN (
                'IN',
                'OUT'
            )
        ),

    CONSTRAINT chk_invoice_payment_amount
        CHECK (amount > 0)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 8. Settlement view
--
-- expected_shop_effect:
-- positive = money should enter shop
-- negative = money should leave shop
--
-- remaining_effect:
-- positive = customer still owes shop
-- negative = shop still owes customer
-- ------------------------------------------------------------

CREATE OR REPLACE VIEW vw_invoice_settlement AS

SELECT
    i.id AS invoice_id,
    i.invoice_number,
    i.customer_id,
    i.invoice_type,
    i.invoice_status,
    i.net_direction,
    i.net_amount,

    CAST(
        CASE
            WHEN i.net_direction = 'CUSTOMER_PAYS_SHOP'
                THEN i.net_amount

            WHEN i.net_direction = 'SHOP_PAYS_CUSTOMER'
                THEN -i.net_amount

            ELSE 0
        END
        AS DECIMAL(16,2)
    ) AS expected_shop_effect,

    CAST(
        COALESCE(
            SUM(
                CASE
                    WHEN ip.direction = 'IN'
                        THEN ip.amount
                    WHEN ip.direction = 'OUT'
                        THEN -ip.amount
                    ELSE 0
                END
            ),
            0
        )
        AS DECIMAL(16,2)
    ) AS actual_payment_effect,

    CAST(
        (
            CASE
                WHEN i.net_direction = 'CUSTOMER_PAYS_SHOP'
                    THEN i.net_amount

                WHEN i.net_direction = 'SHOP_PAYS_CUSTOMER'
                    THEN -i.net_amount

                ELSE 0
            END
        )
        -
        COALESCE(
            SUM(
                CASE
                    WHEN ip.direction = 'IN'
                        THEN ip.amount
                    WHEN ip.direction = 'OUT'
                        THEN -ip.amount
                    ELSE 0
                END
            ),
            0
        )
        AS DECIMAL(16,2)
    ) AS remaining_effect

FROM invoices i

LEFT JOIN invoice_payments ip
    ON ip.invoice_id = i.id

GROUP BY
    i.id,
    i.invoice_number,
    i.customer_id,
    i.invoice_type,
    i.invoice_status,
    i.net_direction,
    i.net_amount;


-- ------------------------------------------------------------
-- 9. Archived invoice documents
--
-- PDF is not accounting truth.
-- Database is accounting truth.
--
-- But archived PDF guarantees exact historical reproduction.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS invoice_documents (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    invoice_id BIGINT UNSIGNED NOT NULL,

    document_type VARCHAR(30) NOT NULL,

    storage_path VARCHAR(1000) NOT NULL,

    sha256 CHAR(64) DEFAULT NULL,

    template_version VARCHAR(50) DEFAULT NULL,

    generated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    generated_by_user_id INT UNSIGNED DEFAULT NULL,

    PRIMARY KEY (id),

    KEY idx_invoice_documents_invoice (
        invoice_id
    ),

    KEY idx_invoice_documents_type (
        document_type
    ),

    CONSTRAINT fk_invoice_document_invoice
        FOREIGN KEY (invoice_id)
        REFERENCES invoices(id),

    CONSTRAINT fk_invoice_document_user
        FOREIGN KEY (generated_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_invoice_document_type
        CHECK (
            document_type IN (
                'ORIGINAL_FINALIZED',
                'REPRINT_RENDER',
                'SETTLEMENT_RECEIPT',
                'LEGACY_ARCHIVE'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 10. Invoice event/audit timeline
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS invoice_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    invoice_id BIGINT UNSIGNED NOT NULL,

    event_type VARCHAR(40) NOT NULL,

    related_invoice_id BIGINT UNSIGNED DEFAULT NULL,

    notes VARCHAR(1000) DEFAULT NULL,
    details_json JSON DEFAULT NULL,

    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    performed_by_user_id INT UNSIGNED DEFAULT NULL,

    PRIMARY KEY (id),

    KEY idx_invoice_events_invoice (
        invoice_id
    ),

    KEY idx_invoice_events_type (
        event_type
    ),

    KEY idx_invoice_events_date (
        occurred_at
    ),

    CONSTRAINT fk_invoice_event_invoice
        FOREIGN KEY (invoice_id)
        REFERENCES invoices(id),

    CONSTRAINT fk_invoice_event_related_invoice
        FOREIGN KEY (related_invoice_id)
        REFERENCES invoices(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_invoice_event_user
        FOREIGN KEY (performed_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_invoice_event_type
        CHECK (
            event_type IN (
                'CREATED',
                'FINALIZED',
                'PRINTED',
                'REPRINTED',
                'PAYMENT_ADDED',
                'PAYMENT_CORRECTED',
                'COPIED_TO_NEW_BILL',
                'VOIDED',
                'MIGRATED'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- INITIAL PAYMENT METHODS
-- ============================================================

INSERT INTO payment_methods (
    code,
    name,
    affects_physical_cash,
    requires_bank_account,
    sort_order
)
VALUES

    (
        'PHYSICAL_CASH',
        'Physical Cash',
        TRUE,
        FALSE,
        1
    ),

    (
        'BANK_TRANSFER',
        'Bank Transfer',
        FALSE,
        TRUE,
        2
    ),

    (
        'INSTAPAY',
        'InstaPay',
        FALSE,
        TRUE,
        3
    ),

    (
        'CARD_POS',
        'Card / POS',
        FALSE,
        TRUE,
        4
    ),

    (
        'OTHER',
        'Other',
        FALSE,
        FALSE,
        99
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    affects_physical_cash = VALUES(affects_physical_cash),
    requires_bank_account = VALUES(requires_bank_account),
    sort_order = VALUES(sort_order);


-- ============================================================
-- BILLING PERMISSIONS
-- ============================================================

INSERT INTO permissions (
    code,
    name,
    description
)
VALUES

    (
        'INVOICE_VIEW',
        'View Invoices',
        'Allows viewing current and historical invoices'
    ),

    (
        'INVOICE_CREATE',
        'Create Invoice',
        'Allows creating billing drafts'
    ),

    (
        'INVOICE_FINALIZE',
        'Finalize Invoice',
        'Allows finalizing an invoice'
    ),

    (
        'INVOICE_REPRINT',
        'Reprint Invoice',
        'Allows invoice reprinting'
    ),

    (
        'INVOICE_CLONE',
        'Use Invoice as New Bill',
        'Allows copying an old invoice into a new draft'
    ),

    (
        'INVOICE_VOID',
        'Void Invoice',
        'Allows authorized invoice voiding'
    ),

    (
        'PAYMENT_RECORD',
        'Record Invoice Payment',
        'Allows cash or bank settlement entries'
    ),

    (
        'PAYMENT_CORRECT',
        'Correct Invoice Payment',
        'Allows correction of payment method or amount'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);


-- Owner receives only permissions not already assigned.

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
    6,
    'Billing, immutable invoice snapshots, payments and archives'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);