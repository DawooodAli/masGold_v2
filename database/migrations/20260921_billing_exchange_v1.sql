-- masGold v2 — Unified Billing V1
-- Same invoice can contain:
--   1) inventory sold to customer
--   2) old gold bought from customer
-- Net = sale_total - old_gold_total
--
-- This migration is additive. It does not modify historical production tables.

CREATE TABLE IF NOT EXISTS billing_invoice_sequences (
    sequence_date DATE NOT NULL,
    seq INT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY (sequence_date)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS billing_invoices (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    invoice_code VARCHAR(30) NOT NULL,

    -- customer_id is intentionally nullable and not FK-bound in V1.
    -- Customer master-data integration can bind it later without losing snapshots.
    customer_id BIGINT UNSIGNED NULL,
    customer_name_snapshot VARCHAR(150) NOT NULL,

    employee_id INT UNSIGNED NOT NULL,

    sell_price_21_per_gram DECIMAL(14,4) NOT NULL DEFAULT 0,
    buy_price_21_per_gram DECIMAL(14,4) NOT NULL DEFAULT 0,

    sale_total DECIMAL(16,2) NOT NULL DEFAULT 0,
    old_gold_total DECIMAL(16,2) NOT NULL DEFAULT 0,
    net_customer_due DECIMAL(16,2) NOT NULL DEFAULT 0,

    sale_weight_total_grams DECIMAL(12,3) NOT NULL DEFAULT 0,
    old_gold_weight_total_grams DECIMAL(12,3) NOT NULL DEFAULT 0,

    status VARCHAR(20) NOT NULL DEFAULT 'FINALIZED',
    notes VARCHAR(1000) NULL,

    finalized_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_billing_invoice_code (invoice_code),
    KEY ix_billing_invoice_customer (customer_id),
    KEY ix_billing_invoice_employee (employee_id),
    KEY ix_billing_invoice_finalized (finalized_at),

    CONSTRAINT fk_billing_invoice_employee
        FOREIGN KEY (employee_id)
        REFERENCES employees(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT chk_billing_invoice_status
        CHECK (status IN ('FINALIZED', 'REVERSED'))
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS billing_sale_lines (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    invoice_id BIGINT UNSIGNED NOT NULL,
    inventory_item_id BIGINT UNSIGNED NOT NULL,

    barcode_snapshot VARCHAR(100) NULL,
    item_type_snapshot VARCHAR(150) NOT NULL,
    karat_value_snapshot INT NOT NULL,

    weight_grams DECIMAL(12,3) NOT NULL,
    gold_price_per_gram DECIMAL(14,4) NOT NULL,
    making_per_gram DECIMAL(14,4) NOT NULL DEFAULT 0,
    line_total DECIMAL(16,2) NOT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY ix_billing_sale_inventory_item (inventory_item_id),
    KEY ix_billing_sale_invoice (invoice_id),

    CONSTRAINT fk_billing_sale_invoice
        FOREIGN KEY (invoice_id)
        REFERENCES billing_invoices(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_billing_sale_inventory_item
        FOREIGN KEY (inventory_item_id)
        REFERENCES inventory_items(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT chk_billing_sale_weight
        CHECK (weight_grams > 0),

    CONSTRAINT chk_billing_sale_price
        CHECK (gold_price_per_gram > 0),

    CONSTRAINT chk_billing_sale_making
        CHECK (making_per_gram >= 0),

    CONSTRAINT chk_billing_sale_total
        CHECK (line_total >= 0)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS billing_old_gold_lines (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    invoice_id BIGINT UNSIGNED NOT NULL,
    old_gold_movement_id BIGINT UNSIGNED NOT NULL,

    -- Confirmed from the current masGold v2 schema:
    -- karats.id = TINYINT UNSIGNED
    karat_id TINYINT UNSIGNED NOT NULL,
    karat_value_snapshot INT NOT NULL,

    weight_grams DECIMAL(12,3) NOT NULL,
    buy_price_per_gram DECIMAL(14,4) NOT NULL,
    line_total DECIMAL(16,2) NOT NULL,
    notes VARCHAR(1000) NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_billing_old_gold_movement (old_gold_movement_id),
    KEY ix_billing_old_gold_invoice (invoice_id),
    KEY ix_billing_old_gold_karat (karat_id),

    CONSTRAINT fk_billing_old_gold_invoice
        FOREIGN KEY (invoice_id)
        REFERENCES billing_invoices(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_billing_old_gold_movement
        FOREIGN KEY (old_gold_movement_id)
        REFERENCES old_gold_movements(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_billing_old_gold_karat
        FOREIGN KEY (karat_id)
        REFERENCES karats(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT chk_billing_old_gold_weight
        CHECK (weight_grams > 0),

    CONSTRAINT chk_billing_old_gold_price
        CHECK (buy_price_per_gram > 0),

    CONSTRAINT chk_billing_old_gold_total
        CHECK (line_total >= 0)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;
