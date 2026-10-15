"""Settings → المستخدمون والصلاحيات: users, their roles / activation, role grants and credentials.

Formats what UserAdminService returns; nothing here decides authorization (the service re-checks USERS_MANAGE
and the administrator's password for every write). The administrator's password and any new password cross
from QML to the service in the same call and are never stored on this object, shown or logged. A user's
identity (user_id) is an immutable decimal string, never inferred from a name.
"""
from PySide6.QtCore import QObject, Property, Signal, Slot

from app.formatting import datetime_text

PERMISSION_TEXT = {
    "APP_LOGIN": "الدخول للتطبيق", "BILLING_USE": "استخدام الفواتير", "INVENTORY_VIEW": "عرض المخزون",
    "INVENTORY_MANAGE": "إدارة المخزون", "CUSTOMERS_VIEW": "عرض العملاء", "CUSTOMERS_MANAGE": "إدارة العملاء",
    "REPORTS_VIEW": "عرض التقارير", "OWNER_JRD_VIEW": "عرض الجرد السري للمالك", "PAYMENT_CORRECT": "تصحيح الدفع",
    "DEAD_CASH_VIEW": "عرض الديون المعدومة", "DEAD_CASH_MANAGE": "شطب الديون المعدومة",
    "DEAD_CASH_REVERSE": "عكس شطب الدين", "LEGACY_BILLS_VIEW": "عرض الفواتير القديمة",
    "LEGACY_BILLS_REPRINT": "إعادة طباعة الفواتير القديمة", "SETTINGS_MANAGE": "إدارة الإعدادات",
    "USERS_MANAGE": "إدارة المستخدمين والصلاحيات", "AUDIT_VIEW": "عرض سجل التدقيق",
    "MANAGEMENT_REVIEW": "مراجعات المدير", "TRANSACTION_CORRECT": "تصحيح / عكس العمليات",
    "INVOICE_SETTLEMENT_APPROVE": "الموافقة على تسوية الفاتورة", "CUSTOMER_ACCOUNT_SETTLE": "تسوية حساب العميل",
    "SUPPLIER_VIEW": "عرض الموردين", "SUPPLIER_MANAGE": "إدارة الموردين",
    "SUPPLIER_INVOICE_EDIT": "تحرير فواتير الموردين", "SUPPLIER_INVOICE_POST": "ترحيل فواتير الموردين",
    "SUPPLIER_INTAKE": "إدخال قطع فواتير الموردين", "SUPPLIER_INVOICE_REVERSE": "عكس فاتورة مورد مرحّلة",
    "SUPPLIER_MAKING_PAY": "دفع مصنعيات الموردين", "SUPPLIER_MAKING_PAY_REVERSE": "عكس دفعة مصنعيات المورد",
    "MARKET_TRIP_MANAGE": "عهدة السوق (نقدية وذهب)",
}
ROLE_TEXT = {"OWNER": "المالك", "MANAGER": "المدير", "SALESPERSON": "البائع", "CASHIER": "أمين الصندوق"}


