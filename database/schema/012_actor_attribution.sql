-- ============================================================
-- masGold v2
-- 012_actor_attribution.sql
--
-- Records the real employee who physically handled a transaction.
--
-- user_id     = software/audit account
-- employee_id = person who actually handled the gold/item
-- ============================================================


-- ============================================================
-- INVENTORY ITEM
-- Who physically entered/received this item into stock
-- ============================================================

ALTER TABLE inventory_items
    ADD COLUMN entered_by_employee_id INT UNSIGNED NULL
    AFTER created_by_user_id;

ALTER TABLE inventory_items
    ADD CONSTRAINT fk_inventory_items_entered_employee
        FOREIGN KEY (entered_by_employee_id)
        REFERENCES employees(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT;


CREATE INDEX idx_inventory_items_entered_employee
    ON inventory_items(entered_by_employee_id);


-- ============================================================
-- INVENTORY MOVEMENTS
-- Employee responsible for the physical movement
-- ============================================================

ALTER TABLE inventory_movements
    ADD COLUMN performed_by_employee_id INT UNSIGNED NULL
    AFTER created_by_user_id;

ALTER TABLE inventory_movements
    ADD CONSTRAINT fk_inventory_movements_employee
        FOREIGN KEY (performed_by_employee_id)
        REFERENCES employees(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT;


CREATE INDEX idx_inventory_movements_employee
    ON inventory_movements(performed_by_employee_id);


-- ============================================================
-- OLD GOLD
-- Employee who actually received / bought the old gold
-- ============================================================

ALTER TABLE old_gold_movements
    ADD COLUMN received_by_employee_id INT UNSIGNED NULL
    AFTER created_by_user_id;

ALTER TABLE old_gold_movements
    ADD CONSTRAINT fk_old_gold_movements_employee
        FOREIGN KEY (received_by_employee_id)
        REFERENCES employees(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT;


CREATE INDEX idx_old_gold_movements_employee
    ON old_gold_movements(received_by_employee_id);