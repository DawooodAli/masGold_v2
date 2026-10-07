-- Rollback is possible while every Return is still legacy-compatible (CURRENT_BUY, no rounding, an original
-- invoice-line source, a stored SHOP_BUY price) and nothing used the Return Scrap hold. The metadata backfill
-- alone never blocks it.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM old_gold_holds WHERE return_line_id IS NOT NULL)
 OR EXISTS(SELECT 1 FROM old_gold_movements m JOIN old_gold_movement_types t ON t.id = m.movement_type_id
           WHERE t.code IN ('RETURN_SCRAP_IN', 'RETURN_TO_STOCK_OUT', 'REVERSAL_RETURN_SCRAP_OUT'))
 OR EXISTS(SELECT 1 FROM invoice_returns
           WHERE valuation_mode <> 'CURRENT_BUY' OR rounding_adjustment <> 0 OR refund_basis <> total_amount
              OR total_amount <> gold_amount + making_refund)
 OR EXISTS(SELECT 1 FROM invoice_return_lines
           WHERE valuation_mode <> 'CURRENT_BUY' OR historical_line_value IS NOT NULL
              OR source_lifecycle_line_id IS NOT NULL OR source_invoice_line_id IS NULL
              OR shop_buy_price_per_gram IS NULL OR shop_buy_price_per_gram <= 0)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>33),
 'SELECT * FROM ROLLBACK_033_REFUSED_return_v2_data_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- old_gold_holds back to Exchange-only (every remaining hold has a lifecycle line: guarded above).
-- MySQL refuses NULL -> NOT NULL on a column used by a foreign key (ERROR 1832, found in the 033 rehearsal):
-- the FK is dropped, the column restored, and the FK re-created exactly as 032 defined it.
ALTER TABLE old_gold_holds
    DROP CHECK chk_old_gold_hold_source,
    DROP FOREIGN KEY fk_old_gold_hold_return_line,
    DROP FOREIGN KEY fk_old_gold_hold_lifecycle_line;
ALTER TABLE old_gold_holds
    DROP INDEX uq_old_gold_hold_return_line,
    DROP COLUMN return_line_id,
    MODIFY COLUMN lifecycle_line_id BIGINT UNSIGNED NOT NULL;
ALTER TABLE old_gold_holds
    ADD CONSTRAINT fk_old_gold_hold_lifecycle_line FOREIGN KEY (lifecycle_line_id) REFERENCES invoice_lifecycle_lines (id);

-- invoice_return_lines back to the 022 shape and checks.
ALTER TABLE invoice_return_lines
    DROP CHECK chk_return_line_origin,
    DROP CHECK chk_return_line_amounts_v2,
    DROP FOREIGN KEY fk_return_line_lifecycle_source,
    DROP FOREIGN KEY fk_return_line_source;
ALTER TABLE invoice_return_lines
    DROP INDEX uq_return_active_identified_lifecycle_line,
    DROP INDEX idx_return_line_lifecycle_source,
    DROP COLUMN active_identified_lifecycle_line_id,
    DROP COLUMN source_lifecycle_line_id,
    DROP COLUMN historical_line_value,
    DROP COLUMN valuation_mode,
    MODIFY COLUMN source_invoice_line_id BIGINT UNSIGNED NOT NULL,
    MODIFY COLUMN shop_buy_price_per_gram DECIMAL(14,4) NOT NULL;
ALTER TABLE invoice_return_lines
    ADD CONSTRAINT fk_return_line_source FOREIGN KEY (source_invoice_line_id) REFERENCES invoice_lines (id);
ALTER TABLE invoice_return_lines ADD CONSTRAINT chk_return_line_amounts CHECK (
    (weight_grams > 0) AND (weight_grams <= source_weight_grams) AND (shop_buy_price_per_gram > 0)
    AND (gold_amount >= 0) AND (making_refund >= 0) AND ((making_refund = 0) OR (historical_making_total IS NOT NULL)));

-- invoice_returns back to the 022 shape and checks.
ALTER TABLE invoice_returns DROP CHECK chk_return_amounts_v2;
ALTER TABLE invoice_returns
    DROP COLUMN rounding_adjustment,
    DROP COLUMN refund_basis,
    DROP COLUMN valuation_mode;
ALTER TABLE invoice_returns ADD CONSTRAINT chk_return_amounts CHECK (
    (gold_amount >= 0) AND (making_refund >= 0) AND (money_paid >= 0) AND (account_amount >= 0)
    AND (total_amount = (gold_amount + making_refund))
    AND ((return_status <> 'FINALIZED') OR ((total_amount > 0) AND ((money_paid + account_amount) = total_amount)))
    AND ((return_status = 'FINALIZED') OR ((money_paid = 0) AND (account_amount = 0))));

DELETE FROM old_gold_movement_types
 WHERE code IN ('RETURN_SCRAP_IN', 'RETURN_TO_STOCK_OUT', 'REVERSAL_RETURN_SCRAP_OUT');
DELETE FROM schema_migrations WHERE version=33;
