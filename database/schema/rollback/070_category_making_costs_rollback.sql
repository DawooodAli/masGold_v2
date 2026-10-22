-- Rollback of 070. REFUSES when any category cost is set or a later migration exists: the owner's cost figures are never deleted to make a rollback pass.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM item_category_costs)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 70),
 'SELECT * FROM ROLLBACK_070_REFUSED_costs_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS item_category_costs;
DELETE FROM schema_migrations WHERE version = 70;
