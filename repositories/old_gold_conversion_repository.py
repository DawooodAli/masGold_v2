class OldGoldConversionRepository:
    """
    SQL layer for the old-gold gram ledger and converting grams
    into one new inventory item.

    Old gold is NOT an inventory-item table.
    Balance = SUM(old_gold_movements.weight_delta_grams) per karat.
    """

    def __init__(self, connection):
        self.connection = connection

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

    def get_active_item_type(self, item_type_id):
        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT id, code, name, default_category_id, is_active
                FROM item_types
                WHERE id = %s
                LIMIT 1
                """,
                (int(item_type_id),),
            )
            return cursor.fetchone()
        finally:
            cursor.close()

    def get_active_karat(self, karat_id):
        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT id, karat_value, purity, name, is_active
                FROM karats
                WHERE id = %s
                LIMIT 1
                """,
                (int(karat_id),),
            )
            return cursor.fetchone()
        finally:
            cursor.close()

    def get_category(self, category_id):
        if not category_id:
            return None

        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT id, code, name, is_active
                FROM item_categories
                WHERE id = %s
                LIMIT 1
                """,
                (int(category_id),),
            )
            return cursor.fetchone()
        finally:
            cursor.close()

    def get_factory(self, factory_id):
        if not factory_id:
            return None

        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT id, code, name, is_active
                FROM factories
                WHERE id = %s
                LIMIT 1
                """,
                (int(factory_id),),
            )
            return cursor.fetchone()
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
                SELECT
                    id,
                    code,
                    expected_direction,
                    is_active
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
                not in (
                    expected_direction,
                    "EITHER",
                )
            ):
                raise ValueError(
                    f"Old-gold movement type '{code}' has an invalid direction."
                )

            return int(row["id"])

        finally:
            cursor.close()


    def get_movement_type_id(self, code):
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

    # =========================================================
    # OLD GOLD BALANCE
    # =========================================================

    def get_old_gold_balances(self):
        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT
                    k.id AS karat_id,
                    k.karat_value,
                    k.name AS karat_name,
                    COALESCE(
                        SUM(og.weight_delta_grams),
                        0
                    ) AS balance_grams

                FROM karats k

                LEFT JOIN old_gold_movements og
                    ON og.karat_id = k.id
                    AND og.weight_delta_grams IS NOT NULL

                WHERE k.is_active = TRUE

                GROUP BY
                    k.id,
                    k.karat_value,
                    k.name,
                    k.sort_order

                ORDER BY
                    k.sort_order,
                    k.karat_value
                """
            )

            return cursor.fetchall()

        finally:
            cursor.close()

    def lock_old_gold_balance(self, karat_id):
        """
        Lock the current ledger rows for this karat while calculating
        available grams, preventing simultaneous overspending.
        """

        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT
                    id,
                    weight_delta_grams
                FROM old_gold_movements
                WHERE
                    karat_id = %s
                    AND weight_delta_grams IS NOT NULL
                FOR UPDATE
                """,
                (int(karat_id),),
            )

            return cursor.fetchall()

        finally:
            cursor.close()

    # =========================================================
    # NEW INVENTORY RESULT
    # =========================================================

    def create_new_inventory_item(
        self,
        *,
        item_type_id,
        karat_id,
        category_id,
        factory_id,
        weight_grams,
        size,
        notes,
        employee_id,
    ):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO inventory_items (
                    item_type_id,
                    karat_id,
                    category_id,
                    factory_id,
                    weight_grams,
                    size,
                    stock_classification,
                    inventory_status,
                    acquisition_source,
                    acquired_at,
                    notes,
                    created_by_user_id,
                    entered_by_employee_id,
                    updated_by_user_id
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    'NEW_STOCK',
                    'IN_STOCK',
                    'OLD_GOLD_MANUFACTURE',
                    NOW(),
                    %s,
                    NULL,
                    %s,
                    NULL
                )
                """,
                (
                    int(item_type_id),
                    int(karat_id),
                    int(category_id) if category_id else None,
                    int(factory_id) if factory_id else None,
                    weight_grams,
                    size,
                    notes,
                    int(employee_id),
                ),
            )

            return int(cursor.lastrowid)

        finally:
            cursor.close()

    def assign_barcode(
        self,
        *,
        inventory_item_id,
        barcode_value,
    ):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO inventory_barcodes (
                    inventory_item_id,
                    barcode_value,
                    barcode_status,
                    assigned_by_user_id,
                    reason
                )
                VALUES (
                    %s,
                    %s,
                    'ACTIVE',
                    NULL,
                    'OLD_GOLD_CONVERSION'
                )
                """,
                (
                    int(inventory_item_id),
                    str(barcode_value),
                ),
            )

        finally:
            cursor.close()

    def create_inventory_movement(
        self,
        *,
        inventory_item_id,
        movement_type_id,
        to_weight_grams,
        employee_id,
        reason,
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
                    %s,
                    %s,
                    NULL,
                    'NEW_STOCK',
                    NULL,
                    'IN_STOCK',
                    NULL,
                    %s,
                    %s,
                    NOW(),
                    NULL,
                    %s
                )
                """,
                (
                    int(inventory_item_id),
                    int(movement_type_id),
                    to_weight_grams,
                    str(reason),
                    int(employee_id),
                ),
            )

        finally:
            cursor.close()

    # =========================================================
    # OLD GOLD OUT + AUDIT LINK
    # =========================================================

    def create_old_gold_out_movement(
        self,
        *,
        karat_id,
        consumed_weight_grams,
        result_inventory_item_id,
        employee_id,
        conversion_code,
        notes,
    ):
        movement_type_id = (
            self.get_old_gold_movement_type_id(
                "MANUFACTURE_OUT",
                expected_direction="OUT",
            )
        )

        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO old_gold_movements (
                    movement_type_id,
                    karat_id,
                    weight_delta_grams,
                    related_inventory_item_id,
                    reference_type,
                    reference_id,
                    reference_code,
                    received_by_employee_id,
                    created_by_user_id,
                    notes,
                    occurred_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    'INVENTORY_ITEM',
                    %s,
                    %s,
                    %s,
                    NULL,
                    %s,
                    NOW()
                )
                """,
                (
                    int(movement_type_id),
                    int(karat_id),
                    -consumed_weight_grams,
                    int(result_inventory_item_id),
                    int(result_inventory_item_id),
                    str(conversion_code),
                    int(employee_id),
                    notes,
                ),
            )

            return int(cursor.lastrowid)

        finally:
            cursor.close()


    def create_conversion_batch(
        self,
        *,
        conversion_code,
        old_gold_movement_id,
        result_inventory_item_id,
        karat_id,
        consumed_weight_grams,
        result_weight_grams,
        weight_difference_grams,
        employee_id,
        notes,
    ):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO old_gold_conversion_batches (
                    conversion_code,
                    old_gold_movement_id,
                    result_inventory_item_id,
                    karat_id,
                    consumed_weight_grams,
                    result_weight_grams,
                    weight_difference_grams,
                    performed_by_employee_id,
                    performed_by_user_id,
                    notes
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    NULL,
                    %s
                )
                """,
                (
                    str(conversion_code),
                    int(old_gold_movement_id),
                    int(result_inventory_item_id),
                    int(karat_id),
                    consumed_weight_grams,
                    result_weight_grams,
                    weight_difference_grams,
                    int(employee_id),
                    notes,
                ),
            )

            return int(cursor.lastrowid)

        finally:
            cursor.close()
