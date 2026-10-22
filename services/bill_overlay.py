"""The A5 bill as a black overlay on the shop's PREPRINTED stationery.

The paper already carries the logos, the gold separator lines, the watermark, the footer
(address, phones, social, conditions) and the QR code. This module prints ONLY the variable
black data the old program printed: the header (customer • barcode/number • date/time), the
sale-items grid and the money box. It never draws a logo, a watermark or the footer.

Geometry source — the old masGold prototype (`masGold/modules/bill_printer.py`): its reportlab
document was regenerated with 10 rows and measured (every value below in the `PROTO_*`
constants is that PDF's millimetre coordinate on the 210 x 148 page). The prototype then printed
through SumatraPDF, whose default "shrink to printable area" scaled the whole page down to the
Brother's printable area (4.23 mm hardware margin each side) and centred it: that is the
PHYSICAL result on the old invoices. `BillCalibration` reproduces it — physical = origin +
scale * prototype — and adds small X/Y offsets for per-printer calibration.

Pipeline: build_overlay(invoice) -> BillOverlay (pure data, no Qt)
          compose(overlay, calibration) -> [Op] in PHYSICAL paper millimetres (pure, testable)
          validate(ops, calibration) -> refuses anything crossing the preprinted boundaries
          paint(painter, ops, dots_per_mm) -> the only Qt-dependent step.
"""
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
import os

# ---------------------------------------------------------------- paper
PAPER_WIDTH_MM, PAPER_HEIGHT_MM = 210.0, 148.0          # A5 landscape — unchanged from the prototype
MAX_SALE_ITEMS = 10                                       # the prototype's one-sheet rule
MAX_SALE_ITEMS_TEXT = "الحد الأقصى للفاتورة 10 أصناف بيع"
SALE_LINE_TYPES = ("SELL_INVENTORY", "SELL_UNIDENTIFIED", "SELL_OLD_GOLD")

# ---------------------------------------------------------------- prototype geometry (mm on its PDF page)
# Fonts: Segoe UI / Segoe UI Bold (segoeui.ttf / segoeuib.ttf) for Arabic and English alike.
FONT_FAMILY = "Segoe UI"
HEADER_PT, TABLE_HEADER_PT, TABLE_BODY_PT, MONEY_PT = 10.0, 11.0, 10.0, 11.0
GRID_PT, GRID_GRAY = 1.0, 128                             # reportlab colors.grey, 1 pt

# Header: a 3-cell table centred on the page (1.7 / 1.8 / 1.7 in).
PROTO_HEADER_CELLS = {"left": (39.05, 82.23), "middle": (82.23, 127.95), "right": (127.95, 171.13)}
PROTO_HEADER_ROW1 = (8.2, 13.3)                           # name | barcode | date
PROTO_HEADER_ROW2 = (16.8, 21.9)                          # place | number | time
PROTO_BARCODE_Y, PROTO_BARCODE_H = 8.2, 5.08              # barHeight 0.2 in
PROTO_BAR_MODULE_MM = 0.254                               # barWidth 0.01 in

# Sale grid: 7 columns, 6.1 in wide, centred; reportlab lays RTL columns left -> right reversed.
PROTO_TABLE_X = 27.53
PROTO_TABLE_TOP = 30.42
PROTO_TABLE_HEADER_H = 14.11                              # two lines of 11 pt bold + 8 pt padding
PROTO_TABLE_ROW_H = 6.35                                  # 10 pt + default 3 pt padding (ROWHEIGHT is ignored)
# (key, Arabic, English, width mm) — physical left to right.
PROTO_COLUMNS = (("notes", "ملاحظات", "Notes", 43.18), ("qty", "العدد", "Qty", 12.70),
                 ("karat", "العيار", "Kerat", 15.24), ("weight", "وزن", "Weight", 20.32),
                 ("brand", "الماركة", "Brand", 25.40), ("category", "الصنف", "Category", 25.40),
                 ("serial", "رقم", "Serial", 12.70))
PROTO_TABLE_W = sum(column[3] for column in PROTO_COLUMNS)  # 154.94

# Money box: Spacer 0.1 in, then one bordered 2-cell row (3.8 in words | 2.3 in label), text right-aligned.
PROTO_MONEY_GAP = 2.54
PROTO_MONEY_WORDS_W = 96.52
PROTO_MONEY_PAD_X = 2.82                                  # 8 pt
PROTO_MONEY_H_ONE_LINE = 11.29                            # 11 pt + 10 pt padding top/bottom
PROTO_MONEY_LINE_H = 4.66                                 # 11 pt * 1.2 leading
PROTO_MONEY_PAD_Y_TWO_LINES = 1.15                        # V2 adaptation: a second line fits in ~the same box with modest vertical padding

# ---------------------------------------------------------------- physical mapping (Sumatra "shrink")
BROTHER_HW_MARGIN_MM = 4.233                              # 12 pt, as the HL-L2360D driver reports
_PRINTABLE_W = PAPER_WIDTH_MM - 2 * BROTHER_HW_MARGIN_MM
_PRINTABLE_H = PAPER_HEIGHT_MM - 2 * BROTHER_HW_MARGIN_MM
PROTOTYPE_SCALE = min(_PRINTABLE_W / PAPER_WIDTH_MM, _PRINTABLE_H / PAPER_HEIGHT_MM)   # ≈ 0.9428
PROTOTYPE_ORIGIN_X = BROTHER_HW_MARGIN_MM + (_PRINTABLE_W - PAPER_WIDTH_MM * PROTOTYPE_SCALE) / 2  # ≈ 6.01
PROTOTYPE_ORIGIN_Y = BROTHER_HW_MARGIN_MM + (_PRINTABLE_H - PAPER_HEIGHT_MM * PROTOTYPE_SCALE) / 2  # ≈ 4.23

