from repositories.base_repository import BaseRepository


class MasterDataRepository(BaseRepository):

    def get_active_karats(self) -> list[dict]:
        return self._fetch_all(
            """
            SELECT
                id,
                karat_value,
                purity,
                name,
                sort_order
            FROM karats
            WHERE is_active = TRUE
            ORDER BY sort_order, karat_value
            """
        )

    def get_active_categories(self) -> list[dict]:
        return self._fetch_all(
            """
            SELECT
                id,
                code,
                name,
                purchase_making_min_per_gram,
                purchase_making_max_per_gram,
                sale_making_min_per_gram,
                sale_making_max_per_gram,
                manager_approval_below_per_gram,
                sort_order,
                notes,
                is_active
            FROM item_categories
            WHERE is_active = TRUE
            ORDER BY sort_order, code
            """
        )

    def get_all_categories(self) -> list[dict]:
        return self._fetch_all(
            """
            SELECT
                id,
                code,
                name,
                purchase_making_min_per_gram,
                purchase_making_max_per_gram,
                sale_making_min_per_gram,
                sale_making_max_per_gram,
                manager_approval_below_per_gram,
                sort_order,
                notes,
                is_active
            FROM item_categories
            ORDER BY
                sort_order,
                code
            """
        )
    def get_active_factories(self) -> list[dict]:
        return self._fetch_all(
            """
            SELECT
                id,
                code,
                name,
                notes
            FROM factories
            WHERE is_active = TRUE
            ORDER BY name
            """
        )

    def get_active_employees(self) -> list[dict]:
        return self._fetch_all(
            """
            SELECT
                id,
                employee_code,
                name,
                notes
            FROM employees
            WHERE is_active = TRUE
            ORDER BY name
            """
        )
    def get_all_factories(self) -> list[dict]:
        return self._fetch_all(
            """
            SELECT
                id,
                code,
                name,
                notes,
                is_active
            FROM factories
            ORDER BY
                is_active DESC,
                name
            """
        )

    def get_active_item_types(self) -> list[dict]:
        return self._fetch_all(
            """
            SELECT
                it.id,
                it.code,
                it.name,
                it.default_category_id,
                c.code AS default_category_code,
                c.name AS default_category_name,
                it.notes
            FROM item_types it

            LEFT JOIN item_categories c
                ON c.id = it.default_category_id

            WHERE it.is_active = TRUE

            ORDER BY
                it.sort_order,
                it.name
            """
        )

    def get_all_item_types(self) -> list[dict]:
        return self._fetch_all(
            """
            SELECT
                it.id,
                it.code,
                it.name,
                it.default_category_id,
                c.code AS default_category_code,
                c.name AS default_category_name,
                it.notes,
                it.is_active
            FROM item_types it

            LEFT JOIN item_categories c
                ON c.id = it.default_category_id

            ORDER BY
                it.is_active DESC,
                it.sort_order,
                it.name
            """
        )
    def get_category_by_id(
        self,
        category_id: int,
    ) -> dict | None:

        return self._fetch_one(
            """
            SELECT
                id,
                code,
                name,
                purchase_making_min_per_gram,
                purchase_making_max_per_gram,
                sale_making_min_per_gram,
                sale_making_max_per_gram,
                manager_approval_below_per_gram,
                notes,
                is_active
            FROM item_categories
            WHERE id = %s
            """,
            (category_id,),
        )

    def get_factory_by_id(
            self,
            factory_id: int,
    ) -> dict | None:

        return self._fetch_one(
            """
            SELECT
                id,
                code,
                name,
                notes,
                is_active
            FROM factories
            WHERE id = %s
            """,
            (factory_id,),
        )

    def get_item_type_by_id(
            self,
            item_type_id: int,
    ) -> dict | None:

        return self._fetch_one(
            """
            SELECT
                id,
                code,
                name,
                default_category_id,
                notes,
                is_active
            FROM item_types
            WHERE id = %s
            """,
            (item_type_id,),
        )
    def create_factory(
        self,
        code: str | None,
        name: str,
        notes: str | None = None,
    ) -> int:

        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO factories (
                    code,
                    name,
                    notes
                )
                VALUES (%s, %s, %s)
                """,
                (
                    code,
                    name,
                    notes,
                ),
            )

            return cursor.lastrowid

        finally:
            cursor.close()

    def update_factory(
        self,
        factory_id: int,
        code: str | None,
        name: str,
        notes: str | None = None,
        is_active: bool = True,
    ) -> None:

        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                UPDATE factories
                SET
                    code = %s,
                    name = %s,
                    notes = %s,
                    is_active = %s
                WHERE id = %s
                """,
                (
                    code,
                    name,
                    notes,
                    is_active,
                    factory_id,
                ),
            )


        finally:
            cursor.close()

    def create_item_type(
        self,
        code: str | None,
        name: str,
        default_category_id: int | None,
        notes: str | None = None,
    ) -> int:

        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO item_types (
                    code,
                    name,
                    default_category_id,
                    notes
                )
                VALUES (%s, %s, %s, %s)
                """,
                (
                    code,
                    name,
                    default_category_id,
                    notes,
                ),
            )

            return cursor.lastrowid

        finally:
            cursor.close()

    def update_item_type(
        self,
        item_type_id: int,
        code: str | None,
        name: str,
        default_category_id: int | None,
        notes: str | None = None,
        is_active: bool = True,
    ) -> None:

        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                UPDATE item_types
                SET
                    code = %s,
                    name = %s,
                    default_category_id = %s,
                    notes = %s,
                    is_active = %s
                WHERE id = %s
                """,
                (
                    code,
                    name,
                    default_category_id,
                    notes,
                    is_active,
                    item_type_id,
                ),
            )

            if cursor.rowcount == 0:
                raise ValueError(
                    f"Item type ID {item_type_id} does not exist."
                )

        finally:
            cursor.close()

    # =========================================================
    # PERMANENT DELETE (unused master data only)
    # =========================================================

    # Every table that makes a master record "used by stock". Checked with locking reads so a
    # concurrent insert either commits first (and is seen) or waits for this delete to finish.
    # inventory_items.factory_id / category_id are ON DELETE SET NULL in the schema, so this check —
    # not the foreign key — is what keeps historical stock references intact.
    STOCK_REFERENCES = {
        "factories": (
            ("inventory_items", "factory_id"),
            ("inventory_reconciliation_cases", "observed_factory_id"),
            ("old_gold_movements", "factory_id"),
            ("analytics_segments", "factory_id"),
        ),
        "item_types": (
            ("inventory_items", "item_type_id"),
            ("inventory_reconciliation_cases", "observed_item_type_id"),
            ("analytics_segments", "item_type_id"),
        ),
        "item_categories": (
            ("inventory_items", "category_id"),
            ("inventory_reconciliation_cases", "observed_category_id"),
            ("analytics_segments", "category_id"),
        ),
    }

    def lock_master_row(self, table: str, row_id: int) -> dict | None:
        if table not in self.STOCK_REFERENCES:
            raise ValueError(f"Unknown master table {table}.")
        return self._fetch_one(
            f"SELECT id FROM {table} WHERE id = %s FOR UPDATE",
            (row_id,),
        )

    def stock_references(self, table: str, row_id: int) -> list[str]:
        """Referencing tables that hold at least one row for this master record (locking reads)."""
        found = []
        for child, column in self.STOCK_REFERENCES[table]:
            if self._fetch_one(
                f"SELECT 1 AS used FROM {child} WHERE {column} = %s LIMIT 1 FOR SHARE",
                (row_id,),
            ):
                found.append(child)
        return found

    def item_types_defaulting_to(self, category_id: int) -> int:
        row = self._fetch_one(
            "SELECT COUNT(*) AS n FROM item_types WHERE default_category_id = %s FOR SHARE",
            (category_id,),
        )
        return int(row["n"])

    def delete_master_row(self, table: str, row_id: int) -> None:
        if table not in self.STOCK_REFERENCES:
            raise ValueError(f"Unknown master table {table}.")
        cursor = self.connection.cursor()
        try:
            cursor.execute(f"DELETE FROM {table} WHERE id = %s", (row_id,))
            if cursor.rowcount != 1:
                raise ValueError("Master record does not exist.")
        finally:
            cursor.close()

    # =========================================================
    # CATEGORIES
    # =========================================================

    def create_category(
        self,
        code: str,
        name: str,
        purchase_min,
        purchase_max,
        sale_min,
        sale_max,
        manager_approval_below,
        notes: str | None = None,
    ) -> int:

        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO item_categories (
                    code,
                    name,
                    purchase_making_min_per_gram,
                    purchase_making_max_per_gram,
                    sale_making_min_per_gram,
                    sale_making_max_per_gram,
                    manager_approval_below_per_gram,
                    sort_order,
                    notes
                )
                SELECT %s, %s, %s, %s, %s, %s, %s, COALESCE(MAX(sort_order), 0) + 1, %s
                FROM item_categories
                """,
                (
                    code,
                    name,
                    purchase_min,
                    purchase_max,
                    sale_min,
                    sale_max,
                    manager_approval_below,
                    notes,
                ),
            )

            return cursor.lastrowid

        finally:
            cursor.close()

    def set_category_active(
        self,
        category_id: int,
        is_active: bool,
    ) -> None:

        cursor = self.connection.cursor()

        try:
            cursor.execute(
                "UPDATE item_categories SET is_active = %s WHERE id = %s",
                (is_active, category_id),
            )

        finally:
            cursor.close()

    def update_category_rules(
        self,
        category_id: int,
        purchase_min: float | None,
        purchase_max: float | None,
        sale_min: float | None,
        sale_max: float | None,
        manager_approval_below: float | None,
        notes: str | None = None,
        is_active: bool = True,
    ) -> None:

        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                UPDATE item_categories
                SET
                    purchase_making_min_per_gram = %s,
                    purchase_making_max_per_gram = %s,
                    sale_making_min_per_gram = %s,
                    sale_making_max_per_gram = %s,
                    manager_approval_below_per_gram = %s,
                    notes = %s,
                    is_active = %s
                WHERE id = %s
                """,
                (
                    purchase_min,
                    purchase_max,
                    sale_min,
                    sale_max,
                    manager_approval_below,
                    notes,
                    is_active,
                    category_id,
                ),
            )


        finally:
            cursor.close()