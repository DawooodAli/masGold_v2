-- Market-party roles on the Supplier Master: one identity (suppliers, S000000 codes) may be a supplier, a market intermediary, or both.
--   is_supplier      the party has a supplier account (purchase invoices, making payments, the gold account in its dedicated karat)
--   is_intermediary  the party can later hold shop-owned gold / money in the market (no ledger exists for it yet)
-- At least one role is required. Existing rows become supplier-only (is_supplier = 1, is_intermediary = 0): no other field and no ledger,
-- invoice or payment is touched, and a supplier whose karat is still unresolved (NULL) keeps that state - nothing is guessed or backfilled.
-- An intermediary-only party has NO supplier-account karat (the check below): its future holdings carry their own karat / fineness.
-- Every step is guarded, so a re-run changes nothing.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=43),
 'DO 0', 'SELECT * FROM MIGRATION_044_REFUSED_missing_043');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
 AND TABLE_NAME = 'suppliers' AND COLUMN_NAME = 'is_supplier'),
 "ALTER TABLE suppliers ADD COLUMN is_supplier BOOLEAN NOT NULL DEFAULT TRUE AFTER is_active", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
 AND TABLE_NAME = 'suppliers' AND COLUMN_NAME = 'is_intermediary'),
 "ALTER TABLE suppliers ADD COLUMN is_intermediary BOOLEAN NOT NULL DEFAULT FALSE AFTER is_supplier", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'suppliers' AND CONSTRAINT_NAME = 'chk_suppliers_role'),
 "ALTER TABLE suppliers ADD CONSTRAINT chk_suppliers_role CHECK (is_supplier IN (0,1) AND is_intermediary IN (0,1) AND (is_supplier = 1 OR is_intermediary = 1))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'suppliers' AND CONSTRAINT_NAME = 'chk_suppliers_intermediary_karat'),
 "ALTER TABLE suppliers ADD CONSTRAINT chk_suppliers_intermediary_karat CHECK (is_supplier = 1 OR karat_id IS NULL)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (44,'Market-party roles on the supplier master: is_supplier / is_intermediary, at least one role')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
