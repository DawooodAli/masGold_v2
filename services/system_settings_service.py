"""System Settings (migration 081): keyed configuration values in `system_settings`.

A setting is a name, a stored text value, a type that decides how that text is read back, an
optional description and a public flag. Reading a public setting needs SYSTEM_SETTINGS_VIEW;
reading a private one and every write need the same right rechecked inside the transaction, and
a write also needs SYSTEM_SETTINGS_MANAGE. The actor is always the AppSession user, never a value
coming from the interface.

Values are stored as text and cast on read (INT / DECIMAL / BOOL / JSON / STRING). A setting is
never deleted: it is updated in place and every change is written to the shared audit trail with
its previous and new value, so history stays readable."""
from decimal import Decimal, InvalidOperation
from typing import Any

from repositories.system_settings_repository import SystemSettingsRepository
from services import setting_rules
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction


VIEW = "SYSTEM_SETTINGS_VIEW"
MANAGE = "SYSTEM_SETTINGS_MANAGE"

# The stored types of the table's ENUM column, in the order the form offers them.
TYPES = ("STRING", "INT", "DECIMAL", "BOOL", "JSON")
TYPE_TEXT = {"STRING": "نص", "INT": "عدد صحيح", "DECIMAL": "عدد عشري", "BOOL": "نعم / لا", "JSON": "JSON"}

# Keys every user may read without the settings screen (the interface needs them for labels and defaults).
PUBLIC_KEYS = ("shop_name", "shop_address", "shop_phone", "currency_symbol", "date_format", "invoice_print_copies")

KEY_MAX = 64
DESCRIPTION_MAX = 255


class SettingsError(ValueError):
    """A business-rule refusal, shown to the employee with its own message."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def clean_key(value):
    key = str(value or "").strip()
    if not key:
        raise SettingsError("KEY_REQUIRED", "اكتب اسم الإعداد.")
    if len(key) > KEY_MAX:
        raise SettingsError("KEY_LONG", f"اسم الإعداد حتى {KEY_MAX} حرفًا.")
    # The key is used as an identifier and in URLs and audit metadata: keep it simple and stable.
    if not all(char.isalnum() or char in "._-" for char in key):
        raise SettingsError("KEY_INVALID", "اسم الإعداد يقبل الحروف والأرقام والنقطة والشرطة والشرطة السفلية فقط.")
    return key


def clean_description(value):
    description = " ".join(str(value or "").split())
    if len(description) > DESCRIPTION_MAX:
        raise SettingsError("DESCRIPTION_LONG", f"الوصف حتى {DESCRIPTION_MAX} حرفًا.")
    return description or None


def cast_and_check(value, setting_type):
    """Validate the typed value against its type and return the stored text form.

    The stored form is always text; the type decides how it is read back. A value that does not
    fit its type is refused here rather than stored and read back as something else later."""
    if setting_type not in TYPES:
        raise SettingsError("TYPE_INVALID", "نوع الإعداد غير صحيح.")
    text = str(value).strip() if value is not None else ""
    if setting_type == "INT":
        try:
            int(text)
        except ValueError:
            raise SettingsError("VALUE_INVALID", "القيمة يجب أن تكون عددًا صحيحًا.")
    elif setting_type == "DECIMAL":
        try:
            Decimal(text or "0")
        except InvalidOperation:
            raise SettingsError("VALUE_INVALID", "القيمة يجب أن تكون عددًا صحيحًا أو عشريًا.")
    elif setting_type == "BOOL":
        if text.lower() not in ("0", "1", "true", "false", "yes", "no", "on", "off", ""):
            raise SettingsError("VALUE_INVALID", "القيمة نعم أو لا فقط.")
    elif setting_type == "JSON":
        import json
        try:
            json.loads(text or "null")
        except ValueError:
            raise SettingsError("VALUE_INVALID", "القيمة ليست JSON صحيحًا.")
    return text


def entry(row):
    return {"key": row["setting_key"], "value": row["setting_value"], "type": row["setting_type"],
            "type_text": TYPE_TEXT.get(row["setting_type"], row["setting_type"]),
            "description": row["description"] or "", "public": bool(row["is_public"])}


class SystemSettingsService:
    def __init__(self, session, *, transaction_factory=invoice_transaction,
                 repository=SystemSettingsRepository, authorization=None):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        self._authorization = authorization or AuthorizationService(session)

    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        return actor

    def can_view(self):
        actor = self._session.current_actor()
        return actor is not None and VIEW in actor.permissions

    def can_manage(self):
        actor = self._session.current_actor()
        return actor is not None and MANAGE in actor.permissions

    def overview(self, *, _expected_actor=None):
        """Every setting with its type and description, plus what this user may do."""
        actor = self._actor(_expected_actor)
        if VIEW not in actor.permissions:
            raise PermissionError("إعدادات النظام للمالك فقط.")
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, VIEW)
            repository = self._repository(connection)
            return {"settings": [entry(r) for r in repository.get_all()],
                    "can_manage": self.can_manage()}

    def public_settings(self, *, _expected_actor=None):
        """The public settings only, for the interface of a user who may not open the screen."""
        actor = self._actor(_expected_actor)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, VIEW)
            repository = self._repository(connection)
            return {r["setting_key"]: repository._cast_value(r["setting_value"], r["setting_type"])
                    for r in repository.get_public()}

    def set_value(self, key, value, setting_type="STRING", description=None, is_public=False, *, _expected_actor=None):
        """Create or update one setting (MANAGE, rechecked inside the transaction)."""
        actor = self._actor(_expected_actor)
        key = clean_key(key)
        setting_type = str(setting_type or "STRING").upper()
        stored = cast_and_check(value, setting_type)
        # A key the business rules APPLY (081, `setting_rules.SPEC`) must land inside its range: the
        # services fall back to the default silently, so refusing here is what keeps the screen's
        # "saved" honest. A key outside that SPEC is not one of ours and keeps the type check alone.
        problem = setting_rules.writer_problem(key, stored)
        if problem:
            raise SettingsError("VALUE_RANGE", problem)
        description = clean_description(description)
        public = 1 if is_public else 0

        def work(repository, session_actor):
            previous = repository.get_by_key(key)
            old = None if previous is None else previous["setting_value"]
            repository.set_value(key, stored, setting_type, description, public, session_actor.user_id)
            repository.audit(user_id=session_actor.user_id, event_type="SYSTEM_SETTING_SET",
                             entity_type="system_settings", entity_id=None, entity_reference=key,
                             description=f"إعداد النظام: {key}",
                             metadata={"key": key, "old": old, "new": stored, "type": setting_type, "public": public},
                             occurred_at=repository.now())
            return {"key": key, "changed": old != stored}

        return self._write(_expected_actor, work)

    def _write(self, expected_actor, work):
        session_actor = self._actor(expected_actor)
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, MANAGE)
            result = work(self._repository(connection), actor)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result
