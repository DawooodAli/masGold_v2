"""The UI harness clicks a control only after its containers are laid out (InvoicePageTests.settle_layout).

Background: a full run failed once with the scrap cashback typed into the weight field: the cashback field had just become visible in its
RowLayout, still had its default size (120 x 56) on top of its neighbour, and two position samples 20 ms apart both came before the frame
that lays the row out. This pins the mechanism with a minimal scene, deterministically (no timing involved)."""
import os
import unittest

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness

SCENE = b'''
import QtQuick
import QtQuick.Layouts
Item { width: 400; height: 50
  RowLayout { anchors.fill: parent; spacing: 2
    Rectangle { objectName: "appearing"; visible: false; implicitWidth: 120; implicitHeight: 56; Layout.preferredWidth: 84; Layout.preferredHeight: 40 }
    Rectangle { objectName: "neighbour"; Layout.fillWidth: true; implicitHeight: 40 }
  }
}'''


class SettleLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.engine = QQmlEngine()
        self.component = QQmlComponent(self.engine)                                     # kept alive: the scene belongs to it
        self.component.setData(SCENE, QUrl())
        self.root = self.component.create()
        self.window = QQuickWindow()
        self.window.resize(400, 50)
        self.root.setParentItem(self.window.contentItem())
        self.window.show()
        QTest.qWait(50)
        self.appearing = self.root.findChild(QQuickItem, "appearing")
        self.neighbour = self.root.findChild(QQuickItem, "neighbour")

    def tearDown(self):
        self.window.close()

    def test_a_control_that_just_appeared_still_overlaps_its_neighbour_until_laid_out(self):
        self.appearing.setVisible(True)
        self.assertEqual((self.appearing.width(), self.neighbour.x()), (120, 0))          # the race: default size, on top of the neighbour

    def test_settle_layout_places_it_before_any_click(self):
        self.appearing.setVisible(True)
        harness.InvoicePageTests.settle_layout(self.appearing)
        self.assertEqual((self.appearing.width(), self.appearing.height()), (84, 40))
        self.assertGreaterEqual(self.neighbour.x(), self.appearing.x() + self.appearing.width())   # side by side, no overlap


if __name__ == "__main__":
    unittest.main()
