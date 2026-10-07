from decimal import Decimal, InvalidOperation
from services.number_text import normalize_digits

from mysql.connector import IntegrityError

from database.transaction import DatabaseTransaction
from repositories.master_data_repository import MasterDataRepository


class MasterDataInUse(ValueError):
    """Permanent delete refused: the record has been linked to stock / inventory data."""

    def __init__(self, table: str, references: list[str]):
        super().__init__(f"{table} is referenced by stock data: {', '.join(references)}.")
        self.table, self.references = table, references


class MasterDataService:
    """Factories, item types and categories. Reads are open to the logged-in app (Billing and Inventory
    list them); EVERY mutation requires MASTER_DATA_MANAGE through the strict write path, first thing
    inside the mutation's own transaction."""

    CATEGORY_CODE_MAX = 20
    MANAGE_PERMISSION = "MASTER_DATA_MANAGE"

    def __init__(self, session=None, *, authorization=None,
                 transaction_factory=DatabaseTransaction, repository=MasterDataRepository):
        self._session = session
        self._authorization = authorization
        self._transaction = transaction_factory
        self._repository = repository

    def can_manage(self) -> bool:
        """UI hint only (buttons); the database check in _authorize_write is authoritative."""
        actor = self._session.current_actor() if self._session is not None else None
        return actor is not None and self.MANAGE_PERMISSION in actor.permissions

    def _authorize_write(self, connection):
        """Strict write-time check (locks the user, employee and grants): a permission revoked in the
        database refuses the write even while a stale session still lists it."""
        if self._session is None:
            raise PermissionError("يلزم تسجيل الدخول لتعديل البيانات الأساسية.")
        actor = self._session.require_authenticated()
        if self._authorization is None:
            from services.authorization_service import AuthorizationService
            self._authorization = AuthorizationService(self._session)
        verified = self._authorization.require_permission_for_write(connection, actor, self.MANAGE_PERMISSION)
        if not self._session.is_current(actor):
            raise PermissionError("انتهت جلسة الدخول.")
        return verified

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
            with self._transaction() as transaction:
                self._authorize_write(transaction.connection)

                repository = self._repository(
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
            with self._transaction() as transaction:
                self._authorize_write(transaction.connection)

                repository = self._repository(
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
            with self._transaction() as transaction:
                self._authorize_write(transaction.connection)

                repository = self._repository(
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

            with self._transaction() as transaction:
                self._authorize_write(transaction.connection)

                repository = self._repository(
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

        with self._transaction() as transaction:
            self._authorize_write(transaction.connection)

            repository = self._repository(
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

    # =========================================================
    # CATEGORIES — create / activate
    # =========================================================

    @staticmethod
    def _money(value, label: str) -> Decimal | None:
        """Optional non-negative per-gram amount as Decimal (never float)."""
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            amount = Decimal(normalize_digits(str(value)).strip())
        except InvalidOperation:
            raise ValueError("Invalid numeric value.") from None
        if not amount.is_finite():
            raise ValueError("Invalid numeric value.")
        if amount < 0:
            raise ValueError(f"{label} cannot be negative.")
        return amount.quantize(Decimal("0.01"))

    def create_category(
        self,
        code: str,
        name: str | None = None,
        purchase_min=None,
        purchase_max=None,
        sale_min=None,
        sale_max=None,
        manager_approval_below=None,
        notes: str | None = None,
    ) -> int:
        """A new category in the same item_categories model as the seeded A/B/C/D/L/T."""

        code = (self._clean_text(code) or "").upper()
        if not code:
            raise ValueError("Category code is required.")
        if len(code) > self.CATEGORY_CODE_MAX or any(ch.isspace() for ch in code):
            raise ValueError("Category code is invalid.")
        name = self._clean_text(name) or code
        if len(name) > 100:
            raise ValueError("Category name is too long.")

        values = {
            "purchase_min": self._money(purchase_min, "Purchase making minimum"),
            "purchase_max": self._money(purchase_max, "Purchase making maximum"),
            "sale_min": self._money(sale_min, "Sale making minimum"),
            "sale_max": self._money(sale_max, "Sale making maximum"),
        }
        self._validate_range(values["purchase_min"], values["purchase_max"], "Purchase making")
        self._validate_range(values["sale_min"], values["sale_max"], "Sale making")
        manager = self._money(manager_approval_below, "Manager approval threshold")

        try:
            with self._transaction() as transaction:
                self._authorize_write(transaction.connection)
                repository = self._repository(transaction.connection)
                return repository.create_category(
                    code=code,
                    name=name,
                    manager_approval_below=manager,
                    notes=self._clean_text(notes),
                    **values,
                )
        except IntegrityError as exc:
            raise ValueError("Category code already exists.") from exc

    def set_category_active(self, category_id: int, is_active: bool) -> None:
        """Disable / reactivate without rewriting the stored making rules."""

        with self._transaction() as transaction:
            self._authorize_write(transaction.connection)
            repository = self._repository(transaction.connection)
            if repository.get_category_by_id(category_id) is None:
                raise ValueError("Category does not exist.")
            repository.set_category_active(category_id, bool(is_active))

    # =========================================================
    # PERMANENT DELETE — only for records never linked to stock
    # =========================================================

    def _delete_unused(self, table: str, row_id: int, missing: str) -> None:
        with self._transaction() as transaction:
            self._authorize_write(transaction.connection)
            repository = self._repository(transaction.connection)
            # 1) exclusive row lock: new stock rows referencing it (FK check) wait behind this delete;
            # 2) locking reads of every stock table: rows committed before the lock are seen.
            if repository.lock_master_row(table, row_id) is None:
                raise ValueError(missing)
            references = repository.stock_references(table, row_id)
            if references:
                raise MasterDataInUse(table, references)
            if table == "item_categories" and repository.item_types_defaulting_to(row_id):
                raise ValueError("Category is the default category of an item type.")
            try:
                repository.delete_master_row(table, row_id)
            except IntegrityError as exc:
                if getattr(exc, "errno", None) == 1451:        # ER_ROW_IS_REFERENCED_2: RESTRICT FK
                    raise MasterDataInUse(table, ["foreign key"]) from exc
                raise

    def delete_factory(self, factory_id: int) -> None:
        self._delete_unused("factories", int(factory_id), "Factory does not exist.")

    def delete_item_type(self, item_type_id: int) -> None:
        self._delete_unused("item_types", int(item_type_id), "Item type does not exist.")

    def delete_category(self, category_id: int) -> None:
        self._delete_unused("item_categories", int(category_id), "Category does not exist.")

    def get_karats(self) -> list[dict]:

        with self._transaction() as transaction:
            repository = self._repository(
                transaction.connection
            )

            return repository.get_active_karats()

    def get_categories(self) -> list[dict]:

        with self._transaction() as transaction:
            repository = self._repository(
                transaction.connection
            )

            return repository.get_active_categories()

    def get_all_categories(self) -> list[dict]:

        with self._transaction() as transaction:
            repository = self._repository(
                transaction.connection
            )

            return repository.get_all_categories()
    def get_factories(self) -> list[dict]:

        with self._transaction() as transaction:
            repository = self._repository(
                transaction.connection
            )

            return repository.get_active_factories()

    def get_all_factories(self) -> list[dict]:
        """Every row with `ever_used` (display hint for the permanent-delete area; the delete re-checks)."""

        with self._transaction() as transaction:
            repository = self._repository(
                transaction.connection
            )

            used = repository.used_master_ids("factories")
            return [dict(row, ever_used=int(row["id"]) in used) for row in repository.get_all_factories()]

    def get_employees(self) -> list[dict]:

        with self._transaction() as transaction:
            repository = self._repository(
                transaction.connection
            )

            return repository.get_active_employees()
    def get_item_types(self) -> list[dict]:

        with self._transaction() as transaction:
            repository = self._repository(
                transaction.connection
            )

            return repository.get_active_item_types()

    def get_all_item_types(self) -> list[dict]:
        """Every row with `ever_used` (display hint for the permanent-delete area; the delete re-checks)."""

        with self._transaction() as transaction:
            repository = self._repository(
                transaction.connection
            )

            used = repository.used_master_ids("item_types")
            return [dict(row, ever_used=int(row["id"]) in used) for row in repository.get_all_item_types()]