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
RIGHT_DATA_W, RIGHT_DATA_SHIFT = 146, 6     # the right tag's data face is narrower on the real stock (its right edge was cut in the photo)
LEFT_DATA_W, LEFT_DATA_SHIFT = 148, -6      # the left tag's content sat against its right edge in the print: a narrower face, a little further left
LEFT_LOGO_SHIFT = -16                       # the left tag's logo was a little right of its face centre in the second print
RIGHT_DY = 6                                # the right tag's faces sit a little lower on the real stock (free space below the data face)
RIGHT_LOGO_SHIFT = -2                       # its logo face sits further right (the word lost its first letters)
BAR_TOP, BAR_H = 27, 46                        # inside the data face
MAX_BARCODE_DOTS = FACE_W - 2 * 4              # the symbol must keep a white edge inside its face
WORD = "مجوهرات ماس"
THRESHOLD = 150                                # grey level below which a painted pixel becomes a black dot

# The old AD mark, cropped from the bitmap the printer has always been sent (96 x 48 dots, 12 bytes a row), zlib + base64.
MARK_W, MARK_H, MARK_ROW_BYTES = 94, 44, 12
MARK_Z = "eNpd0cENwyAMBVBHOeTICIzCaETqYKWbtBvkyAHhfn+HlDSKyEPBBmMRbSJJtQoeG2EFl+Ey+RBZbTDXyd38diN4u7z/uZwuEi6/b45Yx6Mg93CiSXwr5u5wOSM33BmJszTJN2O2KF64YdXq1pb05o1Gudh5djh9hOHVXCLzw6iMqSquARUzVd2GA41lcGSgO9OL+YHag+pHq99DVH2dxi5a7Hh2h7Mx7tktdPdemFP3HmX+8979XMRioj7ZLouh0dIvpeQkCA=="   # the owner's AD logo (black version, letters only), 1-bit
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
        self.native = None                     # a list = the data-face text is left to the printer's own font (see `ztext`)
        self.p = QPainter(self.image)
        self.p.setRenderHint(QPainter.Antialiasing, True)
        self.p.setRenderHint(QPainter.TextAntialiasing, True)

    def width(self, text, px, heavy=False):
        return self.QFontMetrics(_font(px, heavy)).horizontalAdvance(text)

    def fit(self, text, px, room, minimum=11, heavy=False):
        while px > minimum and self.width(text, px, heavy) > room:
            px -= 1
        return px

    def text(self, text, px, baseline, *, left=None, right=None, center=None, colour=None, heavy=False, rtl=False):
        width = self.width(text, px, heavy)
        x = left if left is not None else right - width if right is not None else center - width // 2
        self.p.setFont(_font(px, heavy))
        self.p.setPen(colour or self.black)
        from PySide6.QtCore import Qt
        self.p.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)   # Arabic words read right to left
        self.p.drawText(x, baseline, text)
        return width

    def ztext(self, text, base, *, left=None, right=None, center=None, height=17, cell=10, room=None, px=20):
        """Data-face text. With `native` on it becomes a ^FT / ^ACN field in the printer's own font C (the look of the
        old label, cell `cell` dots per character); otherwise it is painted with Cairo. Returns the width in dots."""
        if self.native is None:
            return self.text(text, px, base, left=left, right=right, center=center)
        if room is not None and text and cell * len(text) > room:
            cell = max(6, room // len(text))
        width = cell * len(text)
        x = left if left is not None else right - width if right is not None else center - width // 2
        self.native.append(f"^FT{int(x)},{int(base)}^ACN,{height},{cell}^FD{text}^FS")
        return width

    def finish(self):
        if self.p.isActive():                                     # always end the painter before its picture goes away (a refused label raises in the middle of painting)
            self.p.end()


RIGHT_PAD = 10                               # the narrower right face keeps a slimmer inset so the row does not have to shrink
DATA_PAD = 14                                 # equal inset on both sides so the rows sit centred in the face


def _row_px(canvas, fields, width, pad=DATA_PAD, top=20, minimum=16):
    """The biggest size at which weight, factory and karat all fit side by side in a face of this width."""
    karat = f"{fields['karat']}K" if fields["karat"] else ""
    for px in range(top, minimum - 1, -1):
        used = sum(canvas.width(t, px) for t in (fields["weight"], fields["factory"], karat))
        gaps = 6 * (2 if fields["factory"] else 1) + 6
        if used + gaps <= width - 2 * pad:
            return px
    return minimum


def _data_face(canvas, left, fields, width=FACE_W, side="left", dy=0, px=20, pad=DATA_PAD):
    """One data face. `fields`: barcode, weight, size, karat, factory (strings)."""
    p = canvas.p
    inner_l, inner_r = left + pad, left + width - pad
    base1 = (DATA_Y + dy) + 21
    weight_w = canvas.ztext(fields["weight"], base1, left=inner_l, px=px)
    karat_text = f"{fields['karat']}K" if fields["karat"] else ""
    karat_w = canvas.ztext(karat_text, base1, right=inner_r, px=px) if karat_text else 0
    factory = fields["factory"]
    if factory:
        room = (inner_r - karat_w) - (inner_l + weight_w) - 10
        if canvas.native is None:
            fpx = canvas.fit(factory, px, room)
            canvas.text(factory, fpx, base1, center=(inner_l + weight_w + inner_r - karat_w) // 2)
        else:
            canvas.ztext(factory, base1, center=(inner_l + weight_w + inner_r - karat_w) // 2, room=room)
    widths = code128_widths(fields["barcode"])
    total = sum(widths)
    if total > width - 8:
        raise LabelError(f"الباركود {fields['barcode']} طويل ولا يتسع في اللاصق.")
    x = left + (width - total) // 2
    p.setRenderHint(p.RenderHint.Antialiasing, False)
    for index, w in enumerate(widths):
        if index % 2 == 0:
            p.fillRect(x, (DATA_Y + dy) + BAR_TOP, w, BAR_H, canvas.black)
        x += w
    p.setRenderHint(p.RenderHint.Antialiasing, True)
    base3 = (DATA_Y + dy) + BAR_TOP + BAR_H + 5 + 15
    size = str(fields["size"])
    size_right = inner_r - (canvas.width("K", px) if karat_text else 0)   # both tags: the size sits under the two karat digits (the K hangs over the edge)
    if canvas.native is None:
        size_w = canvas.width(size, 18)
        number_px = canvas.fit(fields["barcode"], 18, (inner_r - inner_l) - size_w - 10, minimum=14)
        canvas.text(fields["barcode"], number_px, base3, left=inner_l)
        canvas.text(size, 18, base3, right=size_right)
    else:
        size_w = canvas.ztext(size, base3, right=size_right)
        canvas.ztext(fields["barcode"], base3, left=inner_l, height=18, room=(inner_r - inner_l) - size_w - 10)


def _logo_face(canvas, side, category, mark):
    x, y, w, h = LOGO_FACES[side]
    center = x + w // 2 + (RIGHT_LOGO_SHIFT if side == "right" else LEFT_LOGO_SHIFT)
    px = canvas.fit(WORD, 16, w - 48, minimum=12, heavy=True)      # the name is painted as text at its own size, not downscaled from a picture
    tight = canvas.QFontMetrics(_font(px, True)).tightBoundingRect(WORD)
    gap = 9
    top = y + (h - (MARK_H + gap + tight.height())) // 2 + (RIGHT_DY if side == "right" else 0)
    canvas.p.drawImage(center - MARK_W // 2, top, mark)
    word_center = center - (8 if side == "right" else 0)             # the right tag's name sits a little further left (its right edge was cut in the print)
    canvas.text(WORD, px, top + MARK_H + gap - tight.top(), center=word_center, heavy=True, rtl=True)
    if category:                                                  # the category letter, in the corner away from the logo
        if side == "left":
            canvas.text(category, 18, top + MARK_H // 2 + 7, right=x + w - 20)
        else:
            canvas.text(category, 18, top + 16, left=x + 8)      # the right tag has no room beside the logo (its edge is cut): top corner, left of the A's slant


def render_sheet(left_fields, right_fields=None, native=None):
    """The 641 x 310 sheet as a QImage (greyscale painting; `sheet_rows` thresholds it into dots).
    `native`: a list that collects the data-face text as ZPL fields of the printer's own font instead of painting it."""
    canvas = _Painter()
    canvas.native = native
    try:
        mark = _mark_image()
        right_ok = bool(right_fields and right_fields.get("barcode"))
        row = min(_row_px(canvas, left_fields, LEFT_DATA_W), _row_px(canvas, right_fields, RIGHT_DATA_W, pad=RIGHT_PAD) if right_ok else 20)   # one size on both tags, as big as the narrower face allows
        _data_face(canvas, LEFT_X + LEFT_DATA_SHIFT, left_fields, LEFT_DATA_W, side="left", px=row)
        _logo_face(canvas, "left", left_fields.get("category", ""), mark)
        if right_ok:
            _data_face(canvas, RIGHT_X + RIGHT_DATA_SHIFT, right_fields, RIGHT_DATA_W, side="right", px=row, pad=RIGHT_PAD)
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
    """The graphic of the barcodes and logos plus the data text in the printer's own font C (the old label's look)."""
    rows, row_bytes = sheet_rows(render_sheet(left_fields, right_fields))      # all text painted in Cairo: any size, no blocky bitmap font
    return gfa_command(rows, row_bytes)
