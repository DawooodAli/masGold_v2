"""Local (per-workstation) printer choice: one Bill printer and one Label printer.

A physical Windows printer belongs to this computer, not to the shop's business data,
so it is kept in QSettings (the current Windows user's registry / ini), never in MySQL.
The two printers are independent keys: changing one never touches the other.
"""
from PySide6.QtCore import QSettings

ORGANIZATION, APPLICATION = "masGold", "masGold_v2"
BILL_KEY, LABEL_KEY = "printing/bill_printer", "printing/label_printer"
# Physical calibration of the preprinted A5 bill (mm). Missing keys = the built-in provisional
# values in services/bill_overlay.BillCalibration. Only the Bill printer; never the Label printer.
BILL_STYLE_KEY = "printing/bill_style"        # "variant_c" (default) or "prototype"
BILL_CALIBRATION_KEYS = {"upper_gold_y_mm": "printing/bill_upper_gold_y_mm",
                         "lower_gold_y_mm": "printing/bill_lower_gold_y_mm",
                         "offset_x_mm": "printing/bill_offset_x_mm",
                         "offset_y_mm": "printing/bill_offset_y_mm"}

# The prototype's proven formats (masGold/modules/bill_printer.py and inventory_page.py).
BILL_FORMAT_TEXT = "A5 أفقي — 210 × 148 مم (ورق فواتير مطبوع مسبقاً)"
LABEL_FORMAT_TEXT = "ليبل Zebra مزدوج — 641 × 310 نقطة (≈ 80 × 39 مم عند 203 dpi)"


def installed_printers():
    """The printers Windows (through Qt) reports right now — never typed by the user."""
    from PySide6.QtPrintSupport import QPrinterInfo
    return sorted(p.printerName() for p in QPrinterInfo.availablePrinters())


class PrinterSettings:
    def __init__(self, store=None):
        self._store = store if store is not None else QSettings(
            QSettings.NativeFormat, QSettings.UserScope, ORGANIZATION, APPLICATION)

    def _get(self, key):
        value = self._store.value(key, "")
        return str(value or "").strip()

    def _set(self, key, name):
        self._store.setValue(key, (name or "").strip())
        self._store.sync()

    def bill_printer(self):
        return self._get(BILL_KEY)

    def label_printer(self):
        return self._get(LABEL_KEY)

    def set_bill_printer(self, name):
        self._set(BILL_KEY, name)

    def set_label_printer(self, name):
        self._set(LABEL_KEY, name)

    def bill_calibration(self):
        values = {}
        for name, key in BILL_CALIBRATION_KEYS.items():
            raw = str(self._store.value(key, "") or "").strip()
            try:
                values[name] = float(raw) if raw else None
            except ValueError:
                values[name] = None
        return values

    def set_bill_calibration(self, **values):
        for name, value in values.items():
            self._store.setValue(BILL_CALIBRATION_KEYS[name], "" if value is None else str(float(value)))
        self._store.sync()

    def bill_style(self):
        return self._get(BILL_STYLE_KEY)

    def set_bill_style(self, style):
        self._set(BILL_STYLE_KEY, style)
