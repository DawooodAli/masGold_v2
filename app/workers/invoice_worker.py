"""Run Billing V2 reads and finalization away from the GUI thread."""
import logging
import traceback

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


def _arabic(message):
    return any("؀" <= char <= "ۿ" for char in message)


class InvoiceWorker(QThread):
    # request id, session generation, operation, result, error message, error code, error kind
    completed = Signal(int, str, str, object, str, str, str)

    def __init__(self, call, session, actor, request, operation, parent=None):
        super().__init__(parent)
        self._call = call              # callable(_expected_actor=actor) -> result
        self._session = session
        self._actor = actor
        self._request = request
        self._operation = operation

    def run(self):
        result, error, code, kind = None, "", "", ""
        try:
            if not self._session.is_current(self._actor):
                return
            # Bind the service operation to the login that started it, even if a
            # logout happens between this check and the service's own checks.
            result = self._call(_expected_actor=self._actor)
        except (ValueError, PermissionError) as exc:
            # Business-rule refusals (validation, confirmations, permissions) are
            # shown to the employee; only unexpected failures are logged as errors.
            message = str(exc)
            error = message if _arabic(message) else self._failure_message()
            code, kind = getattr(exc, "code", "") or "", type(exc).__name__
        except Exception as exc:
            self._report(exc)
            error, kind = self._failure_message(), type(exc).__name__
        finally:
            self._call = None
        self.completed.emit(self._request, str(self._actor.generation), self._operation,
                            result, error, code, kind)

    def _failure_message(self):
        if self._operation == "finalize":
            return "تعذر حفظ الفاتورة ولم يُحفظ أي جزء منها. راجع البيانات وأعد المحاولة."
        if self._operation == "print":
            # The invoice itself is already finalized; only the print step failed.
            return "تم حفظ الفاتورة بنجاح، ولكن تعذرت الطباعة."
        return "تعذر تحميل بيانات الفاتورة. أعد المحاولة."

    def _report(self, exc):
        # Diagnostics without payload, customer data, SQL parameters or messages.
        frames = " -> ".join(f"{frame.name}:{frame.lineno}" for frame in traceback.extract_tb(exc.__traceback__))
        logger.error("Invoice operation=%s exception=%s code=%s frames=%s",
                     self._operation, type(exc).__name__, getattr(exc, "code", getattr(exc, "errno", None)), frames)
