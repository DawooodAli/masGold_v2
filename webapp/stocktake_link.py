"""The link between the iPad camera scanner and the desktop الجرد بالباركود session (owner request 2026-11-04).

The desktop view model registers a handler while its stocktake screen is open and withdraws it when the screen closes. The HTTP server threads call `scan()`: the handler hands the code to the GUI thread
(the one place the counting session lives) and waits for its answer. No database write happens: a scan only moves a piece from المتبقي to تم الجرد inside the transient session."""
import threading

_lock = threading.Lock()
_handler = None


def set_handler(handler):
    global _handler
    with _lock:
        _handler = handler


def clear_handler(handler=None):
    """Withdraw `handler` (or whatever is registered when None)."""
    global _handler
    with _lock:
        if handler is None or _handler == handler:
            _handler = None


def is_open():
    with _lock:
        return _handler is not None


def scan(code):
    """The desktop's answer {result, message, ...}, or None when no stocktake screen is open."""
    with _lock:
        handler = _handler
    return None if handler is None else handler(code)
