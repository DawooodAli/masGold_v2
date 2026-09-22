from database.transaction import DatabaseTransaction
from repositories.inventory_repository import InventoryRepository
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from mysql.connector import IntegrityError

class InventoryService:
    @staticmethod
    def _clean_optional_text(
            value,
            max_length: int,
            error_message: str,
    ) -> str | None:

        if value is None:
            return None

        value = str(value).strip()

        if not value:
            return None

        if len(value) > max_length:
            raise ValueError(error_message)

        return value

    @staticmethod
    def _required_id(
            value,
            error_message: str,
    ) -> int:

        try:
            value = int(value)

        except (TypeError, ValueError):
            raise ValueError(error_message)

        if value <= 0:
            raise ValueError(error_message)

        return value

    @staticmethod
    def _optional_id(value) -> int | None:

        if value is None:
            return None

        try:
            value = int(value)

        except (TypeError, ValueError):
            return None

        if value <= 0:
            return None

        return value

    @staticmethod
    def _parse_weight(value) -> Decimal:

        if value is None:
            raise ValueError(
                "Weight is required."
            )

        text = str(value).strip()

        if not text:
            raise ValueError(
                "Weight is required."
            )

        text = (
            text
            .replace("٫", ".")
            .replace(",", ".")
        )

        try:
            weight = Decimal(text)

        except InvalidOperation:
            raise ValueError(
                "Weight must be a valid number."
            )

        weight = weight.quantize(
            Decimal("0.001"),
            rounding=ROUND_HALF_UP,
        )

        if weight <= 0:
            raise ValueError(
                "Weight must be greater than zero."
            )

        return weight
    def get_stock_items(
        self,
        search: str = "",
        limit: int = 500,
    ) -> list[dict]:

        search = (search or "").strip()

        try:
            limit = int(limit)

        except (TypeError, ValueError):
            limit = 500

        limit = max(
            1,
            min(limit, 1000),
        )


        with DatabaseTransaction() as transaction:

            repository = InventoryRepository(
                transaction.connection
            )

            return repository.get_stock_items(
                search=search,
                limit=limit,
            )


    def get_stock_summary(self) -> dict:

        with DatabaseTransaction() as transaction:

            repository = InventoryRepository(
                transaction.connection
            )

            return repository.get_stock_summary()

    def create_inventory_item(
            self,
            employee_id,
            item_type_id,
            karat_id,
            category_id,
            factory_id,
            weight_grams,
            size,
            stock_classification,
            barcode,
            notes,
            user_id: int | None = None,
    ) -> int:

        employee_id = self._required_id(
            employee_id,
            "Employee is required.",
        )

        item_type_id = self._required_id(
            item_type_id,
            "Item type is required.",
        )

        karat_id = self._required_id(
            karat_id,
            "Karat is required.",
        )

        category_id = self._optional_id(
            category_id
        )

        factory_id = self._optional_id(
            factory_id
        )

        weight = self._parse_weight(
            weight_grams
        )

        size = self._clean_optional_text(
            size,
            50,
            "Size is too long.",
        )

        barcode = self._clean_optional_text(
            barcode,
            100,
            "Barcode is too long.",
        )

        notes = self._clean_optional_text(
            notes,
            1000,
            "Notes are too long.",
        )

        stock_classification = str(
            stock_classification or ""
        ).strip().upper()

        if stock_classification not in (
                "NEW_STOCK",
                "OLD_STOCK",
        ):
            raise ValueError(
                "Invalid stock classification."
            )

        try:

            with DatabaseTransaction() as transaction:

                repository = InventoryRepository(
                    transaction.connection
                )

                # ----------------------------------------------
                # Employee
                # ----------------------------------------------

                employee = (
                    repository
                    .get_active_employee_by_id(
                        employee_id
                    )
                )

                if employee is None:
                    raise ValueError(
                        "Employee is not active or does not exist."
                    )

                # ----------------------------------------------
                # Item type
                # ----------------------------------------------

                item_type = (
                    repository
                    .get_active_item_type_by_id(
                        item_type_id
                    )
                )

                if item_type is None:
                    raise ValueError(
                        "Item type is not active or does not exist."
                    )
                # Use the item's default category when
                # the caller did not explicitly select one.
                if category_id is None:

                    default_category_id = (
                        item_type.get(
                            "default_category_id"
                        )
                    )

                    if default_category_id is not None:
                        category_id = int(
                            default_category_id
                        )
                # ----------------------------------------------
                # Karat
                # ----------------------------------------------

                karat = (
                    repository
                    .get_active_karat_by_id(
                        karat_id
                    )
                )

                if karat is None:
                    raise ValueError(
                        "Karat is not active or does not exist."
                    )

                # ----------------------------------------------
                # Category
                # ----------------------------------------------

                if category_id is not None:

                    category = (
                        repository
                        .get_active_category_by_id(
                            category_id
                        )
                    )

                    if category is None:
                        raise ValueError(
                            "Category is not active or does not exist."
                        )

                # ----------------------------------------------
                # Factory
                # ----------------------------------------------

                if factory_id is not None:

                    factory = (
                        repository
                        .get_active_factory_by_id(
                            factory_id
                        )
                    )

                    if factory is None:
                        raise ValueError(
                            "Factory is not active or does not exist."
                        )

                # ----------------------------------------------
                # Movement type
                # ----------------------------------------------

                movement_type_id = (
                    repository
                    .get_inventory_movement_type_id(
                        "ITEM_CREATED"
                    )
                )

                if movement_type_id is None:
                    raise RuntimeError(
                        "ITEM_CREATED movement type is not configured."
                    )

                # ----------------------------------------------
                # 1. Physical inventory item
                # ----------------------------------------------

                inventory_item_id = (
                    repository.create_inventory_item(
                        item_type_id=item_type_id,
                        karat_id=karat_id,
                        category_id=category_id,
                        factory_id=factory_id,
                        weight_grams=weight,
                        size=size,
                        stock_classification=(
                            stock_classification
                        ),

                        # This screen is manual stock entry.
                        # Purchase receiving will later use
                        # MARKET_PURCHASE.
                        acquisition_source="MANUAL_ENTRY",

                        notes=notes,
                        entered_by_employee_id=(
                            employee_id
                        ),
                        created_by_user_id=user_id,
                    )
                )

                # ----------------------------------------------
                # 2. Optional barcode
                # ----------------------------------------------

                if barcode:
                    repository.assign_barcode(
                        inventory_item_id=(
                            inventory_item_id
                        ),
                        barcode_value=barcode,
                        assigned_by_user_id=user_id,
                    )

                # ----------------------------------------------
                # 3. Permanent movement history
                # ----------------------------------------------

                repository.create_inventory_movement(
                    inventory_item_id=(
                        inventory_item_id
                    ),
                    movement_type_id=(
                        movement_type_id
                    ),
                    stock_classification=(
                        stock_classification
                    ),
                    weight_grams=weight,
                    performed_by_employee_id=(
                        employee_id
                    ),
                    performed_by_user_id=user_id,
                )

                return inventory_item_id


        except IntegrityError as exc:

            if getattr(
                    exc,
                    "errno",
                    None,
            ) == 1062:
                raise ValueError(
                    "Barcode already exists."
                ) from exc

            raise