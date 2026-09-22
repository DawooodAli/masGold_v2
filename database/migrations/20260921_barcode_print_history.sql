-- masGold v2
-- Barcode print history / batch ledger
-- 2026-09-21
--
-- This migration is additive. It does not modify existing inventory/barcode rows.

CREATE TABLE IF NOT EXISTS barcode_print_jobs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    batch_code VARCHAR(50) NOT NULL,
    printer_name VARCHAR(150) NOT NULL,
    item_count INT UNSIGNED NOT NULL,
    physical_label_count INT UNSIGNED NOT NULL,
    reprint_of_job_id BIGINT UNSIGNED NULL,
    requested_by_user_id INT UNSIGNED NULL,
    requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_barcode_print_jobs_batch_code (batch_code),
    KEY ix_barcode_print_jobs_requested_at (requested_at),
    KEY ix_barcode_print_jobs_reprint_of (reprint_of_job_id),

    CONSTRAINT fk_barcode_print_jobs_reprint_of
        FOREIGN KEY (reprint_of_job_id)
        REFERENCES barcode_print_jobs(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;


CREATE TABLE IF NOT EXISTS barcode_print_job_items (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    print_job_id BIGINT UNSIGNED NOT NULL,
    position_order INT UNSIGNED NOT NULL,
    inventory_item_id BIGINT UNSIGNED NOT NULL,
    barcode_value VARCHAR(100) NOT NULL,
    was_new_barcode TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_barcode_print_job_position (
        print_job_id,
        position_order
    ),
    KEY ix_barcode_print_job_items_inventory (
        inventory_item_id
    ),
    KEY ix_barcode_print_job_items_barcode (
        barcode_value
    ),

    CONSTRAINT fk_barcode_print_job_items_job
        FOREIGN KEY (print_job_id)
        REFERENCES barcode_print_jobs(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_barcode_print_job_items_inventory
        FOREIGN KEY (inventory_item_id)
        REFERENCES inventory_items(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;


CREATE TABLE IF NOT EXISTS barcode_print_job_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    print_job_id BIGINT UNSIGNED NOT NULL,
    event_type VARCHAR(20) NOT NULL,
    message VARCHAR(1000) NULL,
    event_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY ix_barcode_print_job_events_job (
        print_job_id,
        id
    ),
    KEY ix_barcode_print_job_events_at (
        event_at
    ),

    CONSTRAINT fk_barcode_print_job_events_job
        FOREIGN KEY (print_job_id)
        REFERENCES barcode_print_jobs(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;
