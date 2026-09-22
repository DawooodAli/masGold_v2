-- masGold v2
-- OLD_STOCK -> NEW_STOCK conversion audit
-- Run once.
--
-- Existing inventory rows are not deleted.
-- Source OLD_STOCK pieces become REMOVED only when a conversion succeeds.

ALTER TABLE inventory_items
    MODIFY acquisition_source ENUM(
        'MARKET_PURCHASE',
        'OLD_GOLD_MANUFACTURE',
        'OLD_STOCK_CONVERSION',
        'LEGACY_MIGRATION',
        'MANUAL_ENTRY'
    ) NOT NULL;


CREATE TABLE IF NOT EXISTS inventory_conversion_batches (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    conversion_code VARCHAR(50) NOT NULL,
    result_inventory_item_id BIGINT UNSIGNED NOT NULL,
    source_total_weight_grams DECIMAL(12,3) NOT NULL,
    result_weight_grams DECIMAL(12,3) NOT NULL,
    weight_difference_grams DECIMAL(12,3) NOT NULL,
    performed_by_employee_id INT UNSIGNED NOT NULL,
    performed_by_user_id INT UNSIGNED NULL,
    notes VARCHAR(1000) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_inventory_conversion_code (conversion_code),
    UNIQUE KEY uq_inventory_conversion_result (result_inventory_item_id),
    KEY ix_inventory_conversion_created_at (created_at),
    KEY ix_inventory_conversion_employee (performed_by_employee_id),

    CONSTRAINT fk_inventory_conversion_result
        FOREIGN KEY (result_inventory_item_id)
        REFERENCES inventory_items(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_inventory_conversion_employee
        FOREIGN KEY (performed_by_employee_id)
        REFERENCES employees(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;


CREATE TABLE IF NOT EXISTS inventory_conversion_sources (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    conversion_batch_id BIGINT UNSIGNED NOT NULL,
    source_inventory_item_id BIGINT UNSIGNED NOT NULL,
    source_weight_grams DECIMAL(12,3) NOT NULL,
    source_barcode_value VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    -- One physical source piece can be consumed only once.
    UNIQUE KEY uq_inventory_conversion_source_item (
        source_inventory_item_id
    ),

    KEY ix_inventory_conversion_sources_batch (
        conversion_batch_id
    ),

    CONSTRAINT fk_inventory_conversion_sources_batch
        FOREIGN KEY (conversion_batch_id)
        REFERENCES inventory_conversion_batches(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_inventory_conversion_sources_item
        FOREIGN KEY (source_inventory_item_id)
        REFERENCES inventory_items(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;
