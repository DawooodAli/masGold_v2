-- Customer cash in / out (owner 2026-10-28): a customer pays or takes money through any cash / bank / wallet account and the
-- movement lands in the customer statement. It is a manual deposit / withdrawal (the existing financial operation, same reversal)
-- tied to ONE customer-ledger row; these two categories and ledger types make that possible. The advance / custody flows stay in history.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=85),
 'DO 0', 'SELECT * FROM MIGRATION_086_REFUSED_missing_085');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO cash_categories(code, name, direction, is_active, is_system, sort_order) VALUES
 ('CUSTOMER_CASH_IN', 'نقدية داخلة من عميل', 'IN', 1, 1, 210),
 ('CUSTOMER_CASH_OUT', 'نقدية خارجة لعميل', 'OUT', 1, 1, 220)
 ON DUPLICATE KEY UPDATE code = code;

INSERT INTO customer_transaction_types(code, name, expected_direction, description) VALUES
 ('CUSTOMER_CASH_IN', 'Customer Cash In', 'DECREASE', 'Money the customer paid the shop (settlement or deposit): the balance moves toward the customer credit'),
 ('CUSTOMER_CASH_OUT', 'Customer Cash Out', 'INCREASE', 'Money the shop gave the customer: the balance moves toward what the customer owes')
 ON DUPLICATE KEY UPDATE name = VALUES(name);

INSERT INTO schema_migrations(version,name) VALUES (86,'Customer cash in / out categories and ledger types')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