# The owner's ruler on the real preprinted stock (2026-09-27), physical mm from the sheet's TOP edge:
# sheet 210 x 145 mm (3 mm shorter than A5); upper gold line 25.2 mm; lower gold line 20.0 mm
# above the bottom edge = 145 - 20 = 125.0 mm. The body between the lines is 99.8 mm.
MEASURED_SHEET_HEIGHT_MM = 145.0
MEASURED_UPPER_GOLD_Y_MM = 25.2
MEASURED_LOWER_GOLD_Y_MM = 125.0
# The old program's PHYSICAL output, measured on the photographed old bill against those two lines:
# its grid starts ~1.7 mm under the upper gold line (~26.9 mm) and its address row sits ~17.4 mm,
# i.e. 6.0 mm higher than the pure A5 + Sumatra-shrink model (32.9 mm / 16.5+6 mm). The whole
# overlay is shifted up by that amount; the first physical print confirms or corrects it.
OLD_BILL_OFFSET_Y_MM = -6.0


@dataclass(frozen=True)
class BillCalibration:
    """Everything a physical calibration may change — nothing else in the layout moves."""
    upper_gold_y_mm: float = MEASURED_UPPER_GOLD_Y_MM
    lower_gold_y_mm: float = MEASURED_LOWER_GOLD_Y_MM
    offset_x_mm: float = 0.0                               # + moves everything right
    offset_y_mm: float = OLD_BILL_OFFSET_Y_MM              # + moves everything down
    scale: float = PROTOTYPE_SCALE
    origin_x_mm: float = PROTOTYPE_ORIGIN_X
    origin_y_mm: float = PROTOTYPE_ORIGIN_Y

    def x(self, proto_mm):
        return self.origin_x_mm + self.offset_x_mm + self.scale * proto_mm

    def y(self, proto_mm):
        return self.origin_y_mm + self.offset_y_mm + self.scale * proto_mm

    def length(self, proto_mm):
        return self.scale * proto_mm

    def pt(self, points):                                  # fonts and line widths shrink with the page
        return self.scale * points


# ---------------------------------------------------------------- content model (pure data)
@dataclass(frozen=True)
class BillRow:
    serial: str
    category: str
    brand: str
    weight: str
    karat: str
    qty: str
    notes: str


@dataclass(frozen=True)
class BillOverlay:
    invoice_number: str
    customer_name: str
    place: str
    date_text: str
    time_text: str
    rows: tuple
    total_label: str               # "إجمالي الفاتورة : 23,000 جنيه"
    total_words: str               # the commercial total in Arabic words
    payment_label: str = ""        # money that physically moved; decides the 2-line box. The incoming
                                   # "المستلم من العميل" wording is NOT printed (owner, 2026-10-05)
    payment_methods: str = ""      # "نقدي : 5,000 • InstaPay - البنك الأهلي : 15,000"
    extra: dict = field(default_factory=dict)


METHOD_TEXT = {"PHYSICAL_CASH": "نقدي", "MOBILE_WALLET": "محفظة", "INSTAPAY": "InstaPay",
               "BANK_TRANSFER": "تحويل بنكي"}
LINE_TYPE_TEXT = {"SELL_INVENTORY": "بيع", "SELL_UNIDENTIFIED": "بيع", "SELL_OLD_GOLD": "بيع من الكسر"}


def amount_text(value):
    """8350 -> '8,350'; 8350.5 -> '8,350.50' (the prototype printed whole pounds)."""
    value = Decimal(str(value))
    return f"{int(value):,}" if value == value.to_integral_value() else f"{value:,.2f}"


def _ltr(text):
    return "⁦" + text + "⁩"                     # a left-to-right isolate inside the RTL line


def short_account_name(account):
    """'البنك الأهلي المصري - فرع القناطر' -> 'الأهلي المصري': the customer needs the bank, not the
    branch; the amount next to it must never be the part that gets cut off."""
    name = (account or "").split(" - ")[0].strip()
    return name[len("البنك "):].strip() if name.startswith("البنك ") else name


def short_method_label(payment, account, opposite, outgoing):
    label = METHOD_TEXT.get(payment["payment_method_code"], payment.get("payment_method_name") or "")
    if account:
        label = f"{label} - {short_account_name(account)}"
    if opposite:
        label = f"{label} ({'مستلم' if outgoing else 'مدفوع'})"
    return label


