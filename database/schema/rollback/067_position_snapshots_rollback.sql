-- Rollback of 067. REFUSES when any snapshot exists or a later migration exists: stocktaking evidence is never deleted to make a rollback pass.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM position_snapshots)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 67),
 'SELECT * FROM ROLLBACK_067_REFUSED_snapshots_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS position_snapshots;
DELETE FROM schema_migrations WHERE version = 67;
