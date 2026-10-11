-- Direct supplier GOLD settlement from available shop Scrap (SGxxxxxx).
--   A settlement is an immutable document that posts, atomically in ONE transaction:
--     * one COUNTED Scrap OUT of the DELIVERED master karat (the actual physical grams, deducted once), and
--     * one NEGATIVE supplier gold movement in the supplier's DEDICATED karat (the shop owes the supplier less).
--   credited grams = delivered grams x delivered karat / supplier karat. The POSTED credit (3 decimals, ROUND_HALF_UP) is derived straight from
--   the high-precision calculation; the six-decimal ANALYTICAL value is stored beside it (it is not an exact value: a ratio such as 21/18 is
--   not representable) and the rounding difference is stored as posted - analytical, so nothing accumulates silently.
--   Supplier balances stay grams of the dedicated karat; the conversion happens at settlement only. No cash, making, valuation, sale or purchase.
--   source_kind is fixed to SHOP_SCRAP: custody, intermediary holdings and assayed bars arrive in later slices.
-- The only correction is a full reversal (RV) through transaction_corrections (standalone source, no fabricated invoice): the stored delivered
-- grams return to Scrap and the stored posted credit is restored to the supplier. Nothing is rewritten.
-- Every step is guarded, so a re-run changes nothing.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=44),
 'DO 0', 'SELECT * FROM MIGRATION_045_REFUSED_missing_044');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS supplier_gold_settlements (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 supplier_id INT UNSIGNED NOT NULL,
 source_kind VARCHAR(12) NOT NULL DEFAULT 'SHOP_SCRAP',
 delivered_karat_id TINYINT UNSIGNED NOT NULL,
 delivered_karat_value TINYINT UNSIGNED NOT NULL,
 target_karat_id TINYINT UNSIGNED NOT NULL,
 target_karat_value TINYINT UNSIGNED NOT NULL,
 delivered_weight_grams DECIMAL(14,3) NOT NULL,
 credited_weight_grams DECIMAL(14,3) NOT NULL,
 credited_analytical_grams DECIMAL(18,6) NOT NULL,
 rounding_difference_grams DECIMAL(18,6) NOT NULL,
 settlement_date DATE NOT NULL,
 reference VARCHAR(100) DEFAULT NULL,
 notes VARCHAR(1000) DEFAULT NULL,
 request_key CHAR(36) NOT NULL,
 request_hash CHAR(64) NOT NULL,
 performed_by_user_id INT UNSIGNED NOT NULL,
 performed_by_employee_id INT UNSIGNED DEFAULT NULL,
 occurred_at DATETIME NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_sgs_request (request_key),
 KEY idx_sgs_supplier (supplier_id, settlement_date, id),
 KEY idx_sgs_delivered_karat (delivered_karat_id),
 KEY idx_sgs_target_karat (target_karat_id),
 CONSTRAINT fk_sgs_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
 CONSTRAINT fk_sgs_delivered_karat FOREIGN KEY (delivered_karat_id) REFERENCES karats(id),
 CONSTRAINT fk_sgs_target_karat FOREIGN KEY (target_karat_id) REFERENCES karats(id),
 CONSTRAINT fk_sgs_user FOREIGN KEY (performed_by_user_id) REFERENCES users(id),
 CONSTRAINT fk_sgs_employee FOREIGN KEY (performed_by_employee_id) REFERENCES employees(id),
 CONSTRAINT chk_sgs_source CHECK (source_kind = 'SHOP_SCRAP'),
 CONSTRAINT chk_sgs_weights CHECK (delivered_weight_grams > 0 AND credited_weight_grams > 0 AND credited_analytical_grams > 0),
 CONSTRAINT chk_sgs_rounding CHECK (rounding_difference_grams = credited_weight_grams - credited_analytical_grams)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Insert-only links from a settlement to the two ledger movements it posted (one Scrap movement, one supplier gold movement).
CREATE TABLE IF NOT EXISTS supplier_gold_settlement_legs (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 settlement_id BIGINT UNSIGNED NOT NULL,
 leg_kind VARCHAR(5) NOT NULL,
 old_gold_movement_id BIGINT UNSIGNED DEFAULT NULL,
 supplier_gold_movement_id BIGINT UNSIGNED DEFAULT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_sgsl_kind (settlement_id, leg_kind),
 UNIQUE KEY uq_sgsl_scrap_movement (old_gold_movement_id),
 UNIQUE KEY uq_sgsl_gold_movement (supplier_gold_movement_id),
 CONSTRAINT fk_sgsl_settlement FOREIGN KEY (settlement_id) REFERENCES supplier_gold_settlements(id),
 CONSTRAINT fk_sgsl_scrap_movement FOREIGN KEY (old_gold_movement_id) REFERENCES old_gold_movements(id),
 CONSTRAINT fk_sgsl_gold_movement FOREIGN KEY (supplier_gold_movement_id) REFERENCES supplier_gold_movements(id),
 CONSTRAINT chk_sgsl_leg CHECK (
  (leg_kind = 'SCRAP' AND old_gold_movement_id IS NOT NULL AND supplier_gold_movement_id IS NULL)
  OR (leg_kind = 'GOLD' AND supplier_gold_movement_id IS NOT NULL AND old_gold_movement_id IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Standalone correction source (no fabricated invoice); full reversal only. The WHOLE current rule (migration 037) is restated and only
-- the new source is added: every earlier source and restriction is preserved.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='transaction_corrections' AND CONSTRAINT_NAME='chk_correction_source'),
 'ALTER TABLE transaction_corrections DROP CHECK chk_correction_source','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK (
 (source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL)
 OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL)
 OR (source_type IN ('FINANCIAL_OPERATION','CUSTOMER_DEPOSIT','SUPPLIER_MAKING_PAYMENT','MARKET_TRIP_ENTRY','SUPPLIER_GOLD_SETTLEMENT')
     AND invoice_id IS NULL AND correction_type='FULL_REVERSAL'));

INSERT INTO old_gold_movement_types(code,name,description,expected_direction) VALUES
 ('SUPPLIER_GOLD_SETTLEMENT_OUT','Supplier Gold Settlement Out','Scrap delivered to a supplier against its gold account: leaves the counted shop balance (actual physical grams of the delivered karat)','OUT'),
 ('REVERSAL_SUPPLIER_GOLD_SETTLEMENT_IN','Reversal Supplier Gold Settlement In','A reversed supplier gold settlement: the delivered Scrap returns to the counted shop balance','IN')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description), expected_direction=VALUES(expected_direction);
INSERT INTO supplier_gold_movement_types(code,name,description) VALUES
 ('SETTLEMENT','Supplier Gold Settlement','Gold delivered to the supplier from shop Scrap; the grams owed to the supplier decrease (credited in the supplier karat)'),
 ('SETTLEMENT_REVERSAL','Supplier Gold Settlement Reversal','A reversed gold settlement; the grams owed to the supplier are restored')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);

INSERT INTO permissions(code,name,description) VALUES
 ('SUPPLIER_GOLD_SETTLE','Settle Supplier Gold','Deliver shop Scrap to a supplier against its gold account'),
 ('SUPPLIER_GOLD_SETTLE_REVERSE','Reverse Supplier Gold Settlement','Reverse a posted supplier gold settlement (owner only)')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code='SUPPLIER_GOLD_SETTLE'
 WHERE r.code IN ('OWNER','MANAGER') AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code='SUPPLIER_GOLD_SETTLE_REVERSE'
 WHERE r.code = 'OWNER' AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);

INSERT INTO schema_migrations(version,name) VALUES (45,'Supplier gold settlement from shop Scrap: SG documents, legs, movement types and permissions')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
