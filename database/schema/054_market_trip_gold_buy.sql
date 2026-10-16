-- Cash gold purchase for a market custody (شراء ذهب نقدي): the OWNER turns custody CASH into custody gold by buying scrap in the market.
--   GOLD_BUY   a new custody entry kind (MTxxxxxx-n). It SPENDS custody cash (custody cash = issued - returned - paid to suppliers + sale proceeds
--              - purchases) and ADDS gold to the custody per (karat, designation) (custody gold = ... + purchased), over the entries that were not
--              reversed. The cash never touched Main Safe or a bank (it was already in custody): NO cash_movements / bank_movements row exists,
--              and no Scrap movement either (the gold reaches the shop Scrap only through a normal custody return).
--   document   market_trip_gold_buys: one immutable row per entry with the vendor identity (MANDATORY: a registered party OR a typed name, always
--              stored as a name snapshot plus a normalized key for KPI grouping), the authoritative total and the price-warning evidence.
--   lines      market_trip_gold_buy_lines: the purchased scrap, one row per (karat, designation, actual grams, agreed price). The designation is the
--              OWNER's explicit choice (MELT or LOGO); gold of a new line is never UNCLASSIFIED.
-- A purchase NEVER writes a supplier / making / settlement ledger: it has no foreign key to any of them (the vendor is only an identity
-- reference). Every step is guarded, so a re-run changes nothing.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=53),
 'DO 0', 'SELECT * FROM MIGRATION_054_REFUSED_missing_053');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 1. the GOLD_BUY entry kind
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%GOLD_BUY%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entries' AND CONSTRAINT_NAME = 'chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 2. the purchase document (vendor identity is mandatory and immutable)
CREATE TABLE IF NOT EXISTS market_trip_gold_buys (
 entry_id BIGINT UNSIGNED NOT NULL,
 entry_kind VARCHAR(12) NOT NULL DEFAULT 'GOLD_BUY',
 vendor_kind VARCHAR(16) NOT NULL,
 vendor_party_id INT UNSIGNED DEFAULT NULL,
 vendor_name VARCHAR(150) NOT NULL,
 vendor_name_key VARCHAR(150) NOT NULL,
 total_price DECIMAL(16,2) NOT NULL,
 reference_total DECIMAL(16,2) DEFAULT NULL,
 deviation_percent DECIMAL(9,2) DEFAULT NULL,
 price_warning_acknowledged TINYINT(1) NOT NULL DEFAULT 0,
 buy_date DATE NOT NULL,
 reference VARCHAR(100) DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (entry_id),
 KEY idx_mtgb_vendor_party (vendor_party_id),
 KEY idx_mtgb_vendor_key (vendor_name_key),
 KEY idx_mtgb_date (buy_date),
 CONSTRAINT fk_mtgb_entry FOREIGN KEY (entry_id, entry_kind) REFERENCES market_trip_entries(id, entry_kind),
 CONSTRAINT fk_mtgb_party FOREIGN KEY (vendor_party_id) REFERENCES suppliers(id),
 CONSTRAINT chk_mtgb_kind CHECK (entry_kind = 'GOLD_BUY'),
 CONSTRAINT chk_mtgb_vendor CHECK (
  ((vendor_kind = 'REGISTERED_PARTY' AND vendor_party_id IS NOT NULL) OR (vendor_kind = 'THIRD_PARTY' AND vendor_party_id IS NULL))
  AND CHAR_LENGTH(TRIM(vendor_name)) > 0 AND CHAR_LENGTH(TRIM(vendor_name_key)) > 0),
 CONSTRAINT chk_mtgb_total CHECK (total_price > 0),
 CONSTRAINT chk_mtgb_warning CHECK (
  (reference_total IS NULL AND deviation_percent IS NULL)
  OR (reference_total IS NOT NULL AND deviation_percent IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. the purchased scrap (the total of the document is the SUM of its lines: enforced by the one posting transaction and the integrity tests)
CREATE TABLE IF NOT EXISTS market_trip_gold_buy_lines (
 entry_id BIGINT UNSIGNED NOT NULL,
 line_no SMALLINT UNSIGNED NOT NULL,
 karat_id TINYINT UNSIGNED NOT NULL,
 designation VARCHAR(12) NOT NULL,
 weight_grams DECIMAL(14,3) NOT NULL,
 line_price DECIMAL(16,2) NOT NULL,
 PRIMARY KEY (entry_id, line_no),
 KEY idx_mtgbl_source (karat_id, designation),
 CONSTRAINT fk_mtgbl_buy FOREIGN KEY (entry_id) REFERENCES market_trip_gold_buys(entry_id),
 CONSTRAINT fk_mtgbl_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT chk_mtgbl_designation CHECK (designation IN ('MELT','LOGO')),
 CONSTRAINT chk_mtgbl_weight CHECK (weight_grams > 0),
 CONSTRAINT chk_mtgbl_price CHECK (line_price > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (54,'Cash gold purchase for a market custody: GOLD_BUY entries, the purchase document and its scrap lines')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
