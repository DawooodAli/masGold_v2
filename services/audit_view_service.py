"""The recent activity (audit_log) in one READ-ONLY snapshot. Right: USERS_MANAGE (the owner), re-checked in the transaction; the actor is the session user."""
from repositories.audit_view_repository import AuditViewRepository
from services.authorization_service import AuthorizationService
from services.gold_position_service import _Discard, read_transaction

PERMISSION = "USERS_MANAGE"
MAX_ROWS = 100


class AuditViewService:
    def __init__(self, session, *, transaction_factory=read_transaction, authorization=None, repository=AuditViewRepository):
        self._session, self._transaction, self._repository = session, transaction_factory, repository
        self._authorization = authorization or AuthorizationService(session)

    def recent(self, text="", *, _expected_actor=None):
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if PERMISSION not in actor.permissions:
            raise PermissionError("سجل النشاط للمالك فقط.")
        text = str(text or "").strip()[:60]
        try:
            with self._transaction() as connection:
                self._authorization.require_permission_read(connection, actor, PERMISSION)
                raise _Discard(self._repository(connection).recent(text, MAX_ROWS))
        except _Discard as done:
            return done.args[0]
