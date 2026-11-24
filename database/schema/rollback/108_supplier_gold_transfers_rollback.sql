-- Rollback of 108 (supplier gold transfers). Refuses when a later migration exists or any transfer was posted (history is never deleted silently).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 108),
 'SELECT * FROM ROLLBACK_108_REFUSED_later_migration_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_transfers')
   AND (SELECT COUNT(*) FROM supplier_gold_transfers) > 0,
 'SELECT * FROM ROLLBACK_108_REFUSED_transfers_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF((SELECT COUNT(*) FROM supplier_gold_movements m JOIN supplier_gold_movement_types t ON t.id = m.movement_type_id
   WHERE t.code IN ('ACCOUNT_TRANSFER_OUT','ACCOUNT_TRANSFER_IN','ACCOUNT_TRANSFER_OUT_REVERSAL','ACCOUNT_TRANSFER_IN_REVERSAL')) > 0,
 'SELECT * FROM ROLLBACK_108_REFUSED_transfer_movements_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS supplier_gold_transfers;
DELETE FROM supplier_gold_movement_types WHERE code IN ('ACCOUNT_TRANSFER_OUT','ACCOUNT_TRANSFER_IN','ACCOUNT_TRANSFER_OUT_REVERSAL','ACCOUNT_TRANSFER_IN_REVERSAL');
DELETE FROM schema_migrations WHERE version = 108;
