"""The barcode label sheet drawn as ONE one-bit picture (owner 2026-11-13: the approved sheet, «مجوهرات ماس» in Cairo).

The Zebra printer has no Arabic shaping and only fixed fonts, so the whole 641 x 310 dot sheet (two tags, each with a DATA face and a LOGO face either side of the fold) is painted here with Qt and
sent as one ^GFA graphic (zlib + base64 + CRC, the same ":Z64:" form the old label already used). The approved layout, in dots (203 dpi):

* data face (160 x 102): weight (left), factory (middle) and karat (right) in the first row; a Code 128 barcode with 1-dot bars, centred; the number and the ring size under it; every element
  inside a 10-dot margin so a feed error of about a millimetre never clips anything;
* logo face (160 x 104): the old AD mark (unchanged bitmap) above «مجوهرات ماس» in Cairo, centred; the category letter in a corner at a size that can be read.

Nothing here touches the database or the printer. Qt is imported lazily like the rest of the print code."""
import base64
import os
import zlib

from services.bill_overlay import _CODE128, _CODE128_STOP

SHEET_W, SHEET_H = 641, 310
FACE_W = 160
LEFT_X, RIGHT_X = 4, 481                      # the left edge of each tag's faces on the sheet
DATA_Y, DATA_H = 104, 102                      # both tags' data faces
LOGO_FACES = {"left": (LEFT_X, 206, FACE_W, 104), "right": (RIGHT_X, 0, FACE_W, 104)}   # the left tag's logo is below its data face, the right tag's above
MARGIN = 10
BAR_TOP, BAR_H = 27, 46                        # inside the data face
MAX_BARCODE_DOTS = FACE_W - 2 * 4              # the symbol must keep a white edge inside its face
WORD = "مجوهرات ماس"
THRESHOLD = 150                                # grey level below which a painted pixel becomes a black dot

# The old AD mark, cropped from the bitmap the printer has always been sent (96 x 48 dots, 12 bytes a row), zlib + base64.
MARK_W, MARK_H, MARK_ROW_BYTES = 96, 48, 12
MARK_Z = ("eNpt0ssNwyAQBNC1fPCRElwKpZEO0hIdpISkg3DMwWIyO4CNlFjGeljWflibJZgF4Ga8ug+5ysg0ustkfrT4w10nM9LlbCs+3a/JhS6nt9OfZpVy8NVLDpM3Jhte6V0Fev7LJntHMRtgcTgNJznBd/hjRlqaUWcz8/pjtktvw+ygme3SAV7D5Er"
          "zDh628uVshcrb5KDgzVFW6ifPaudyo7l0F3dCO/PI1Z2j9m1GUfu7m5UOH+7bjofGgtPeAUzmqKERvPVrfAE6PEgt")

_FONT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", "fonts", "Cairo.ttf")
_family_cache = {}


class LabelError(ValueError):
    pass


# ------------------------------------------------------------------ Code 128
START_B, START_C, CODE_B = 104, 105, 100


def code128_codes(value):
    """Code 128 symbol values without the check digit: digits in pairs (subset C, an odd last digit through a switch to B), anything else in subset B."""
    text = str(value)
    if not text or any(not 32 <= ord(ch) <= 126 for ch in text):
        raise LabelError("الباركود لا يمكن ترميزه (Code 128).")
    if text.isdigit() and len(text) >= 2:
        codes = [START_C] + [int(text[i:i + 2]) for i in range(0, len(text) - 1, 2)]
        if len(text) % 2:
            codes += [CODE_B, ord(text[-1]) - 32]
        return codes
    return [START_B] + [ord(ch) - 32 for ch in text]


def code128_widths(value):
    """Bar / space widths in modules, starting with a bar (check digit and stop included)."""
    codes = code128_codes(value)
    checksum = (codes[0] + sum(index * code for index, code in enumerate(codes[1:], start=1))) % 103
    return [int(w) for w in "".join(_CODE128[code] for code in codes + [checksum]) + _CODE128_STOP]


