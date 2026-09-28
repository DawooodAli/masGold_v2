"""Barcode input independent of the Windows keyboard layout (keyboard-wedge scanners).

The scanner presses physical keys; the ACTIVE layout decides the characters (Arabic letters under
the Arabic layout). Barcode fields map the hardware scan code instead. Here the same physical key
sequence is sent twice — once with the text the English layout produces, once with the text the
Arabic layout produces — and must give the same barcode. Real QKeyEvents (with nativeScanCode)
go through the real Main.qml fields.
"""
from decimal import Decimal
import os
from pathlib import Path
import unittest

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtTest import QTest

from app.barcode_input import INVALID_TEXT, burst_barcode, canonical_barcode, char_for_scan_code
from tests import test_invoice_page_qml as page_harness

D = Decimal
# Set-1 scan codes of the physical US keys, and what each layout types on them.
SCAN = {"A": 0x1E, "C": 0x2E, "L": 0x26, "F": 0x21, "M": 0x32, "-": 0x0C,
        "0": 0x0B, "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06, "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A}
ARABIC_101 = {"A": "ِ", "C": "}", "L": "/", "F": "]", "M": "’", "-": "-"}   # Shift+key on Arabic (101)
ARABIC_101_LOWER = {"A": "ش", "C": "ؤ", "L": "م", "F": "ب", "M": "ة"}


def strokes_for(barcode, layout):
    """(scan_code, shift, text) per key press, as a keyboard-wedge scanner types `barcode`."""
    out = []
    for ch in barcode:
        shift = ch.isalpha() and ch.isupper()
        code = SCAN[ch.upper()]
        if layout == "en" or ch.isdigit():
            text = ch
        else:
            text = (ARABIC_101 if shift else ARABIC_101_LOWER).get(ch.upper(), "؟")
        out.append((code, shift, text))
    return out


class MappingTests(unittest.TestCase):
    def test_same_physical_keys_give_the_same_barcode_on_both_layouts(self):
        for barcode in ("ACC260927001", "01260925", "000000LF6-4MM56002807003205198"):
            for layout in ("en", "ar"):
                typed = "".join(char_for_scan_code(code, shift) for code, shift, _ in strokes_for(barcode, layout))
                self.assertEqual(typed, barcode, (barcode, layout))

    def test_keys_that_are_not_barcode_characters_keep_their_meaning(self):
        for code in (0x1C, 0x0E, 0x0F, 0x01, 0x39):                   # Enter, Backspace, Tab, Esc, Space
            self.assertEqual(char_for_scan_code(code), "")
        self.assertEqual(char_for_scan_code(0x148), "")                 # ↑ (extended) is not keypad 8
        self.assertEqual(char_for_scan_code(0x48), "8")                 # keypad 8
        self.assertEqual(char_for_scan_code(0x02, True), "")            # Shift+1 is "!", not a barcode char

    def test_canonical_text_and_safe_refusal(self):
        self.assertEqual(canonical_barcode("  ACC260927001 "), ("ACC260927001", ""))
        self.assertEqual(canonical_barcode("٠١٢٦٠٩٢٥"), ("01260925", ""))          # Arabic-Indic digits
        self.assertEqual(canonical_barcode("’لآ5000000002127"), ("", INVALID_TEXT))  # never "corrected"
        long_value = "000000LF6-4MM56002807003205198" * 2
        self.assertEqual(canonical_barcode(long_value), (long_value, ""))          # never truncated

    def test_scanner_bursts_are_recognised_and_typing_is_not(self):
        fast = [(i * 8, code, shift) for i, (code, shift, _) in enumerate(strokes_for("ACC260927001", "ar"))]
        self.assertEqual(burst_barcode(fast), "ACC260927001")
        slow = [(i * 180, code, shift) for i, (code, shift, _) in enumerate(strokes_for("ACC260927001", "ar"))]
        self.assertEqual(burst_barcode(slow), "")
        self.assertEqual(burst_barcode(fast[:3]), "")                                # too short for a scan


