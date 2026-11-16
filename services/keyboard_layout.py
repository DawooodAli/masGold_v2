"""A barcode typed or scanned while the Arabic keyboard layout is active arrives as Arabic letters
(the scanner sends key positions). This maps the standard Arabic 101 layout back to the Latin keys
so a stock search still finds the barcode. Used only as a fallback when the text itself finds nothing."""
import re

_PAIRS = {
    "ض": "q", "ص": "w", "ث": "e", "ق": "r", "ف": "t", "غ": "y", "ع": "u", "ه": "i", "خ": "o", "ح": "p",
    "ش": "a", "س": "s", "ي": "d", "ب": "f", "ل": "g", "ا": "h", "ت": "j", "ن": "k", "م": "l",
    "ئ": "z", "ء": "x", "ؤ": "c", "ر": "v", "ى": "n", "ة": "m",
    "لا": "b",
    # shifted keys (a scanner sending capital letters)
    "َ": "Q", "ً": "W", "ُ": "E", "ٌ": "R", "لإ": "T", "إ": "Y", "‘": "U", "÷": "I", "×": "O", "؛": "P",
    "ِ": "A", "ٍ": "S", "]": "D", "[": "F", "لأ": "G", "أ": "H", "ـ": "J", "،": "K", "/": "L",
    "ْ": "X", "{": "C", "}": "V", "لآ": "B", "آ": "N", "’": "M",
}
_PATTERN = re.compile("|".join(sorted(map(re.escape, _PAIRS), key=len, reverse=True)))
_ARABIC = re.compile("[؀-ۿ]")


def has_arabic(text):
    return bool(_ARABIC.search(text or ""))


def to_latin_keys(text):
    """'قم01116' (Arabic layout) -> 'rm01116'; text without Arabic letters is returned unchanged."""
    text = text or ""
    if not has_arabic(text):
        return text
    return _PATTERN.sub(lambda m: _PAIRS[m.group(0)], text)
