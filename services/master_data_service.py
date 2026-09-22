from mysql.connector import IntegrityError

from database.transaction import DatabaseTransaction
from repositories.master_data_repository import MasterDataRepository


class MasterDataService:

    @staticmethod
    def _clean_text(
        value: str | None,
    ) -> str | None:

        if value is None:
            return None

        value = value.strip()

        return value or None

    @staticmethod
    def _validate_range(
        minimum: float | None,
        maximum: float | None,
        label: str,
    ) -> None:

        if minimum is not None and minimum < 0:
            raise ValueError(
                f"{label} minimum cannot be negative."
            )

        if maximum is not None and maximum < 0:
            raise ValueError(
                f"{label} maximum cannot be negative."
            )

        if (
            minimum is not None
            and maximum is not None
            and minimum > maximum
        ):
            raise ValueError(
                f"{label} minimum cannot exceed maximum."
            )

    def create_factory(
        self,
        name: str,
        code: str | None = None,
        notes: str | None = None,
    ) -> int:

        name = self._clean_text(name)
        code = self._clean_text(code)
        notes = self._clean_text(notes)

        if not name:
            raise ValueError(
                "Factory name is required."
            )

        try:
            with DatabaseTransaction() as transaction:

                repository = MasterDataRepository(
                    transaction.connection
                )

                return repository.create_factory(
                    code=code,
                    name=name,
                    notes=notes,
                )

        except IntegrityError as exc:
            raise ValueError(
                "Factory code already exists."
            ) from exc

    def update_factory(
        self,
        factory_id: int,
        name: str,
        code: str | None = None,
        notes: str | None = None,
        is_active: bool = True,
    ) -> None:

        name = self._clean_text(name)
        code = self._clean_text(code)
        notes = self._clean_text(notes)

        if not name:
            raise ValueError(
                "Factory name is required."
            )

        try:
            with DatabaseTransaction() as transaction:

                repository = MasterDataRepository(
                    transaction.connection
                )
                existing = repository.get_factory_by_id(
                    factory_id
                )

                if existing is None:
                    raise ValueError(
                        "Factory does not exist."
                    )
                repository.update_factory(
                    factory_id=factory_id,
                    code=code,
                    name=name,
                    notes=notes,
                    is_active=is_active,
                )

        except IntegrityError as exc:
            raise ValueError(
                "Factory code already exists."
            ) from exc

    def create_item_type(
        self,
        name: str,
        code: str | None = None,
        default_category_id: int | None = None,
        notes: str | None = None,
    ) -> int:

        name = self._clean_text(name)
        code = self._clean_text(code)
        notes = self._clean_text(notes)

        if not name:
            raise ValueError(
                "Item type name is required."
            )

        try:
            with DatabaseTransaction() as transaction:

                repository = MasterDataRepository(
                    transaction.connection
                )

                if default_category_id is not None:
                    category = repository.get_category_by_id(
                        default_category_id
                    )

                    if category is None:
                        raise ValueError(
                            "Default category does not exist."
                        )

                return repository.create_item_type(
                    code=code,
                    name=name,
                    default_category_id=default_category_id,
                    notes=notes,
                )

        except IntegrityError as exc:
            raise ValueError(
                "Item type name or code already exists."
            ) from exc

    def update_item_type(
            self,
            item_type_id: int,
            name: str,
            code: str | None = None,
            default_category_id: int | None = None,
            notes: str | None = None,
            is_active: bool = True,
    ) -> None:

        name = self._clean_text(name)
        code = self._clean_text(code)
        notes = self._clean_text(notes)

        if not name:
            raise ValueError(
                "Item type name is required."
            )

        try:

            with DatabaseTransaction() as transaction:

                repository = MasterDataRepository(
                    transaction.connection
                )

                existing = repository.get_item_type_by_id(
                    item_type_id
                )

                if existing is None:
                    raise ValueError(
                        "Item type does not exist."
                    )

                if default_category_id is not None:

                    category = repository.get_category_by_id(
                        default_category_id
                    )

                    if category is None:
                        raise ValueError(
                            "Default category does not exist."
                        )

                repository.update_item_type(
                    item_type_id=item_type_id,
                    code=code,
                    name=name,
                    default_category_id=default_category_id,
                    notes=notes,
                    is_active=is_active,
                )

        except IntegrityError as exc:

            raise ValueError(
                "Item type name or code already exists."
            ) from exc
    def update_category_rules(
        self,
        category_id: int,
        purchase_min: float | None = None,
        purchase_max: float | None = None,
        sale_min: float | None = None,
        sale_max: float | None = None,
        manager_approval_below: float | None = None,
        notes: str | None = None,
        is_active: bool = True,
    ) -> None:

        self._validate_range(
            purchase_min,
            purchase_max,
            "Purchase making",
        )

        self._validate_range(
            sale_min,
            sale_max,
            "Sale making",
        )

        if (
            manager_approval_below is not None
            and manager_approval_below < 0
        ):
            raise ValueError(
                "Manager approval threshold cannot be negative."
            )

        notes = self._clean_text(notes)

        with DatabaseTransaction() as transaction:

            repository = MasterDataRepository(
                transaction.connection
            )

            category = repository.get_category_by_id(
                category_id
            )

            if category is None:
                raise ValueError(
                    "Category does not exist."
                )

            repository.update_category_rules(
                category_id=category_id,
                purchase_min=purchase_min,
                purchase_max=purchase_max,
                sale_min=sale_min,
                sale_max=sale_max,
                manager_approval_below=manager_approval_below,
                notes=notes,
                is_active=is_active,
            )

    def get_karats(self) -> list[dict]:

        with DatabaseTransaction() as transaction:
            repository = MasterDataRepository(
                transaction.connection
            )

            return repository.get_active_karats()

    def get_categories(self) -> list[dict]:

        with DatabaseTransaction() as transaction:
            repository = MasterDataRepository(
                transaction.connection
            )

            return repository.get_active_categories()

    def get_all_categories(self) -> list[dict]:

        with DatabaseTransaction() as transaction:
            repository = MasterDataRepository(
                transaction.connection
            )

            return repository.get_all_categories()
    def get_factories(self) -> list[dict]:

        with DatabaseTransaction() as transaction:
            repository = MasterDataRepository(
                transaction.connection
            )

            return repository.get_active_factories()

    def get_all_factories(self) -> list[dict]:

        with DatabaseTransaction() as transaction:
            repository = MasterDataRepository(
                transaction.connection
            )

            return repository.get_all_factories()

    def get_employees(self) -> list[dict]:

        with DatabaseTransaction() as transaction:
            repository = MasterDataRepository(
                transaction.connection
            )

            return repository.get_active_employees()
    def get_item_types(self) -> list[dict]:

        with DatabaseTransaction() as transaction:
            repository = MasterDataRepository(
                transaction.connection
            )

            return repository.get_active_item_types()

    def get_all_item_types(self) -> list[dict]:

        with DatabaseTransaction() as transaction:
            repository = MasterDataRepository(
                transaction.connection
            )

            return repository.get_all_item_types()