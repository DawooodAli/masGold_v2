from PySide6.QtCore import QThread, Signal

from services.authentication_service import AuthenticationError, SetupAlreadyCompleted, GENERIC_ERROR


class AuthenticationWorker(QThread):
    completed = Signal(int, str, object, str)

    def __init__(self, service, username, password, request_id, generation, parent=None,
                 *, operation="login", payload=None):
        super().__init__(parent)
        self._service = service
        self._username = username
        self._password = password
        self._request_id = request_id
        self._generation = generation
        self._operation = operation
        self._payload = payload or {}

    def run(self):
        result, error = None, ""
        try:
            if self._operation == "check":
                result = self._service.first_run_status()
            elif self._operation == "dev_login":
                # TEMPORARY DEVELOPMENT AUTO-LOGIN — DISABLE BEFORE PRODUCTION
                result = self._service.establish_development_session(self._username)
            elif self._operation == "setup":
                result = self._service.create_first_owner(**self._payload)
            else:
                result = self._service.authenticate(self._username, self._password)
        except SetupAlreadyCompleted:
            result = {"setup_required": False, "employees": []}
        except AuthenticationError as exc:
            error = str(exc)
        except ValueError as exc:
            error = str(exc) if self._operation == "setup" else GENERIC_ERROR
        except Exception:
            error = ("تعذر الاتصال أو إكمال الإعداد. أعد المحاولة؛ إن تم إنشاء الحساب فسيظهر تسجيل الدخول."
                     if self._operation != "login" else GENERIC_ERROR)
        finally:
            self._password = None
            self._username = None
            self._payload.clear()
        self.completed.emit(self._request_id, self._generation, result, error)
