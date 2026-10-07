from datetime import datetime
from decimal import Decimal, InvalidOperation

from mysql.connector import IntegrityError

from database.transaction import DatabaseTransaction
from repositories.old_gold_conversion_repository import (
    OldGoldConversionRepository,
)
from services.old_gold_availability import held_grams, shortage_message


class OldGoldConversionService:
    """
    Converts bulk old-gold grams into one NEW_STOCK inventory item.

    Atomic transaction:
    - verify old-gold balance by karat
    - create the new inventory item
    - optionally assign barcode
    - append inventory ITEM_CREATED movement
    - append negative old-gold ledger movement
    - append conversion audit link
    """

    MAX_NOTES_LENGTH = 1000
    MAX_SIZE_LENGTH = 50
    MAX_BARCODE_LENGTH = 100

    # Consuming Scrap grams is an old-gold ledger movement: it needs OLD_GOLD_MANAGE ("normal old-gold
    # movements") through the strict write path, and its actor is ALWAYS the authenticated AppSession user.
    MANAGE_PERMISSION = "OLD_GOLD_MANAGE"

    def __init__(self, session=None, authorization=None):
        self._session = session
        self._authorization = authorization

    def can_manage(self) -> bool:
        """UI hint only; the database check in _authorize_write is authoritative."""
        actor = self._session.current_actor() if self._session is not None else None
        return actor is not None and self.MANAGE_PERMISSION in actor.permissions

    def _require_actor(self):
        if self._session is None:
            raise PermissionError("يلزم تسجيل الدخول لتحويل الكسر.")
        actor = self._session.require_authenticated()
        if self.MANAGE_PERMISSION not in actor.permissions:
            raise PermissionError("لا توجد صلاحية لتحويل الكسر.")
        return actor

    def _authorize_write(self, connection, actor):
        """Strict write-time check (locks the user, employee and grants) inside the conversion's transaction."""
        if self._authorization is None:
            from services.authorization_service import AuthorizationService
            self._authorization = AuthorizationService(self._session)
        verified = self._authorization.require_permission_for_write(connection, actor, self.MANAGE_PERMISSION)
        if not self._session.is_current(actor):
            raise PermissionError("انتهت جلسة الدخول.")
        return verified

    @staticmethod
    def _clean_text(value):
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _positive_weight(value, label):
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
                f"{label} must be a valid number."
            ) from exc

        if result <= 0:
            raise ValueError(
                f"{label} must be greater than zero."
            )

        return result

    @staticmethod
    def _conversion_code():
        return datetime.now().strftime(
            "OGC-%Y%m%d-%H%M%S-%f"
        )

    def get_balances(self):
        with DatabaseTransaction() as transaction:
            repository = OldGoldConversionRepository(
                transaction.connection
            )

            return repository.get_old_gold_balances()

    def convert_to_new_stock(
        self,
        *,
        employee_id=None,
        user_id=None,
        item_type_id,
        karat_id,
        category_id,
        factory_id,
        result_weight_grams,
        consumed_old_gold_grams,
        size,
        barcode_value,
        notes,
    ):
        # Authorization first: nothing is validated, read or written for a caller without the right.
        session_actor = self._require_actor()
        if user_id not in (None, "", 0) and int(user_id) != int(session_actor.user_id):
            raise PermissionError("لا يمكن تحديد مستخدم آخر لتحويل الكسر.")      # the actor is the session's

        # No manually chosen employee (owner review 2026-10-02): the authenticated actor is recorded.
        employee_id = int(employee_id or 0)
        user_id = int(session_actor.user_id)
        item_type_id = int(item_type_id or 0)
        karat_id = int(karat_id or 0)
        category_id = int(category_id or 0)
        factory_id = int(factory_id or 0)

        if item_type_id <= 0:
            raise ValueError(
                "Item type is required."
            )

        if karat_id <= 0:
            raise ValueError(
                "Karat is required."
            )

        result_weight = self._positive_weight(
            result_weight_grams,
            "Result weight",
        )

        consumed_weight = self._positive_weight(
            consumed_old_gold_grams,
            "Consumed old-gold weight",
        )

        size = self._clean_text(size)
        barcode_value = self._clean_text(
            barcode_value
        )
        notes = self._clean_text(notes)

        if size and len(size) > self.MAX_SIZE_LENGTH:
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
                # The attributed actor is the verified, locked session user (never the caller's argument).
                user_id = int(self._authorize_write(transaction.connection, session_actor).user_id)

                repository = OldGoldConversionRepository(
                    transaction.connection
                )

                if employee_id > 0:
                    employee = repository.get_active_employee(
                        employee_id
                    )

                    if (
                        not employee
                        or not bool(
                            employee.get("is_active")
                        )
                    ):
                        raise ValueError(
                            "Employee does not exist or is inactive."
                        )

                item_type = repository.get_active_item_type(
                    item_type_id
                )

                if (
                    not item_type
                    or not bool(
                        item_type.get("is_active")
                    )
                ):
                    raise ValueError(
                        "Item type does not exist or is inactive."
                    )

                karat = repository.get_active_karat(
                    karat_id
                )

                if (
                    not karat
                    or not bool(
                        karat.get("is_active")
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
                    category = repository.get_category(
                        category_id
                    )

                    if (
                        not category
                        or not bool(
                            category.get("is_active")
                        )
                    ):
                        raise ValueError(
                            "Category does not exist or is inactive."
                        )

                if factory_id > 0:
                    factory = repository.get_factory(
                        factory_id
                    )

                    if (
                        not factory
                        or not bool(
                            factory.get("is_active")
                        )
                    ):
                        raise ValueError(
                            "Factory does not exist or is inactive."
                        )

                locked_rows = repository.lock_old_gold_balance(
                    karat_id
                )

                available_balance = sum(
                    (
                        Decimal(
                            str(
                                row.get(
                                    "weight_delta_grams"
                                )
                                or 0
                            )
                        )
                        for row in locked_rows
                    ),
                    Decimal("0.000"),
                ).quantize(
                    Decimal("0.001")
                )

                if consumed_weight > available_balance:
                    held = held_grams(repository, karat_id)
                    if held > 0:
                        # 032: the Scrap is physically there but HELD for a manager decision.
                        raise ValueError(
                            shortage_message(
                                karat.get("karat_value") if isinstance(karat, dict) else karat_id,
                                consumed_weight,
                                available_balance,
                                held,
                            )
                        )
                    raise ValueError(
                        "Not enough old-gold balance."
                    )

                result_item_id = repository.create_new_inventory_item(
                    item_type_id=item_type_id,
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
                    weight_grams=result_weight,
                    size=size,
                    notes=notes,
                    employee_id=employee_id or None,
                    user_id=user_id,
                )

                if barcode_value:
                    repository.assign_barcode(
                        inventory_item_id=result_item_id,
                        barcode_value=barcode_value,
                    )

                item_created_type_id = (
                    repository.get_movement_type_id(
                        "ITEM_CREATED"
                    )
                )

                conversion_code = (
                    self._conversion_code()
                )

                repository.create_inventory_movement(
                    inventory_item_id=result_item_id,
                    movement_type_id=item_created_type_id,
                    to_weight_grams=result_weight,
                    employee_id=employee_id or None,
                    user_id=user_id,
                    reason=(
                        f"{conversion_code}: converted from old gold"
                    ),
                )

                old_gold_movement_id = (
                    repository.create_old_gold_out_movement(
                        karat_id=karat_id,
                        consumed_weight_grams=consumed_weight,
                        result_inventory_item_id=result_item_id,
                        employee_id=employee_id or None,
                    user_id=user_id,
                        conversion_code=conversion_code,
                        notes=notes,
                    )
                )

                weight_difference = (
                    result_weight
                    - consumed_weight
                ).quantize(
                    Decimal("0.001")
                )

                repository.create_conversion_batch(
                    conversion_code=conversion_code,
                    old_gold_movement_id=old_gold_movement_id,
                    result_inventory_item_id=result_item_id,
                    karat_id=karat_id,
                    consumed_weight_grams=consumed_weight,
                    result_weight_grams=result_weight,
                    weight_difference_grams=weight_difference,
                    employee_id=employee_id or None,
                    user_id=user_id,
                    notes=notes,
                )

                remaining_balance = (
                    available_balance
                    - consumed_weight
                ).quantize(
                    Decimal("0.001")
                )

                return {
                    "conversion_code": conversion_code,
                    "result_inventory_item_id": result_item_id,
                    "karat_value": karat.get("karat_value"),
                    "available_before_grams": available_balance,
                    "consumed_weight_grams": consumed_weight,
                    "result_weight_grams": result_weight,
                    "weight_difference_grams": weight_difference,
                    "remaining_balance_grams": remaining_balance,
                }

        except IntegrityError as exc:
            if getattr(exc, "errno", None) == 1062:
                raise ValueError(
                    "Barcode already exists."
                ) from exc

            raise
