-- Return V2 (owner decisions 2026-10-03).
--   * Valuation modes. HISTORICAL (inside the full-refund window): the historical value the customer paid
--     for the returned portion (invoice line_total, or an Exchange replacement's allocated value);
--     CURRENT_BUY (after the window): grams × current SHOP_BUY. Both: refund_basis rounded ONCE to EGP 5.
--   * A Return line can come from an original invoice line OR an Exchange replacement line (the piece the
--     customer holds now) — exactly one source.
--   * A returned identified piece enters Scrap at once, HELD for the manager (old_gold_holds gets a Return
--     source next to the Exchange one) with Return-specific Scrap movement types.
-- Metadata backfill only for pre-033 Returns: CURRENT_BUY, refund_basis = total_amount, rounding 0 (no money,
-- status, review or timestamp changes). Already-applied migrations stay untouched.
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';

SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=32),
 'DO 0', 'SELECT * FROM MIGRATION_033_REFUSED_missing_032');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Every existing Return already satisfies the 022 rule the backfill relies on (total = gold + making).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM invoice_returns WHERE total_amount <> gold_amount + making_refund),
 'SELECT * FROM MIGRATION_033_REFUSED_return_total_not_gold_plus_making', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- ------------------------------------------------------------------ invoice_returns
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_returns' AND COLUMN_NAME='valuation_mode'),
 "ALTER TABLE invoice_returns
    ADD COLUMN valuation_mode VARCHAR(20) NOT NULL DEFAULT 'CURRENT_BUY' AFTER making_refund,
    ADD COLUMN refund_basis DECIMAL(16,2) NOT NULL DEFAULT 0.00 AFTER valuation_mode,
    ADD COLUMN rounding_adjustment DECIMAL(16,2) NOT NULL DEFAULT 0.00 AFTER refund_basis", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Metadata backfill: pre-033 Returns are CURRENT_BUY with no rounding; their basis is their total.
