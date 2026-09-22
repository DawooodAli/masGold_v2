from decimal import Decimal


class BillingRepository:
    """
    Database access for the unified customer bill:
    - sell inventory items to customer
    - buy old gold grams from customer
    - both sides may exist in the same invoice
    """

    def __init__(self, connection):
        self.connection = connection

    # =========================================================
    # READS
    # =========================================================

    def get_sellable_item_by_barcode(self, barcode_value):
        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT
                    ii.id AS inventory_item_id,
                    ii.item_type_id,
                    ii.karat_id,
                    ii.category_id,
                    ii.factory_id,
                    ii.weight_grams,
                    ii.size,
                    ii.stock_classification,
                    ii.inventory_status,
                    it.name AS item_type_name,
                    k.karat_value,
                    c.code AS category_code,
                    f.code AS factory_code,
                    f.name AS factory_name,
                    ib.barcode_value
                FROM inventory_barcodes ib
                JOIN inventory_items ii
                    ON ii.id = ib.inventory_item_id
                JOIN item_types it
                    ON it.id = ii.item_type_id
                JOIN karats k
                    ON k.id = ii.karat_id
                LEFT JOIN item_categories c
                    ON c.id = ii.category_id
                LEFT JOIN factories f
                    ON f.id = ii.factory_id
                WHERE
                    ib.barcode_value = %s
                    AND ib.barcode_status = 'ACTIVE'
                    AND ii.inventory_status = 'IN_STOCK'
                LIMIT 1
                """,
                (str(barcode_value),),
            )

            return cursor.fetchone()

        finally:
            cursor.close()

    def get_active_employee(self, employee_id):
        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT id, employee_code, name, is_active
                FROM employees
                WHERE id = %s
                LIMIT 1
                """,
                (int(employee_id),),
            )
            return cursor.fetchone()
        finally:
            cursor.close()

    def get_karat(self, karat_id):
        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT id, karat_value, name, is_active
                FROM karats
                WHERE id = %s
                LIMIT 1
                """,
                (int(karat_id),),
            )
            return cursor.fetchone()
        finally:
            cursor.close()

    def get_inventory_movement_type_id(self, code):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                SELECT id
                FROM inventory_movement_types
                WHERE code = %s
                LIMIT 1
                """,
                (str(code),),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError(
                    f"Inventory movement type '{code}' does not exist."
                )
            return int(row[0])
        finally:
            cursor.close()

    def get_old_gold_movement_type_id(
        self,
        code,
        expected_direction=None,
    ):
        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT id, code, expected_direction, is_active
                FROM old_gold_movement_types
                WHERE code = %s
                LIMIT 1
                """,
                (str(code),),
            )
            row = cursor.fetchone()

            if not row:
                raise ValueError(
                    f"Old-gold movement type '{code}' does not exist."
                )

            if not bool(row.get("is_active")):
                raise ValueError(
                    f"Old-gold movement type '{code}' is inactive."
                )

            if (
                expected_direction
                and row.get("expected_direction")
                not in (expected_direction, "EITHER")
            ):
                raise ValueError(
                    f"Old-gold movement type '{code}' has an invalid direction."
                )

            return int(row["id"])
        finally:
            cursor.close()

    # =========================================================
    # INVOICE NUMBER
    # =========================================================

    def next_invoice_code(self, invoice_date):
        """
        Prototype-compatible daily sequence:
            01YYMMDD, 02YYMMDD, ...

        The upsert is atomic across multiple workstations.
        Must be called inside the invoice transaction.
        """
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO billing_invoice_sequences (
                    sequence_date,
                    seq
                )
                VALUES (%s, 1)
                ON DUPLICATE KEY UPDATE
                    seq = seq + 1
                """,
                (invoice_date,),
            )

            cursor.execute(
                """
                SELECT seq
                FROM billing_invoice_sequences
                WHERE sequence_date = %s
                """,
                (invoice_date,),
            )
            row = cursor.fetchone()
            seq = int(row[0])

            date_part = invoice_date.strftime("%y%m%d")
            return f"{seq:02d}{date_part}"

        finally:
            cursor.close()

    # =========================================================
    # LOCK INVENTORY BEFORE SALE
    # =========================================================

    def lock_inventory_items(self, item_ids):
        item_ids = [int(value) for value in item_ids]

        if not item_ids:
            return []

        placeholders = ", ".join(["%s"] * len(item_ids))

        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                f"""
                SELECT
                    ii.id AS inventory_item_id,
                    ii.item_type_id,
                    ii.karat_id,
                    ii.category_id,
                    ii.factory_id,
                    ii.weight_grams,
                    ii.stock_classification,
                    ii.inventory_status,
                    it.name AS item_type_name,
                    k.karat_value,
                    ib.barcode_value
                FROM inventory_items ii
                JOIN item_types it
                    ON it.id = ii.item_type_id
                JOIN karats k
                    ON k.id = ii.karat_id
                LEFT JOIN inventory_barcodes ib
                    ON ib.inventory_item_id = ii.id
                    AND ib.barcode_status = 'ACTIVE'
                WHERE ii.id IN ({placeholders})
                FOR UPDATE
                """,
                tuple(item_ids),
            )
            rows = cursor.fetchall()
        finally:
            cursor.close()

        by_id = {
            int(row["inventory_item_id"]): row
            for row in rows
        }

        return [
            by_id[item_id]
            for item_id in item_ids
            if item_id in by_id
        ]

    # =========================================================
    # HEADER + LINES
    # =========================================================

    def create_invoice(
        self,
        *,
        invoice_code,
        customer_name,
        customer_id,
        employee_id,
        sell_price_21,
        buy_price_21,
        sale_total,
        old_gold_total,
        net_customer_due,
        sale_weight_total,
        old_gold_weight_total,
        notes,
    ):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO billing_invoices (
                    invoice_code,
                    customer_id,
                    customer_name_snapshot,
                    employee_id,
                    sell_price_21_per_gram,
                    buy_price_21_per_gram,
                    sale_total,
                    old_gold_total,
                    net_customer_due,
                    sale_weight_total_grams,
                    old_gold_weight_total_grams,
                    status,
                    notes,
                    finalized_at
                )
                VALUES (
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    'FINALIZED',
                    %s,
                    NOW()
                )
                """,
                (
                    str(invoice_code),
                    int(customer_id) if customer_id else None,
                    str(customer_name),
                    int(employee_id),
                    sell_price_21,
                    buy_price_21,
                    sale_total,
                    old_gold_total,
                    net_customer_due,
                    sale_weight_total,
                    old_gold_weight_total,
                    notes,
                ),
            )
            return int(cursor.lastrowid)
        finally:
            cursor.close()

    def add_sale_line(
        self,
        *,
        invoice_id,
        inventory_item_id,
        barcode_snapshot,
        item_type_snapshot,
        karat_value_snapshot,
        weight_grams,
        gold_price_per_gram,
        making_per_gram,
        line_total,
    ):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO billing_sale_lines (
                    invoice_id,
                    inventory_item_id,
                    barcode_snapshot,
                    item_type_snapshot,
                    karat_value_snapshot,
                    weight_grams,
                    gold_price_per_gram,
                    making_per_gram,
                    line_total
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                """,
                (
                    int(invoice_id),
                    int(inventory_item_id),
                    barcode_snapshot,
                    item_type_snapshot,
                    int(karat_value_snapshot),
                    weight_grams,
                    gold_price_per_gram,
                    making_per_gram,
                    line_total,
                ),
            )
            return int(cursor.lastrowid)
        finally:
            cursor.close()

    def add_old_gold_line(
        self,
        *,
        invoice_id,
        old_gold_movement_id,
        karat_id,
        karat_value_snapshot,
        weight_grams,
        buy_price_per_gram,
        line_total,
        notes,
    ):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO billing_old_gold_lines (
                    invoice_id,
                    old_gold_movement_id,
                    karat_id,
                    karat_value_snapshot,
                    weight_grams,
                    buy_price_per_gram,
                    line_total,
                    notes
                )
                VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                """,
                (
                    int(invoice_id),
                    int(old_gold_movement_id),
                    int(karat_id),
                    int(karat_value_snapshot),
                    weight_grams,
                    buy_price_per_gram,
                    line_total,
                    notes,
                ),
            )
            return int(cursor.lastrowid)
        finally:
            cursor.close()

    # =========================================================
    # INVENTORY SALE SIDE
    # =========================================================

    def mark_inventory_sold(self, inventory_item_id):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                UPDATE inventory_items
                SET
                    inventory_status = 'SOLD',
                    updated_by_user_id = NULL,
                    updated_at = NOW()
                WHERE
                    id = %s
                    AND inventory_status = 'IN_STOCK'
                """,
                (int(inventory_item_id),),
            )

            if cursor.rowcount != 1:
                raise ValueError(
                    f"Inventory item #{inventory_item_id} is no longer available."
                )
        finally:
            cursor.close()

    def add_inventory_sold_movement(
        self,
        *,
        inventory_item_id,
        movement_type_id,
        stock_classification,
        weight_grams,
        employee_id,
        invoice_code,
    ):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO inventory_movements (
                    inventory_item_id,
                    movement_type_id,
                    from_stock_classification,
                    to_stock_classification,
                    from_inventory_status,
                    to_inventory_status,
                    from_weight_grams,
                    to_weight_grams,
                    reason,
                    occurred_at,
                    performed_by_user_id,
                    performed_by_employee_id
                )
                VALUES (
                    %s, %s,
                    %s, %s,
                    'IN_STOCK', 'SOLD',
                    %s, %s,
                    %s,
                    NOW(),
                    NULL,
                    %s
                )
                """,
                (
                    int(inventory_item_id),
                    int(movement_type_id),
                    stock_classification,
                    stock_classification,
                    weight_grams,
                    weight_grams,
                    f"Invoice {invoice_code}",
                    int(employee_id),
                ),
            )
        finally:
            cursor.close()

    # =========================================================
    # OLD GOLD PURCHASE SIDE
    # =========================================================

    def add_customer_old_gold_movement(
        self,
        *,
        movement_type_id,
        karat_id,
        weight_grams,
        gold_price_per_gram,
        cash_value,
        movement_group_id,
        invoice_id,
        invoice_code,
        customer_id,
        customer_name,
        employee_id,
        notes,
    ):
        cursor = self.connection.cursor()

        movement_notes = (
            f"شراء كسر من العميل: {customer_name}"
            + (f" — {notes}" if notes else "")
        )

        try:
            cursor.execute(
                """
                INSERT INTO old_gold_movements (
                    movement_type_id,
                    karat_id,
                    weight_delta_grams,
                    gold_price_per_gram,
                    cash_value,
                    movement_group_id,
                    reference_type,
                    reference_id,
                    reference_code,
                    notes,
                    occurred_at,
                    created_by_user_id,
                    received_by_employee_id,
                    movement_code,
                    customer_id,
                    invoice_id
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s,
                    'BILLING_INVOICE',
                    %s, %s,
                    %s,
                    NOW(),
                    NULL,
                    %s,
                    'CUSTOMER_BUY_IN',
                    %s,
                    %s
                )
                """,
                (
                    int(movement_type_id),
                    int(karat_id),
                    weight_grams,
                    gold_price_per_gram,
                    cash_value,
                    str(movement_group_id),
                    int(invoice_id),
                    str(invoice_code),
                    movement_notes,
                    int(employee_id),
                    int(customer_id) if customer_id else None,
                    int(invoice_id),
                ),
            )
            return int(cursor.lastrowid)
        finally:
            cursor.close()
