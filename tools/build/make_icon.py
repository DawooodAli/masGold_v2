"""Draws the masGold program icon (a gold ring with a stone on a dark rounded tile) with Qt and writes tools/build/masgold.ico (PNG-compressed, 16..256 px).

usage:  python -m tools.build.make_icon [ring|medallion|bars]        (QT_QPA_PLATFORM=offscreen is set by the script)
The icon is a build asset only: the .spec file reads the resulting .ico. No Pillow needed."""
import os
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase, QGuiApplication, QImage, QLinearGradient, QPainter, QPen, QPolygonF, QRadialGradient

SIZES = (16, 24, 32, 48, 64, 128, 256)
TARGET = Path(__file__).with_name("masgold.ico")
VARIANTS = ("ring", "medallion", "bars", "gem", "word")


def _tile(p, s):
    tile = QLinearGradient(0, 0, 0, s)
    tile.setColorAt(0, QColor("#23262F"))
    tile.setColorAt(1, QColor("#0D0E12"))
    p.setBrush(QBrush(tile))
    p.setPen(QPen(QColor("#B8862B"), max(1.0, s * 0.018)))
    inset = s * 0.03
    p.drawRoundedRect(QRectF(inset, inset, s - 2 * inset, s - 2 * inset), s * 0.22, s * 0.22)


def _font():
    for folder in (Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts", Path("C:/Windows/Fonts")):
        font = folder / "Cairo.ttf"
        if font.exists():
            ident = QFontDatabase.addApplicationFont(str(font))
            families = QFontDatabase.applicationFontFamilies(ident)
            if families:
                return families[0]
    return "Arial"


def draw_medallion(size):
    """A gold coin with the dark Arabic letter «م» (مجوهرات ماس) and a small stone above it."""
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
    s = float(size)
    _tile(p, s)
    coin = QRadialGradient(QPointF(s * 0.40, s * 0.36), s * 0.55)
    coin.setColorAt(0, QColor("#FFE9A6"))
    coin.setColorAt(0.55, QColor("#D9A441"))
    coin.setColorAt(1, QColor("#9C6A17"))
    p.setBrush(QBrush(coin))
    p.setPen(QPen(QColor("#F8DE8E"), max(1.0, s * 0.02)))
    centre, radius = QPointF(s * 0.5, s * 0.52), s * 0.36
    p.drawEllipse(centre, radius, radius)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(120, 78, 12, 200), max(1.0, s * 0.014)))
    p.drawEllipse(centre, radius * 0.82, radius * 0.82)
    font = QFont(_font())
    font.setBold(True)
    font.setPixelSize(int(s * 0.46))
    p.setFont(font)
    p.setPen(QColor("#2A1B05"))
    p.drawText(QRectF(s * 0.14, s * 0.2, s * 0.72, s * 0.62), Qt.AlignCenter, "م")
    p.end()
    return image


def draw_bars(size):
    """Three stacked gold bars (ingots) with a stone on top."""
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    s = float(size)
    _tile(p, s)

    def bar(cx, cy, w, h):
        top = QPolygonF([QPointF(cx - w * 0.36, cy - h / 2), QPointF(cx + w * 0.36, cy - h / 2), QPointF(cx + w / 2, cy + h / 2), QPointF(cx - w / 2, cy + h / 2)])
        grad = QLinearGradient(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        grad.setColorAt(0, QColor("#FBE49A"))
        grad.setColorAt(0.5, QColor("#DCA73F"))
        grad.setColorAt(1, QColor("#A8751F"))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor("#F8DE8E"), max(1.0, s * 0.012)))
        p.drawPolygon(top)
    bar(s * 0.31, s * 0.80, s * 0.40, s * 0.15)
    bar(s * 0.69, s * 0.80, s * 0.40, s * 0.15)
    bar(s * 0.50, s * 0.62, s * 0.40, s * 0.15)
    top, girdle, tip = s * 0.13, s * 0.24, s * 0.40
    left, right, l2, r2 = s * 0.35, s * 0.65, s * 0.43, s * 0.57
    stone = QLinearGradient(left, top, right, tip)
    stone.setColorAt(0, QColor("#F4FBFF"))
    stone.setColorAt(0.55, QColor("#A9DCF2"))
    stone.setColorAt(1, QColor("#5FA9CC"))
    p.setBrush(QBrush(stone))
    p.setPen(QPen(QColor("#E9F6FC"), max(1.0, s * 0.012)))
    p.drawPolygon(QPolygonF([QPointF(l2, top), QPointF(r2, top), QPointF(right, girdle), QPointF(s * 0.5, tip), QPointF(left, girdle)]))
    p.end()
    return image


