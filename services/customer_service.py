from datetime import date, datetime, time, timedelta

from mysql.connector import IntegrityError

from database.transaction import DatabaseTransaction
from repositories.customer_repository import CustomerRepository


class CustomerService:
    """The Customers management page: browsing, searching, profiles, statements and Dead Cash history.

    Every read here is the CUSTOMERS PAGE context and requires CUSTOMERS_VIEW, re-checked against the
    database (non-locking) inside the read's own transaction. Choosing a customer while creating an invoice
    is a different context: it goes through InvoiceService.search_customers under the invoice permissions
    and returns only the fields Billing needs — never through this service.
    """
    PAGE_SIZE = 100
    VIEW_PERMISSION = "CUSTOMERS_VIEW"
    MANAGE_PERMISSION = "CUSTOMERS_MANAGE"      # create / edit / deactivate / reactivate a customer
    DEAD_CASH_VIEW = "DEAD_CASH_VIEW"           # Dead Cash DETAIL: history, amounts, reasons, DC references
    DEAD_CASH_TYPES = {"DEAD_CASH_WRITE_OFF": "شطب رصيد", "DEAD_CASH_REVERSAL": "عكس شطب رصيد"}
    WARNING_TEXT = "سبق شطب رصيد لهذا العميل"

    def __init__(self, session=None, *, authorization=None):
        self._session = session
        self._authorization = authorization

    def _authorize_page_read(self, connection) -> None:
        """One authoritative, non-locking check per bounded read of the Customers page."""
        if self._session is None:
            raise PermissionError("يلزم تسجيل الدخول لعرض العملاء.")
        actor = self._session.require_authenticated()
        if self._authorization is None:
            from services.authorization_service import AuthorizationService
            self._authorization = AuthorizationService(self._session)
        self._authorization.require_permission_read(connection, actor, self.VIEW_PERMISSION)

    def _dead_cash_visible(self, connection) -> bool:
        """Whether the database (non-locking) currently grants DEAD_CASH_VIEW. Called after the page check,
        inside the same read, so a refusal here only means the Dead Cash detail right is missing."""
        try:
            self._authorization.require_permission_read(connection, self._session.require_authenticated(),
                                                        self.DEAD_CASH_VIEW)
            return True
        except PermissionError:
            return False

    @classmethod
    def _warning_only(cls, flags):
        """Active flags without Dead Cash detail: the caution stays, the linked write-off and its reason go."""
        out = []
        for flag in flags:
            flag = dict(flag)
            flag.pop("related_dead_cash_id", None)
            if flag.get("flag_type") == "DEAD_CASH_WARNING":
                flag["reason"] = cls.WARNING_TEXT
            out.append(flag)
        return out

    @classmethod
    def _ledger_without_dead_cash_detail(cls, rows):
        """Ledger rows keep their amount and running balance (the balance must reconcile); a write-off /
        reversal row loses its reason, reference and link — they are Dead Cash detail."""
        out = []
        for row in rows:
            row = dict(row)
            label = cls.DEAD_CASH_TYPES.get(row.get("transaction_type_code"))
            if label:
                row.update(notes=None, reference_code=None, reference_id=None, description=label)
            out.append(row)
        return out

    def _restricted_dead_cash_page(self):
        return {"items": [], "page": 1, "page_size": self.PAGE_SIZE, "has_more": False, "restricted": True}

    def _authorize_write(self, connection):
        """The strict write-time check, first thing inside the mutation's own transaction: the user, the
        employee and the grants are locked, so a permission revoked in the database refuses the write even
        while the session still lists it. Returns the verified actor (the write's attribution)."""
        if self._session is None:
            raise PermissionError("يلزم تسجيل الدخول لتعديل بيانات العملاء.")
        actor = self._session.require_authenticated()
        if self._authorization is None:
            from services.authorization_service import AuthorizationService
            self._authorization = AuthorizationService(self._session)
        verified = self._authorization.require_permission_for_write(connection, actor, self.MANAGE_PERMISSION)
        if not self._session.is_current(actor):
            raise PermissionError("انتهت جلسة الدخول.")
        return verified

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
    def _paged(cls, rows: list[dict], page: int, page_size: int = 100) -> dict:
        return {
            "items": rows[:page_size],
            "page": page,
            "page_size": page_size,
            "has_more": len(rows) > page_size,
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

    def get_customer_transaction_types(self) -> list[dict]:
        with DatabaseTransaction() as transaction:
            return CustomerRepository(transaction.connection).get_customer_transaction_types()

    def search_customers(
        self,
        search: str = "",
        status: str = "ALL",
        customer_type_id: int | None = None,
        balance_status: str = "ALL",
        page: int = 1,
        page_size: int = 100,
    ) -> dict:
        if not isinstance(search, str):
            raise ValueError("نص البحث غير صحيح.")
        if status not in ("ALL", "ACTIVE", "INACTIVE"):
            raise ValueError("حالة العميل المحددة غير صحيحة.")
        if balance_status not in self._BALANCE_STATES:
            raise ValueError("حالة الرصيد المحددة غير صحيحة.")
        customer_type_id = self._optional_id(customer_type_id, "نوع العميل", 65535)
        page = self._page_number(page)
        page_size = self._required_id(page_size, "حجم الصفحة", self.PAGE_SIZE)
        active = None if status == "ALL" else status == "ACTIVE"
        balance = None if balance_status == "ALL" else balance_status
        with DatabaseTransaction() as transaction:
            self._authorize_page_read(transaction.connection)
            repository = CustomerRepository(transaction.connection)
            rows = repository.search_customers(
                search=search.strip(),
                is_active=active,
                customer_type_id=customer_type_id,
                balance_status=balance,
                limit=page_size + 1,
                offset=(page - 1) * page_size,
            )
            return self._paged(rows, page, page_size)

    def get_customer_details(self, customer_id: int) -> dict:
        customer_id = self._customer_id(customer_id)
        with DatabaseTransaction() as transaction:
            self._authorize_page_read(transaction.connection)
            return self._require_customer(
                CustomerRepository(transaction.connection), customer_id,
            )

    def get_customer_for_selection(
        self, customer_id: int, allow_inactive: bool = False,
    ) -> dict:
        if not isinstance(allow_inactive, bool):
            raise ValueError("خيار السماح بالعملاء غير النشطين غير صحيح.")
        customer = self.get_customer_details(customer_id)
        if not allow_inactive and not customer["is_active"]:
            raise ValueError("العميل غير نشط ولا يمكن اختياره في هذه الشاشة.")
        return customer

    @staticmethod
    def _statement_date(value: str | None, label: str) -> date | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise ValueError(f"{label} غير صحيح. استخدم الصيغة سنة-شهر-يوم.")
        value = value.strip()
        if not value:
            return None
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{label} غير صحيح. استخدم الصيغة سنة-شهر-يوم.") from None
        if parsed.isoformat() != value or parsed.year < 1000:
            raise ValueError(f"{label} غير صحيح. استخدم الصيغة سنة-شهر-يوم.")
        return parsed

    @classmethod
    def _statement_result(
        cls, raw: dict | None, page: int,
        from_date: date | None, to_date: date | None,
        transaction_type_code: str | None,
    ) -> dict:
        if raw is None:
            raise ValueError("العميل غير موجود أو تعذر العثور على بيانات رصيده.")
        summary = dict(raw["summary"])
        current_balance = raw["customer"]["balance"]
        if current_balance is None or summary["ledger_balance"] != current_balance:
            raise ValueError("تعذر عرض كشف الحساب: رصيد الحركات لا يطابق الرصيد الحالي. يلزم مراجعة البيانات.")
        summary.update({
            "current_balance": current_balance,
            "from_date": from_date.isoformat() if from_date else "",
            "to_date": to_date.isoformat() if to_date else "",
            "transaction_type_code": transaction_type_code or "",
            "type_filter_active": transaction_type_code is not None,
        })
        return {
            "customer": raw["customer"],
            "summary": summary,
            **cls._paged(raw["items"], page),
            # Count is essential: lower allocated IDs can commit after higher
            # IDs. This detects append-only changes, not arbitrary old-row edits.
            "ledger_token": (
                int(summary["ledger_count"]), int(summary["ledger_max_id"]),
                summary["ledger_balance"],
            ),
            "loaded_at": raw["loaded_at"],
            "ledger_changed": False,
            "message": "",
        }

    def get_customer_statement(
        self,
        customer_id: int,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        transaction_type_code: str | None = None,
        page: int = 1,
        expected_ledger_token: tuple | None = None,
    ) -> dict:
        customer_id = self._customer_id(customer_id)
        page = self._page_number(page)
        first_date = self._statement_date(from_date, "تاريخ البداية")
        last_date = self._statement_date(to_date, "تاريخ النهاية")
        if first_date and last_date and first_date > last_date:
            raise ValueError("تاريخ البداية يجب ألا يتجاوز تاريخ النهاية.")
        from_at = datetime.combine(first_date, time.min) if first_date else None
        try:
            to_exclusive = (
                datetime.combine(last_date + timedelta(days=1), time.min)
                if last_date else None
            )
        except OverflowError:
            raise ValueError("تاريخ النهاية خارج النطاق المتاح.") from None
        if transaction_type_code is not None:
            if not isinstance(transaction_type_code, str) or len(transaction_type_code) > 60:
                raise ValueError("نوع الحركة المحدد غير صحيح.")
            transaction_type_code = transaction_type_code.strip() or None
        if expected_ledger_token is not None and (
            not isinstance(expected_ledger_token, tuple) or len(expected_ledger_token) != 3
        ):
            raise ValueError("أعد تحميل كشف الحساب قبل الانتقال بين الصفحات.")

        with DatabaseTransaction() as transaction:
            self._authorize_page_read(transaction.connection)
            repository = CustomerRepository(transaction.connection)
            if transaction_type_code is not None:
                types = repository.get_customer_transaction_types()
                if not any(row["code"] == transaction_type_code for row in types):
                    raise ValueError("نوع الحركة المحدد غير موجود.")
            parameters = dict(
                from_at=from_at, to_exclusive=to_exclusive,
                transaction_type_code=transaction_type_code,
                limit=self.PAGE_SIZE + 1,
            )
            raw = repository.get_customer_statement(
                customer_id, **parameters, offset=(page - 1) * self.PAGE_SIZE,
            )
            result = self._statement_result(raw, page, first_date, last_date, transaction_type_code)
            if expected_ledger_token is not None and expected_ledger_token != result["ledger_token"]:
                # Discard the requested old page, then atomically publish one
                # fresh first-page snapshot and its matching totals/profile.
                if page != 1:
                    raw = repository.get_customer_statement(customer_id, **parameters, offset=0)
                    result = self._statement_result(raw, 1, first_date, last_date, transaction_type_code)
                result["ledger_changed"] = True
                result["message"] = "تغيرت حركات العميل؛ تم تحديث كشف الحساب والعودة إلى الصفحة الأولى."
            # Refresh the associated panels before publishing a new customer
            # summary. Under the configured REPEATABLE READ isolation these
            # reads share the transaction snapshot with the final statement.
            flags = repository.get_active_customer_flags(customer_id)
            if self._dead_cash_visible(transaction.connection):
                result["active_flags"] = flags
                result["dead_cash_page"] = self._paged(
                    repository.get_dead_cash_history(
                        customer_id, limit=self.PAGE_SIZE + 1, offset=0,
                    ),
                    1,
                )
            else:                                    # never queried, not merely hidden in QML
                result["items"] = self._ledger_without_dead_cash_detail(result["items"])
                result["active_flags"] = self._warning_only(flags)
                result["dead_cash_page"] = self._restricted_dead_cash_page()
            return result

    def get_customer_profile(self, customer_id: int) -> dict:
        customer_id = self._customer_id(customer_id)
        with DatabaseTransaction() as transaction:
            self._authorize_page_read(transaction.connection)
            repository = CustomerRepository(transaction.connection)
            statement = self._statement_result(
                repository.get_customer_statement(customer_id, limit=self.PAGE_SIZE + 1),
                1, None, None, None,
            )
            profile = {
                "customer": statement["customer"],
                "active_flags": repository.get_active_customer_flags(customer_id),
                "transactions_page": {
                    key: statement[key] for key in ("items", "page", "page_size", "has_more")
                },
                "statement_summary": statement["summary"],
                "ledger_token": statement["ledger_token"],
                "statement_loaded_at": statement["loaded_at"],
            }
            if self._dead_cash_visible(transaction.connection):
                profile.update(dead_cash_restricted=False, dead_cash_page=self._paged(
                    repository.get_dead_cash_history(customer_id, limit=self.PAGE_SIZE + 1, offset=0), 1),
                    dead_cash_summary=repository.dead_cash_summary(customer_id))
            else:                                    # warning only: no history / amounts / reasons queried
                profile["transactions_page"]["items"] = self._ledger_without_dead_cash_detail(
                    profile["transactions_page"]["items"])
                profile.update(dead_cash_restricted=True, active_flags=self._warning_only(profile["active_flags"]),
                               dead_cash_page=self._restricted_dead_cash_page(), dead_cash_summary=None)
            return profile

    def get_customer_transactions(self, customer_id: int, page: int = 1) -> dict:
        statement = self.get_customer_statement(customer_id, page=page)
        return {key: statement[key] for key in ("items", "page", "page_size", "has_more")}

    def get_dead_cash_history(self, customer_id: int, page: int = 1) -> dict:
        customer_id = self._customer_id(customer_id)
        page = self._page_number(page)
        with DatabaseTransaction() as transaction:
            self._authorize_page_read(transaction.connection)
            self._authorization.require_permission_read(          # Dead Cash detail: its own right
                transaction.connection, self._session.require_authenticated(), self.DEAD_CASH_VIEW)
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
                actor = self._authorize_write(transaction.connection)
                repository = CustomerRepository(transaction.connection)
                self._validate_customer_type(repository, values["customer_type_id"])
                return repository.create_customer(
                    **values, created_by_user_id=actor.user_id,      # the verified actor, never the caller's value
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
                actor = self._authorize_write(transaction.connection)
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
                    customer_id=customer_id, **values, updated_by_user_id=actor.user_id,
                )
        except IntegrityError as exc:
            self._translate_integrity_error(exc)

    def _set_customer_active(
        self, customer_id: int, is_active: bool, user_id: int | None,
    ) -> None:
        customer_id = self._customer_id(customer_id)
        user_id = self._optional_id(user_id, "رقم المستخدم", 4294967295)
        with DatabaseTransaction() as transaction:
            actor = self._authorize_write(transaction.connection)
            repository = CustomerRepository(transaction.connection)
            existing = repository.get_customer_for_update(customer_id)
            if existing is None:
                raise ValueError("العميل غير موجود.")
            if bool(existing["is_active"]) == is_active:
                return
            # A nonzero balance and active flags never block this profile-only
            # change. The caller displays warnings before requesting it.
            repository.set_customer_active(
                customer_id, is_active, updated_by_user_id=actor.user_id,
            )

    def deactivate_customer(self, customer_id: int, user_id: int | None = None) -> None:
        self._set_customer_active(customer_id, False, user_id)

    def reactivate_customer(self, customer_id: int, user_id: int | None = None) -> None:
        self._set_customer_active(customer_id, True, user_id)
