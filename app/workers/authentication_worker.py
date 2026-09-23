from PySide6.QtCore import QThread, Signal

from services.authentication_service import AuthenticationError, GENERIC_ERROR


class AuthenticationWorker(QThread):
    completed = Signal(int, str, object, str)

    def __init__(self, service, username, password, request_id, generation, parent=None):
        super().__init__(parent)
        self._service = service
        self._username = username
        self._password = password
        self._request_id = request_id
        self._generation = generation

    def run(self):
        result, error = None, ""
        try:
            result = self._service.authenticate(self._username, self._password)
        except AuthenticationError as exc:
            error = str(exc)
        except Exception:
            error = GENERIC_ERROR
        finally:
            self._password = None
            self._username = None
        self.completed.emit(self._request_id, self._generation, result, error)