def draw_gem(size):
    """One big faceted gold diamond (ماس = diamond), flat and readable even at 16 px."""
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    s = float(size)
    _tile(p, s)
    pt = lambda x, y: QPointF(s * x, s * y)
    # girdle line at y=0.40; crown above, pavilion below
    crown = [(0.24, 0.40), (0.36, 0.22), (0.64, 0.22), (0.76, 0.40)]
    shades = {"top": "#FFF0B8", "l": "#EBC25E", "r": "#C98F2B", "mid": "#F6D87F", "p_l": "#D9A441", "p_m": "#E8BB52", "p_r": "#A8751F"}

    def poly(points, color):
        p.setBrush(QColor(color))
        p.setPen(QPen(QColor("#2A1B05"), max(1.0, s * 0.012)))
        p.drawPolygon(QPolygonF([pt(x, y) for x, y in points]))
    poly([(0.36, 0.22), (0.64, 0.22), (0.58, 0.40), (0.42, 0.40)], shades["top"])
    poly([(0.24, 0.40), (0.36, 0.22), (0.42, 0.40)], shades["l"])
    poly([(0.76, 0.40), (0.64, 0.22), (0.58, 0.40)], shades["r"])
    poly([(0.24, 0.40), (0.42, 0.40), (0.5, 0.80)], shades["p_l"])
    poly([(0.42, 0.40), (0.58, 0.40), (0.5, 0.80)], shades["p_m"])
    poly([(0.58, 0.40), (0.76, 0.40), (0.5, 0.80)], shades["p_r"])
    p.end()
    return image


def draw_word(size):
    """The shop name «ماس» in gold on the dark tile."""
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
    s = float(size)
    _tile(p, s)
    font = QFont(_font())
    font.setBold(True)
    font.setPixelSize(int(s * 0.34))
    p.setFont(font)
    gold = QLinearGradient(0, s * 0.25, 0, s * 0.8)
    gold.setColorAt(0, QColor("#FBE49A"))
    gold.setColorAt(1, QColor("#C48A25"))
    p.setPen(QPen(QBrush(gold), 1))
    p.drawText(QRectF(0, s * 0.18, s, s * 0.62), Qt.AlignCenter, "ماس")
    p.setPen(QPen(QColor("#B8862B"), max(1.0, s * 0.012)))
    p.drawLine(QPointF(s * 0.24, s * 0.80), QPointF(s * 0.76, s * 0.80))
    p.end()
    return image


def draw(size, variant="ring"):
    if variant == "gem":
        return draw_gem(size)
    if variant == "word":
        return draw_word(size)
    if variant == "medallion":
        return draw_medallion(size)
    if variant == "bars":
        return draw_bars(size)
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    s = float(size)
    # dark tile with a thin gold edge
    tile = QLinearGradient(0, 0, 0, s)
    tile.setColorAt(0, QColor("#23262F"))
    tile.setColorAt(1, QColor("#0D0E12"))
    p.setBrush(QBrush(tile))
    p.setPen(QPen(QColor("#B8862B"), max(1.0, s * 0.018)))
    inset = s * 0.03
    p.drawRoundedRect(QRectF(inset, inset, s - 2 * inset, s - 2 * inset), s * 0.22, s * 0.22)
    # the ring
    gold = QLinearGradient(0, s * 0.35, s, s * 0.95)
    gold.setColorAt(0, QColor("#F8DE8E"))
    gold.setColorAt(0.5, QColor("#D9A441"))
    gold.setColorAt(1, QColor("#A8751F"))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QBrush(gold), s * 0.085))
    radius = s * 0.255
    centre = QPointF(s * 0.5, s * 0.63)
    p.drawEllipse(centre, radius, radius)
    # the stone: a cut diamond sitting on the ring
    top, girdle, tip = s * 0.14, s * 0.27, s * 0.40
    left, right, l2, r2 = s * 0.33, s * 0.67, s * 0.42, s * 0.58
    outline = QPolygonF([QPointF(l2, top), QPointF(r2, top), QPointF(right, girdle), QPointF(s * 0.5, tip), QPointF(left, girdle)])
    stone = QLinearGradient(left, top, right, tip)
    stone.setColorAt(0, QColor("#F4FBFF"))
    stone.setColorAt(0.55, QColor("#A9DCF2"))
    stone.setColorAt(1, QColor("#5FA9CC"))
    p.setBrush(QBrush(stone))
    p.setPen(QPen(QColor("#E9F6FC"), max(1.0, s * 0.012)))
    p.drawPolygon(outline)
    if size >= 32:                                                                  # facets only where they can be seen
        p.setPen(QPen(QColor(255, 255, 255, 170), max(1.0, s * 0.01)))
        p.drawLine(QPointF(left, girdle), QPointF(right, girdle))
        p.drawLine(QPointF(l2, top), QPointF(s * 0.5, girdle))
        p.drawLine(QPointF(r2, top), QPointF(s * 0.5, girdle))
        p.drawLine(QPointF(s * 0.5, girdle), QPointF(s * 0.5, tip))
    p.end()
    return image


def png_bytes(image):
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def build(variant="ring"):
    _app = QGuiApplication.instance() or QGuiApplication([])
    pngs = [(size, png_bytes(draw(size, variant))) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, payload = b"", b""
    for size, png in pngs:
        entries += struct.pack("<BBBBHHII", 0 if size >= 256 else size, 0 if size >= 256 else size, 0, 0, 1, 32, len(png), offset + len(payload))
        payload += png
    TARGET.write_bytes(header + entries + payload)
    return TARGET


if __name__ == "__main__":
    import sys
    choice = sys.argv[1] if len(sys.argv) > 1 else "ring"
    if choice not in VARIANTS:
        raise SystemExit("usage: python -m tools.build.make_icon [" + "|".join(VARIANTS) + "]")
    print("wrote", build(choice), TARGET.stat().st_size, "bytes")
