from repositories.base_repository import BaseRepository


# The ONE definition of "currently physical shop stock" (alias ii = inventory_items). Every stock
# list, summary, the Stock Balance (بيان المخزون) and the Barcode Stocktake (الجرد بالباركود) use it.
# SOLD, RETURNED_PENDING (awaiting inspection), CONVERTED_TO_OLD_GOLD, SCRAPPED and REMOVED are not
# current stock; a returned piece counts again only after inspection puts it back IN_STOCK.
CURRENT_STOCK = "ii.inventory_status = 'IN_STOCK'"
STOCK_CLASSES = ("NEW_STOCK", "OLD_STOCK")


class InventoryRepository(BaseRepository):

    def get_stock_items(
        self,
        search: str = "",
        limit: int = 500,
        karat_id: int | None = None,
        item_type_id: int | None = None,
    ) -> list[dict]:

        search = (search or "").strip()
        like = f"%{search}%"
        # Karat / item-type filters combine with the search (and with each other).
        extra, extra_params = "", []
        if karat_id is not None:
            extra += " AND ii.karat_id = %s"
            extra_params.append(karat_id)
        if item_type_id is not None:
            extra += " AND ii.item_type_id = %s"
            extra_params.append(item_type_id)

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
                {current}{extra}

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
            """.format(current=CURRENT_STOCK, extra=extra),
            (
                *extra_params,
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
                            WHEN ii.stock_classification = 'OLD_STOCK'
                            THEN 1
                            ELSE 0
                        END
                    ),
                    0
                ) AS old_stock_items,

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
                {current}
            """.format(current=CURRENT_STOCK)
        )

        return row or {}

    # ========================================================
    # STOCK BALANCE — بيان المخزون (read only)
    # ========================================================

    def get_stock_balance(self, stock_classification: str | None = None) -> list[dict]:
        """Current stock aggregated in MySQL: one row per (karat, item type) that has pieces.
        COUNT / SUM run on DECIMAL(12,3) weights; nothing is loaded per piece."""
        params = []
        where = CURRENT_STOCK
        if stock_classification is not None:
            where += " AND ii.stock_classification = %s"
            params.append(stock_classification)
        return self._fetch_all(
            f"""
            SELECT
                k.id AS karat_id,
                k.karat_value,
                k.name AS karat_name,
                k.sort_order AS karat_sort_order,
                it.id AS item_type_id,
                it.name AS item_type_name,
                COUNT(*) AS piece_count,
                SUM(ii.weight_grams) AS weight_grams
            FROM inventory_items ii
            INNER JOIN karats k ON k.id = ii.karat_id
            INNER JOIN item_types it ON it.id = ii.item_type_id
            WHERE {where}
            GROUP BY k.id, k.karat_value, k.name, k.sort_order, it.id, it.name
            ORDER BY k.sort_order, k.karat_value DESC, it.name
            """,
            tuple(params),
        )

    # ========================================================
    # BARCODE STOCKTAKE — الجرد بالباركود (read only source set)
    # ========================================================

    def get_stocktake_source(self, item_type_id: int | None = None, karat_id: int | None = None) -> list[dict]:
        """The pieces expected on the shelf for one counting scope (same predicate as the balance)."""
        params = []
        where = CURRENT_STOCK
        if item_type_id is not None:
            where += " AND ii.item_type_id = %s"
            params.append(item_type_id)
        if karat_id is not None:
            where += " AND ii.karat_id = %s"
            params.append(karat_id)
        return self._fetch_all(
            f"""
            SELECT
                ii.id AS inventory_item_id,
                ib.barcode_value,
                it.name AS item_type_name,
                ii.karat_id,
                k.karat_value,
                k.sort_order AS karat_sort_order,
                ii.weight_grams,
                ii.size
            FROM inventory_items ii
            INNER JOIN item_types it ON it.id = ii.item_type_id
            INNER JOIN karats k ON k.id = ii.karat_id
            LEFT JOIN inventory_barcodes ib
                ON ib.inventory_item_id = ii.id
                AND ib.barcode_status = 'ACTIVE'
            WHERE {where}
            ORDER BY k.sort_order, it.name, ib.barcode_value, ii.id
            """,
            tuple(params),
        )

    def get_stocktake_filters(self) -> dict:
        """Item types and karats offered as stocktake filters (canonical reference data)."""
        return {
            "item_types": self._fetch_all(
                "SELECT id, name FROM item_types WHERE is_active = TRUE ORDER BY sort_order, name"),
            "karats": self._fetch_all(
                "SELECT id, karat_value, name FROM karats WHERE is_active = TRUE ORDER BY sort_order, karat_value"),
        }

    def get_stock_weight_by_karat(self) -> list[dict]:
        """Current stock grams per karat (MySQL SUM on DECIMAL); the 21K equivalent is derived from it."""
        return self._fetch_all(
            f"""
            SELECT k.karat_value, SUM(ii.weight_grams) AS weight_grams
            FROM inventory_items ii
            INNER JOIN karats k ON k.id = ii.karat_id
            WHERE {CURRENT_STOCK}
            GROUP BY k.karat_value
            """
        )

    def get_returned_pending_weight_by_karat(self) -> list[dict]:
        """Returned pieces physically back in the shop but awaiting inspection (not CURRENT_STOCK until a manager decides); read only."""
        return self._fetch_all(
            """
            SELECT k.karat_value, SUM(ii.weight_grams) AS weight_grams
            FROM inventory_items ii
            INNER JOIN karats k ON k.id = ii.karat_id
            WHERE ii.inventory_status = 'RETURNED_PENDING'
            GROUP BY k.karat_value
            """
        )

    # ========================================================
    # EXACT IDENTITY — any status (traceability, never "current stock")
    # ========================================================

    def find_piece_identity(self, text: str) -> dict | None:
        """One piece by its EXACT barcode (any barcode status) or its item number (#7 / 7), whatever its
        inventory status. Read only: it is never listed, counted, sold or re-barcoded from here."""
        text = (text or "").strip()
        if not text:
            return None
        number = text[1:] if text.startswith("#") else text
        item_id = int(number) if number.isdigit() and len(number) <= 18 else None
        piece = self._fetch_one(
            """
            SELECT ii.id AS inventory_item_id, ii.inventory_status, ii.weight_grams, ii.size,
                   ii.stock_classification, ii.karat_id, ii.item_type_id,
                   it.name AS item_type_name, k.karat_value, f.code AS factory_code, f.name AS factory_name,
                   c.code AS category_code,
                   (SELECT b.barcode_value FROM inventory_barcodes b WHERE b.inventory_item_id = ii.id
                     ORDER BY (b.barcode_status = 'ACTIVE') DESC, b.id DESC LIMIT 1) AS barcode_value
            FROM inventory_items ii
            INNER JOIN item_types it ON it.id = ii.item_type_id
            INNER JOIN karats k ON k.id = ii.karat_id
            LEFT JOIN factories f ON f.id = ii.factory_id
            LEFT JOIN item_categories c ON c.id = ii.category_id
            WHERE ii.id = (SELECT b.inventory_item_id FROM inventory_barcodes b WHERE b.barcode_value = %s
                           ORDER BY b.id DESC LIMIT 1)
               OR ii.id = %s
            ORDER BY (ii.id = %s) ASC
            LIMIT 1
            """,
            (text, item_id if item_id is not None else -1, item_id if item_id is not None else -1),
        )
        if piece is None:
            return None
        sale = self._fetch_one(
            """
            SELECT i.id AS invoice_id, i.invoice_number, COALESCE(i.finalized_at, i.invoice_date) AS invoice_date,
                   s.customer_name_snapshot AS customer_name
            FROM invoice_lines l
            INNER JOIN invoices i ON i.id = l.invoice_id
            LEFT JOIN invoice_customer_snapshots s ON s.invoice_id = i.id
            WHERE l.inventory_item_id = %s AND l.line_type = 'SELL_INVENTORY'
            ORDER BY i.id DESC
            LIMIT 1
            """,
            (piece["inventory_item_id"],),
        )
        piece["sale"] = sale
        return piece

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
            entered_by_employee_id: int | None,
            created_by_user_id: int | None = None,
            acquisition_making_per_gram=None,
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
                    updated_by_user_id,
                    acquisition_making_per_gram
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
                    acquisition_making_per_gram,
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
            performed_by_employee_id: int | None,
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