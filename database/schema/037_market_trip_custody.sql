-- Market-trip custody core: the OWNER takes cash (from MAIN_SAFE) and Scrap (by existing master karat and physical grams) to
-- the market and brings back what was not used. A trip is a document with insert-only ENTRIES:
--   ISSUE   cash out of MAIN_SAFE and/or counted Scrap OUT  -> the same quantities enter the trip's CUSTODY
--   RETURN  the custody leaves the trip -> cash back into MAIN_SAFE and/or counted Scrap IN of the SAME karat
-- Custody is derived: issued - returned (entries that were reversed do not count). It is shop-owned but unavailable inside the
-- shop: Scrap really leaves the counted balance, so every existing Scrap consumer sees it as gone. No supplier movement, sale,
-- consumption, loss or write-off exists in this slice: a trip closes only when everything issued has come back.
-- Entries are immutable; the only correction is a full reversal (RV) through transaction_corrections. The trip header carries
-- one mutable column (status OPEN/CLOSED) and every change of it is an insert-only event with its actor and time.
-- Every step is guarded, so a re-run changes nothing.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=36),
 'DO 0', 'SELECT * FROM MIGRATION_037_REFUSED_missing_036');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS market_trips (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 status VARCHAR(10) NOT NULL DEFAULT 'OPEN',
 notes VARCHAR(1000) DEFAULT NULL,
 created_by_user_id INT UNSIGNED NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 status_changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 CONSTRAINT fk_mt_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_mt_status CHECK (status IN ('OPEN','CLOSED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_trip_status_events (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 trip_id BIGINT UNSIGNED NOT NULL,
 event_type VARCHAR(10) NOT NULL,
 reason VARCHAR(1000) DEFAULT NULL,
 performed_by_user_id INT UNSIGNED NOT NULL,
 occurred_at DATETIME NOT NULL,
 PRIMARY KEY (id),
 KEY idx_mtse_trip (trip_id, id),
 CONSTRAINT fk_mtse_trip FOREIGN KEY (trip_id) REFERENCES market_trips(id),
 CONSTRAINT fk_mtse_user FOREIGN KEY (performed_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_mtse_type CHECK (event_type IN ('OPENED','CLOSED','REOPENED')),
 CONSTRAINT chk_mtse_reason CHECK (event_type <> 'REOPENED' OR (reason IS NOT NULL AND CHAR_LENGTH(TRIM(reason)) > 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_trip_entries (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 trip_id BIGINT UNSIGNED NOT NULL,
 entry_no INT UNSIGNED NOT NULL,
 entry_kind VARCHAR(6) NOT NULL,
 entry_date DATE NOT NULL,
 notes VARCHAR(1000) DEFAULT NULL,
 request_key CHAR(36) NOT NULL,
 request_hash CHAR(64) NOT NULL,
 performed_by_user_id INT UNSIGNED NOT NULL,
 performed_by_employee_id INT UNSIGNED DEFAULT NULL,
 occurred_at DATETIME NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_mte_request (request_key),
 UNIQUE KEY uq_mte_no (trip_id, entry_no),
 CONSTRAINT fk_mte_trip FOREIGN KEY (trip_id) REFERENCES market_trips(id),
 CONSTRAINT fk_mte_user FOREIGN KEY (performed_by_user_id) REFERENCES users(id),
 CONSTRAINT fk_mte_employee FOREIGN KEY (performed_by_employee_id) REFERENCES employees(id),
 CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Cash of an entry: one leg bound to exactly one cash movement on MAIN_SAFE (OUT for an issue, IN for a return).
CREATE TABLE IF NOT EXISTS market_trip_entry_cash (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 entry_id BIGINT UNSIGNED NOT NULL,
 amount DECIMAL(16,2) NOT NULL,
 cash_account_id SMALLINT UNSIGNED NOT NULL,
 cash_movement_id BIGINT UNSIGNED NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_mtec_entry (entry_id),
 UNIQUE KEY uq_mtec_movement (cash_movement_id),
 CONSTRAINT fk_mtec_entry FOREIGN KEY (entry_id) REFERENCES market_trip_entries(id),
 CONSTRAINT fk_mtec_account FOREIGN KEY (cash_account_id) REFERENCES cash_accounts(id),
 CONSTRAINT fk_mtec_movement FOREIGN KEY (cash_movement_id) REFERENCES cash_movements(id),
 CONSTRAINT chk_mtec_amount CHECK (amount > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Gold of an entry: physical grams of one existing master karat, bound to exactly one counted Scrap movement.
CREATE TABLE IF NOT EXISTS market_trip_entry_gold (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 entry_id BIGINT UNSIGNED NOT NULL,
 karat_id TINYINT UNSIGNED NOT NULL,
 weight_grams DECIMAL(12,3) NOT NULL,
 scrap_movement_id BIGINT UNSIGNED NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_mteg_entry_karat (entry_id, karat_id),
 UNIQUE KEY uq_mteg_movement (scrap_movement_id),
 KEY idx_mteg_karat (karat_id),
 CONSTRAINT fk_mteg_entry FOREIGN KEY (entry_id) REFERENCES market_trip_entries(id),
 CONSTRAINT fk_mteg_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT fk_mteg_movement FOREIGN KEY (scrap_movement_id) REFERENCES old_gold_movements(id),
 CONSTRAINT chk_mteg_weight CHECK (weight_grams > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Standalone correction source (no fabricated invoice); full reversal only. The whole current rule is restated.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='transaction_corrections' AND CONSTRAINT_NAME='chk_correction_source'),
 'ALTER TABLE transaction_corrections DROP CHECK chk_correction_source','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK (
 (source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL)
 OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL)
 OR (source_type IN ('FINANCIAL_OPERATION','CUSTOMER_DEPOSIT','SUPPLIER_MAKING_PAYMENT','MARKET_TRIP_ENTRY')
     AND invoice_id IS NULL AND correction_type='FULL_REVERSAL'));

INSERT INTO old_gold_movement_types(code,name,description,expected_direction) VALUES
 ('TRIP_CUSTODY_OUT','Market Trip Custody Out','Scrap taken to the market by the owner: leaves the counted shop balance, enters trip custody','OUT'),
 ('TRIP_CUSTODY_RETURN_IN','Market Trip Custody Return In','Unused Scrap brought back from a market trip: same karat, physical grams','IN'),
 ('REVERSAL_TRIP_ISSUE_IN','Reversal Trip Issue In','A reversed trip issue: the Scrap returns to the counted shop balance','IN'),
 ('REVERSAL_TRIP_RETURN_OUT','Reversal Trip Return Out','A reversed trip return: the Scrap goes back into trip custody','OUT')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description), expected_direction=VALUES(expected_direction);
INSERT INTO cash_movement_types(code,name,expected_direction,description) VALUES
 ('TRIP_CASH_ISSUE','Market Trip Cash Issue','OUT','Cash taken from the Main Safe to the market (custody, not a payment)'),
 ('TRIP_CASH_RETURN','Market Trip Cash Return','IN','Unused market-trip cash returned to the Main Safe')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);

INSERT INTO permissions(code,name,description) VALUES
 ('MARKET_TRIP_MANAGE','Manage Market Trips','Issue custody, record returns, close, reopen and reverse market trips (owner only)')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code='MARKET_TRIP_MANAGE'
 WHERE r.code = 'OWNER' AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);

INSERT INTO schema_migrations(version,name) VALUES (37,'Market-trip custody core: issue, return, close and reversal')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
