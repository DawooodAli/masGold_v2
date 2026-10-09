-- Purchasing & Suppliers V1: each supplier is dedicated to ONE karat (18, 21 or 24). Supplier invoices inherit it and the
-- supplier gold account is kept in PHYSICAL grams of that karat (no Eq21 conversion in the supplier account).
--
-- Backward compatible and non-destructive:
--   * suppliers.karat_id is NULLABLE. A supplier gets a karat here ONLY when its complete history identifies exactly one
--     supported karat: every invoice (drafts and cancelled ones included) and every gold movement agree. A supplier
--     without history, with mixed karats, or with an invoice / ledger disagreement stays NULL for an explicit decision.
--     Nothing is defaulted and nothing is guessed (no name matching).
--   * No invoice, group, piece or ledger row is touched. supplier_gold_movements.equivalent_21_delta_grams keeps its
--     stored values and stays an internal compatibility column; it no longer drives any supplier account amount.
--   * suppliers.updated_at is left as it was by the backfill.
-- Every step is guarded, so a re-run changes nothing.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=34),
 'DO 0', 'SELECT * FROM MIGRATION_035_REFUSED_missing_034');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
 NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
   AND TABLE_NAME = 'suppliers' AND COLUMN_NAME = 'karat_id'),
 "ALTER TABLE suppliers ADD COLUMN karat_id TINYINT UNSIGNED DEFAULT NULL AFTER name",
 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
 NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE()
   AND TABLE_NAME = 'suppliers' AND INDEX_NAME = 'idx_suppliers_karat'),
 "ALTER TABLE suppliers ADD KEY idx_suppliers_karat (karat_id)",
 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
 NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
   AND TABLE_NAME = 'suppliers' AND CONSTRAINT_NAME = 'fk_suppliers_karat'),
 "ALTER TABLE suppliers ADD CONSTRAINT fk_suppliers_karat FOREIGN KEY (karat_id) REFERENCES karats(id)",
 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Safe backfill: exactly ONE distinct karat across invoices (any status) AND gold movements, and that karat is 18 / 21 / 24.
UPDATE suppliers s
JOIN (SELECT h.supplier_id, MIN(h.karat_id) AS karat_id
      FROM (SELECT supplier_id, karat_id FROM supplier_purchase_invoices
            UNION ALL
            SELECT supplier_id, karat_id FROM supplier_gold_movements) h
      GROUP BY h.supplier_id
      HAVING COUNT(DISTINCT h.karat_id) = 1) one ON one.supplier_id = s.id
JOIN karats k ON k.id = one.karat_id AND k.karat_value IN (18, 21, 24)
SET s.karat_id = one.karat_id, s.updated_at = s.updated_at
WHERE s.karat_id IS NULL;

INSERT INTO schema_migrations(version,name) VALUES (35,'Suppliers are dedicated to one karat (supplier gold account in physical grams)')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
