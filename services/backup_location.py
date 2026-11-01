"""Settings → النسخ الاحتياطي التلقائي: the folder the daily database backups are written to. SETTINGS_MANAGE is rechecked from the AppSession on every call
(the actor is never supplied by QML). The folder is a choice of this computer (config/backup_settings.py); the scheduled tasks pick it up on their next run."""
from config import backup_settings


class BackupLocationService:
    MANAGE = "SETTINGS_MANAGE"

    def __init__(self, session, settings=backup_settings):
        self._session = session
        self._settings = settings

    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if self.MANAGE not in actor.permissions:
            raise PermissionError("ضبط مكان النسخ الاحتياطي للمالك فقط.")
        return actor

    def can_manage(self):
        actor = self._session.current_actor()
        return actor is not None and self.MANAGE in actor.permissions

    def current(self, *, _expected_actor=None):
        self._actor(_expected_actor)
        folder = self._settings.destination()
        reachable = self._settings.reachable(folder)
        return {"folder": str(folder), "default": str(self._settings.DEFAULT_DEST), "ok": reachable,
                "message": "" if reachable else f"الدرايف {folder.drive or folder} غير متاح الآن: النسخ الاحتياطي سيفشل حتى تغيّر المكان."}

    def save(self, folder, *, _expected_actor=None):
        self._actor(_expected_actor)
        self._settings.save(folder)
        return self.current()

    def test(self, folder, *, _expected_actor=None):
        """Checks a folder (creates it when its drive exists and writes / removes a small test file); nothing is saved."""
        self._actor(_expected_actor)
        ok, message = self._settings.check(folder)
        return {"folder": str(folder or "").strip(), "ok": ok, "message": message}
