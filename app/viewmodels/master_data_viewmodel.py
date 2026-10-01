from PySide6.QtCore import (
    QObject,
    Property,
    Signal,
    Slot,
)

from services.master_data_service import MasterDataInUse, MasterDataService
from decimal import Decimal, InvalidOperation

class MasterDataViewModel(QObject):

    dataChanged = Signal()
    errorChanged = Signal()
    actionChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._service = MasterDataService()
        self._default_service = True

        self._categories = []
        self._all_categories = []
        self._factories = []
        self._all_factories = []
        self._item_types = []
        self._all_item_types = []
        self._error_message = ""
        self._karats = []
        self._categories = []
        self._employees = []
        self._factories = []
        self._item_types = []
        self._action_message = ""
        self._action_success = True
        self._action_scope = ""          # which Master Data page owns the message

        self.refresh()

    # =========================================================
    # COUNTS
    # =========================================================
    @staticmethod
    def _qml_safe(value):
        """
        Convert Python/MySQL values to types QML can display safely.
        """

        if isinstance(value, Decimal):
            return float(value)

        if isinstance(value, dict):
            return {
                key: MasterDataViewModel._qml_safe(item)
                for key, item in value.items()
            }

        if isinstance(value, (list, tuple)):
            return [
                MasterDataViewModel._qml_safe(item)
                for item in value
            ]

        return value

    @Property("QVariantList", notify=dataChanged)
    def employees(self):
        return self._employees

    @Property(int, notify=dataChanged)
    def employeeCount(self):
        return len(self._employees)

    @Property("QVariantList", notify=dataChanged)
    def karats(self):
        return self._karats
    @Property(int, notify=dataChanged)
    def categoryCount(self):
        return len(self._categories)

    @Property(int, notify=dataChanged)
    def factoryCount(self):
        return len(self._factories)

    @Property(int, notify=dataChanged)
    def disabledFactoryCount(self):
        return len(
            [
                factory
                for factory in self._all_factories
                if not bool(
                    factory.get("is_active")
                )
            ]
        )

    @Property(int, notify=dataChanged)
    def allFactoryCount(self):
        return len(self._all_factories)

    @Property(int, notify=dataChanged)
    def itemTypeCount(self):
        return len(self._item_types)

    # =========================================================
    # FACTORY LISTS
    # =========================================================

    @Property("QVariantList", notify=dataChanged)
    def factories(self):
        return self._factories

    @Property("QVariantList", notify=dataChanged)
    def categories(self):
        return self._categories

    @Property("QVariantList", notify=dataChanged)
    def inactiveCategories(self):
        return [
            category
            for category in self._all_categories
            if not bool(category.get("is_active"))
        ]

    @Property(int, notify=dataChanged)
    def disabledCategoryCount(self):
        return len([
            category
            for category in self._all_categories
            if not bool(category.get("is_active"))
        ])

    @Property(int, notify=dataChanged)
    def allCategoryCount(self):
        return len(self._all_categories)
    @Property("QVariantList", notify=dataChanged)
    def itemTypes(self):
        return self._item_types

    @Property("QVariantList", notify=dataChanged)
    def inactiveItemTypes(self):

        return [
            item_type
            for item_type in self._all_item_types
            if not bool(
                item_type.get("is_active")
            )
        ]

    @Property(int, notify=dataChanged)
    def disabledItemTypeCount(self):

        return len(
            [
                item_type
                for item_type in self._all_item_types
                if not bool(
                item_type.get("is_active")
            )
            ]
        )

    @Property(int, notify=dataChanged)
    def allItemTypeCount(self):
        return len(self._all_item_types)
    @Property("QVariantList", notify=dataChanged)
    def inactiveFactories(self):

        return [
            factory
            for factory in self._all_factories
            if not bool(
                factory.get("is_active")
            )
        ]

    # =========================================================
    # CATEGORY DISPLAY
    # =========================================================

    @Property(str, notify=dataChanged)
    def categoryCodes(self):

        codes = []

        for row in self._categories:

            code = (
                row.get("code")
                or row.get("name")
                or ""
            )

            if code:
                codes.append(
                    str(code)
                )

        return "  ".join(codes)

    # =========================================================
    # DATABASE ERROR
    # =========================================================

    @Property(str, notify=errorChanged)
    def errorMessage(self):
        return self._error_message

    # =========================================================
    # ACTION RESULT
    # =========================================================

    @Property(str, notify=actionChanged)
    def actionMessage(self):
        return self._action_message

    @Property(bool, notify=actionChanged)
    def actionSuccess(self):
        return self._action_success

    def attach_session(self, session):
        """The logged-in session: every Master Data mutation is re-authorized (MASTER_DATA_MANAGE)."""
        if self._default_service:
            self._service = MasterDataService(session)
        self.dataChanged.emit()

    @Property(bool, notify=dataChanged)
    def canManage(self):
        """UI hint only; the service re-checks the database on every mutation."""
        can = getattr(self._service, "can_manage", None)
        return bool(can()) if callable(can) else False

    @Property(str, notify=actionChanged)
    def actionScope(self):
        """factories / itemTypes / categories: only that page shows the message."""
        return self._action_scope

    @Slot()
    def clearAction(self):
        """Navigation (or the success timeout) clears the page message."""
        if self._action_message or self._action_scope:
            self._action_message, self._action_success, self._action_scope = "", True, ""
            self.actionChanged.emit()

    def _set_action(self, scope, success, message):
        self._action_scope, self._action_success, self._action_message = scope, success, message
        self.actionChanged.emit()

    # =========================================================
    # HELPERS
    # =========================================================

    def _factory_by_id(
        self,
        factory_id,
    ):

        for factory in self._all_factories:

            try:

                if int(
                    factory.get("id")
                ) == int(factory_id):

                    return factory

            except (
                TypeError,
                ValueError,
            ):
                continue

        return None

    @staticmethod
    def _translate_factory_error(exc):

        if isinstance(exc, PermissionError):
            return "ليس لديك صلاحية تعديل البيانات الأساسية (المصانع والأصناف والفئات)."

        message = str(exc)

        if (
            "Factory name is required"
            in message
        ):
            return "اسم المصنع مطلوب."

        if (
            "Factory code already exists"
            in message
        ):
            return (
                "كود المصنع مستخدم بالفعل."
            )

        if (
            "Factory does not exist"
            in message
        ):
            return "المصنع غير موجود."

        return (
            "تعذر تنفيذ العملية: "
            + message
        )

    # =========================================================
    # REFRESH
    # =========================================================

    @Slot()
    def refresh(self):

        try:

            self._categories = self._qml_safe(
                self._service.get_categories()
                or []
            )
            self._all_categories = self._qml_safe(
                self._service.get_all_categories()
                or []
            )
            self._factories = (
                self._service.get_factories()
                or []
            )

            self._all_factories = (
                self._service.get_all_factories()
                or []
            )

            self._item_types = (
                self._service.get_item_types()
                or []
            )
            self._all_item_types = (
                    self._service.get_all_item_types()
                    or []
            )
            self._karats = self._qml_safe(
                self._service.get_karats()
                or []
            )

            self._categories = self._qml_safe(
                self._service.get_categories()
                or []
            )

            self._all_categories = self._qml_safe(
                self._service.get_all_categories()
                or []
            )
            self._employees = self._qml_safe(
                self._service.get_employees()
                or []
            )
            self._factories = self._qml_safe(
                self._service.get_factories()
                or []
            )

            self._all_factories = self._qml_safe(
                self._service.get_all_factories()
                or []
            )

            self._item_types = self._qml_safe(
                self._service.get_item_types()
                or []
            )

            self._all_item_types = self._qml_safe(
                self._service.get_all_item_types()
                or []
            )
            self._error_message = ""

            self.dataChanged.emit()
            self.errorChanged.emit()

        except Exception as exc:

            self._error_message = str(exc)

            self.errorChanged.emit()

    # =========================================================
    # CREATE FACTORY
    # =========================================================

    @Slot(
        str,
        str,
        str,
        result=bool,
    )
    def createFactory(
        self,
        name,
        code,
        notes,
    ):

        self._action_scope = "factories"

        try:

            factory_id = (
                self._service.create_factory(
                    name=name,
                    code=code,
                    notes=notes,
                )
            )

            self._action_success = True

            self._action_message = (
                "تم حفظ المصنع بنجاح"
                f" — رقم {factory_id}"
            )

            self.refresh()
            self.actionChanged.emit()

            return True

        except Exception as exc:

            self._action_success = False

            self._action_message = (
                self._translate_factory_error(
                    exc
                )
            )

            self.actionChanged.emit()

            return False

    # =========================================================
    # UPDATE FACTORY
    # =========================================================

    @Slot(
        int,
        str,
        str,
        str,
        result=bool,
    )
    def updateFactory(
        self,
        factory_id,
        name,
        code,
        notes,
    ):

        self._action_scope = "factories"

        try:

            self._service.update_factory(
                factory_id=factory_id,
                name=name,
                code=code,
                notes=notes,
                is_active=True,
            )

            self._action_success = True

            self._action_message = (
                "تم تعديل بيانات المصنع بنجاح."
            )

            self.refresh()
            self.actionChanged.emit()

            return True

        except Exception as exc:

            self._action_success = False

            self._action_message = (
                self._translate_factory_error(
                    exc
                )
            )

            self.actionChanged.emit()

            return False

    # =========================================================
    # DISABLE FACTORY
    # =========================================================

    @Slot(
        int,
        result=bool,
    )
    def disableFactory(
        self,
        factory_id,
    ):

        self._action_scope = "factories"

        factory = self._factory_by_id(
            factory_id
        )

        if factory is None:

            self._action_success = False

            self._action_message = (
                "تعذر العثور على المصنع."
            )

            self.actionChanged.emit()

            return False

        try:

            self._service.update_factory(
                factory_id=factory_id,
                name=(
                    factory.get("name")
                    or ""
                ),
                code=factory.get("code"),
                notes=factory.get("notes"),
                is_active=False,
            )

            self._action_success = True

            self._action_message = (
                "تم تعطيل المصنع: "
                + (
                    factory.get("name")
                    or ""
                )
            )

            self.refresh()
            self.actionChanged.emit()

            return True

        except Exception as exc:

            self._action_success = False

            self._action_message = (
                self._translate_factory_error(
                    exc
                )
            )

            self.actionChanged.emit()

            return False

    # =========================================================
    # REACTIVATE FACTORY
    # =========================================================

    @Slot(
        int,
        result=bool,
    )
    def reactivateFactory(
        self,
        factory_id,
    ):

        self._action_scope = "factories"

        factory = self._factory_by_id(
            factory_id
        )

        if factory is None:

            self._action_success = False

            self._action_message = (
                "تعذر العثور على المصنع."
            )

            self.actionChanged.emit()

            return False

        try:

            self._service.update_factory(
                factory_id=factory_id,
                name=(
                    factory.get("name")
                    or ""
                ),
                code=factory.get("code"),
                notes=factory.get("notes"),
                is_active=True,
            )

            self._action_success = True

            self._action_message = (
                "تم إعادة تفعيل المصنع: "
                + (
                    factory.get("name")
                    or ""
                )
            )

            self.refresh()
            self.actionChanged.emit()

            return True

        except Exception as exc:

            self._action_success = False

            self._action_message = (
                self._translate_factory_error(
                    exc
                )
            )

            self.actionChanged.emit()

            return False

    def _item_type_by_id(
            self,
            item_type_id,
    ):

        for item_type in self._all_item_types:

            try:

                if int(
                        item_type.get("id")
                ) == int(item_type_id):
                    return item_type

            except (
                    TypeError,
                    ValueError,
            ):
                continue

        return None

    @staticmethod
    def _translate_item_type_error(exc):

        if isinstance(exc, PermissionError):
            return "ليس لديك صلاحية تعديل البيانات الأساسية (المصانع والأصناف والفئات)."

        message = str(exc)

        if "Item type name is required" in message:
            return "اسم الصنف مطلوب."

        if (
                "Item type name or code already exists"
                in message
        ):
            return "اسم الصنف أو الكود مستخدم بالفعل."

        if "Item type does not exist" in message:
            return "الصنف غير موجود."

        if "Default category does not exist" in message:
            return "الفئة الافتراضية غير موجودة."

        return (
                "تعذر تنفيذ العملية: "
                + message
        )

    @Slot(
        str,
        str,
        int,
        str,
        result=bool,
    )
    def createItemType(
            self,
            name,
            code,
            default_category_id,
            notes,
    ):

        self._action_scope = "itemTypes"

        try:

            category_id = (
                default_category_id
                if default_category_id > 0
                else None
            )

            item_type_id = (
                self._service.create_item_type(
                    name=name,
                    code=code,
                    default_category_id=category_id,
                    notes=notes,
                )
            )

            self._action_success = True

            self._action_message = (
                "تم حفظ الصنف بنجاح"
                f" — رقم {item_type_id}"
            )

            self.refresh()
            self.actionChanged.emit()

            return True

        except Exception as exc:

            self._action_success = False

            self._action_message = (
                self._translate_item_type_error(
                    exc
                )
            )

            self.actionChanged.emit()

            return False

    @Slot(
        int,
        str,
        str,
        int,
        str,
        result=bool,
    )
    def updateItemType(
            self,
            item_type_id,
            name,
            code,
            default_category_id,
            notes,
    ):

        self._action_scope = "itemTypes"

        try:

            category_id = (
                default_category_id
                if default_category_id > 0
                else None
            )

            self._service.update_item_type(
                item_type_id=item_type_id,
                name=name,
                code=code,
                default_category_id=category_id,
                notes=notes,
                is_active=True,
            )

            self._action_success = True
            self._action_message = (
                "تم تعديل بيانات الصنف بنجاح."
            )

            self.refresh()
            self.actionChanged.emit()

            return True

        except Exception as exc:

            self._action_success = False

            self._action_message = (
                self._translate_item_type_error(
                    exc
                )
            )

            self.actionChanged.emit()

            return False

    @Slot(
        int,
        result=bool,
    )
    def disableItemType(
            self,
            item_type_id,
    ):

        self._action_scope = "itemTypes"

        item_type = self._item_type_by_id(
            item_type_id
        )

        if item_type is None:
            self._action_success = False
            self._action_message = (
                "تعذر العثور على الصنف."
            )

            self.actionChanged.emit()

            return False

        try:

            self._service.update_item_type(
                item_type_id=item_type_id,
                name=item_type.get("name") or "",
                code=item_type.get("code"),
                default_category_id=(
                    item_type.get(
                        "default_category_id"
                    )
                ),
                notes=item_type.get("notes"),
                is_active=False,
            )

            self._action_success = True

            self._action_message = (
                    "تم تعطيل الصنف: "
                    + (
                            item_type.get("name")
                            or ""
                    )
            )

            self.refresh()
            self.actionChanged.emit()

            return True

        except Exception as exc:

            self._action_success = False

            self._action_message = (
                self._translate_item_type_error(
                    exc
                )
            )

            self.actionChanged.emit()

            return False

    @Slot(
        int,
        result=bool,
    )
    def reactivateItemType(
            self,
            item_type_id,
    ):

        self._action_scope = "itemTypes"

        item_type = self._item_type_by_id(
            item_type_id
        )

        if item_type is None:
            self._action_success = False
            self._action_message = (
                "تعذر العثور على الصنف."
            )

            self.actionChanged.emit()

            return False

        try:

            self._service.update_item_type(
                item_type_id=item_type_id,
                name=item_type.get("name") or "",
                code=item_type.get("code"),
                default_category_id=(
                    item_type.get(
                        "default_category_id"
                    )
                ),
                notes=item_type.get("notes"),
                is_active=True,
            )

            self._action_success = True

            self._action_message = (
                    "تم إعادة تفعيل الصنف: "
                    + (
                            item_type.get("name")
                            or ""
                    )
            )

            self.refresh()
            self.actionChanged.emit()

            return True

        except Exception as exc:

            self._action_success = False

            self._action_message = (
                self._translate_item_type_error(
                    exc
                )
            )

            self.actionChanged.emit()

            return False

    def _category_by_id(self, category_id):

        for category in self._all_categories:

            try:
                if int(category.get("id")) == int(category_id):
                    return category

            except (TypeError, ValueError):
                continue

        return None

    @staticmethod
    def _optional_float(value):
        """Optional amount parsed as Decimal (the historical name is kept for callers)."""

        if value is None:
            return None

        text = str(value).strip()

        if not text:
            return None

        text = text.replace("٫", ".")

        if "," in text and "." not in text:
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")

        try:
            amount = Decimal(text)

        except InvalidOperation as exc:
            raise ValueError(
                "Invalid numeric value."
            ) from exc

        if not amount.is_finite():
            raise ValueError("Invalid numeric value.")

        return amount

    @staticmethod
    def _translate_category_error(exc):

        if isinstance(exc, PermissionError):
            return "ليس لديك صلاحية تعديل البيانات الأساسية (المصانع والأصناف والفئات)."

        message = str(exc)

        translations = {
            "Purchase making minimum cannot be negative.":
                "الحد الأدنى لمصنعية الشراء لا يمكن أن يكون سالباً.",

            "Purchase making maximum cannot be negative.":
                "الحد الأقصى لمصنعية الشراء لا يمكن أن يكون سالباً.",

            "Purchase making minimum cannot exceed maximum.":
                "الحد الأدنى لمصنعية الشراء لا يمكن أن يتجاوز الحد الأقصى.",

            "Sale making minimum cannot be negative.":
                "الحد الأدنى لمصنعية البيع لا يمكن أن يكون سالباً.",

            "Sale making maximum cannot be negative.":
                "الحد الأقصى لمصنعية البيع لا يمكن أن يكون سالباً.",

            "Sale making minimum cannot exceed maximum.":
                "الحد الأدنى لمصنعية البيع لا يمكن أن يتجاوز الحد الأقصى.",

            "Manager approval threshold cannot be negative.":
                "حد موافقة المدير لا يمكن أن يكون سالباً.",

            "Category does not exist.":
                "الفئة غير موجودة.",

            "Invalid numeric value.":
                "أحد القيم الرقمية غير صحيح.",

            "Category code is required.":
                "كود الفئة مطلوب.",

            "Category code is invalid.":
                "كود الفئة غير صالح (بدون مسافات، 20 حرفاً كحد أقصى).",

            "Category name is too long.":
                "اسم الفئة طويل جداً.",

            "Category code already exists.":
                "كود الفئة مستخدم بالفعل.",

            "Category is the default category of an item type.":
                "لا يمكن حذف الفئة نهائيًا لأنها الفئة الافتراضية لأحد الأصناف. "
                "غيّر الفئة الافتراضية للصنف أولاً أو عطّل الفئة.",
        }

        return translations.get(
            message,
            "تعذر تنفيذ العملية: " + message
        )

    @Slot(
        int,
        str,
        str,
        str,
        str,
        str,
        str,
        result=bool
    )
    def updateCategoryRules(
            self,
            category_id,
            purchase_min,
            purchase_max,
            sale_min,
            sale_max,
            manager_approval_below,
            notes,
    ):

        self._action_scope = "categories"

        try:

            purchase_min_value = self._optional_float(
                purchase_min
            )

            purchase_max_value = self._optional_float(
                purchase_max
            )

            sale_min_value = self._optional_float(
                sale_min
            )

            sale_max_value = self._optional_float(
                sale_max
            )

            manager_value = self._optional_float(
                manager_approval_below
            )

            self._service.update_category_rules(
                category_id=category_id,
                purchase_min=purchase_min_value,
                purchase_max=purchase_max_value,
                sale_min=sale_min_value,
                sale_max=sale_max_value,
                manager_approval_below=manager_value,
                notes=notes,
                is_active=True,
            )

            self._action_success = True
            self._action_message = (
                "تم حفظ قواعد الفئة بنجاح."
            )

            self.refresh()
            self.actionChanged.emit()

            return True

        except Exception as exc:

            self._action_success = False

            self._action_message = (
                self._translate_category_error(exc)
            )

            self.actionChanged.emit()

            return False

    # =========================================================
    # CATEGORIES — create / disable / reactivate
    # =========================================================

    @Slot(str, str, str, str, str, str, str, str, result=bool)
    def createCategory(self, code, name, purchase_min, purchase_max, sale_min, sale_max,
                       manager_approval_below, notes):
        try:
            category_id = self._service.create_category(
                code=code, name=name,
                purchase_min=self._optional_float(purchase_min),
                purchase_max=self._optional_float(purchase_max),
                sale_min=self._optional_float(sale_min),
                sale_max=self._optional_float(sale_max),
                manager_approval_below=self._optional_float(manager_approval_below),
                notes=notes,
            )
        except Exception as exc:
            self._set_action("categories", False, self._translate_category_error(exc))
            return False
        self.refresh()
        self._set_action("categories", True,
                         "تم إضافة الفئة بنجاح: " + str(code or "").strip().upper()
                         + f" — رقم {category_id}")
        return True

    def _set_category_active(self, category_id, active):
        category = self._category_by_id(category_id)
        if category is None:
            self._set_action("categories", False, "تعذر العثور على الفئة.")
            return False
        try:
            self._service.set_category_active(int(category_id), active)
        except Exception as exc:
            self._set_action("categories", False, self._translate_category_error(exc))
            return False
        self.refresh()
        self._set_action("categories", True,
                         ("تم إعادة تفعيل الفئة: " if active else "تم تعطيل الفئة: ")
                         + str(category.get("code") or ""))
        return True

    @Slot(int, result=bool)
    def disableCategory(self, category_id):
        return self._set_category_active(category_id, False)

    @Slot(int, result=bool)
    def reactivateCategory(self, category_id):
        return self._set_category_active(category_id, True)

    # =========================================================
    # PERMANENT DELETE — the service decides (never QML)
    # =========================================================

    IN_USE = {
        "factories": "لا يمكن حذف المصنع نهائيًا لأنه مرتبط ببيانات في المخزون. يمكنك تعطيله فقط.",
        "itemTypes": "لا يمكن حذف الصنف نهائيًا لأنه مرتبط ببيانات في المخزون. يمكنك تعطيله فقط.",
        "categories": "لا يمكن حذف الفئة نهائيًا لأنها مرتبطة ببيانات في المخزون. يمكنك تعطيلها فقط.",
    }
    DELETED = {"factories": "تم حذف المصنع نهائيًا: ", "itemTypes": "تم حذف الصنف نهائيًا: ",
               "categories": "تم حذف الفئة نهائيًا: "}

    def _delete(self, scope, record, delete, translate):
        if record is None:
            self._set_action(scope, False, "تعذر العثور على السجل.")
            return False
        try:
            delete(int(record["id"]))
        except MasterDataInUse:
            self._set_action(scope, False, self.IN_USE[scope])
            return False
        except Exception as exc:
            self._set_action(scope, False, translate(exc))
            return False
        self.refresh()
        label = record.get("name") if scope != "categories" else record.get("code")
        self._set_action(scope, True, self.DELETED[scope] + str(label or ""))
        return True

    @Slot(int, result=bool)
    def deleteFactory(self, factory_id):
        return self._delete("factories", self._factory_by_id(factory_id),
                            self._service.delete_factory, self._translate_factory_error)

    @Slot(int, result=bool)
    def deleteItemType(self, item_type_id):
        return self._delete("itemTypes", self._item_type_by_id(item_type_id),
                            self._service.delete_item_type, self._translate_item_type_error)

    @Slot(int, result=bool)
    def deleteCategory(self, category_id):
        return self._delete("categories", self._category_by_id(category_id),
                            self._service.delete_category, self._translate_category_error)
