-- Customer merge (owner 2026-10-29): a duplicate customer is merged into the one that stays. Every record of the merged customer is moved to the
-- surviving customer in one transaction (services/customer_merge_service.py); the merged row is kept, deactivated and points at the survivor.
-- Only one column and its self-reference are added; nothing is rewritten here.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=86),
 'DO 0', 'SELECT * FROM MIGRATION_087_REFUSED_missing_086');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='customers' AND COLUMN_NAME='merged_into_customer_id'),
 "ALTER TABLE customers ADD COLUMN merged_into_customer_id BIGINT UNSIGNED DEFAULT NULL AFTER is_active", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='customers' AND CONSTRAINT_NAME='fk_customers_merged_into'),
 "ALTER TABLE customers ADD CONSTRAINT fk_customers_merged_into FOREIGN KEY (merged_into_customer_id) REFERENCES customers(id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='customers' AND CONSTRAINT_NAME='chk_customers_merged_inactive'),
 "ALTER TABLE customers ADD CONSTRAINT chk_customers_merged_inactive CHECK (merged_into_customer_id IS NULL OR is_active = 0)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (87,'Customer merge: merged_into_customer_id')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
