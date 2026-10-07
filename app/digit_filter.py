"""Application-wide: typed Arabic-Indic / Persian digits (and the Arabic decimal separator) become
ASCII digits before any field receives them, so every number in the UI uses 0-9 and "." only.

One event filter on the application covers every TextField (weights, money, percentages, sizes,
barcodes, invoice numbers, quantities) without per-field code. Letters are never touched."""
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtGui import QKeyEvent

from services.number_text import normalize_digits


class DigitKeyFilter(QObject):
    def eventFilter(self, watched, event):
        if event.type() in (QEvent.KeyPress, QEvent.KeyRelease):
            text = event.text()
            if text:
                normalized = normalize_digits(text)
                if normalized != text:
                    replacement = QKeyEvent(event.type(), event.key(), event.modifiers(),
                                            event.nativeScanCode(), event.nativeVirtualKey(),
                                            event.nativeModifiers(), normalized, event.isAutoRepeat(),
                                            event.count())
                    QCoreApplication.sendEvent(watched, replacement)
                    return True
        return False
