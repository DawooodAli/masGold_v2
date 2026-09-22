-- masGold v2
-- Old Gold gram ledger support for "محولة من الكسر"
-- Run once.
--
-- IMPORTANT:
-- Old gold is NOT itemized inventory.
-- It is a signed gram ledger by karat:
--   positive weight_delta_grams = grams entering old gold
--   negative weight_delta_grams = grams leaving old gold
--
-- This migration is additive and does not delete existing rows.

CREATE TABLE IF NOT EXISTS old_gold_movements (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    karat_id INT UNSIGNED NOT NULL,
    weight_delta_grams DECIMAL(12,3) NOT NULL,
    movement_code VARCHAR(40) NOT NULL,
    customer_id BIGINT UNSIGNED NULL,
    invoice_id BIGINT UNSIGNED NULL,
    factory_id INT UNSIGNED NULL,
    related_inventory_item_id BIGINT UNSIGNED NULL,
    received_by_employee_id INT UNSIGNED NULL,
    performed_by_user_id INT UNSIGNED NULL,
    notes VARCHAR(1000) NULL,
    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY ix_old_gold_karat_date (karat_id, occurred_at),
    KEY ix_old_gold_movement_code (movement_code),
    KEY ix_old_gold_related_inventory (related_inventory_item_id),

    CONSTRAINT fk_old_gold_karat
        FOREIGN KEY (karat_id)
        REFERENCES karats(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_old_gold_employee
        FOREIGN KEY (received_by_employee_id)
        REFERENCES employees(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_old_gold_inventory_item
        FOREIGN KEY (related_inventory_item_id)
        REFERENCES inventory_items(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;


-- If old_gold_movements already existed in the v2 database,
-- add the canonical ledger columns only when they are missing.
DROP PROCEDURE IF EXISTS masgold_add_column_if_missing;

DELIMITER $$

CREATE PROCEDURE masgold_add_column_if_missing(
    IN p_table_name VARCHAR(64),
    IN p_column_name VARCHAR(64),
    IN p_column_definition TEXT
)
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = p_table_name
          AND column_name = p_column_name
    ) THEN
        SET @sql_text = CONCAT(
            'ALTER TABLE `',
            p_table_name,
            '` ADD COLUMN `',
            p_column_name,
            '` ',
            p_column_definition
        );

        PREPARE stmt FROM @sql_text;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$

DELIMITER ;

CALL masgold_add_column_if_missing(
    'old_gold_movements',
    'karat_id',
    'INT UNSIGNED NULL'
);

CALL masgold_add_column_if_missing(
    'old_gold_movements',
    'weight_delta_grams',
    'DECIMAL(12,3) NULL'
);

CALL masgold_add_column_if_missing(
    'old_gold_movements',
    'movement_code',
    'VARCHAR(40) NULL'
);

CALL masgold_add_column_if_missing(
    'old_gold_movements',
    'customer_id',
    'BIGINT UNSIGNED NULL'
);

CALL masgold_add_column_if_missing(
    'old_gold_movements',
    'invoice_id',
    'BIGINT UNSIGNED NULL'
);

CALL masgold_add_column_if_missing(
    'old_gold_movements',
    'factory_id',
    'INT UNSIGNED NULL'
);

CALL masgold_add_column_if_missing(
    'old_gold_movements',
    'related_inventory_item_id',
    'BIGINT UNSIGNED NULL'
);

CALL masgold_add_column_if_missing(
    'old_gold_movements',
    'received_by_employee_id',
    'INT UNSIGNED NULL'
);

CALL masgold_add_column_if_missing(
    'old_gold_movements',
    'performed_by_user_id',
    'INT UNSIGNED NULL'
);

CALL masgold_add_column_if_missing(
    'old_gold_movements',
    'notes',
    'VARCHAR(1000) NULL'
);

CALL masgold_add_column_if_missing(
    'old_gold_movements',
    'occurred_at',
    'DATETIME NULL DEFAULT CURRENT_TIMESTAMP'
);

CALL masgold_add_column_if_missing(
    'old_gold_movements',
    'created_at',
    'DATETIME NULL DEFAULT CURRENT_TIMESTAMP'
);

DROP PROCEDURE IF EXISTS masgold_add_column_if_missing;


CREATE TABLE IF NOT EXISTS old_gold_conversion_batches (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    conversion_code VARCHAR(50) NOT NULL,

    old_gold_movement_id BIGINT UNSIGNED NOT NULL,
    result_inventory_item_id BIGINT UNSIGNED NOT NULL,

    karat_id INT UNSIGNED NOT NULL,

    consumed_weight_grams DECIMAL(12,3) NOT NULL,
    result_weight_grams DECIMAL(12,3) NOT NULL,
    weight_difference_grams DECIMAL(12,3) NOT NULL,

    performed_by_employee_id INT UNSIGNED NOT NULL,
    performed_by_user_id INT UNSIGNED NULL,

    notes VARCHAR(1000) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_old_gold_conversion_code (conversion_code),
    UNIQUE KEY uq_old_gold_conversion_movement (old_gold_movement_id),
    UNIQUE KEY uq_old_gold_conversion_result (result_inventory_item_id),

    KEY ix_old_gold_conversion_karat (karat_id),
    KEY ix_old_gold_conversion_created_at (created_at),

    CONSTRAINT fk_old_gold_conversion_movement
        FOREIGN KEY (old_gold_movement_id)
        REFERENCES old_gold_movements(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_old_gold_conversion_result
        FOREIGN KEY (result_inventory_item_id)
        REFERENCES inventory_items(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_old_gold_conversion_karat
        FOREIGN KEY (karat_id)
        REFERENCES karats(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_old_gold_conversion_employee
        FOREIGN KEY (performed_by_employee_id)
        REFERENCES employees(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;
