-- Refused once any hold or any of the 032 movement types was used, or a later migration exists:
-- the Scrap ledger and the returned pieces would otherwise lose their meaning.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM old_gold_holds)
 OR EXISTS(SELECT 1 FROM old_gold_movements m JOIN old_gold_movement_types t ON t.id = m.movement_type_id
           WHERE t.code IN ('EXCHANGE_RETURN_SCRAP_IN', 'EXCHANGE_RETURN_TO_STOCK_OUT',
                            'REVERSAL_EXCHANGE_RETURN_SCRAP_OUT'))
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>32),
 'SELECT * FROM ROLLBACK_032_REFUSED_holds_exist_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP VIEW IF EXISTS vw_old_gold_available;
DROP TABLE IF EXISTS old_gold_holds;
DELETE FROM old_gold_movement_types
 WHERE code IN ('EXCHANGE_RETURN_SCRAP_IN', 'EXCHANGE_RETURN_TO_STOCK_OUT', 'REVERSAL_EXCHANGE_RETURN_SCRAP_OUT');
DELETE FROM schema_migrations WHERE version=32;
