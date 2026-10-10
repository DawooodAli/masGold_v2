"""The shared QML test harness walks the item tree through InvoicePageTests.child_items / visual_item / visible_item.

Background: two separate full runs failed with `AttributeError: 'QMetaObject' object has no attribute 'objectName'` inside that walk
(SupplierPageTests ... pieceAllowance and ManagerReviewPageTests ... reviewCard_MAKING_PRICE); both tests passed in isolation, and two
older page tests already filter non-QQuickItem children silently. HOW a QMetaObject wrapper reaches the walk is NOT established. These
tests pin what the harness now guarantees regardless of the cause: a non-item child still FAILS the test (nothing is skipped or
retried), but with the evidence (type, object, index and ancestors), while ordinary trees are walked exactly as before and unrelated
errors are never swallowed."""
import unittest

from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem

from tests import test_invoice_page_qml as harness

APP = QGuiApplication.instance() or QGuiApplication([])


class Walker:
    """The three helpers bound to a minimal stand-in (they only need `self.child_items`)."""
    child_items = staticmethod(harness.InvoicePageTests.child_items)
    visual_item = harness.InvoicePageTests.visual_item
    visible_item = harness.InvoicePageTests.visible_item


def item(name, parent=None):
    node = QQuickItem(parent)
    node.setObjectName(name)
    return node


class StrangeChildren(QQuickItem):
    """An item whose childItems() returns what the real walks twice met: a QMetaObject wrapper next to a genuine item."""

    def childItems(self):
        return [item("genuine"), QObject().metaObject()]


class FailingChildren(QQuickItem):
    def childItems(self):
        raise RuntimeError("a genuine failure")


class TreeWalkTests(unittest.TestCase):
    def test_a_non_item_child_fails_the_walk_with_the_evidence(self):
        page = item("page")
        row = StrangeChildren()
        row.setObjectName("parentRow")
        row.setParentItem(page)
        with self.assertRaises(AssertionError) as context:
            list(Walker.child_items(row))
        message = str(context.exception)
        for part in ("QMetaObject", "child 1 of 2", "StrangeChildren", "parentRow", "page"):
            self.assertIn(part, message)
        self.assertNotIsInstance(context.exception, AttributeError)

    def test_the_named_walks_also_fail_instead_of_skipping_the_object(self):
        row = StrangeChildren()
        row.setObjectName("row")
        for walk in (Walker().visual_item, Walker().visible_item):
            with self.assertRaises(AssertionError) as context:
                walk("never-present", row)
            self.assertIn("QMetaObject", str(context.exception))

    def test_an_ordinary_tree_is_walked_as_before(self):
        root = item("root")
        middle = item("middle", root)
        target = item("target", middle)
        hidden = item("hidden", root)
        hidden.setVisible(False)
        walker = Walker()
        self.assertIs(walker.visual_item("target", root), target)
        self.assertIs(walker.visible_item("target", root), target)
        self.assertIs(walker.visual_item("hidden", root), hidden)
        self.assertIsNone(walker.visible_item("hidden", root))                        # present but not visible
        self.assertIsNone(walker.visual_item("absent", root))
        self.assertEqual([c.objectName() for c in Walker.child_items(root)], ["middle", "hidden"])

    def test_an_unrelated_error_is_never_swallowed(self):
        with self.assertRaises(RuntimeError):
            list(Walker.child_items(FailingChildren()))


if __name__ == "__main__":
    unittest.main()
