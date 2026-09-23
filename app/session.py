"""One process-local identity, established only by verified authentication results."""
from dataclasses import dataclass
from threading import RLock

from PySide6.QtCore import QObject, Property, Signal, Slot


@dataclass(frozen=True)
class ActorContext:
    user_id: int
    employee_id: int | None
    display_name: str
    permissions: frozenset[str]
    generation: int


class AppSession(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = RLock()
        self._actor = None
        self._generation = 0

    def current_actor(self):
        with self._lock:
            return self._actor

    def require_authenticated(self):
        actor = self.current_actor()
        if actor is None:
            raise PermissionError("يلزم تسجيل الدخول.")
        return actor

    def is_current(self, actor):
        with self._lock:
            return actor is not None and actor is self._actor

    def _establish(self, result, expected_generation):
        # Not a Qt slot: there is deliberately no QML identity setter.
        from services.authentication_service import AuthenticationResult, _VERIFIED
        if not isinstance(result, AuthenticationResult) or result._proof is not _VERIFIED:
            raise PermissionError("Unverified authentication result")
        with self._lock:
            if expected_generation != self._generation:
                return False
            self._generation += 1
            self._actor = ActorContext(result.user_id, result.employee_id,
                                       result.display_name, result.permissions,
                                       self._generation)
        self.changed.emit()
        return True

    @Slot()
    def logout(self):
        with self._lock:
            self._generation += 1
            self._actor = None
        self.changed.emit()

    @Property(bool, notify=changed)
    def authenticated(self):
        return self.current_actor() is not None

    @Property(str, notify=changed)
    def userId(self):
        actor = self.current_actor()
        return str(actor.user_id) if actor else ""

    @Property(str, notify=changed)
    def employeeId(self):
        actor = self.current_actor()
        return str(actor.employee_id) if actor and actor.employee_id is not None else ""

    @Property(str, notify=changed)
    def displayName(self):
        actor = self.current_actor()
        return actor.display_name if actor else ""

    @Property("QStringList", notify=changed)
    def permissions(self):
        actor = self.current_actor()
        return sorted(actor.permissions) if actor else []

    @Property(str, notify=changed)
    def generation(self):
        with self._lock:
            return str(self._generation)

    @Slot(str, result=bool)
    def hasPermission(self, code):
        actor = self.current_actor()
        return bool(actor and code in actor.permissions)
