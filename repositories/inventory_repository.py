from repositories.base_repository import BaseRepository


class InventoryRepository(BaseRepository):

    def get_stock_items(
        self,
        search: str = "",
        limit: int = 500,
    ) -> list[dict]:

        search = (search or "").strip()
        like = f"%{search}%"

        return self._fetch_all(
            """
            SELECT
                ii.id AS inventory_item_id,

                ib.barcode_value,

                ii.item_type_id,
                it.name AS item_type_name,

                ii.karat_id,
                k.karat_value,
                k.name AS karat_name,

                ii.category_id,
                c.code AS category_code,
                c.name AS category_name,

                ii.factory_id,
                f.code AS factory_code,
                f.name AS factory_name,

                ii.weight_grams,
                ii.size,

                ii.stock_classification,
                ii.inventory_status,

                ii.acquisition_source,
                ii.acquired_at,

                ii.notes,
                ii.created_by_user_id

            FROM inventory_items ii

            INNER JOIN item_types it
                ON it.id = ii.item_type_id

            INNER JOIN karats k
                ON k.id = ii.karat_id

            LEFT JOIN item_categories c
                ON c.id = ii.category_id

            LEFT JOIN factories f
                ON f.id = ii.factory_id

            LEFT JOIN inventory_barcodes ib
                ON ib.inventory_item_id = ii.id
                AND ib.barcode_status = 'ACTIVE'

            WHERE
                ii.inventory_status = 'IN_STOCK'

                AND (
                    %s = ''

                    OR CAST(ii.id AS CHAR) LIKE %s

                    OR COALESCE(
                        ib.barcode_value,
                        ''
                    ) LIKE %s

                    OR it.name LIKE %s

                    OR COALESCE(
                        f.name,
                        ''
                    ) LIKE %s

                    OR COALESCE(
                        c.code,
                        ''
                    ) LIKE %s

                    OR CAST(
                        ii.weight_grams AS CHAR
                    ) LIKE %s
                )

            ORDER BY
                ii.acquired_at DESC,
                ii.id DESC

            LIMIT %s
            """,
            (
                search,
                like,
                like,
                like,
                like,
                like,
                like,
                limit,
            ),
        )


    def get_stock_summary(self) -> dict:

        row = self._fetch_one(
            """
            SELECT
                COUNT(*) AS total_items,

                COALESCE(
                    SUM(
                        CASE
                            WHEN ii.stock_classification = 'NEW_STOCK'
                            THEN 1
                            ELSE 0
                        END
                    ),
                    0
                ) AS new_stock_items,

                COALESCE(
                    SUM(
                        CASE
                            WHEN ib.id IS NULL
                            THEN 1
                            ELSE 0
                        END
                    ),
                    0
                ) AS without_barcode_items,

                SUM(
                    CASE
                        WHEN ib.id IS NULL
                        THEN 1
                        ELSE 0
                    END
                ) AS without_barcode_items,

                COALESCE(
                    SUM(ii.weight_grams),
                    0
                ) AS total_weight_grams,

                COALESCE(
                    SUM(
                        CASE
                            WHEN ii.stock_classification =
                                 'NEW_STOCK'
                            THEN ii.weight_grams
                            ELSE 0
                        END
                    ),
                    0
                ) AS new_stock_weight_grams,

                COALESCE(
                    SUM(
                        CASE
                            WHEN ii.stock_classification =
                                 'OLD_STOCK'
                            THEN ii.weight_grams
                            ELSE 0
                        END
                    ),
                    0
                ) AS old_stock_weight_grams

            FROM inventory_items ii

            LEFT JOIN inventory_barcodes ib
                ON ib.inventory_item_id = ii.id
                AND ib.barcode_status = 'ACTIVE'

            WHERE
                ii.inventory_status = 'IN_STOCK'
            """
        )

        return row or {}

    def get_active_employee_by_id(
            self,
            employee_id: int,
    ) -> dict | None:

        return self._fetch_one(
            """
            SELECT
                id,
                employee_code,
                name
            FROM employees
            WHERE id = %s
              AND is_active = TRUE
            """,
            (employee_id,),
        )

    def get_active_item_type_by_id(
            self,
            item_type_id: int,
    ) -> dict | None:

        return self._fetch_one(
            """
            SELECT
                id,
                code,
                name,
                default_category_id
            FROM item_types
            WHERE id = %s
              AND is_active = TRUE
            """,
            (item_type_id,),
        )

    def get_active_karat_by_id(
            self,
            karat_id: int,
    ) -> dict | None:

        return self._fetch_one(
            """
            SELECT
                id,
                karat_value,
                purity,
                name
            FROM karats
            WHERE id = %s
              AND is_active = TRUE
            """,
            (karat_id,),
        )

    def get_active_category_by_id(
            self,
            category_id: int,
    ) -> dict | None:

        return self._fetch_one(
            """
            SELECT
                id,
                code,
                name
            FROM item_categories
            WHERE id = %s
              AND is_active = TRUE
            """,
            (category_id,),
        )

    def get_active_factory_by_id(
            self,
            factory_id: int,
    ) -> dict | None:

        return self._fetch_one(
            """
            SELECT
                id,
                code,
                name
            FROM factories
            WHERE id = %s
              AND is_active = TRUE
            """,
            (factory_id,),
        )

    def get_inventory_movement_type_id(
            self,
            code: str,
    ) -> int | None:

        row = self._fetch_one(
            """
            SELECT id
            FROM inventory_movement_types
            WHERE code = %s
              AND is_active = TRUE
            """,
            (code,),
        )

        if not row:
            return None

        return int(row["id"])

    def create_inventory_item(
            self,
            item_type_id: int,
            karat_id: int,
            category_id: int | None,
            factory_id: int | None,
            weight_grams,
            size: str | None,
            stock_classification: str,
            acquisition_source: str,
            notes: str | None,
            entered_by_employee_id: int,
            created_by_user_id: int | None = None,
    ) -> int:

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
                    %s,
                    'IN_STOCK',
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    item_type_id,
                    karat_id,
                    category_id,
                    factory_id,
                    weight_grams,
                    size,
                    stock_classification,
                    acquisition_source,
                    notes,
                    created_by_user_id,
                    entered_by_employee_id,
                    created_by_user_id,
                ),
            )

            return int(cursor.lastrowid)

        finally:
            cursor.close()

    def assign_barcode(
            self,
            inventory_item_id: int,
            barcode_value: str,
            assigned_by_user_id: int | None = None,
    ) -> int:

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
                    %s,
                    'Initial barcode assignment'
                )
                """,
                (
                    inventory_item_id,
                    barcode_value,
                    assigned_by_user_id,
                ),
            )

            return int(cursor.lastrowid)

        finally:
            cursor.close()

    def create_inventory_movement(
            self,
            inventory_item_id: int,
            movement_type_id: int,
            stock_classification: str,
            weight_grams,
            performed_by_employee_id: int,
            performed_by_user_id: int | None = None,
    ) -> int:

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
                    %s,

                    NULL,
                    'IN_STOCK',

                    NULL,
                    %s,

                    'Initial inventory entry',
                    NOW(),

                    %s,
                    %s
                )
                """,
                (
                    inventory_item_id,
                    movement_type_id,
                    stock_classification,
                    weight_grams,
                    performed_by_user_id,
                    performed_by_employee_id,
                ),
            )

            return int(cursor.lastrowid)

        finally:
            cursor.close()