class BarcodeFieldTests(page_harness.InvoicePageTests):
    """Real key events (with hardware scan codes) through the real Billing / Inventory fields."""

    def keys(self, barcode, layout, delay_ms=0):
        for code, shift, text in strokes_for(barcode, layout):
            modifiers = Qt.ShiftModifier if shift else Qt.NoModifier
            for kind in (QEvent.KeyPress, QEvent.KeyRelease):
                QGuiApplication.sendEvent(self.root, QKeyEvent(kind, 0, modifiers, code, 0, 0, text))
            if delay_ms:
                QTest.qWait(delay_ms)

    def enter(self):
        for kind in (QEvent.KeyPress, QEvent.KeyRelease):
            QGuiApplication.sendEvent(self.root, QKeyEvent(kind, Qt.Key_Return, Qt.NoModifier, 0x1C, 0, 0, "\r"))

    def add_pieces(self, *barcodes):
        for i, barcode in enumerate(barcodes):
            self.store.items[f"2{i:02d}"] = {**self.store.items["100"], "inventory_item_id": f"2{i:02d}",
                                             "barcode_value": barcode}

    def test_barcode_scan_is_identical_under_english_and_arabic_layouts(self):
        self.add_pieces("ACC260927001", "ACC260927002")
        self.select_salesperson()
        for barcode, layout in (("ACC260927001", "en"), ("ACC260927002", "ar")):
            count = self.vm.saleCount
            self.click("invoiceBarcodeField")
            self.keys(barcode, layout)
            self.assertEqual(self.text("invoiceBarcodeField"), barcode, layout)     # canonical, not Arabic
            self.enter()                                                            # the scanner's terminator
            self.wait_until(lambda: self.vm.saleCount == count + 1, f"{layout} scan not added")
        self.assertEqual([line["barcode"] for line in self.vm._sales], ["ACC260927001", "ACC260927002"])

    def test_numeric_barcode_and_repeated_scans(self):
        self.select_salesperson()
        self.click("invoiceBarcodeField")
        self.keys("01260925", "ar")
        self.enter()
        self.wait_until(lambda: self.vm.saleCount == 1)
        self.keys("01260925", "ar")                                                  # the same piece again
        self.enter()
        self.wait_until(lambda: "مضافة بالفعل" in self.vm.errorMessage, "duplicate not refused")
        self.assertEqual(self.vm.saleCount, 1)

    def test_manual_typing_and_correction_stay_usable(self):
        self.add_pieces("ACC260927001")
        self.select_salesperson()
        self.click("invoiceBarcodeField")
        self.keys("ACC2609270019", "ar", delay_ms=40)                                # typed by hand, one key extra
        for kind in (QEvent.KeyPress, QEvent.KeyRelease):
            QGuiApplication.sendEvent(self.root, QKeyEvent(kind, Qt.Key_Backspace, Qt.NoModifier, 0x0E, 0, 0, "\b"))
        self.assertEqual(self.text("invoiceBarcodeField"), "ACC260927001")
        self.enter()
        self.wait_until(lambda: self.vm.saleCount == 1, "corrected entry not added")

    def test_invalid_characters_are_refused_not_guessed(self):
        self.select_salesperson()
        self.item("invoiceBarcodeField").setProperty("text", "’لآ5000000002127")    # e.g. pasted
        self.click("invoiceBarcodeField")
        self.enter()
        self.wait_until(lambda: "حروف غير صالحة" in self.vm.errorMessage, "invalid barcode accepted")
        self.assertEqual(self.vm.saleCount, 0)

    def test_item_search_converts_only_a_scanner_burst(self):
        self.select_salesperson()
        QTest.keyClick(self.root, Qt.Key_F3)
        self.wait_until(lambda: self.item("itemSearchText").property("activeFocus"))
        self.keys("ACC260927001", "ar")                                              # a burst: converted on Enter
        self.enter()
        self.wait_until(lambda: self.text("itemSearchText") == "ACC260927001", "burst not converted")
        self.item("itemSearchText").setProperty("text", "")
        self.keys("ACC", "ar", delay_ms=160)                                         # ordinary typing: untouched
        self.enter()
        QTest.qWait(80)
        self.assertEqual(self.text("itemSearchText"), "ِ}}")

    def test_long_barcode_never_overflows_its_box(self):
        inventory = self.controller.operational_viewmodels["inventoryVM"]
        base = {"stock_classification": "NEW_STOCK", "item_type_name": "خاتم", "karat_value": 21,
                "weight_grams": "3.500", "factory_name": "لوكس", "category_code": "A", "size": "18",
                "acquired_at": "2026-09-27 10:00:00"}
        values = ["000000LF6-4MM56002807003205198-EXTRA-LONG-9999", "000000LF6-4MM56002807003205198", "01260925"]
        inventory._items = [{**base, "inventory_item_id": str(900 + i), "barcode_value": v} for i, v in enumerate(values)]
        inventory.dataChanged.emit()
        self.root.setProperty("currentPage", 2)
        target = os.getenv("MASGOLD_UI_REVIEW_DIR")
        for width, height in page_harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(300)
            shown = []
            for text in self.all_visual("inventoryBarcodeText"):
                box = text.parentItem().parentItem()
                self.assertTrue(box.property("clip"))
                left = text.mapToItem(box, QPointF(0, 0)).x()
                self.assertGreaterEqual(left, 0)
                self.assertLessEqual(left + text.width(), box.width() + 0.5, "text wider than its box")
                self.assertLessEqual(text.property("paintedWidth"), text.width() + 1, "painted outside its width")
                shown.append(text.property("text"))
            self.assertEqual(sorted(shown), sorted(values))                          # the data is never shortened
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)
                self.root.grabWindow().save(str(Path(target) / f"inventory-long-barcode-{width}x{height}.png"))
        self.assertEqual(self.warnings, [])

    def all_visual(self, name, parent=None, found=None):
        found = [] if found is None else found
        parent = parent or self.root.contentItem()
        if parent.objectName() == name and parent.property("visible"):
            found.append(parent)
        for child in parent.childItems():
            self.all_visual(name, child, found)
        return found


    # ------------------------------------------------------------ one physical piece, once per invoice
    def scan(self, barcode, layout="en"):
        count = len(self.vm._sales)
        self.click("invoiceBarcodeField")
        self.keys(barcode, layout)
        self.enter()
        self.wait_until(lambda: len(self.vm._sales) != count or "مضافة بالفعل" in self.vm.errorMessage,
                        f"scan of {barcode} had no result")

    def snapshot_money(self):
        self.settle()
        return dict(self.vm.summary), [dict(leg) for leg in self.vm.paymentLegs], self.vm.invoiceTotal

    def test_duplicate_scan_under_the_other_layout_is_refused_with_no_effect(self):
        self.add_pieces("ACC260927001")
        self.select_salesperson()
        self.scan("ACC260927001", "ar")                                           # Arabic layout: added
        self.total("80000")
        before = self.snapshot_money()
        self.scan("ACC260927001", "en")                                           # English layout: same piece
        self.assertEqual(self.vm.errorMessage, "هذه القطعة مضافة بالفعل إلى الفاتورة")
        self.assertEqual([l["inventory_item_id"] for l in self.vm._sales], ["200"])
        self.assertEqual(self.snapshot_money(), before)                           # no financial effect at all
        self.assertTrue(self.item("invoiceBarcodeField").property("activeFocus"))  # ready for the next scan
        self.assertEqual(self.text("invoiceBarcodeField"), "")

    def found(self, barcode):
        return self.vm._service_instance().find_item_by_barcode(barcode)

    def test_barcode_then_search_and_search_then_barcode_are_refused(self):
        self.add_pieces("ACC260927001", "ACC260927002")
        self.select_salesperson()
        self.scan("ACC260927001")                                                 # barcode first
        self.vm._search_results = [self.found("ACC260927001")]
        self.assertFalse(self.vm.addSearchedItem("200"))                          # then F3: refused
        self.vm._search_results = [self.found("ACC260927002")]
        self.assertTrue(self.vm.addSearchedItem("201"))                           # F3 first
        self.scan("ACC260927002")                                                 # then barcode: refused
        self.assertEqual(self.vm.errorMessage, "هذه القطعة مضافة بالفعل إلى الفاتورة")
        self.assertEqual([l["inventory_item_id"] for l in self.vm._sales], ["200", "201"])

    def test_remove_then_add_again_is_allowed(self):
        self.add_pieces("ACC260927001")
        self.select_salesperson()
        self.scan("ACC260927001")
        self.vm.removeSaleLine(0)
        self.assertEqual(self.vm.saleCount, 0)
        self.scan("ACC260927001")
        self.assertEqual([l["inventory_item_id"] for l in self.vm._sales], ["200"])


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(MappingTests))
    suite.addTests(BarcodeFieldTests(n) for n in loader.getTestCaseNames(BarcodeFieldTests)
                   if n.startswith(("test_barcode", "test_numeric", "test_manual", "test_invalid", "test_item_search",
                                    "test_long", "test_duplicate", "test_remove")))
    return suite
