"""Service-boundary authorization; UI permission checks are advisory only.

Two explicit paths (never a boolean flag, so the choice is visible in review):

require_permission_read       read-only screens. The user, employee and grants are re-read from the
                              database with ordinary non-locking SELECTs, so a revoked permission or a
                              deactivated user is refused on the next read — but one reader never blocks
                              another reader (or a writer) of the same logged-in user.
require_permission_for_write  every protected mutation. The user / employee rows and the grant rows are
                              locked FOR UPDATE for the rest of the caller's transaction, so the identity
                              and the permission cannot change under the write. `require_permission` is
                              this same strict path (its historical name).
"""
from app.session import ActorContext
from repositories.authentication_repository import AuthenticationRepository


class AuthorizationService:
    def __init__(self, session, repository_factory=AuthenticationRepository):
        self._session = session
        self._repository = repository_factory

    def _verify(self, connection, actor_context, permission_code, *, lock, require_employee):
        if not self._session.is_current(actor_context):
            raise PermissionError("انتهت جلسة الدخول.")
        repository = self._repository(connection)
        user = repository.get_identity(actor_context.user_id, lock=lock)
        if (not user or not user["is_active"] or user["must_change_password"]
                or (user["employee_id"] is not None and not user.get("employee_active"))):
            raise PermissionError("المستخدم غير مخول لتنفيذ العملية.")
        permissions = repository.effective_permissions(user["id"], lock=lock)
        if "APP_LOGIN" not in permissions or permission_code not in permissions:
            raise PermissionError("لا توجد صلاحية لتنفيذ العملية.")
        if require_employee and user["employee_id"] is None:
            raise PermissionError("يلزم ربط المستخدم بموظف نشط.")
        if not self._session.is_current(actor_context):
            raise PermissionError("انتهت جلسة الدخول.")
        # Capture the fresh relationship for this operation's audit snapshot.
        return ActorContext(int(user["id"]), user["employee_id"], user["display_name"],
                            permissions, actor_context.generation)

    def require_permission_for_write(self, connection, actor_context, permission_code,
                                     *, require_employee=False):
        """Authoritative write-time check: locks the identity and its grants for the transaction."""
        return self._verify(connection, actor_context, permission_code, lock=True,
                            require_employee=require_employee)

    # The historical name of the strict (locking) path; every existing caller is a protected write.
    require_permission = require_permission_for_write

    def require_any_permission_read(self, connection, actor_context, permission_codes):
        """Read-only check for screens opened by ANY ONE of several permissions (no row lock).

        The grants come from the database, so the screen stays open while one of them remains and is
        refused as soon as the last one is revoked."""
        verified = self._verify(connection, actor_context, "APP_LOGIN", lock=False, require_employee=False)
        if not set(permission_codes) & set(verified.permissions):
            raise PermissionError("لا توجد صلاحية لتنفيذ العملية.")
        return verified

    def require_permission_read(self, connection, actor_context, permission_code):
        """Read-only check against current database state, with no row lock of any kind.

        Never use this for a mutation: it does not hold the identity or the grants stable."""
        return self._verify(connection, actor_context, permission_code, lock=False,
                            require_employee=False)
