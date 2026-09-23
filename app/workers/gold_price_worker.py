"""Run price reads, network refresh and activations away from the GUI thread."""
import logging
import traceback

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class GoldPriceWorker(QThread):
    completed = Signal(int, str, str, object, str)

    def __init__(self, service, session, actor, request, operation, payload=(), parent=None):
        super().__init__(parent)
        self._service = service
        self._session = session
        self._actor = actor
        self._request = request
        self._operation = operation
        self._payload = payload

    def run(self):
        result, error = None, ""
        try:
            if not self._session.is_current(self._actor):
                return
            operation = getattr(self._service, {
                "snapshot": "snapshot", "refresh": "refresh",
                "manual": "activate_manual", "auto": "return_to_auto",
            }[self._operation])
            if self._operation == "snapshot":
                result = operation(*self._payload)
            else:
                # Bind the service operation to the initiating login, even if logout
                # occurs between the worker's check and service authorization.
                result = operation(*self._payload, _expected_actor=self._actor)
        except (ValueError, PermissionError) as exc:
            self._report_failure(exc)
            message = str(exc)
            error = message if any("\u0600" <= char <= "\u06ff" for char in message) else self._failure_message()
        except Exception as exc:
            self._report_failure(exc)
            error = self._failure_message()
        finally:
            self._payload = ()
        self.completed.emit(self._request, str(self._actor.generation),
                            self._operation, result, error)

    def _failure_message(self):
        if self._operation == "manual":
            return "تعذر تأكيد اعتماد السعر اليدوي. حدّث الأسعار للتحقق من آخر حالة محفوظة ثم أعد المحاولة."
        return "تعذر تحديث أسعار الذهب. بقيت آخر أسعار معتمدة محفوظة؛ أعد المحاولة."

    def _report_failure(self, exc):
        # Useful diagnostics without logging payload, reason, credentials, SQL
        # parameters, exception messages, or locals from traceback frames.
        frames = " -> ".join(f"{frame.name}:{frame.lineno}"
                             for frame in traceback.extract_tb(exc.__traceback__))
        logger.error("Gold price operation=%s exception=%s code=%s frames=%s",
                     self._operation, type(exc).__name__, getattr(exc, "errno", None), frames)
