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
                manager_approval_below_per_gram
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