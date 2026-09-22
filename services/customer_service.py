from mysql.connector import IntegrityError

from database.transaction import DatabaseTransaction
from repositories.customer_repository import CustomerRepository


class CustomerService:
    PAGE_SIZE = 100
    _MAX_CUSTOMER_ID = 18446744073709551615
    _EDIT_FIELDS = (
        "name", "mobile", "place", "customer_type_id", "notes",
    )
    _SNAPSHOT_FIELDS = _EDIT_FIELDS + ("is_active",)
    _BALANCE_STATES = (
        "ALL", "CUSTOMER_OWES_SHOP", "SHOP_OWES_CUSTOMER", "SETTLED",
    )

    @staticmethod
    def _required_id(value, label: str, maximum: int) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{label} غير صحيح.")
        if isinstance(value, int):
            number = value
        elif isinstance(value, str) and value.strip().isdecimal():
            digits = value.strip().lstrip("0٠۰")
            if len(digits) > len(str(maximum)):
                raise ValueError(f"{label} غير صحيح.")
            number = int(digits or "0")
        else:
            raise ValueError(f"{label} غير صحيح.")
        if not 0 < number <= maximum:
            raise ValueError(f"{label} غير صحيح.")
        return number

    @classmethod
    def _customer_id(cls, value) -> int:
        return cls._required_id(value, "رقم العميل", cls._MAX_CUSTOMER_ID)

    @classmethod
    def _optional_id(cls, value, label: str, maximum: int) -> int | None:
        if value is None:
            return None
        return cls._required_id(value, label, maximum)

    @staticmethod
    def _clean_text(value: str | None, label: str, max_length: int) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{label} غير صحيح.")
        if len(value) > max_length:
            raise ValueError(f"{label}: الحد الأقصى {max_length} حرفاً.")
        # Validate only. Saved spelling, digits and whitespace belong to the
        # employee; Arabic normalization is exclusively part of searching.
        return value

    @classmethod
    def _profile_values(
        cls, name, mobile, place, customer_type_id, notes,
    ) -> dict:
        values = {
            "name": cls._clean_text(name, "اسم العميل", 150),
            "mobile": cls._clean_text(mobile, "رقم المحمول", 50),
            "place": cls._clean_text(place, "المكان", 150),
            "customer_type_id": cls._optional_id(customer_type_id, "نوع العميل", 65535),
            "notes": cls._clean_text(notes, "الملاحظات", 1000),
        }
        if not values["name"] or not values["name"].strip():
            raise ValueError("اسم العميل مطلوب.")
        return values

    @classmethod
    def _page_number(cls, page) -> int:
        return cls._required_id(page, "رقم الصفحة", 2147483647)

    @classmethod
    def _paged(cls, rows: list[dict], page: int) -> dict:
        return {
            "items": rows[:cls.PAGE_SIZE],
            "page": page,
            "page_size": cls.PAGE_SIZE,
            "has_more": len(rows) > cls.PAGE_SIZE,
        }

    @staticmethod
    def _require_customer(repository: CustomerRepository, customer_id: int) -> dict:
        customer = repository.get_customer_by_id(customer_id)
        if customer is None:
            raise ValueError("العميل غير موجود أو تعذر العثور على بيانات رصيده.")
        return customer

    @staticmethod
    def _validate_customer_type(
        repository: CustomerRepository,
        customer_type_id: int | None,
        existing_type_id: int | None = None,
    ) -> None:
        if customer_type_id is None:
            return
        customer_type = repository.get_customer_type_by_id(
            customer_type_id, for_update=True,
        )
        if customer_type is None:
            raise ValueError("نوع العميل المحدد غير موجود.")
        if not customer_type["is_active"] and customer_type_id != existing_type_id:
            raise ValueError("لا يمكن اختيار نوع عميل غير نشط.")

    @staticmethod
    def _translate_integrity_error(exc: IntegrityError) -> None:
        if exc.errno == 1062 and "uq_customers_code" in (exc.msg or ""):
            raise ValueError(
                "تعذر إنشاء العميل لأن الكود المولد مستخدم بالفعل لعميل آخر. لم يتم حفظ العميل."
            ) from exc
        raise exc

    def get_customer_types(self) -> list[dict]:
        with DatabaseTransaction() as transaction:
            return CustomerRepository(transaction.connection).get_customer_types()

    def search_customers(
        self,
        search: str = "",
        status: str = "ALL",
        customer_type_id: int | None = None,
        balance_status: str = "ALL",
        page: int = 1,
    ) -> dict:
        if not isinstance(search, str):
            raise ValueError("نص البحث غير صحيح.")
        if status not in ("ALL", "ACTIVE", "INACTIVE"):
            raise ValueError("حالة العميل المحددة غير صحيحة.")
        if balance_status not in self._BALANCE_STATES:
            raise ValueError("حالة الرصيد المحددة غير صحيحة.")
        customer_type_id = self._optional_id(customer_type_id, "نوع العميل", 65535)
        page = self._page_number(page)
        active = None if status == "ALL" else status == "ACTIVE"
        balance = None if balance_status == "ALL" else balance_status
        with DatabaseTransaction() as transaction:
            repository = CustomerRepository(transaction.connection)
            rows = repository.search_customers(
                search=search.strip(),
                is_active=active,
                customer_type_id=customer_type_id,
                balance_status=balance,
                limit=self.PAGE_SIZE + 1,
                offset=(page - 1) * self.PAGE_SIZE,
            )
            return self._paged(rows, page)

    def get_customer_details(self, customer_id: int) -> dict:
        customer_id = self._customer_id(customer_id)
        with DatabaseTransaction() as transaction:
            return self._require_customer(
                CustomerRepository(transaction.connection), customer_id,
            )

    def get_customer_profile(self, customer_id: int) -> dict:
        customer_id = self._customer_id(customer_id)
        with DatabaseTransaction() as transaction:
            repository = CustomerRepository(transaction.connection)
            customer = self._require_customer(repository, customer_id)
            return {
                "customer": customer,
                "active_flags": repository.get_active_customer_flags(customer_id),
                "transactions_page": self._paged(
                    repository.get_customer_transactions(
                        customer_id, limit=self.PAGE_SIZE + 1, offset=0,
                    ),
                    1,
                ),
                "dead_cash_page": self._paged(
                    repository.get_dead_cash_history(
                        customer_id, limit=self.PAGE_SIZE + 1, offset=0,
                    ),
                    1,
                ),
            }

    def get_customer_transactions(self, customer_id: int, page: int = 1) -> dict:
        customer_id = self._customer_id(customer_id)
        page = self._page_number(page)
        with DatabaseTransaction() as transaction:
            repository = CustomerRepository(transaction.connection)
            self._require_customer(repository, customer_id)
            rows = repository.get_customer_transactions(
                customer_id,
                limit=self.PAGE_SIZE + 1,
                offset=(page - 1) * self.PAGE_SIZE,
            )
            return self._paged(rows, page)

    def get_dead_cash_history(self, customer_id: int, page: int = 1) -> dict:
        customer_id = self._customer_id(customer_id)
        page = self._page_number(page)
        with DatabaseTransaction() as transaction:
            repository = CustomerRepository(transaction.connection)
            self._require_customer(repository, customer_id)
            rows = repository.get_dead_cash_history(
                customer_id,
                limit=self.PAGE_SIZE + 1,
                offset=(page - 1) * self.PAGE_SIZE,
            )
            return self._paged(rows, page)

    def create_customer(
        self,
        name: str,
        mobile: str | None = None,
        place: str | None = None,
        customer_type_id: int | None = None,
        notes: str | None = None,
        user_id: int | None = None,
    ) -> int:
        values = self._profile_values(
            name, mobile, place, customer_type_id, notes,
        )
        user_id = self._optional_id(user_id, "رقم المستخدم", 4294967295)
        try:
            with DatabaseTransaction() as transaction:
                repository = CustomerRepository(transaction.connection)
                self._validate_customer_type(repository, values["customer_type_id"])
                return repository.create_customer(
                    **values, created_by_user_id=user_id,
                )
        except IntegrityError as exc:
            self._translate_integrity_error(exc)

    def update_customer(
        self,
        customer_id: int,
        *,
        name: str,
        mobile: str | None,
        place: str | None,
        customer_type_id: int | None,
        notes: str | None,
        original_values: dict,
        user_id: int | None = None,
    ) -> None:
        customer_id = self._customer_id(customer_id)
        values = self._profile_values(
            name, mobile, place, customer_type_id, notes,
        )
        user_id = self._optional_id(user_id, "رقم المستخدم", 4294967295)
        if not isinstance(original_values, dict) or any(
            field not in original_values for field in self._SNAPSHOT_FIELDS
        ):
            raise ValueError("أعد فتح نافذة تعديل العميل لتحميل بياناته الحالية.")
        try:
            with DatabaseTransaction() as transaction:
                repository = CustomerRepository(transaction.connection)
                existing = repository.get_customer_for_update(customer_id)
                if existing is None:
                    raise ValueError("العميل غير موجود.")
                # Compare raw fields, including NULL and active status. Timestamp
                # precision alone is insufficient to detect concurrent edits.
                if any(
                    existing[field] != original_values[field]
                    for field in self._SNAPSHOT_FIELDS
                ):
                    raise ValueError("تغيرت بيانات العميل منذ فتح النافذة. أعد فتح التعديل وحاول مجدداً.")
                self._validate_customer_type(
                    repository, values["customer_type_id"], existing["customer_type_id"],
                )
                if all(existing[field] == values[field] for field in self._EDIT_FIELDS):
                    return
                repository.update_customer(
                    customer_id=customer_id, **values, updated_by_user_id=user_id,
                )
        except IntegrityError as exc:
            self._translate_integrity_error(exc)

    def _set_customer_active(
        self, customer_id: int, is_active: bool, user_id: int | None,
    ) -> None:
        customer_id = self._customer_id(customer_id)
        user_id = self._optional_id(user_id, "رقم المستخدم", 4294967295)
        with DatabaseTransaction() as transaction:
            repository = CustomerRepository(transaction.connection)
            existing = repository.get_customer_for_update(customer_id)
            if existing is None:
                raise ValueError("العميل غير موجود.")
            if bool(existing["is_active"]) == is_active:
                return
            # A nonzero balance and active flags never block this profile-only
            # change. The caller displays warnings before requesting it.
            repository.set_customer_active(
                customer_id, is_active, updated_by_user_id=user_id,
            )

    def deactivate_customer(self, customer_id: int, user_id: int | None = None) -> None:
        self._set_customer_active(customer_id, False, user_id)

    def reactivate_customer(self, customer_id: int, user_id: int | None = None) -> None:
        self._set_customer_active(customer_id, True, user_id)