class UserAdminViewModel(QObject):
    changed = Signal()

    def __init__(self, session, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._workers, self._request, self._latest, self._busy = set(), 0, {}, set()
        self._shutdown = False
        self._data, self._error, self._message = {}, "", ""
        self._selected_user = ""

    # ------------------------------------------------------------ plumbing
    def _service_instance(self):
        if self._service is None:
            from services.user_admin_service import UserAdminService
            self._service = UserAdminService(self._session)
        return self._service

    def _start(self, operation, call):
        from app.workers.invoice_worker import InvoiceWorker
        actor = self._session.current_actor()
        if self._shutdown or actor is None:
            return
        self._request += 1
        self._latest[operation] = self._request
        self._busy.add(operation)
        worker = InvoiceWorker(call, self._session, actor, self._request, operation, self)
        worker.completed.connect(self._completed)
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        self.changed.emit()
        worker.start()

    @Slot(int, str, str, object, str, str, str)
    def _completed(self, request, generation, operation, result, error, code, kind):
        actor = self._session.current_actor()
        if self._shutdown or actor is None or str(actor.generation) != generation:
            return
        if self._latest.get(operation) != request:
            return
        self._busy.discard(operation)
        getattr(self, f"_on_{operation}")(result, error)
        self.changed.emit()

    @Slot()
    def shutdown(self):
        self._shutdown = True
        for worker in list(self._workers):
            worker.wait(5000)

    # ------------------------------------------------------------ read
    @Slot()
    def refresh(self):
        if not self.canManage:
            return
        service = self._service_instance()
        self._start("overview", lambda _expected_actor: service.overview(_expected_actor=_expected_actor))

    def _on_overview(self, result, error):
        if error:
            self._error = error
            return
        self._data, self._error = result, ""
        if not any(u["id"] == self._selected_user for u in result["users"]):
            self._selected_user = ""

    @Slot(str)
    def selectUser(self, user_id):
        self._selected_user = user_id if any(u["id"] == user_id for u in self._data.get("users", [])) else ""
        self.changed.emit()

    @Slot()
    def clearMessage(self):
        if self._message or self._error:
            self._message = self._error = ""
            self.changed.emit()

    # ------------------------------------------------------------ writes (each re-checked by the service)
    def _write(self, operation, text, call):
        if operation in self._busy:
            return
        self._message = self._error = ""
        self._done_text = text
        self._start("write", lambda _expected_actor: call(self._service_instance(), _expected_actor))
        self.changed.emit()

    def _on_write(self, result, error):
        if error:
            self._error = error
            return
        self._message = self._done_text if result.get("changed", True) else "لا يوجد تغيير."
        self.refresh()

    @Slot(str, bool, str)
    def setUserActive(self, user_id, active, admin_password):
        self._write("write", "تم تفعيل المستخدم." if active else "تم تعطيل المستخدم.",
                    lambda s, a: s.set_user_active(user_id, active, admin_password, _expected_actor=a))

    @Slot(str, "QStringList", str)
    def setUserRoles(self, user_id, role_codes, admin_password):
        self._write("write", "تم تحديث أدوار المستخدم.",
                    lambda s, a: s.set_user_roles(user_id, list(role_codes), admin_password, _expected_actor=a))

    @Slot(str, str, bool, str)
    def setRolePermission(self, role_code, permission_code, granted, admin_password):
        self._write("write", "تم منح الصلاحية." if granted else "تم سحب الصلاحية.",
                    lambda s, a: s.set_role_permission(role_code, permission_code, granted, admin_password,
                                                       _expected_actor=a))

    @Slot(str, str, str, str, str)
    def resetPassword(self, user_id, new_password, confirmation, reason, admin_password):
        self._write("write", "تمت إعادة تعيين كلمة المرور.",
                    lambda s, a: s.reset_password(user_id, new_password, confirmation, reason, admin_password,
                                                  _expected_actor=a))

    @Slot(str, str, str, str, str, str, str)
    def createUser(self, username, display_name, employee_id, role_code, new_password, confirmation, admin_password):
        self._write("write", "تم إنشاء المستخدم.",
                    lambda s, a: s.create_user(username, display_name, employee_id or None, role_code, new_password,
                                               confirmation, admin_password, _expected_actor=a))

    # ------------------------------------------------------------ properties
    @Property(bool, notify=changed)
    def canManage(self):
        actor = self._session.current_actor()
        return bool(actor and "USERS_MANAGE" in actor.permissions)

    @Property(bool, notify=changed)
    def busy(self):
        return bool(self._busy)

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(str, notify=changed)
    def selectedUserId(self):
        return self._selected_user

    @Property("QVariantList", notify=changed)
    def users(self):
        out = []
        for u in self._data.get("users", []):
            roles = u["roles"]
            out.append({
                "id": u["id"], "username": u["username"], "displayName": u["display_name"],
                "active": u["is_active"], "stateText": "نشط" if u["is_active"] else "معطل",
                "roleCodes": [r["code"] for r in roles],
                "rolesText": "، ".join(ROLE_TEXT.get(r["code"], r["name"]) for r in roles) or "بدون دور",
                "employee": u["employee_name"] or "غير مرتبط بموظف",
                "lastLogin": datetime_text(u["last_login_at"]) if u["last_login_at"] else "لم يسجل الدخول",
                "isSelf": u["is_self"], "isOwner": u["is_owner"], "selected": u["id"] == self._selected_user})
        return out

    @Property("QVariantList", notify=changed)
    def roles(self):
        return [{"code": r["code"], "name": ROLE_TEXT.get(r["code"], r["name"]), "locked": r["locked"],
                 "active": r["is_active"], "userCount": r["user_count"], "permissions": r["permissions"]}
                for r in self._data.get("roles", [])]

    @Property("QVariantList", notify=changed)
    def permissions(self):
        return [{"code": p["code"], "name": PERMISSION_TEXT.get(p["code"], p["name"]),
                 "description": p["description"], "ownerOnly": p["owner_only"]}
                for p in self._data.get("permissions", [])]

    @Property("QVariantList", notify=changed)
    def employees(self):
        return [{"id": e["id"], "name": (e["code"] + " — " if e["code"] else "") + e["name"]}
                for e in self._data.get("employees", [])]
