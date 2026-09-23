"""Service-boundary authorization; UI permission checks are advisory only."""
from app.session import ActorContext
from repositories.authentication_repository import AuthenticationRepository


class AuthorizationService:
    def __init__(self, session, repository_factory=AuthenticationRepository):
        self._session = session
        self._repository = repository_factory

    def require_permission(self, connection, actor_context, permission_code,
                           *, require_employee=False):
        if not self._session.is_current(actor_context):
            raise PermissionError("انتهت جلسة الدخول.")
        repository = self._repository(connection)
        user = repository.get_identity(actor_context.user_id, lock=True)
        if (not user or not user["is_active"] or user["must_change_password"]
                or (user["employee_id"] is not None and not user.get("employee_active"))):
            raise PermissionError("المستخدم غير مخول لتنفيذ العملية.")
        permissions = repository.effective_permissions(user["id"], lock=True)
        if "APP_LOGIN" not in permissions or permission_code not in permissions:
            raise PermissionError("لا توجد صلاحية لتنفيذ العملية.")
        if require_employee and user["employee_id"] is None:
            raise PermissionError("يلزم ربط المستخدم بموظف نشط.")
        if not self._session.is_current(actor_context):
            raise PermissionError("انتهت جلسة الدخول.")
        # Capture the fresh relationship for this operation's audit snapshot.
        return ActorContext(int(user["id"]), user["employee_id"], user["display_name"],
                            permissions, actor_context.generation)
