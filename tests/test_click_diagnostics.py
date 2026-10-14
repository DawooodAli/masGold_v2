"""The click diagnostics of the UI harness: a FAILED test prints the clicks that never reached their control; a passing test prints nothing. A lost click is simulated by
swallowing the mouse event (the real defect class: a press absorbed by something else)."""
from contextlib import redirect_stderr
import io
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QByteArray, QMetaMethod
from tests import click_diagnostics
from tests import test_invoice_page_qml as harness


class FakeTest:
    _click_records = ()

    def id(self):
        return "fake.test"


class ReportTests(unittest.TestCase):
    def test_nothing_is_printed_without_clicks_or_when_every_click_arrived(self):
        stream = io.StringIO()
        fake = FakeTest()
        click_diagnostics.report(fake, stream)
        fake._click_records = [{"target": "a", "reached": True}]
        click_diagnostics.report(fake, stream)
        self.assertEqual(stream.getvalue().count("LOST"), 0)               # an arrived click is listed as context only (the tail), never as lost

    def test_a_lost_click_is_listed_with_its_evidence(self):
        stream = io.StringIO()
        fake = FakeTest()
        fake._click_records = [{"target": "first", "reached": True}, {"target": "second", "reached": False, "overlayBefore": {"visibleChildren": 2}, "hit": ["QQuickOverlay(z=1000001.0)"]},
                               {"target": "third", "reached": None}]
        click_diagnostics.report(fake, stream)
        text = stream.getvalue()
        self.assertIn("1 lost of the last 3 clicks", text)
        self.assertIn("LOST " + '{"target": "second"', text)
        self.assertIn("QQuickOverlay", text)


class SignalDetectionTests(unittest.TestCase):
    def test_qquick_mouse_event_signals_are_identified_by_name_and_signature(self):
        def item_for(name, signature):
            method = Mock()
            method.methodType.return_value = QMetaMethod.MethodType.Signal
            method.name.return_value = QByteArray(name.encode("ascii"))
            method.methodSignature.return_value = QByteArray(signature.encode("ascii"))
            meta = Mock()
            meta.methodCount.return_value = 1
            meta.method.return_value = method
            item = Mock()
            item.metaObject.return_value = meta
            return item

        self.assertTrue(click_diagnostics._has_qquick_mouse_event(
            item_for("pressed", "pressed(QQuickMouseEvent*)"), "pressed"))
        self.assertFalse(click_diagnostics._has_qquick_mouse_event(
            item_for("pressed", "pressed()"), "pressed"))
        self.assertFalse(click_diagnostics._has_qquick_mouse_event(
            item_for("released", "released(QQuickMouseEvent*)"), "pressed"))


def swallowed_click_case():
    """A harness test whose click never reaches its control (the mouse event is swallowed) and which then fails, built lazily so it is never discovered."""
    class Swallowed(harness.InvoicePageTests):
        def test_lost(self):
            with patch.object(harness.QTest, "mouseClick", lambda *a, **k: None):
                self.click(self.first_button())
            self.fail("the control never reacted")

        def first_button(self):
            """Any visible, enabled, named button of the window (the first found)."""
            def walk(item):
                if item.property("visible") and item.property("enabled") and item.objectName() and hasattr(item, "clicked") and item.width() > 0:
                    return item
                for child in item.childItems():
                    found = walk(child)
                    if found is not None:
                        return found
                return None
            found = walk(self.root.contentItem())
            if found is None:
                self.skipTest("no enabled button on the page")
            return found

        def test_ok(self):
            pass

    return Swallowed


class HarnessIntegrationTests(unittest.TestCase):
    def test_a_failed_test_prints_its_lost_click_and_a_passing_test_prints_nothing(self):
        case = swallowed_click_case()
        stream = io.StringIO()
        runner = unittest.TextTestRunner(stream=io.StringIO())            # a suite, so the harness class fixtures (the QML window) are set up
        with redirect_stderr(stream):
            failing = runner.run(unittest.TestSuite([case("test_lost")]))
            passing = runner.run(unittest.TestSuite([case("test_ok")]))
        self.assertEqual((len(failing.failures), len(passing.failures)), (1, 0))
        output = stream.getvalue()
        self.assertEqual(output.count("click diagnostics for failed test"), 1)       # only the failed test reports
        self.assertIn("LOST", output)
        self.assertIn('"reached": false', output)
        self.assertIn("overlayBefore", output)


if __name__ == "__main__":
    unittest.main()