def build_overlay(invoice):
    """The printed content, only from the persisted invoice (its snapshots and finalize time).

    Customer-account data is INTERNAL: balance_before/after, the deferred amount and its
    direction (على العميل / للعميل) are never read here, so they can never reach the paper.
    Old gold bought from the customer is part of the totals but is not a printed row."""
    sale_lines = [line for line in invoice["lines"] if line["line_type"] in SALE_LINE_TYPES]
    if len(sale_lines) > MAX_SALE_ITEMS:
        raise ValueError(MAX_SALE_ITEMS_TEXT)
    rows = tuple(BillRow(
        serial=str(index + 1),
        category=(line.get("item_name_snapshot") or line.get("category_snapshot")
                  or LINE_TYPE_TEXT.get(line["line_type"], "")),
        brand=line.get("factory_snapshot") or "—",         # (the prototype printed "NA")
        weight=f"{Decimal(str(line['weight_grams'])):.3f}",
        karat=str(line.get("karat_value") or ""),
        qty=str(int(line.get("quantity") or 1)),
        notes=(line.get("notes") or "").strip()) for index, line in enumerate(sale_lines))

    when = invoice.get("finalized_at") or invoice.get("invoice_date")
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when)
        except ValueError:
            when = None
    date_text = when.strftime("%d-%m-%Y") if when else ""
    time_text = when.strftime("%H:%M") if when else ""

    total = Decimal(str(invoice["net_amount"]))
    if invoice["net_direction"] == "SETTLED" or total == 0:
        total_label, total_words = "بدل ذهب بدون نقدية", "صفر جنيها فقط لاغير"
    else:
        total_label = f"إجمالي الفاتورة : {_ltr(amount_text(total))} جنيه"
        total_words = money_in_words(total)

    # Only money that physically moved (invoice_payments are real money only).
    accounts = {row["payment_id"]: row for row in invoice.get("payment_accounts", [])}
    outgoing = invoice["net_direction"] == "SHOP_PAYS_CUSTOMER"
    main = "OUT" if outgoing else "IN"
    moved, methods, structured = Decimal("0"), [], []
    # Only methods that actually moved money: a zero-value payment row is never printed.
    payments = [p for p in invoice.get("payments", []) if Decimal(str(p["amount"])) != 0]
    for payment in payments:
        amount = Decimal(str(payment["amount"]))
        label = METHOD_TEXT.get(payment["payment_method_code"], payment.get("payment_method_name") or "")
        account = (accounts.get(payment["payment_id"]) or {}).get("bank_account_name")
        if account:
            label = f"{label} - {account}"
        if payment["direction"] == main:
            moved += amount
        else:
            label = f"{label} ({'مستلم' if outgoing else 'مدفوع'})"      # never the printed phrase المستلم من العميل
        methods.append(f"{label} : {_ltr(amount_text(amount))}")
        structured.append((short_method_label(payment, account, bool(payment["direction"] != main), outgoing),
                           amount_text(amount)))
    single_full = (len(payments) == 1 and payments[0]["direction"] == main and moved == total)
    payment_label = payment_methods = ""
    if payments and not single_full:
        payment_label = f"{'المدفوع للعميل' if outgoing else 'المستلم من العميل'} : {_ltr(amount_text(moved))} جنيه"
        payment_methods = " • ".join(methods)

    return BillOverlay(
        invoice_number=str(invoice["invoice_number"]),
        customer_name=invoice.get("customer_name_snapshot") or "عميل نقدي",
        place=invoice.get("place_snapshot") or "",
        date_text=date_text, time_text=time_text, rows=rows,
        total_label=total_label, total_words=total_words,
        payment_label=payment_label, payment_methods=payment_methods,
        # The same facts, unformatted, for the Variant C layout (never account data).
        extra={"settled": invoice["net_direction"] == "SETTLED" or total == 0,
               "total": amount_text(total), "moved": amount_text(moved),
               "payment_title": "المدفوع للعميل" if outgoing else "المستلم من العميل", "outgoing": outgoing,
               "methods": tuple(structured), "second_line": bool(payment_label)})


# ---------------------------------------------------------------- Arabic amount in words
# A faithful port of num2words 0.5.14 `num2words(n, lang='ar')` for non-negative integers — the
# exact function the prototype used — so v2 needs no extra dependency and prints the same words.
_ONES = ("", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة", "عشرة",
         "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر", "ستة عشر", "سبعة عشر",
         "ثمانية عشر", "تسعة عشر")
_TENS = ("عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون")
_HUNDREDS = ("", "مائة", "مئتان", "ثلاثمائة", "أربعمائة", "خمسمائة", "ستمائة", "سبعمائة", "ثمانمائة",
             "تسعمائة")
_APPENDED_TWOS = ("مئتا", "ألفا", "مليونا", "مليارا", "تريليونا")
_TWOS = ("مئتان", "ألفان", "مليونان", "ملياران", "تريليونان")
_GROUP = ("مائة", "ألف", "مليون", "مليار", "تريليون")
_APPENDED_GROUP = ("", "ألفاً", "مليوناً", "ملياراً", "تريليوناً")
_PLURAL_GROUPS = ("", "آلاف", "ملايين", "مليارات", "تريليونات")


