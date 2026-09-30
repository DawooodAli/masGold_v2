"""A database-free authorization double: the grants live in `.permissions` (the "database"), separate from
the session cache, so a test can revoke or restore a permission while the user stays logged in."""
import dataclasses


class FakeAuthorization:
    def __init__(self, session, permissions=None):
        self.session = session
        self._permissions = permissions
        self.checked = []                       # ("READ" | "WRITE", permission code or tuple of codes)

    @property
    def permissions(self):
        actor = self.session.current_actor()
        return self._permissions if self._permissions is not None else (actor.permissions if actor else frozenset())

    @permissions.setter
    def permissions(self, value):
        self._permissions = frozenset(value)

    def _verified(self, kind, actor, codes, any_of=False):
        self.checked.append((kind, codes))
        granted = set(self.permissions)
        wanted = set(codes) if any_of else {codes}
        if not self.session.is_current(actor) or "APP_LOGIN" not in granted or \
                (not wanted & granted if any_of else not wanted <= granted):
            raise PermissionError("لا توجد صلاحية لتنفيذ العملية.")
        return dataclasses.replace(actor, permissions=frozenset(granted))

    def require_permission(self, connection, actor, code, **kwargs):
        return self._verified("WRITE", actor, code)

    require_permission_for_write = require_permission

    def require_permission_read(self, connection, actor, code):
        return self._verified("READ", actor, code)

    def require_any_permission_read(self, connection, actor, codes):
        return self._verified("READ", actor, tuple(codes), any_of=True)


def customers_page_service(permissions=frozenset({"APP_LOGIN", "CUSTOMERS_VIEW", "CUSTOMERS_MANAGE"})):
    """CustomerService for a logged-in user (the Customers page context) with revocable grants."""
    from app.session import AppSession
    from services.authentication_service import AuthenticationResult, _VERIFIED
    from services.customer_service import CustomerService
    session = AppSession()
    session._establish(AuthenticationResult(1, None, "موظف", frozenset(permissions), _VERIFIED), int(session.generation))
    return CustomerService(session, authorization=FakeAuthorization(session))
