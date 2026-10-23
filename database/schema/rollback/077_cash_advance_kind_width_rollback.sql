-- Rollback of 077. REFUSES when a THIRD_PARTY advance exists (its kind would no longer fit) or a later migration exists.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM cash_advances WHERE kind = 'THIRD_PARTY')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 77),
 'SELECT * FROM ROLLBACK_077_REFUSED_third_party_advances_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE cash_advances MODIFY COLUMN kind VARCHAR(10) NOT NULL;
DELETE FROM schema_migrations WHERE version = 77;