def _group_words(number, level, whole):
    tens, hundreds = number % 100, number // 100
    text = ""
    if hundreds:
        if tens == 0 and hundreds == 2:
            text = _APPENDED_TWOS[0]
        else:
            text = _HUNDREDS[hundreds]
            if tens:
                text += " و "
    if tens:
        if tens < 20:
            if tens == 2 and hundreds == 0 and level > 0:
                power = len(str(whole)) - 1
                exact_two = whole > 10 and power % 3 == 0 and whole == 2 * 10 ** power
                text = _APPENDED_TWOS[level] if exact_two else _TWOS[level]
            elif tens == 1 and level > 0:
                text += _GROUP[level]
            else:
                text += _ONES[tens]
        else:
            ones = tens % 10
            if ones:
                text += _ONES[ones] + " و "
            text += _TENS[tens // 10 - 2]
    return text


def arabic_cardinal(number):
    """num2words(number, lang='ar') for 0 <= number < 10**15."""
    number = int(number)
    if number < 0 or number >= 10 ** 15:
        raise ValueError("number out of range")
    if number == 0:
        return "صفر"
    result, level, rest = "", 0, number
    while rest > 0:
        part, rest = rest % 1000, rest // 1000
        words = _group_words(part, level, number)
        if words:
            if level > 0:
                if result:
                    result = "و " + result
                if part not in (1, 2):
                    if part % 100 != 1:
                        if 3 <= part <= 10:
                            result = f"{_PLURAL_GROUPS[level]} {result}"
                        elif result:
                            result = f"{_APPENDED_GROUP[level]} {result}"
                        else:
                            result = f"{_GROUP[level]} {result}"
                    else:
                        result = f"{_GROUP[level]} {result}"
            result = f"{words} {result}"
        level += 1
    return result.strip()


def money_in_words(amount):
    """The prototype's wording: words + ' جنيها'; piasters, when present, are added (never dropped)."""
    amount = Decimal(str(amount))
    pounds = int(amount.to_integral_value(rounding=ROUND_DOWN))
    piasters = int(((amount - pounds) * 100).to_integral_value())
    text = f"{arabic_cardinal(pounds)} جنيها"
    if piasters:
        text += f" و {arabic_cardinal(piasters)} قرشا"
    return text


# ---------------------------------------------------------------- Code128 (the prototype's symbology)
_CODE128 = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232")
_CODE128_STOP = "2331112"
_START_B, _START_C = 104, 105


def code128_modules(value):
    """Bar/space module widths for `value`: Code C for an even all-digit string (as reportlab
    chose for the prototype's numeric bill numbers), otherwise Code B."""
    value = str(value)
    if not value or any(not (32 <= ord(ch) <= 126) for ch in value):
        raise ValueError("Code128 needs printable ASCII")
    if value.isdigit() and len(value) % 2 == 0:
        codes = [_START_C] + [int(value[i:i + 2]) for i in range(0, len(value), 2)]
    else:
        codes = [_START_B] + [ord(ch) - 32 for ch in value]
    checksum = (codes[0] + sum(index * code for index, code in enumerate(codes[1:], start=1))) % 103
    pattern = "".join(_CODE128[code] for code in codes + [checksum]) + _CODE128_STOP
    return [int(width) for width in pattern]


# ---------------------------------------------------------------- layout -> physical ops
@dataclass(frozen=True)
class Op:
    """One drawing primitive in PHYSICAL paper millimetres. kind: 'text' | 'line' | 'box' | 'bar'."""
    kind: str
    x: float
    y: float
    w: float
    h: float
    text: str = ""
    size_pt: float = 0.0
    bold: bool = False
    align: str = "center"          # 'center' | 'right'
    region: str = ""               # 'header' | 'table' | 'money'
    width_pt: float = 0.0
    gray: int = 0                  # 0 = black text; lines: the grid shade
    spans: tuple = ()              # ((text, size_pt, bold, gray), ...) laid out right-to-left on one line
    min_pt: float = 0.0            # text only: step down to this size before eliding (never taller rows)


def money_box_height(overlay):
    # Two lines only when the second line is actually drawn (a BUY invoice); a SALE prints no settlement
    # line, so its box is the one-line box and hugs the total (owner, 2026-10-05).
    if (overlay.payment_label or overlay.payment_methods) and overlay.extra.get("outgoing"):
        return 2 * PROTO_MONEY_LINE_H + 2 * PROTO_MONEY_PAD_Y_TWO_LINES
    return PROTO_MONEY_H_ONE_LINE


STYLE_PROTOTYPE, STYLE_VARIANT_C = "prototype", "variant_c"
BILL_STYLES = (STYLE_PROTOTYPE, STYLE_VARIANT_C)
DEFAULT_BILL_STYLE = STYLE_VARIANT_C          # the owner's preferred direction; the prototype stays selectable


def compose(overlay, calibration=None, style=STYLE_PROTOTYPE, typography=None):
    """The physical ops. `prototype` = the measured old bill as-is (the recoverable baseline);
    `variant_c` = the same boxes, rows and heights with the approved typographic hierarchy."""
    if style == STYLE_VARIANT_C:
        return _compose_variant_c(overlay, calibration or BillCalibration(), typography)
    c = calibration or BillCalibration()
    ops = []

    def text(region, x0, y0, x1, y1, value, size, bold=False, align="center"):
        ops.append(Op("text", c.x(x0), c.y(y0), c.length(x1 - x0), c.length(y1 - y0), value,
                      c.pt(size), bold, align, region))

    def line(region, x0, y0, x1, y1):
        ops.append(Op("line", c.x(x0), c.y(y0), c.length(x1 - x0), c.length(y1 - y0), region=region,
                      width_pt=c.pt(GRID_PT), gray=GRID_GRAY))

    def labelled(region, x0, y0, x1, y1, label, value, size, bold, align="center"):
        """«label : value» as THREE independent runs (the word, a standalone colon, the value as a left-to-right isolate), exactly the text the single string had. The printer's text layout
        moved the colon of the single string to the wrong side of the label next to a date / time / amount (owner physical print, 2026-10-23); separate runs cannot be reordered."""
        parts = tuple((txt, c.pt(size), bold, 0) for txt in (label, " : ", value))
        ops.append(Op("text", c.x(x0), c.y(y0), c.length(x1 - x0), c.length(y1 - y0), "".join(p[0] for p in parts), c.pt(size), bold, align, region, spans=parts))

    # Header (row 1: name • barcode • date, row 2: place • number • time) — bold 10 pt, centred.
    cells = PROTO_HEADER_CELLS
    text("header", cells["right"][0], PROTO_HEADER_ROW1[0], cells["right"][1], PROTO_HEADER_ROW1[1],
         f"الاسم : {overlay.customer_name}", HEADER_PT, True)
    text("header", cells["right"][0], PROTO_HEADER_ROW2[0], cells["right"][1], PROTO_HEADER_ROW2[1],
         f"العنوان: {overlay.place}", HEADER_PT, True)
    labelled("header", cells["left"][0], PROTO_HEADER_ROW1[0], cells["left"][1], PROTO_HEADER_ROW1[1],
             LABEL_DATE, _ltr(overlay.date_text), HEADER_PT, True)
    labelled("header", cells["left"][0], PROTO_HEADER_ROW2[0], cells["left"][1], PROTO_HEADER_ROW2[1],
             LABEL_TIME, _ltr(overlay.time_text), HEADER_PT, True)
    text("header", cells["middle"][0], PROTO_HEADER_ROW2[0], cells["middle"][1], PROTO_HEADER_ROW2[1],
         overlay.invoice_number, HEADER_PT, True)
    modules = code128_modules(overlay.invoice_number)
    bar_x = (cells["middle"][0] + cells["middle"][1]) / 2 - sum(modules) * PROTO_BAR_MODULE_MM / 2
    for index, width in enumerate(modules):
        if index % 2 == 0:                                   # even = bar, odd = space
            ops.append(Op("bar", c.x(bar_x), c.y(PROTO_BARCODE_Y), c.length(width * PROTO_BAR_MODULE_MM),
                          c.length(PROTO_BARCODE_H), region="header"))
        bar_x += width * PROTO_BAR_MODULE_MM

    # Sale grid.
    top, left, right = PROTO_TABLE_TOP, PROTO_TABLE_X, PROTO_TABLE_X + PROTO_TABLE_W
    bottom = top + PROTO_TABLE_HEADER_H + PROTO_TABLE_ROW_H * len(overlay.rows)
    row_edges = [top, top + PROTO_TABLE_HEADER_H] + [
        top + PROTO_TABLE_HEADER_H + PROTO_TABLE_ROW_H * (i + 1) for i in range(len(overlay.rows))]
    for y in row_edges:
        line("table", left, y, right, y)
    x = left
    column_edges = [left]
    for column in PROTO_COLUMNS:
        x += column[3]
        column_edges.append(x)
    for x in column_edges:
        line("table", x, top, x, bottom)
    for (key, arabic, english, _), x0, x1 in zip(PROTO_COLUMNS, column_edges, column_edges[1:]):
        text("table", x0, top, x1, top + PROTO_TABLE_HEADER_H, f"{arabic}\n{english}", TABLE_HEADER_PT, True)
        for index, row in enumerate(overlay.rows):
            y0 = top + PROTO_TABLE_HEADER_H + PROTO_TABLE_ROW_H * index
            text("table", x0, y0, x1, y0 + PROTO_TABLE_ROW_H, getattr(row, key), TABLE_BODY_PT)

    # Money box: [words (left, wide) | label (right)], one bordered box, text right-aligned.
    box_top = bottom + PROTO_MONEY_GAP
    box_h = money_box_height(overlay)
    ops.append(Op("box", c.x(left), c.y(box_top), c.length(PROTO_TABLE_W), c.length(box_h), region="money",
                  width_pt=c.pt(GRID_PT), gray=GRID_GRAY))
    split = left + PROTO_MONEY_WORDS_W
    two_lines = bool(overlay.payment_label or overlay.payment_methods) and bool(overlay.extra.get("outgoing"))
    pad_y = PROTO_MONEY_PAD_Y_TWO_LINES if two_lines else (PROTO_MONEY_H_ONE_LINE - PROTO_MONEY_LINE_H) / 2
    # Line 1 is the prototype's row: [amount in words | total label].
    y0 = box_top + pad_y
    y1 = y0 + PROTO_MONEY_LINE_H
    if overlay.extra.get("settled"):
        text("money", split + PROTO_MONEY_PAD_X, y0, right - PROTO_MONEY_PAD_X, y1, overlay.total_label, MONEY_PT,
             align="right")
    else:
        labelled("money", split + PROTO_MONEY_PAD_X, y0, right - PROTO_MONEY_PAD_X, y1, LABEL_TOTAL,
                 _ltr(overlay.extra.get("total", "")) + " جنيه", MONEY_PT, False, align="right")
    text("money", left + PROTO_MONEY_PAD_X, y0, split - PROTO_MONEY_PAD_X, y1, overlay.total_words, MONEY_PT,
         align="right")
    # Line 2 (V2 only, when money moved differently from the total): the actual payment and its
    # methods across the full box width. A SALE invoice prints NO settlement detail at all (owner,
    # 2026-10-05): the second line stays empty; only a BUY invoice (المدفوع للعميل) prints it.
    if two_lines:
        detail = " — ".join(part for part in (overlay.payment_label, overlay.payment_methods) if part)
        text("money", left + PROTO_MONEY_PAD_X, y1, right - PROTO_MONEY_PAD_X, y1 + PROTO_MONEY_LINE_H, detail,
             MONEY_PT, align="right")
    return ops


# ---------------------------------------------------------------- Variant C (approved direction)
# Identical geometry to the prototype: same header cells and rows, same 14.11 mm table header,
# same 6.35 mm rows (10-row capacity), same money-box position. Only typography (and, when the ink
# check requires it, the money box's line height) differs: values stronger than labels, Arabic
# headings dominant over English, a subtle 0.8 pt grey grid, the total amount the strongest money text.
C_GRID_PT, C_GRID_GRAY = 0.8, 100
C_LABEL_GRAY = 70
# "Label : Value" as THREE independent runs laid out right-to-left: the Arabic word, a standalone colon
# run, then the value (dates / times / amounts as left-to-right isolates). No colon is embedded in an
# Arabic string, so bidi reordering can never move it. (Owner physical review 2026-10-02.)
LABEL_NAME, LABEL_PLACE, LABEL_DATE, LABEL_TIME = "الاسم", "العنوان", "التاريخ", "الوقت"
LABEL_TOTAL = "إجمالي الفاتورة"
COLON = " : "                        # non-breaking spaces: the gap is measured, never trimmed


@dataclass(frozen=True)
class CTypography:
    """Every Variant C size in PROTOTYPE points (the physical size is scale × this)."""
    label: float                   # header labels: الاسم / العنوان / التاريخ / الوقت
    name: float                    # the customer's name (the header's strongest text)
    address: float
    date_time: float
    number: float                  # the invoice number under the barcode
    heading_ar: float              # table headings
    heading_en: float
    body: float                    # table rows (6.35 mm rows are fixed)
    notes_min: float               # long notes step down to this, then "…"
    total_label: float
    total: float                   # إجمالي الفاتورة amount — the strongest money text
    paid_label: float
    paid: float                    # المستلم من العميل / المدفوع للعميل amount
    methods: float
    methods_min: float
    words: float
    money_line_h: float            # prototype mm per money-box line
    name_min: float = 0.0          # a very long name steps down to this, then "…"


# Exactly what the first physical calibration sheet (2026-09-27) printed.
C_TYPE_PRINTED_V1 = CTypography(label=10.0, name=10.5, address=10.0, date_time=10.0, number=11.0,
                                heading_ar=11.0, heading_en=8.5, body=10.0, notes_min=9.0,
                                total_label=11.0, total=12.5, paid_label=10.0, paid=10.0, methods=9.5,
                                methods_min=9.5, words=10.0, money_line_h=PROTO_MONEY_LINE_H)
# The owner's review of that sheet: same geometry, larger text. The money line grows from
# 4.66 to 6.0 prototype mm so the 14.5 pt total's Arabic ink stays inside its line (ink-checked).
C_TYPE_ENLARGED = CTypography(label=10.0, name=13.0, address=12.0, date_time=12.0, number=12.5,
                              heading_ar=12.0, heading_en=9.0, body=11.0, notes_min=9.0,
                              total_label=11.5, total=14.5, paid_label=11.0, paid=13.0, methods=10.5,
                              methods_min=9.0, words=11.0, money_line_h=6.0, name_min=11.0)
C_TYPOGRAPHY = C_TYPE_ENLARGED
# The customer name / address may use the unused space between the barcode and the prototype's
# right cell (the barcode ends ≈ 115 mm): 120 .. 171.13 prototype mm, still clear of the right logo.
C_NAME_CELL = (120.0, 171.13)


def _compose_variant_c(overlay, c, t=None):
    t = t or C_TYPOGRAPHY
    ops = []
    extra = overlay.extra or {}

    def spans(region, x0, y0, x1, y1, parts, align="center", min_pt=0.0):
        scaled = tuple((txt, c.pt(pt), bold, gray) for txt, pt, bold, gray in parts if txt)
        ops.append(Op("text", c.x(x0), c.y(y0), c.length(x1 - x0), c.length(y1 - y0),
                      "".join(p[0] for p in scaled), max(p[1] for p in scaled), False, align, region, spans=scaled,
                      min_pt=c.pt(min_pt) if min_pt else 0.0))

    def text(region, x0, y0, x1, y1, value, size, bold=False, gray=0, align="center", min_pt=0.0):
        ops.append(Op("text", c.x(x0), c.y(y0), c.length(x1 - x0), c.length(y1 - y0), value, c.pt(size), bold,
                      align, region, gray=gray, min_pt=c.pt(min_pt) if min_pt else 0.0))

    def line(region, x0, y0, x1, y1):
        ops.append(Op("line", c.x(x0), c.y(y0), c.length(x1 - x0), c.length(y1 - y0), region=region,
                      width_pt=c.pt(C_GRID_PT), gray=C_GRID_GRAY))

    cells, row1, row2 = PROTO_HEADER_CELLS, PROTO_HEADER_ROW1, PROTO_HEADER_ROW2
    spans("header", C_NAME_CELL[0], row1[0], C_NAME_CELL[1], row1[1],
          [(LABEL_NAME, t.label, True, C_LABEL_GRAY), (COLON, t.label, True, C_LABEL_GRAY), (overlay.customer_name, t.name, True, 0)], min_pt=t.name_min)
    spans("header", C_NAME_CELL[0], row2[0], C_NAME_CELL[1], row2[1],
          [(LABEL_PLACE, t.label, True, C_LABEL_GRAY), (COLON, t.label, True, C_LABEL_GRAY), (overlay.place or "—", t.address, False, 0)],
          min_pt=t.label)
    spans("header", cells["left"][0], row1[0], cells["left"][1], row1[1],
          [(LABEL_DATE, t.label, True, C_LABEL_GRAY), (COLON, t.label, True, C_LABEL_GRAY), (_ltr(overlay.date_text), t.date_time, True, 0)])
    spans("header", cells["left"][0], row2[0], cells["left"][1], row2[1],
          [(LABEL_TIME, t.label, True, C_LABEL_GRAY), (COLON, t.label, True, C_LABEL_GRAY), (_ltr(overlay.time_text), t.date_time, True, 0)])
    text("header", cells["middle"][0], row2[0], cells["middle"][1], row2[1], overlay.invoice_number, t.number, True)
    modules = code128_modules(overlay.invoice_number)
    bar_x = (cells["middle"][0] + cells["middle"][1]) / 2 - sum(modules) * PROTO_BAR_MODULE_MM / 2
    for index, width in enumerate(modules):
        if index % 2 == 0:
            ops.append(Op("bar", c.x(bar_x), c.y(PROTO_BARCODE_Y), c.length(width * PROTO_BAR_MODULE_MM),
                          c.length(PROTO_BARCODE_H), region="header"))
        bar_x += width * PROTO_BAR_MODULE_MM

    top, left, right = PROTO_TABLE_TOP, PROTO_TABLE_X, PROTO_TABLE_X + PROTO_TABLE_W
    head, row_h = PROTO_TABLE_HEADER_H, PROTO_TABLE_ROW_H
    bottom = top + head + row_h * len(overlay.rows)            # only the real rows, never empty ones
    for y in [top, top + head] + [top + head + row_h * (i + 1) for i in range(len(overlay.rows))]:
        line("table", left, y, right, y)
    edges, x = [left], left
    for column in PROTO_COLUMNS:
        x += column[3]
        edges.append(x)
    for x in edges:
        line("table", x, top, x, bottom)
    for (key, arabic, english, _), x0, x1 in zip(PROTO_COLUMNS, edges, edges[1:]):
        text("table", x0, top + 1.0, x1, top + head * 0.58, arabic, t.heading_ar, True)
        text("table", x0, top + head * 0.58, x1, top + head - 1.2, english, t.heading_en, gray=90)
        for index, row in enumerate(overlay.rows):
            y0 = top + head + row_h * index
            value = getattr(row, key)
            text("table", x0, y0, x1, y0 + row_h, _ltr(value) if key == "weight" else value, t.body,
                 min_pt=t.notes_min if key == "notes" else 0.0)

    second = bool(extra.get("second_line")) and bool(extra.get("outgoing"))     # a SALE: one-line box
    line_h = t.money_line_h
    box_top = bottom + PROTO_MONEY_GAP
    pad_y = PROTO_MONEY_PAD_Y_TWO_LINES
    box_h = (2 * line_h + 2 * pad_y) if second else max(PROTO_MONEY_H_ONE_LINE, line_h + 2 * pad_y)
    ops.append(Op("box", c.x(left), c.y(box_top), c.length(PROTO_TABLE_W), c.length(box_h), region="money",
                  width_pt=c.pt(C_GRID_PT), gray=C_GRID_GRAY))
    split, pad = left + PROTO_MONEY_WORDS_W, PROTO_MONEY_PAD_X
    y0 = box_top + (pad_y if second else (box_h - line_h) / 2)
    y1 = y0 + line_h
    if extra.get("settled"):
        text("money", split + pad, y0, right - pad, y1, overlay.total_label, t.total_label, True, align="right")
    else:
        spans("money", split + pad, y0, right - pad, y1,
              [(LABEL_TOTAL, t.total_label, False, 40), (COLON, t.total_label, False, 40),
               (_ltr(extra.get("total", "")) + " ج", t.total, False, 0)], align="right", min_pt=t.paid)
    text("money", left + pad, y0, split - pad, y1, overlay.total_words, t.words, True, gray=45, align="right",
         min_pt=t.methods_min)
    if second:
        # Only a BUY invoice (المدفوع للعميل) has a second line. A SALE prints no settlement detail (no
        # المستلم من العميل, method, amount or customer account) and uses the one-line box above.
        if extra.get("outgoing"):
            spans("money", split + pad, y1, right - pad, y1 + line_h,
                  [(extra.get("payment_title", "") + "  ", t.paid_label, False, 70),
                   (_ltr(extra.get("moved", "")) + " ج", t.paid, False, 30)], align="right", min_pt=t.paid_label)
            methods = "  •  ".join(f"{label} {_ltr(amount)}" for label, amount in extra.get("methods", ()))
            text("money", left + pad, y1, split - pad, y1 + line_h, methods, t.methods, True, gray=75,
                 align="right", min_pt=t.methods_min)
    return ops


class LayoutError(ValueError):
    pass


def validate(ops, calibration=None, printable=None):
    """One sheet, never over the preprinted artwork. `printable` = (left, top, right, bottom) mm
    the driver can actually mark; checked when known (real printer)."""
    c = calibration or BillCalibration()
    epsilon = 0.01
    for op in ops:
        bottom, right = op.y + op.h, op.x + op.w
        if op.region == "header" and bottom > c.upper_gold_y_mm + epsilon:
            raise LayoutError("رأس الفاتورة يتجاوز الخط الذهبي العلوي.")
        if op.region in ("table", "money") and op.y < c.upper_gold_y_mm - epsilon:
            raise LayoutError("جدول الفاتورة يبدأ فوق الخط الذهبي العلوي.")
        if bottom > c.lower_gold_y_mm + epsilon:
            raise LayoutError("محتوى الفاتورة يتجاوز الخط الذهبي السفلي (منطقة التذييل المطبوع).")
        if op.x < -epsilon or op.y < -epsilon or right > PAPER_WIDTH_MM + epsilon:
            raise LayoutError("محتوى الفاتورة خارج حدود الورقة.")
        if printable is not None:
            left_m, top_m, right_m, bottom_m = printable
            if op.x < left_m - epsilon or op.y < top_m - epsilon or right > right_m + epsilon \
                    or bottom > bottom_m + epsilon:
                raise LayoutError("محتوى الفاتورة خارج المساحة القابلة للطباعة في الطابعة.")
    return True


# ---------------------------------------------------------------- Qt painting (the only Qt part)
_FONT_FILES = ("segoeui.ttf", "segoeuib.ttf")               # the prototype's registered TTFs


def ensure_fonts():
    """Segoe UI is a Windows system font; register its files explicitly when Qt's font
    database does not list it (offscreen/preview platforms), so a bill never falls back."""
    from PySide6.QtGui import QFontDatabase
    if FONT_FAMILY in QFontDatabase.families():
        return True
    fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in _FONT_FILES:
        path = os.path.join(fonts, name)
        if os.path.exists(path):
            QFontDatabase.addApplicationFont(path)
    return FONT_FAMILY in QFontDatabase.families()


def paint(painter, ops, dots_per_mm):
    """Replay the ops on any QPainter whose (0, 0) is the paper's top-left corner."""
    from PySide6.QtCore import QLineF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPen, QTextOption

    ensure_fonts()
    black = QColor(0, 0, 0)

    def shade(gray):
        return QColor(gray, gray, gray)

    def font_for(size_pt, bold):
        font = QFont(FONT_FAMILY)
        font.setPixelSize(max(1, round(size_pt / 72.0 * 25.4 * dots_per_mm)))
        font.setBold(bold)
        return font

    painter.save()
    painter.setRenderHint(painter.RenderHint.Antialiasing, False)
    for op in ops:
        rect = QRectF(op.x * dots_per_mm, op.y * dots_per_mm, op.w * dots_per_mm, op.h * dots_per_mm)
        if op.kind == "bar":
            painter.fillRect(rect, black)
        elif op.kind in ("line", "box"):
            pen = QPen(shade(op.gray if op.gray else GRID_GRAY))
            pen.setWidthF(op.width_pt / 72.0 * 25.4 * dots_per_mm)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            if op.kind == "line":
                painter.drawLine(QLineF(rect.topLeft(), rect.bottomRight()))
            else:
                painter.drawRect(rect)
        elif op.kind == "text" and op.spans:
            # One line of runs, right-to-left: the first run is the rightmost (label, then value).
            measured = []
            for value, size, bold, gray in op.spans:
                font = font_for(size, bold)
                measured.append((value, font, gray, QFontMetricsF(font, painter.device()).horizontalAdvance(value)))
            total = sum(m[3] for m in measured)
            largest = max(size for _, size, _, _ in op.spans)
            if op.min_pt and total > rect.width() and largest > op.min_pt:
                ratio = max(op.min_pt / largest, rect.width() / total)
                measured = []
                for value, size, bold, gray in op.spans:
                    font = font_for(size * ratio, bold)
                    measured.append((value, font, gray,
                                     QFontMetricsF(font, painter.device()).horizontalAdvance(value)))
                total = sum(m[3] for m in measured)
            x = rect.right() if op.align == "right" else rect.center().x() + min(total, rect.width()) / 2
            option = QTextOption(Qt.AlignRight | Qt.AlignVCenter)
            option.setTextDirection(Qt.RightToLeft)
            option.setWrapMode(QTextOption.NoWrap)
            for value, font, gray, width in measured:
                width = min(width, max(0.0, x - rect.left()))
                if width <= 0:
                    break
                painter.setFont(font)
                painter.setPen(shade(gray))
                metrics = QFontMetricsF(font, painter.device())
                painter.drawText(QRectF(x - width, rect.top(), width, rect.height()),
                                 metrics.elidedText(value, Qt.ElideRight, width + 0.5), option)
                x -= width
        elif op.kind == "text" and op.text:
            font = font_for(op.size_pt, op.bold)
            value = op.text
            if "\n" not in value and op.min_pt and \
                    QFontMetricsF(font, painter.device()).horizontalAdvance(value) > rect.width():
                font = font_for(op.min_pt, op.bold)        # e.g. long notes: one step smaller first
            painter.setFont(font)
            painter.setPen(shade(op.gray) if op.gray else black)
            option = QTextOption((Qt.AlignRight if op.align == "right" else Qt.AlignHCenter) | Qt.AlignVCenter)
            option.setTextDirection(Qt.RightToLeft)
            option.setWrapMode(QTextOption.NoWrap)
            if "\n" not in value:
                metrics = QFontMetricsF(font, painter.device())
                value = metrics.elidedText(value, Qt.ElideRight, rect.width())   # then "…", never a taller row
            painter.drawText(rect, value, option)
    painter.restore()


def calibration_from_settings(settings=None):
    """The per-workstation calibration kept with the Bill printer choice (QSettings)."""
    if settings is None:
        from config.printer_settings import PrinterSettings
        settings = PrinterSettings()
    values = settings.bill_calibration() if hasattr(settings, "bill_calibration") else {}
    return replace(BillCalibration(), **{k: float(v) for k, v in values.items() if v is not None})


def style_from_settings(settings=None):
    """The bill style chosen on this workstation (default: Variant C; the prototype stays available)."""
    if settings is None:
        from config.printer_settings import PrinterSettings
        settings = PrinterSettings()
    style = settings.bill_style() if hasattr(settings, "bill_style") else ""
    return style if style in BILL_STYLES else DEFAULT_BILL_STYLE