UPDATE invoice_returns SET refund_basis = total_amount
 WHERE valuation_mode = 'CURRENT_BUY' AND rounding_adjustment = 0 AND refund_basis <> total_amount;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_returns' AND CONSTRAINT_NAME='chk_return_amounts'),
 "ALTER TABLE invoice_returns DROP CHECK chk_return_amounts", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_returns' AND CONSTRAINT_NAME='chk_return_amounts_v2'),
 "ALTER TABLE invoice_returns ADD CONSTRAINT chk_return_amounts_v2 CHECK (
    gold_amount >= 0 AND making_refund >= 0 AND money_paid >= 0 AND account_amount >= 0 AND refund_basis >= 0
    AND valuation_mode IN ('HISTORICAL', 'CURRENT_BUY')
    AND (valuation_mode = 'HISTORICAL' OR refund_basis = gold_amount + making_refund)
    AND rounding_adjustment > -5 AND rounding_adjustment < 5
    AND total_amount = refund_basis + rounding_adjustment
    AND (rounding_adjustment = 0 OR MOD(total_amount, 5) = 0)
    AND (return_status <> 'FINALIZED' OR (total_amount > 0 AND money_paid + account_amount = total_amount))
    AND (return_status = 'FINALIZED' OR (money_paid = 0 AND account_amount = 0)))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- ------------------------------------------------------------------ invoice_return_lines
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_return_lines' AND COLUMN_NAME='valuation_mode'),
 "ALTER TABLE invoice_return_lines
    ADD COLUMN valuation_mode VARCHAR(20) NOT NULL DEFAULT 'CURRENT_BUY' AFTER making_refund,
    ADD COLUMN historical_line_value DECIMAL(16,2) DEFAULT NULL AFTER valuation_mode,
    ADD COLUMN source_lifecycle_line_id BIGINT UNSIGNED DEFAULT NULL AFTER source_invoice_line_id,
    MODIFY COLUMN source_invoice_line_id BIGINT UNSIGNED DEFAULT NULL,
    MODIFY COLUMN shop_buy_price_per_gram DECIMAL(14,4) DEFAULT NULL,
    ADD COLUMN active_identified_lifecycle_line_id BIGINT UNSIGNED GENERATED ALWAYS AS (
        CASE WHEN inventory_item_id IS NOT NULL AND reversed_by_correction_id IS NULL
             THEN source_lifecycle_line_id ELSE NULL END) STORED,
    ADD KEY idx_return_line_lifecycle_source (source_lifecycle_line_id),
    ADD UNIQUE KEY uq_return_active_identified_lifecycle_line (active_identified_lifecycle_line_id),
    ADD CONSTRAINT fk_return_line_lifecycle_source FOREIGN KEY (source_lifecycle_line_id)
        REFERENCES invoice_lifecycle_lines (id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_return_lines' AND CONSTRAINT_NAME='chk_return_line_amounts'),
 "ALTER TABLE invoice_return_lines DROP CHECK chk_return_line_amounts", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_return_lines' AND CONSTRAINT_NAME='chk_return_line_amounts_v2'),
 "ALTER TABLE invoice_return_lines ADD CONSTRAINT chk_return_line_amounts_v2 CHECK (
    weight_grams > 0 AND weight_grams <= source_weight_grams AND gold_amount >= 0 AND making_refund >= 0
    AND (making_refund = 0 OR historical_making_total IS NOT NULL)
    AND ((valuation_mode = 'CURRENT_BUY' AND shop_buy_price_per_gram IS NOT NULL AND shop_buy_price_per_gram > 0)
         OR (valuation_mode = 'HISTORICAL' AND historical_line_value IS NOT NULL AND historical_line_value >= 0)))",
 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Exactly one source: an original invoice line, or an Exchange replacement (always an identified piece).
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_return_lines' AND CONSTRAINT_NAME='chk_return_line_origin'),
 "ALTER TABLE invoice_return_lines ADD CONSTRAINT chk_return_line_origin CHECK (
    (source_invoice_line_id IS NULL) <> (source_lifecycle_line_id IS NULL)
    AND (source_lifecycle_line_id IS NULL OR (line_type = 'SELL_INVENTORY' AND inventory_item_id IS NOT NULL)))",
 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- ------------------------------------------------------------------ old_gold_holds: Exchange OR Return source
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='old_gold_holds' AND COLUMN_NAME='return_line_id'),
 "ALTER TABLE old_gold_holds
    MODIFY COLUMN lifecycle_line_id BIGINT UNSIGNED DEFAULT NULL,
    ADD COLUMN return_line_id BIGINT UNSIGNED DEFAULT NULL AFTER lifecycle_line_id,
    ADD UNIQUE KEY uq_old_gold_hold_return_line (return_line_id),
    ADD CONSTRAINT fk_old_gold_hold_return_line FOREIGN KEY (return_line_id) REFERENCES invoice_return_lines (id),
    ADD CONSTRAINT chk_old_gold_hold_source CHECK ((lifecycle_line_id IS NULL) <> (return_line_id IS NULL))",
 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO old_gold_movement_types (code, name, description, expected_direction)
VALUES
    ('RETURN_SCRAP_IN', 'Return Scrap In',
     'A Return: the returned identified piece enters Scrap at its exact karat and weight (held for the manager)', 'IN'),
    ('RETURN_TO_STOCK_OUT', 'Return To Stock Out',
     'Manager decision: the held Return piece leaves Scrap and the same item returns to stock', 'OUT'),
    ('REVERSAL_RETURN_SCRAP_OUT', 'Reversal Return Scrap Out',
     'A reversed Return: the held returned piece leaves Scrap and goes back to the customer', 'OUT')
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description),
    expected_direction = VALUES(expected_direction);

INSERT INTO schema_migrations(version,name) VALUES (33,'Return V2: historical refund, replacement source, Scrap hold')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
