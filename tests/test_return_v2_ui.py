"""Return V2 on screen and the barcode reprint: the real ReturnWorkspace.qml over a real ReturnViewModel and the
in-memory Return store (no database) — identity (#item + stored barcode), the Exchange history and the current
piece, the valuation breakdown, and the enlarged money / weight fields at 1440×900, 1280×800 and 1080×680."""
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from tests import test_return_v2 as v2
from tests import test_return_service as rx


class BarcodeReprintTests(unittest.TestCase):
    """إعادة طباعة الباركود: the SAME stored barcode of the SAME piece; never a new one."""

    def service(self, item):
        from services.barcode_print_service import BarcodePrintService
        repository = Mock()
        repository.get_item_for_label_reprint.return_value = item
        repository.create_print_job.return_value = 41

        @contextmanager
        def transaction():
            yield Mock(connection=Mock())
        printer = Mock(printer_name="ZD", print_items=Mock(return_value=1))
        service = BarcodePrintService.__new__(BarcodePrintService)
        service._printer = printer
        patches = (patch("services.barcode_print_service.DatabaseTransaction", transaction),
                   patch("services.barcode_print_service.BarcodeRepository", return_value=repository))
        return service, repository, printer, patches

    def test_reprint_uses_the_stored_barcode_whatever_the_status(self):
        item = {"inventory_item_id": 200, "barcode_value": "01261001", "inventory_status": "CONVERTED_TO_OLD_GOLD"}
        service, repository, printer, patches = self.service(item)
        with patches[0], patches[1]:
            result = service.reprint_item_barcode("200")
        self.assertEqual((result["barcode_value"], result["inventory_item_id"]), ("01261001", "200"))
        printer.print_items.assert_called_once_with([item])
        repository.assign_barcode.assert_not_called()                        # no new identity, no new barcode
        repository.allocate_barcode_value.assert_not_called()

    def test_a_piece_without_a_barcode_is_refused(self):
        service, repository, printer, patches = self.service({"inventory_item_id": 7, "barcode_value": None})
        with patches[0], patches[1], self.assertRaises(ValueError):
            service.reprint_item_barcode("7")
        printer.print_items.assert_not_called()
        repository.assign_barcode.assert_not_called()


class ReturnPageTests(v2.ReturnV2Case):
    def setUp(self):
        super().setUp()
        from PySide6.QtCore import Qt, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuickControls2 import QQuickStyle
        QQuickStyle.setStyle("Material")
        app = QGuiApplication.instance() or QGuiApplication([])
        app.setLayoutDirection(Qt.RightToLeft)
        self.exchange(hours=48)
        self.at(hours=60)                       # invoice and replacement both inside the full-refund window
        from app.viewmodels.return_viewmodel import ReturnViewModel
        self.vm = ReturnViewModel(self.service._session, self.service, debounce_ms=10)
        self.engine = QQmlApplicationEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda errors: self.warnings.extend(e.toString() for e in errors))
        self.engine.rootContext().setContextProperty("returnVM", self.vm)
        qml_dir = (Path(__file__).resolve().parents[1] / "qml").as_uri()
        source = (f'import QtQuick\nimport QtQuick.Window\nimport "{qml_dir}"\n'
                  'Window { width: 1080; height: 680; visible: true\n'
                  '  ReturnWorkspace { anchors.fill: parent } }')
        self.engine.loadData(source.encode(), QUrl(qml_dir + "/"))
        self.root = self.engine.rootObjects()[0]
        self.vm.open("500")
        self.wait_until(lambda: bool(self.vm.lines))

    def tearDown(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtTest import QTest
        self.vm.shutdown()
        self.root.hide()
        QTest.qWait(30)
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def wait_until(self, predicate):
        from PySide6.QtTest import QTest
        for _ in range(250):
            if predicate():
                return
            QTest.qWait(20)
        self.fail("timed out")

    def find(self, name, parent=None):
        parent = parent or self.root.contentItem()
        if parent.objectName() == name and parent.property("visible"):
            return parent
        for child in parent.childItems():
            found = self.find(name, child)
            if found is not None:
                return found
        return None

    def text(self, name):
        item = self.find(name)
        self.assertIsNotNone(item, name)
        value = item.property("text")
        return value if value is not None else item.property("value")

    def index(self, line_id):
        return [l["lineId"] for l in self.vm.lines].index(line_id)

    def test_identity_exchange_history_and_current_piece(self):
        first = self.index("1")
        self.assertEqual(self.text(f"returnIdentity_{first}"), "B200  •  #200")
        exchanged = next(l for l in self.vm.lines if l["lineId"] == "7")
        self.assertEqual((exchanged["returnable"], exchanged["reason"]), (False, "تم استبدالها عبر EX000009"))
        current = self.index("X91")
        self.assertEqual(self.text(f"returnIdentity_{current}"), "B300  •  #300  •  دبلة")
        self.assertTrue(self.text(f"returnCurrentPiece_{current}").startswith("القطعة الحالية لدى العميل — عبر EX000009"))
        self.assertEqual(self.warnings, [])

    def test_breakdown_and_large_fields_at_all_sizes(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QFontMetricsF
        from PySide6.QtTest import QTest
        self.vm.toggleLine("X91")                                     # the replacement the customer holds
        self.wait_until(lambda: self.vm.summary.get("total") == "37,815.00 ج")
        from app.formatting import money
        self.assertEqual((self.text("returnBasis"), self.text("returnRounding"), self.text("returnTotal")),
                         ("37,816.00 ج", money("-1.00"), "37,815.00 ج"))          # the project's own money format
        self.vm.setAccountAmount("")
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(120)
                for name in ("returnLegAmount_0", "returnAccountAmount"):
                    field = self.find(name)
                    self.assertIsNotNone(field, name)
                    font = field.property("font")
                    self.assertGreaterEqual(font.pixelSize(), 18, name)          # never shrunk
                    room = field.width() - field.property("leftPadding") - field.property("rightPadding")
                    self.assertGreater(room, QFontMetricsF(font).horizontalAdvance("9,999,999.99"), name)
                    corner = field.mapToScene(QPointF(field.width(), field.height()))
                    self.assertTrue(field.mapToScene(QPointF(0, 0)).x() >= 0 and corner.x() <= width, name)
        self.assertEqual(self.warnings, [])

    def test_weight_field_fits_999_999(self):
        from PySide6.QtGui import QFontMetricsF
        from PySide6.QtTest import QTest
        self.vm.toggleLine("3")
        index = self.index("3")
        self.wait_until(lambda: self.find(f"returnWeight_{index}") is not None)
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(80)
                field = self.find(f"returnWeight_{index}")
                font = field.property("font")
                self.assertGreaterEqual(font.pixelSize(), 16)
                room = field.width() - field.property("leftPadding") - field.property("rightPadding")
                self.assertGreater(room, QFontMetricsF(font).horizontalAdvance("999.999"))
        self.assertEqual(self.warnings, [])


if __name__ == "__main__":
    unittest.main()
