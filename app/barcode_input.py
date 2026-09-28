"""Barcode input that does not depend on the Windows keyboard layout.

The scanner is a keyboard-wedge device: it "types" each character by pressing the physical key
that produces it on a US keyboard. Windows turns those key presses into characters through the
ACTIVE layout, so under the Arabic layout the same scan arrives as Arabic letters (for example
Shift+B → "لآ"); digits on the number row happen to survive. The key's hardware scan code
(Qt: KeyEvent.nativeScanCode, set 1 on Windows) identifies the physical key whatever the layout,
so barcode fields map scan codes through a fixed US table instead of trusting event.text.

Only barcode fields use this. Every other field (names, addresses, notes) is untouched.

Canonical barcode characters: A–Z, a–z, 0–9 and "-" (the characters stored barcodes actually
use, plus the scanner separators "." "/" "_" which Code128 labels may carry). Anything else is
refused — never "corrected" into a different, possibly valid, barcode.
"""
import re

from PySide6.QtCore import QObject, Slot

# Set-1 scan code → (unshifted, shifted) on a US layout, limited to barcode characters.
_ROWS = {
    0x02: "1!", 0x03: "2@", 0x04: "3#", 0x05: "4$", 0x06: "5%", 0x07: "6^", 0x08: "7&", 0x09: "8*",
    0x0A: "9(", 0x0B: "0)", 0x0C: "-_",
    0x10: "qQ", 0x11: "wW", 0x12: "eE", 0x13: "rR", 0x14: "tT", 0x15: "yY", 0x16: "uU", 0x17: "iI",
    0x18: "oO", 0x19: "pP",
    0x1E: "aA", 0x1F: "sS", 0x20: "dD", 0x21: "fF", 0x22: "gG", 0x23: "hH", 0x24: "jJ", 0x25: "kK",
    0x26: "lL",
    0x2C: "zZ", 0x2D: "xX", 0x2E: "cC", 0x2F: "vV", 0x30: "bB", 0x31: "nN", 0x32: "mM",
    0x34: "..", 0x35: "/?",
    # Numeric keypad (scanners set to "keypad" digits); NumLock state does not change the scan code.
    0x47: "77", 0x48: "88", 0x49: "99", 0x4A: "--", 0x4B: "44", 0x4C: "55", 0x4D: "66", 0x4F: "11",
    0x50: "22", 0x51: "33", 0x52: "00", 0x53: "..",
}
ALLOWED = re.compile(r"^[A-Za-z0-9\-._/]+$")
# Digits typed/pasted as Arabic-Indic or Persian digits are the same digits.
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
INVALID_TEXT = "الباركود يحتوي على حروف غير صالحة. امسحه مرة أخرى أو اكتبه بالحروف الإنجليزية والأرقام."


def char_for_scan_code(scan_code, shift=False):
    """The canonical character of one physical key press ('' when the key is not a barcode key:
    Enter, Backspace, arrows… keep their normal behaviour)."""
    code = int(scan_code or 0)
    # Extended keys (arrows, Home/End, keypad Enter…) carry 0x100 on Windows: never a character,
    # even where the low byte equals a keypad digit (↑ is 0x148, keypad 8 is 0x48).
    pair = None if code & ~0xFF else _ROWS.get(code)
    if pair is None:
        return ""
    char = pair[1 if shift else 0]
    return char if ALLOWED.match(char) else ""


def canonical_barcode(text):
    """(barcode, error). Trims, maps Arabic-Indic digits; refuses anything outside the set."""
    value = (text or "").strip().translate(_DIGITS)
    if not value:
        return "", ""
    if not ALLOWED.match(value):
        return "", INVALID_TEXT
    return value, ""


def burst_barcode(strokes, *, max_gap_ms=35, min_length=4):
    """For fields that take both Arabic text AND scans (item search): when the whole entry arrived
    as a scanner burst, rebuild it from the scan codes. strokes = [(ms, scan_code, shift), ...].
    Returns the barcode, or '' when this was ordinary typing."""
    if len(strokes) < min_length:
        return ""
    times = [s[0] for s in strokes]
    if any(b - a > max_gap_ms for a, b in zip(times, times[1:])):
        return ""
    chars = [char_for_scan_code(code, shift) for _, code, shift in strokes]
    return "" if not all(chars) else "".join(chars)


class BarcodeInput(QObject):
    """Exposed to QML as `barcodeInput`."""

    @Slot(int, bool, result=str)
    def charFor(self, scan_code, shift):
        return char_for_scan_code(scan_code, shift)

    @Slot(str, result=str)
    def canonical(self, text):
        return canonical_barcode(text)[0]

    @Slot(str, result=str)
    def problem(self, text):
        return canonical_barcode(text)[1]

    @Slot("QVariantList", result=str)
    def fromBurst(self, strokes):
        return burst_barcode([(int(s["t"]), int(s["code"]), bool(s["shift"])) for s in strokes])