# ------------------------------------------------------------------ Z64 graphic
def crc16(data):
    """CRC-16 / CCITT (XMODEM) over the base64 text: the check value ZPL expects after ':Z64:...:' (verified against the strings of the old label)."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def gfa_command(rows, row_bytes):
    """^FO0,0^GFA with the packed rows (1 = black), compressed."""
    raw = bytes(rows)
    encoded = base64.b64encode(zlib.compress(raw)).decode("ascii")
    return f"^FO0,0^GFA,{len(raw)},{len(raw)},{row_bytes},:Z64:{encoded}:{crc16(encoded.encode('ascii')):04X}^FS"


# ------------------------------------------------------------------ painting
def _family():
    from PySide6.QtGui import QFontDatabase, QGuiApplication
    if QGuiApplication.instance() is None:                       # no font database yet: do not cache a fallback
        return "Cairo"
    if "family" not in _family_cache:
        loaded = QFontDatabase.addApplicationFont(_FONT_PATH) if os.path.exists(_FONT_PATH) else -1
        families = QFontDatabase.applicationFontFamilies(loaded) if loaded >= 0 else []
        _family_cache["family"] = families[0] if families else ("Cairo" if "Cairo" in QFontDatabase.families() else "Segoe UI")
    return _family_cache["family"]


def _font(px, heavy=False):
    from PySide6.QtGui import QFont
    font = QFont(_family())
    font.setPixelSize(px)
    font.setWeight(QFont.ExtraBold if heavy else QFont.Bold)
    return font


def _mark_image():
    from PySide6.QtGui import QColor, QImage
    raw = zlib.decompress(base64.b64decode(MARK_Z))
    image = QImage(MARK_W, MARK_H, QImage.Format_RGB32)
    image.fill(QColor(255, 255, 255))
    for y in range(MARK_H):
        for x in range(MARK_W):
            if raw[y * MARK_ROW_BYTES + x // 8] & (0x80 >> (x % 8)):
                image.setPixelColor(x, y, QColor(0, 0, 0))
    return image


class _Painter:
    def __init__(self):
        from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter
        self.QFontMetrics = QFontMetrics
        self.black, self.white = QColor(0, 0, 0), QColor(255, 255, 255)
        self.image = QImage(SHEET_W, SHEET_H, QImage.Format_RGB32)
        self.image.fill(self.white)
        self.p = QPainter(self.image)
        self.p.setRenderHint(QPainter.Antialiasing, True)
        self.p.setRenderHint(QPainter.TextAntialiasing, True)

    def width(self, text, px, heavy=False):
        return self.QFontMetrics(_font(px, heavy)).horizontalAdvance(text)

    def fit(self, text, px, room, minimum=11, heavy=False):
        while px > minimum and self.width(text, px, heavy) > room:
            px -= 1
        return px

    def text(self, text, px, baseline, *, left=None, right=None, center=None, colour=None, heavy=False):
        width = self.width(text, px, heavy)
        x = left if left is not None else right - width if right is not None else center - width // 2
        self.p.setFont(_font(px, heavy))
        self.p.setPen(colour or self.black)
        self.p.drawText(x, baseline, text)
        return width

    def finish(self):
        if self.p.isActive():                                     # always end the painter before its picture goes away (a refused label raises in the middle of painting)
            self.p.end()


def _data_face(canvas, left, fields):
    """One data face. `fields`: barcode, weight, size, karat, factory (strings)."""
    p = canvas.p
    inner_l, inner_r = left + MARGIN, left + FACE_W - MARGIN
    base1 = DATA_Y + 21
    weight_w = canvas.text(fields["weight"], 24, base1, left=inner_l)
    karat_text = f"{fields['karat']}K" if fields["karat"] else ""
    karat_w = canvas.text(karat_text, 24, base1, right=inner_r) if karat_text else 0
    factory = fields["factory"]
    if factory:
        room = (inner_r - karat_w) - (inner_l + weight_w) - 10
        px = canvas.fit(factory, 17, room)
        canvas.text(factory, px, base1, center=(inner_l + weight_w + inner_r - karat_w) // 2)
    widths = code128_widths(fields["barcode"])
    total = sum(widths)
    if total > MAX_BARCODE_DOTS:
        raise LabelError(f"الباركود {fields['barcode']} طويل ولا يتسع في اللاصق.")
    x = left + (FACE_W - total) // 2
    p.setRenderHint(p.RenderHint.Antialiasing, False)
    for index, width in enumerate(widths):
        if index % 2 == 0:
            p.fillRect(x, DATA_Y + BAR_TOP, width, BAR_H, canvas.black)
        x += width
    p.setRenderHint(p.RenderHint.Antialiasing, True)
    base3 = DATA_Y + BAR_TOP + BAR_H + 5 + 15
    size = str(fields["size"])
    size_w = canvas.width(size, 21)
    number_px = canvas.fit(fields["barcode"], 21, (inner_r - inner_l) - size_w - 10, minimum=14)
    canvas.text(fields["barcode"], number_px, base3, left=inner_l)
    canvas.text(size, 21, base3, right=inner_r)


def _logo_face(canvas, side, category, mark):
    x, y, w, h = LOGO_FACES[side]
    center = x + w // 2
    px = canvas.fit(WORD, 21, w - 20, minimum=12, heavy=True)
    metrics = canvas.QFontMetrics(_font(px, True))
    tight = metrics.tightBoundingRect(WORD)
    gap = 8
    top = y + (h - (MARK_H + gap + tight.height())) // 2
    canvas.p.drawImage(center - MARK_W // 2, top, mark)
    canvas.text(WORD, px, top + MARK_H + gap - tight.top(), center=center, heavy=True)
    if category:                                                  # the category letter, in the corner away from the mark
        if side == "left":
            canvas.text(category, 18, y + 22, right=x + w - 8)
        else:
            canvas.text(category, 18, y + 24, left=x + 8)


def render_sheet(left_fields, right_fields=None):
    """The 641 x 310 sheet as a QImage (greyscale painting; `sheet_rows` thresholds it into dots)."""
    canvas = _Painter()
    try:
        mark = _mark_image()
        _data_face(canvas, LEFT_X, left_fields)
        _logo_face(canvas, "left", left_fields.get("category", ""), mark)
        if right_fields and right_fields.get("barcode"):
            _data_face(canvas, RIGHT_X, right_fields)
            _logo_face(canvas, "right", right_fields.get("category", ""), mark)
    finally:
        canvas.finish()
    return canvas.image


def sheet_rows(image):
    """(packed bytes, bytes per row): black dots = 1, most significant bit first."""
    from PySide6.QtGui import QImage
    grey = image.convertToFormat(QImage.Format_Grayscale8)
    stride, width = grey.bytesPerLine(), grey.width()
    data = bytes(grey.constBits())
    row_bytes = (width + 7) // 8
    out = bytearray()
    for y in range(grey.height()):
        line = data[y * stride:y * stride + width]
        value = 0
        for x, level in enumerate(line):
            value = (value << 1) | (1 if level < THRESHOLD else 0)
            if x % 8 == 7:
                out.append(value)
                value = 0
        if width % 8:
            out.append(value << (8 - width % 8))
    return bytes(out), row_bytes


def label_graphic(left_fields, right_fields=None):
    """The ^GFA command that prints the whole sheet."""
    rows, row_bytes = sheet_rows(render_sheet(left_fields, right_fields))
    return gfa_command(rows, row_bytes)
