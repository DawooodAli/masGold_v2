class InventoryConversionRepository:
    """
    SQL layer for consuming OLD_STOCK pieces and producing one NEW_STOCK item.
    """

    def __init__(self, connection):
        self.connection = connection

    # =========================================================
    # LOOKUPS
    # =========================================================

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
                SELECT
                    id,
                    code,
                    name,
                    default_category_id,
                    is_active
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
                SELECT
                    id,
                    karat_value,
                    purity,
                    name,
                    is_active
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
    # OLD STOCK SOURCE LIST
    # =========================================================

    def get_available_old_stock(self):
        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT
                    ii.id AS inventory_item_id,
                    ii.weight_grams,
                    ii.size,
                    ii.acquired_at,

                    it.name AS item_type_name,

                    k.id AS karat_id,
                    k.karat_value,

                    c.code AS category_code,

                    f.code AS factory_code,
                    f.name AS factory_name,

                    ib.barcode_value

                FROM inventory_items ii

                JOIN item_types it
                    ON it.id = ii.item_type_id

                JOIN karats k
                    ON k.id = ii.karat_id

                LEFT JOIN item_categories c
                    ON c.id = ii.category_id

                LEFT JOIN factories f
                    ON f.id = ii.factory_id

                LEFT JOIN inventory_barcodes ib
                    ON ib.inventory_item_id = ii.id
                    AND ib.barcode_status = 'ACTIVE'

                WHERE
                    ii.stock_classification = 'OLD_STOCK'
                    AND ii.inventory_status = 'IN_STOCK'

                ORDER BY
                    ii.acquired_at DESC,
                    ii.id DESC
                """
            )

            return cursor.fetchall()

        finally:
            cursor.close()

    def lock_old_stock_sources(self, source_item_ids):
        source_item_ids = [
            int(value)
            for value in source_item_ids
        ]

        if not source_item_ids:
            return []

        placeholders = ", ".join(
            ["%s"] * len(source_item_ids)
        )

        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                f"""
                SELECT
                    ii.id AS inventory_item_id,
                    ii.karat_id,
                    ii.weight_grams,
                    ii.stock_classification,
                    ii.inventory_status,
                    ib.barcode_value

                FROM inventory_items ii

                LEFT JOIN inventory_barcodes ib
                    ON ib.inventory_item_id = ii.id
                    AND ib.barcode_status = 'ACTIVE'

                WHERE ii.id IN ({placeholders})

                FOR UPDATE
                """,
                tuple(source_item_ids),
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
            for item_id in source_item_ids
            if item_id in by_id
        ]

    # =========================================================
    # WRITES
    # =========================================================

    def create_new_stock_item(
        self,
        *,
        item_type_id,
        karat_id,
        category_id,
        factory_id,
        weight_grams,
        size,
        barcode_value,
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
                    'OLD_STOCK_CONVERSION',
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
                    (
                        int(category_id)
                        if category_id
                        else None
                    ),
                    (
                        int(factory_id)
                        if factory_id
                        else None
                    ),
                    weight_grams,
                    size,
                    notes,
                    int(employee_id),
                ),
            )

            inventory_item_id = int(
                cursor.lastrowid
            )

        finally:
            cursor.close()

        if barcode_value:
            self.assign_barcode(
                inventory_item_id=inventory_item_id,
                barcode_value=barcode_value,
                reason="OLD_STOCK_CONVERSION",
            )

        return inventory_item_id

    def assign_barcode(
        self,
        *,
        inventory_item_id,
        barcode_value,
        reason,
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
                    %s
                )
                """,
                (
                    int(inventory_item_id),
                    str(barcode_value),
                    str(reason),
                ),
            )
        finally:
            cursor.close()

    def consume_old_stock_item(
        self,
        source_item_id,
    ):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                UPDATE inventory_items
                SET
                    inventory_status = 'REMOVED',
                    updated_by_user_id = NULL,
                    updated_at = NOW()
                WHERE
                    id = %s
                    AND stock_classification = 'OLD_STOCK'
                    AND inventory_status = 'IN_STOCK'
                """,
                (int(source_item_id),),
            )

            if cursor.rowcount != 1:
                raise ValueError(
                    f"Old-stock item #{source_item_id} is no longer available."
                )

        finally:
            cursor.close()

    def create_inventory_movement(
        self,
        *,
        inventory_item_id,
        movement_type_id,
        from_stock_classification,
        to_stock_classification,
        from_inventory_status,
        to_inventory_status,
        from_weight_grams,
        to_weight_grams,
        reason,
        performed_by_employee_id,
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
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
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
                    from_stock_classification,
                    to_stock_classification,
                    from_inventory_status,
                    to_inventory_status,
                    from_weight_grams,
                    to_weight_grams,
                    reason,
                    int(performed_by_employee_id),
                ),
            )

        finally:
            cursor.close()

    def create_conversion_batch(
        self,
        *,
        conversion_code,
        result_inventory_item_id,
        source_total_weight_grams,
        result_weight_grams,
        weight_difference_grams,
        employee_id,
        notes,
    ):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO inventory_conversion_batches (
                    conversion_code,
                    result_inventory_item_id,
                    source_total_weight_grams,
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
                    NULL,
                    %s
                )
                """,
                (
                    str(conversion_code),
                    int(result_inventory_item_id),
                    source_total_weight_grams,
                    result_weight_grams,
                    weight_difference_grams,
                    int(employee_id),
                    notes,
                ),
            )

            return int(
                cursor.lastrowid
            )

        finally:
            cursor.close()

    def add_conversion_source(
        self,
        *,
        batch_id,
        source_item_id,
        source_weight_grams,
        source_barcode_value,
    ):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO inventory_conversion_sources (
                    conversion_batch_id,
                    source_inventory_item_id,
                    source_weight_grams,
                    source_barcode_value
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    int(batch_id),
                    int(source_item_id),
                    source_weight_grams,
                    source_barcode_value,
                ),
            )

        finally:
            cursor.close()
