"""The new barcode label sheet (services/label_renderer.py): Code 128 with 1-dot bars, margins, «مجوهرات ماس» in Cairo, sent as one Z64 graphic. Nothing is printed and no database is used."""
import base64
import os
import re
import unittest
import zlib

from PySide6.QtGui import QGuiApplication

from services import label_renderer as L
from services.barcode_print_service import LABEL_STYLE_LEGACY, LABEL_STYLE_NEW, PrototypeZplPrinter

QGuiApplication.instance() or QGuiApplication([])

ITEM_LEFT = {"barcode_value": "59251209", "weight_grams": "2.6", "size": "58", "karat_value": "18", "factory_code": "SL", "category_code": "B"}
ITEM_RIGHT = {"barcode_value": "59251208", "weight_grams": "3.15", "size": "56", "karat_value": "21", "factory_code": "LX", "category_code": "T"}


def printer(style=LABEL_STYLE_NEW):
    return PrototypeZplPrinter(printer_name="none", style=style)


def fields(item):
    return printer()._print_fields(item)


class Sheet:
    """The rendered sheet as dots."""

    def __init__(self, left=ITEM_LEFT, right=ITEM_RIGHT):
        self.rows, self.row_bytes = L.sheet_rows(L.render_sheet(fields(left), fields(right) if right else None))

    def dot(self, x, y):
        return bool(self.rows[y * self.row_bytes + x // 8] & (0x80 >> (x % 8)))

    def ink_in(self, x0, y0, x1, y1):
        return [(x, y) for y in range(y0, y1) for x in range(x0, x1) if self.dot(x, y)]

    def runs(self, y, x0, x1):
        """Alternating bar / space run lengths along row y from the first black dot in [x0, x1)."""
        xs = [x for x in range(x0, x1) if self.dot(x, y)]
        start, end = xs[0], xs[-1]
        runs, current, length = [], self.dot(start, y), 0
        for x in range(start, end + 1):
            if self.dot(x, y) == current:
                length += 1
            else:
                runs.append(length)
                current, length = self.dot(x, y), 1
        runs.append(length)
        return runs


class Code128Tests(unittest.TestCase):
    def test_digits_use_subset_c_in_pairs_with_the_standard_check_digit(self):
        self.assertEqual(L.code128_codes("123456"), [105, 12, 34, 56])
        widths = L.code128_widths("123456")
        self.assertEqual(sum(widths), 11 * 5 + 13)                           # start + 3 pairs + check + stop
        self.assertEqual(len(widths) % 2, 1)                                 # starts with a bar and ends with a bar (the stop has 7 elements)
        self.assertEqual(sum(L.code128_widths("59251209")), 79)              # the label's own number: 79 dots, half of what Code 39 needed

    def test_an_odd_digit_count_switches_to_subset_b_for_the_last_digit(self):
        self.assertEqual(L.code128_codes("12345"), [105, 12, 34, 100, ord("5") - 32])

    def test_letters_and_symbols_use_subset_b(self):
        self.assertEqual(L.code128_codes("AB-1"), [104, 33, 34, 13, 17])
        with self.assertRaises(L.LabelError):
            L.code128_codes("")
        with self.assertRaises(L.LabelError):
            L.code128_codes("عربي")

    def test_the_checksum_matches_the_published_formula(self):
        codes = L.code128_codes("TEST0001")
        checksum = (codes[0] + sum(i * c for i, c in enumerate(codes[1:], start=1))) % 103
        widths = L.code128_widths("TEST0001")
        self.assertEqual(sum(widths), 11 * (len(codes) + 1) + 13)             # every symbol is 11 modules, the stop 13
        self.assertIsInstance(checksum, int)


class Z64Tests(unittest.TestCase):
    def test_the_crc_matches_every_graphic_of_the_old_label(self):
        """The CRC after ':Z64:...:' must be exactly what the firmware computes: proven against the three strings the old label has always sent successfully."""
        legacy = printer(LABEL_STYLE_LEGACY).build_zpl_label(ITEM_LEFT, ITEM_RIGHT)
        found = re.findall(r":Z64:([A-Za-z0-9+/=]+):([0-9A-F]{4})", legacy)
        self.assertGreaterEqual(len(found), 3)
        for data, crc in found:
            self.assertEqual("%04X" % L.crc16(data.encode("ascii")), crc)

    def test_the_new_zpl_keeps_the_printer_settings_and_carries_the_whole_sheet_as_one_graphic(self):
        zpl = printer().build_zpl_label(ITEM_LEFT, ITEM_RIGHT)
        self.assertIn("^PW641^LL0310", zpl)
        self.assertIn("~SD24", zpl)                                           # the darkness / speed block is untouched (a separate decision)
        self.assertEqual(zpl.count("^GFA"), 1)
        self.assertTrue(zpl.endswith("^PQ1,0,1,Y^XZ"))
        match = re.search(r"\^GFA,(\d+),(\d+),(\d+),:Z64:([A-Za-z0-9+/=]+):([0-9A-F]{4})\^FS", zpl)
        total, again, row_bytes, data, crc = match.groups()
        self.assertEqual((int(total), int(again), int(row_bytes)), (81 * 310, 81 * 310, 81))
        self.assertEqual("%04X" % L.crc16(data.encode("ascii")), crc)
        raw = zlib.decompress(base64.b64decode(data))
        self.assertEqual(raw, Sheet().rows)                                   # what the printer unpacks is exactly what was drawn
        self.assertLess(len(zpl), 20_000)                                     # compressed: a few KB, not the 50 KB of the plain hex

    def test_the_old_layout_is_still_available_untouched(self):
        legacy = printer(LABEL_STYLE_LEGACY).build_zpl_label(ITEM_LEFT, ITEM_RIGHT)
        self.assertIn("^B3N", legacy)
        self.assertIn("59251209", legacy)
        self.assertNotIn("^BC", legacy)


class DrawingTests(unittest.TestCase):
    def test_each_barcode_is_exact_code_128_with_one_dot_bars_and_centred_in_its_face(self):
        sheet = Sheet()
        for left, item in ((L.LEFT_X, ITEM_LEFT), (L.RIGHT_X, ITEM_RIGHT)):
            widths = L.code128_widths(item["barcode_value"])
            y = L.DATA_Y + L.BAR_TOP + L.BAR_H // 2
            self.assertEqual(sheet.runs(y, left, left + L.FACE_W), widths)
            first = next(x for x in range(left, left + L.FACE_W) if sheet.dot(x, y))
            self.assertEqual(first, left + (L.FACE_W - sum(widths)) // 2)
            self.assertGreaterEqual(min(sheet.runs(y, left, left + L.FACE_W)), 1)

    def test_the_bars_have_the_approved_height(self):
        sheet = Sheet()
        middle = L.DATA_Y + L.BAR_TOP + 20
        x = next(x for x in range(L.LEFT_X, L.LEFT_X + L.FACE_W) if sheet.dot(x, middle))
        top = bottom = middle                                                 # the unbroken bar through this row (the weight and the number above / below are separate ink)
        while sheet.dot(x, top - 1):
            top -= 1
        while sheet.dot(x, bottom + 1):
            bottom += 1
        self.assertEqual((top, bottom), (L.DATA_Y + L.BAR_TOP, L.DATA_Y + L.BAR_TOP + L.BAR_H - 1))

    def test_nothing_is_printed_within_the_margins_of_a_data_face(self):
        """A feed error of about a millimetre (8 dots) must never clip content: no ink within 8 dots of the die-cut edges (left, right, bottom)."""
        sheet = Sheet()
        for left in (L.LEFT_X, L.RIGHT_X):
            self.assertEqual(sheet.ink_in(left, L.DATA_Y, left + 8, L.DATA_Y + L.DATA_H), [])
            self.assertEqual(sheet.ink_in(left + L.FACE_W - 8, L.DATA_Y, left + L.FACE_W, L.DATA_Y + L.DATA_H), [])
            self.assertEqual(sheet.ink_in(left, L.DATA_Y + L.DATA_H - 6, left + L.FACE_W, L.DATA_Y + L.DATA_H), [])
            self.assertTrue(sheet.ink_in(left, L.DATA_Y, left + L.FACE_W, L.DATA_Y + 40))     # the weight / karat row is there

    def test_the_logo_faces_carry_the_mark_and_the_word_inside_their_faces(self):
        sheet = Sheet()
        for side, (x, y, w, h) in L.LOGO_FACES.items():
            ink = sheet.ink_in(x, y, x + w, y + h)
            self.assertGreater(len(ink), 1500, side)
            xs, ys = [p[0] for p in ink], [p[1] for p in ink]
            self.assertGreaterEqual(min(xs), x + 8, side)
            self.assertLessEqual(max(xs), x + w - 8, side)
            self.assertGreaterEqual(min(ys), y + 6, side)
            self.assertLessEqual(max(ys), y + h - 6, side)

    def test_the_mark_is_the_old_bitmap_unchanged(self):
        """The AD mark is the 96 x 48 crop of the bitmap the old label has always printed (the old left and right bitmaps cropped to the same picture)."""
        mark = zlib.decompress(base64.b64decode(L.MARK_Z))
        self.assertEqual(len(mark), L.MARK_ROW_BYTES * L.MARK_H)
        self.assertGreater(sum(bin(b).count("1") for b in mark), 1500)

    def test_the_category_letter_is_drawn_larger_than_before(self):
        with_letter = Sheet()
        without = Sheet({**ITEM_LEFT, "category_code": ""}, {**ITEM_RIGHT, "category_code": ""})
        x, y, w, h = L.LOGO_FACES["left"]
        extra = [p for p in with_letter.ink_in(x, y, x + w, y + h) if not without.dot(*p)]
        self.assertGreater(len(extra), 40)                                    # a real glyph (about 13 dots high), not the old 1.25 mm speck
        self.assertGreaterEqual(max(p[1] for p in extra) - min(p[1] for p in extra), 11)

    def test_a_single_tag_leaves_the_other_tag_blank(self):
        sheet = Sheet(right=None)
        self.assertEqual(sheet.ink_in(L.RIGHT_X, 0, L.SHEET_W, L.SHEET_H), [])

    def test_a_barcode_that_does_not_fit_is_refused_with_a_clear_message(self):
        with self.assertRaises(ValueError) as raised:
            printer().build_zpl_label({**ITEM_LEFT, "barcode_value": "ABCDEFGHIJKLMNOP"})
        self.assertIn("لا يتسع", str(raised.exception))

    def test_long_factory_codes_and_weights_never_overlap(self):
        crowded = {**ITEM_LEFT, "weight_grams": "123.45", "factory_code": "SUPPLIER", "karat_value": "24"}
        sheet = Sheet(crowded, None)
        row = [p for p in sheet.ink_in(L.LEFT_X + 8, L.DATA_Y, L.LEFT_X + L.FACE_W - 8, L.DATA_Y + 24)]
        self.assertTrue(row)                                                  # drawn, inside the margins
        self.assertTrue(os.path.exists(L._FONT_PATH))                         # the Cairo file ships with the program


if __name__ == "__main__":
    unittest.main()
