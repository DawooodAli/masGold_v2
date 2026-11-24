-- Supplier-to-supplier GOLD transfer (settle from a supplier account) (owner 2026-11-24): gold that one supplier (the SOURCE, e.g. a party that holds gold for the shop) holds on the shop's account is used to
-- settle what the shop owes another supplier (the RECIPIENT). Prepared; NOT applied to any database by the task that wrote it.
--   One transfer = one immutable document GTxxxxxx + TWO supplier gold ledger movements and nothing else (no Scrap, custody, cash, bank, making or inventory):
--     the SOURCE supplier's ledger moves UP by the transferred grams of ITS karat (it holds less for the shop: ACCOUNT_TRANSFER_OUT),
--     the RECIPIENT's ledger moves DOWN by the credited grams of ITS karat (the shop owes it less: ACCOUNT_TRANSFER_IN).
--   credited = transferred x source karat / recipient karat (the settlement rule: posted at 0.001 g ROUND_HALF_UP, analytical value and rounding difference stored beside it).
--   A reversal is one more immutable row of the same table that names the original (reverses_id, unique, mandatory reason) and posts the EXACT opposite movements (the *_REVERSAL types); a
--   reversal is never reversed. The two ledger rows are found by (source_type 'SUPPLIER_GOLD_TRANSFER', the row id). No existing row is rewritten. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=107),
 'DO 0', 'SELECT * FROM MIGRATION_108_REFUSED_missing_107');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO supplier_gold_movement_types(code,name,description) VALUES
 ('ACCOUNT_TRANSFER_OUT','Supplier Gold Transfer Out','Gold taken from the gold a supplier holds for the shop to settle another supplier; the supplier holds less for the shop'),
 ('ACCOUNT_TRANSFER_IN','Supplier Gold Transfer In','Gold credited to a supplier from the gold another supplier holds for the shop; the shop owes the supplier less'),
 ('ACCOUNT_TRANSFER_OUT_REVERSAL','Supplier Gold Transfer Out Reversal','A reversed supplier gold transfer: the source supplier holds the gold for the shop again'),
 ('ACCOUNT_TRANSFER_IN_REVERSAL','Supplier Gold Transfer In Reversal','A reversed supplier gold transfer: the credit given to the recipient supplier is taken back')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);

CREATE TABLE IF NOT EXISTS supplier_gold_transfers (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 from_supplier_id INT UNSIGNED NOT NULL,
 to_supplier_id INT UNSIGNED NOT NULL,
 from_karat_id TINYINT UNSIGNED NOT NULL,
 from_karat_value TINYINT UNSIGNED NOT NULL,
 to_karat_id TINYINT UNSIGNED NOT NULL,
 to_karat_value TINYINT UNSIGNED NOT NULL,
 from_weight_grams DECIMAL(14,3) NOT NULL,
 to_weight_grams DECIMAL(14,3) NOT NULL,
 to_analytical_grams DECIMAL(18,6) NOT NULL,
 rounding_difference_grams DECIMAL(18,6) NOT NULL,
 transfer_date DATE NOT NULL,
 reference VARCHAR(100) DEFAULT NULL,
 notes VARCHAR(1000) DEFAULT NULL,
 reverses_id BIGINT UNSIGNED DEFAULT NULL,
 reason VARCHAR(500) DEFAULT NULL,
 request_key VARCHAR(64) NOT NULL,
 request_hash CHAR(64) NOT NULL,
 performed_by_user_id INT UNSIGNED NOT NULL,
 performed_by_employee_id INT UNSIGNED DEFAULT NULL,
 occurred_at DATETIME NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_sgt_request (request_key),
 UNIQUE KEY uq_sgt_reverses (reverses_id),
 KEY idx_sgt_from (from_supplier_id, id),
 KEY idx_sgt_to (to_supplier_id, id),
 CONSTRAINT fk_sgt_from FOREIGN KEY (from_supplier_id) REFERENCES suppliers(id),
 CONSTRAINT fk_sgt_to FOREIGN KEY (to_supplier_id) REFERENCES suppliers(id),
 CONSTRAINT fk_sgt_from_karat FOREIGN KEY (from_karat_id) REFERENCES karats(id),
 CONSTRAINT fk_sgt_to_karat FOREIGN KEY (to_karat_id) REFERENCES karats(id),
 CONSTRAINT fk_sgt_user FOREIGN KEY (performed_by_user_id) REFERENCES users(id),
 CONSTRAINT fk_sgt_reverses FOREIGN KEY (reverses_id) REFERENCES supplier_gold_transfers(id),
 CONSTRAINT chk_sgt_parties CHECK (from_supplier_id <> to_supplier_id),
 CONSTRAINT chk_sgt_weights CHECK (from_weight_grams > 0 AND to_weight_grams > 0 AND to_analytical_grams > 0),
 CONSTRAINT chk_sgt_rounding CHECK (rounding_difference_grams = to_weight_grams - to_analytical_grams),
 CONSTRAINT chk_sgt_reason CHECK ((reverses_id IS NULL AND reason IS NULL) OR (reverses_id IS NOT NULL AND reason IS NOT NULL AND CHAR_LENGTH(TRIM(reason)) > 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (108,'Supplier gold transfers: settle one supplier from the gold another supplier holds for the shop')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
