from datetime import datetime
from decimal import Decimal, InvalidOperation

from mysql.connector import IntegrityError

from database.transaction import DatabaseTransaction
from repositories.inventory_conversion_repository import (
    InventoryConversionRepository,
)


class InventoryConversionService:
    """
    OLD_STOCK -> NEW_STOCK conversion.

    One transaction:
    - validates and locks source pieces
    - creates the resulting NEW_STOCK piece
    - consumes the selected OLD_STOCK pieces
    - writes movement ledger rows
    - writes conversion linkage/audit
    """

    MAX_NOTES_LENGTH = 1000
    MAX_SIZE_LENGTH = 50
    MAX_BARCODE_LENGTH = 100

    @staticmethod
    def _clean_text(value):
        value = str(
            value or ""
        ).strip()

        return value or None

    @staticmethod
    def _clean_ids(values):
        result = []
        seen = set()

        for value in values or []:
            item_id = int(value)

            if (
                item_id <= 0
                or item_id in seen
            ):
                continue

            result.append(item_id)
            seen.add(item_id)

        return result

    @staticmethod
    def _decimal_weight(value):
        try:
            result = Decimal(
                str(value).strip()
            ).quantize(
                Decimal("0.001")
            )

        except (
            InvalidOperation,
            ValueError,
            AttributeError,
        ) as exc:
            raise ValueError(
                "Weight must be a valid number."
            ) from exc

        if result <= 0:
            raise ValueError(
                "Weight must be greater than zero."
            )

        return result

    @staticmethod
    def _conversion_code():
        return datetime.now().strftime(
            "CONV-%Y%m%d-%H%M%S-%f"
        )

    def get_available_old_stock(self):
        with DatabaseTransaction() as transaction:
            repository = (
                InventoryConversionRepository(
                    transaction.connection
                )
            )

            return (
                repository
                .get_available_old_stock()
            )

    def convert_old_stock_to_new(
        self,
        *,
        employee_id,
        item_type_id,
        karat_id,
        category_id,
        factory_id,
        weight_grams,
        size,
        barcode_value,
        notes,
        source_item_ids,
    ):
        employee_id = int(
            employee_id or 0
        )

        item_type_id = int(
            item_type_id or 0
        )

        karat_id = int(
            karat_id or 0
        )

        category_id = int(
            category_id or 0
        )

        factory_id = int(
            factory_id or 0
        )

        source_item_ids = (
            self._clean_ids(
                source_item_ids
            )
        )

        if employee_id <= 0:
            raise ValueError(
                "Employee is required."
            )

        if item_type_id <= 0:
            raise ValueError(
                "Item type is required."
            )

        if karat_id <= 0:
            raise ValueError(
                "Karat is required."
            )

        if not source_item_ids:
            raise ValueError(
                "At least one old-stock source item is required."
            )

        result_weight = (
            self._decimal_weight(
                weight_grams
            )
        )

        size = self._clean_text(
            size
        )

        barcode_value = (
            self._clean_text(
                barcode_value
            )
        )

        notes = self._clean_text(
            notes
        )

        if (
            size
            and len(size)
            > self.MAX_SIZE_LENGTH
        ):
            raise ValueError(
                "Size is too long."
            )

        if (
            barcode_value
            and len(barcode_value)
            > self.MAX_BARCODE_LENGTH
        ):
            raise ValueError(
                "Barcode is too long."
            )

        if (
            notes
            and len(notes)
            > self.MAX_NOTES_LENGTH
        ):
            raise ValueError(
                "Notes are too long."
            )

        try:
            with DatabaseTransaction() as transaction:
                repository = (
                    InventoryConversionRepository(
                        transaction.connection
                    )
                )

                employee = (
                    repository
                    .get_active_employee(
                        employee_id
                    )
                )

                if (
                    not employee
                    or not bool(
                        employee.get(
                            "is_active"
                        )
                    )
                ):
                    raise ValueError(
                        "Employee does not exist or is inactive."
                    )

                item_type = (
                    repository
                    .get_active_item_type(
                        item_type_id
                    )
                )

                if (
                    not item_type
                    or not bool(
                        item_type.get(
                            "is_active"
                        )
                    )
                ):
                    raise ValueError(
                        "Item type does not exist or is inactive."
                    )

                karat = (
                    repository
                    .get_active_karat(
                        karat_id
                    )
                )

                if (
                    not karat
                    or not bool(
                        karat.get(
                            "is_active"
                        )
                    )
                ):
                    raise ValueError(
                        "Karat does not exist or is inactive."
                    )

                if category_id <= 0:
                    category_id = int(
                        item_type.get(
                            "default_category_id"
                        )
                        or 0
                    )

                if category_id > 0:
                    category = (
                        repository
                        .get_category(
                            category_id
                        )
                    )

                    if (
                        not category
                        or not bool(
                            category.get(
                                "is_active"
                            )
                        )
                    ):
                        raise ValueError(
                            "Category does not exist or is inactive."
                        )

                if factory_id > 0:
                    factory = (
                        repository
                        .get_factory(
                            factory_id
                        )
                    )

                    if (
                        not factory
                        or not bool(
                            factory.get(
                                "is_active"
                            )
                        )
                    ):
                        raise ValueError(
                            "Factory does not exist or is inactive."
                        )

                sources = (
                    repository
                    .lock_old_stock_sources(
                        source_item_ids
                    )
                )

                if len(sources) != len(
                    source_item_ids
                ):
                    raise ValueError(
                        "One or more selected old-stock pieces do not exist."
                    )

                for source in sources:
                    if (
                        source.get(
                            "stock_classification"
                        )
                        != "OLD_STOCK"
                        or source.get(
                            "inventory_status"
                        )
                        != "IN_STOCK"
                    ):
                        raise ValueError(
                            "One or more selected pieces are no longer available as old stock."
                        )

                    if int(
                        source.get(
                            "karat_id"
                        )
                        or 0
                    ) != karat_id:
                        raise ValueError(
                            "All selected old-stock pieces must have the same karat as the resulting new item."
                        )

                source_total = sum(
                    (
                        Decimal(
                            str(
                                source[
                                    "weight_grams"
                                ]
                            )
                        )
                        for source in sources
                    ),
                    Decimal("0.000"),
                ).quantize(
                    Decimal("0.001")
                )

                result_item_id = (
                    repository
                    .create_new_stock_item(
                        item_type_id=(
                            item_type_id
                        ),
                        karat_id=karat_id,
                        category_id=(
                            category_id
                            if category_id > 0
                            else None
                        ),
                        factory_id=(
                            factory_id
                            if factory_id > 0
                            else None
                        ),
                        weight_grams=(
                            result_weight
                        ),
                        size=size,
                        barcode_value=(
                            barcode_value
                        ),
                        notes=notes,
                        employee_id=(
                            employee_id
                        ),
                    )
                )

                item_created_type_id = (
                    repository
                    .get_movement_type_id(
                        "ITEM_CREATED"
                    )
                )

                old_to_new_type_id = (
                    repository
                    .get_movement_type_id(
                        "OLD_TO_NEW"
                    )
                )

                conversion_code = (
                    self._conversion_code()
                )

                weight_difference = (
                    result_weight
                    - source_total
                ).quantize(
                    Decimal("0.001")
                )

                batch_id = (
                    repository
                    .create_conversion_batch(
                        conversion_code=(
                            conversion_code
                        ),
                        result_inventory_item_id=(
                            result_item_id
                        ),
                        source_total_weight_grams=(
                            source_total
                        ),
                        result_weight_grams=(
                            result_weight
                        ),
                        weight_difference_grams=(
                            weight_difference
                        ),
                        employee_id=(
                            employee_id
                        ),
                        notes=notes,
                    )
                )

                # Result piece creation movement.
                repository.create_inventory_movement(
                    inventory_item_id=(
                        result_item_id
                    ),
                    movement_type_id=(
                        item_created_type_id
                    ),
                    from_stock_classification=None,
                    to_stock_classification=(
                        "NEW_STOCK"
                    ),
                    from_inventory_status=None,
                    to_inventory_status=(
                        "IN_STOCK"
                    ),
                    from_weight_grams=None,
                    to_weight_grams=(
                        result_weight
                    ),
                    reason=(
                        f"{conversion_code}: created from old stock"
                    ),
                    performed_by_employee_id=(
                        employee_id
                    ),
                )

                for source in sources:
                    source_item_id = int(
                        source[
                            "inventory_item_id"
                        ]
                    )

                    source_weight = (
                        Decimal(
                            str(
                                source[
                                    "weight_grams"
                                ]
                            )
                        ).quantize(
                            Decimal(
                                "0.001"
                            )
                        )
                    )

                    repository.consume_old_stock_item(
                        source_item_id
                    )

                    repository.create_inventory_movement(
                        inventory_item_id=(
                            source_item_id
                        ),
                        movement_type_id=(
                            old_to_new_type_id
                        ),
                        from_stock_classification=(
                            "OLD_STOCK"
                        ),
                        to_stock_classification=(
                            "OLD_STOCK"
                        ),
                        from_inventory_status=(
                            "IN_STOCK"
                        ),
                        to_inventory_status=(
                            "REMOVED"
                        ),
                        from_weight_grams=(
                            source_weight
                        ),
                        to_weight_grams=(
                            source_weight
                        ),
                        reason=(
                            f"{conversion_code}: consumed into new item #{result_item_id}"
                        ),
                        performed_by_employee_id=(
                            employee_id
                        ),
                    )

                    repository.add_conversion_source(
                        batch_id=batch_id,
                        source_item_id=(
                            source_item_id
                        ),
                        source_weight_grams=(
                            source_weight
                        ),
                        source_barcode_value=(
                            source.get(
                                "barcode_value"
                            )
                        ),
                    )

                return {
                    "conversion_code": (
                        conversion_code
                    ),
                    "result_inventory_item_id": (
                        result_item_id
                    ),
                    "source_item_count": (
                        len(sources)
                    ),
                    "source_total_weight_grams": (
                        source_total
                    ),
                    "result_weight_grams": (
                        result_weight
                    ),
                    "weight_difference_grams": (
                        weight_difference
                    ),
                }

        except IntegrityError as exc:
            # MySQL duplicate key 1062 is most likely an existing barcode.
            if getattr(
                exc,
                "errno",
                None,
            ) == 1062:
                raise ValueError(
                    "Barcode already exists."
                ) from exc

            raise
