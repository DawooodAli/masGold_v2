-- Exchange returned piece → Scrap hold (owner-approved lifecycle, 2026-10-03).
-- At Exchange finalize the returned identified piece leaves sellable stock (SOLD → CONVERTED_TO_OLD_GOLD)
-- and its exact physical weight is credited to the COUNTED Scrap ledger of its own karat
-- (EXCHANGE_RETURN_SCRAP_IN). The credited weight is HELD for the manager's decision:
--   RETURNED_TO_STOCK  the same weight leaves Scrap (EXCHANGE_RETURN_TO_STOCK_OUT); the same item is IN_STOCK.
--   COMMITTED          no movement: the held weight simply becomes available Scrap.
--   REVERSED           the Exchange was reversed while HELD (REVERSAL_EXCHANGE_RETURN_SCRAP_OUT).
-- vw_old_gold_balances stays the PHYSICAL balance; vw_old_gold_available (physical − HELD) is what
-- every Scrap consumer may use. One hold per returned piece: UNIQUE lifecycle line / IN movement /
-- review (one inspection review is created per returned lifecycle line).
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';

SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=31),
 'DO 0', 'SELECT * FROM MIGRATION_032_REFUSED_missing_031');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS old_gold_holds (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    review_id BIGINT UNSIGNED NOT NULL,
    lifecycle_line_id BIGINT UNSIGNED NOT NULL,
    inventory_item_id BIGINT UNSIGNED NOT NULL,
    karat_id TINYINT UNSIGNED NOT NULL,
    weight_grams DECIMAL(12,3) NOT NULL,
    in_movement_id BIGINT UNSIGNED NOT NULL,
    hold_status VARCHAR(20) NOT NULL DEFAULT 'HELD',
    out_movement_id BIGINT UNSIGNED DEFAULT NULL,
    created_by_user_id INT UNSIGNED NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_by_user_id INT UNSIGNED DEFAULT NULL,
    resolved_at DATETIME DEFAULT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_old_gold_hold_review (review_id),
    UNIQUE KEY uq_old_gold_hold_lifecycle_line (lifecycle_line_id),
    UNIQUE KEY uq_old_gold_hold_in_movement (in_movement_id),
    UNIQUE KEY uq_old_gold_hold_out_movement (out_movement_id),
    KEY idx_old_gold_hold_karat_status (karat_id, hold_status),
    KEY idx_old_gold_hold_item (inventory_item_id),
    CONSTRAINT fk_old_gold_hold_review FOREIGN KEY (review_id) REFERENCES management_reviews (id),
    CONSTRAINT fk_old_gold_hold_lifecycle_line FOREIGN KEY (lifecycle_line_id) REFERENCES invoice_lifecycle_lines (id),
    CONSTRAINT fk_old_gold_hold_item FOREIGN KEY (inventory_item_id) REFERENCES inventory_items (id),
    CONSTRAINT fk_old_gold_hold_karat FOREIGN KEY (karat_id) REFERENCES karats (id),
    CONSTRAINT fk_old_gold_hold_in_movement FOREIGN KEY (in_movement_id) REFERENCES old_gold_movements (id),
    CONSTRAINT fk_old_gold_hold_out_movement FOREIGN KEY (out_movement_id) REFERENCES old_gold_movements (id),
    CONSTRAINT fk_old_gold_hold_created_by FOREIGN KEY (created_by_user_id) REFERENCES users (id),
    CONSTRAINT fk_old_gold_hold_resolved_by FOREIGN KEY (resolved_by_user_id) REFERENCES users (id),
    CONSTRAINT chk_old_gold_hold_weight CHECK (weight_grams > 0),
    CONSTRAINT chk_old_gold_hold_status CHECK (hold_status IN ('HELD', 'RETURNED_TO_STOCK', 'COMMITTED', 'REVERSED')),
    CONSTRAINT chk_old_gold_hold_out CHECK (
        (hold_status IN ('RETURNED_TO_STOCK', 'REVERSED')) = (out_movement_id IS NOT NULL)),
    CONSTRAINT chk_old_gold_hold_resolution CHECK (
        (hold_status = 'HELD') = (resolved_at IS NULL)
        AND (resolved_at IS NULL) = (resolved_by_user_id IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO old_gold_movement_types (code, name, description, expected_direction)
VALUES
    ('EXCHANGE_RETURN_SCRAP_IN', 'Exchange Return Scrap In',
     'An Exchange: the returned identified piece enters Scrap at its exact karat and weight (held for the manager)', 'IN'),
    ('EXCHANGE_RETURN_TO_STOCK_OUT', 'Exchange Return To Stock Out',
     'Manager decision: the held Exchange piece leaves Scrap and the same item returns to stock', 'OUT'),
    ('REVERSAL_EXCHANGE_RETURN_SCRAP_OUT', 'Reversal Exchange Return Scrap Out',
     'A reversed Exchange: the held returned piece leaves Scrap and goes back to the customer', 'OUT')
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description),
    expected_direction = VALUES(expected_direction);

CREATE OR REPLACE VIEW vw_old_gold_available AS
SELECT b.karat_id, b.karat_value, b.karat_name, b.balance_grams AS physical_grams,
       CAST(COALESCE(h.held_grams, 0) AS DECIMAL(14,3)) AS held_grams,
       CAST(b.balance_grams - COALESCE(h.held_grams, 0) AS DECIMAL(14,3)) AS available_grams
FROM vw_old_gold_balances b
LEFT JOIN (SELECT karat_id, SUM(weight_grams) AS held_grams FROM old_gold_holds
           WHERE hold_status = 'HELD' GROUP BY karat_id) h ON h.karat_id = b.karat_id;

INSERT INTO schema_migrations(version,name) VALUES (32,'Exchange returned piece: Scrap hold and available Scrap')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
