"""Settings → الطباعة: two independent, workstation-local printers.

A temporary ini-format QSettings stands in for the Windows registry (re-opening the file is
an application restart); the printer list and every physical output are mocked.
"""
from contextlib import contextmanager
import os
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtGui import QGuiApplication

from app.session import AppSession
from app.viewmodels.printer_settings_viewmodel import PrinterSettingsViewModel
from config.printer_settings import BILL_KEY, LABEL_KEY, PrinterSettings
from database.connection import DatabaseConnection
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.barcode_print_service import PrototypeZplPrinter
from services.invoice_print_service import InvoicePrintService

PRINTERS = ["Brother HL-L2360D series", "ZDesigner GK420t", "Microsoft Print to PDF"]


def sync_runner(owner, work, finished):
    """Runs the background print work inline so assertions can follow at once."""
    try:
        finished(work(), "")
    except Exception as exc:
        finished(None, str(exc))


def texts(ops):
    return "\n".join(op.text for op in ops if op.kind == "text")


class PrinterSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        # Printing settings never touch MySQL.
        guard = patch.object(DatabaseConnection, "connect", side_effect=AssertionError("no database"))
        guard.start()
        self.addCleanup(guard.stop)
        handle, self.path = tempfile.mkstemp(suffix=".ini")
        os.close(handle)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))
        self.bill_jobs, self.label_jobs = [], []

    def store(self):
        return QSettings(self.path, QSettings.IniFormat)

    def session(self):
        session = AppSession()
        session._establish(AuthenticationResult(1, None, "المالك", frozenset({"APP_LOGIN", "INVOICE_CREATE"}),
                                                _VERIFIED), int(session.generation))
        return session

    def vm(self, printers=PRINTERS, runner=sync_runner, print_document=None):
        settings = PrinterSettings(self.store())
        bill = InvoicePrintService(self.session(), settings=settings,
                                   print_document=print_document or (lambda ops: (self.bill_jobs.append(
                                       (settings.bill_printer(), texts(ops))), (True, "ok", None))[1]))
        label = PrototypeZplPrinter(settings=settings,
                                    send_raw=lambda name, labels: (self.label_jobs.append((name, labels)),
                                                                   len(labels))[1])
        return PrinterSettingsViewModel(self.session(), settings=settings, printers=lambda: list(printers),
                                        bill_printing=bill, label_printing=label, runner=runner), settings

    def test_the_bill_style_is_a_local_choice_between_the_current_look_and_the_old_programs(self):
        vm, settings = self.vm()
        self.assertEqual(vm.billStyle, "variant_c")                                            # the default stays the current look
        vm.setBillStyle("prototype")
        self.assertEqual((vm.billStyle, vm.messageIsError), ("prototype", False))
        self.assertEqual(self.vm()[0].billStyle, "prototype")                                  # persists after a restart
        vm.setBillStyle("nonsense")
        self.assertEqual((vm.billStyle, vm.messageIsError), ("prototype", True))               # refused, unchanged
        vm.setBillStyle("variant_c")
        self.assertEqual(vm.billStyle, "variant_c")
        self.assertEqual(settings.bill_printer(), "")                                          # the printers are untouched

    def test_bill_and_label_printers_are_stored_independently(self):
        vm, settings = self.vm()
        vm.setBillPrinter("Brother HL-L2360D series")
        self.assertEqual((settings.bill_printer(), settings.label_printer()), ("Brother HL-L2360D series", ""))
        vm.setLabelPrinter("ZDesigner GK420t")
        self.assertEqual((settings.bill_printer(), settings.label_printer()),
                         ("Brother HL-L2360D series", "ZDesigner GK420t"))
        vm.setBillPrinter("Microsoft Print to PDF")                          # changing one leaves the other
        self.assertEqual(settings.label_printer(), "ZDesigner GK420t")
        vm.setLabelPrinter("Brother HL-L2360D series")
        self.assertEqual(settings.bill_printer(), "Microsoft Print to PDF")

    def test_choices_persist_after_a_restart(self):
        vm, _ = self.vm()
        vm.setBillPrinter("Brother HL-L2360D series")
        vm.setLabelPrinter("ZDesigner GK420t")
        del vm
        again = PrinterSettings(self.store())                                # a new process reading the file
        self.assertEqual((again.bill_printer(), again.label_printer()),
                         ("Brother HL-L2360D series", "ZDesigner GK420t"))

    def test_only_installed_printers_can_be_chosen(self):
        vm, settings = self.vm()
        vm.setBillPrinter("طابعة مكتوبة يدوياً")
        vm.setLabelPrinter("")
        self.assertEqual((settings.bill_printer(), settings.label_printer()), ("", ""))
        self.assertTrue(vm.messageIsError)
        self.assertEqual(vm.printers, PRINTERS)

    def test_a_removed_printer_is_shown_unavailable_and_cannot_test_print(self):
        vm, settings = self.vm()
        vm.setBillPrinter("Brother HL-L2360D series")
        vm, _ = self.vm(printers=["ZDesigner GK420t"])                         # the Brother was uninstalled
        self.assertEqual((vm.billPrinter["name"], vm.billPrinter["available"], vm.billPrinter["status"]),
                         ("Brother HL-L2360D series", False, "غير متاحة حالياً"))
        vm.testBillPrint()
        self.assertEqual(self.bill_jobs, [])
        self.assertTrue(vm.messageIsError)

    def test_bill_test_print_uses_only_the_bill_printer_and_writes_nothing(self):
        vm, _ = self.vm()
        vm.setBillPrinter("Brother HL-L2360D series")
        vm.setLabelPrinter("ZDesigner GK420t")
        vm.testBillPrint()
        self.assertEqual([name for name, _ in self.bill_jobs], ["Brother HL-L2360D series"])
        self.assertIn("إجمالي الفاتورة", self.bill_jobs[0][1])
        self.assertEqual(self.label_jobs, [])                                  # the label printer is untouched
        self.assertFalse(vm.messageIsError)

    def test_bill_test_print_runs_on_a_background_thread_and_reports_busy(self):
        import threading
        from PySide6.QtCore import QEventLoop, QTimer
        release, seen = threading.Event(), {}

        def slow_printer(ops):                                                 # a driver that blocks
            seen["thread"] = threading.current_thread() is threading.main_thread()
            release.wait(5)
            self.bill_jobs.append(("Brother HL-L2360D series", texts(ops)))
            return True, "ok", None
        vm, _ = self.vm(runner=None, print_document=slow_printer)
        vm.setBillPrinter("Brother HL-L2360D series")
        vm.testBillPrint()
        # Control is back on the GUI thread while the "driver" is still blocked.
        self.assertTrue(vm.billTestBusy)
        self.assertEqual(vm.message, "جاري إرسال الطباعة...")
        vm.testBillPrint()                                                     # a double click sends nothing extra
        release.set()
        loop = QEventLoop()
        vm.changed.connect(lambda: None if vm.billTestBusy else loop.quit())
        QTimer.singleShot(5000, loop.quit)
        if vm.billTestBusy:
            loop.exec()
        self.assertFalse(vm.billTestBusy)
        self.assertFalse(seen["thread"])                                       # never the GUI thread
        self.assertEqual(len(self.bill_jobs), 1)
        self.assertEqual(vm.message, "تم إرسال فاتورة تجريبية إلى طابعة الفواتير.")

    def test_bill_calibration_is_local_and_independent_of_the_label_printer(self):
        from services.bill_overlay import MEASURED_LOWER_GOLD_Y_MM, calibration_from_settings
        settings = PrinterSettings(self.store())
        settings.set_label_printer("ZDesigner GK420t")
        self.assertEqual(calibration_from_settings(settings).lower_gold_y_mm, MEASURED_LOWER_GOLD_Y_MM)
        settings.set_bill_calibration(lower_gold_y_mm=124.5, offset_x_mm=-2, offset_y_mm=1.5)
        again = calibration_from_settings(PrinterSettings(self.store()))       # after a restart
        self.assertEqual((again.lower_gold_y_mm, again.offset_x_mm, again.offset_y_mm), (124.5, -2.0, 1.5))
        self.assertEqual(PrinterSettings(self.store()).label_printer(), "ZDesigner GK420t")

    def test_label_test_print_uses_only_the_label_printer(self):
        vm, _ = self.vm()
        vm.setBillPrinter("Brother HL-L2360D series")
        vm.setLabelPrinter("ZDesigner GK420t")
        vm.testLabelPrint()
        self.assertEqual([name for name, _ in self.label_jobs], ["ZDesigner GK420t"])
        zpl = self.label_jobs[0][1][0]
        self.assertIn("^PW641^LL0310", zpl)                                    # the prototype label format
        self.assertIn("TEST0001", zpl)
        self.assertEqual(self.bill_jobs, [])

    def test_missing_label_printer_never_affects_the_bill_printer(self):
        vm, settings = self.vm()
        vm.setBillPrinter("Brother HL-L2360D series")                          # no label printer at all
        bill = InvoicePrintService(self.session(), settings=settings)
        with patch("config.printer_settings.installed_printers", lambda: list(PRINTERS)):
            self.assertEqual(bill.bill_printer_ready(), (True, ""))

    def test_missing_bill_printer_is_reported_before_saving(self):
        _, settings = self.vm()
        bill = InvoicePrintService(self.session(), settings=settings)
        with patch.dict(os.environ, {"MASGOLD_PRINTER_NAME": ""}):
            self.assertEqual(bill.bill_printer_ready(),
                             (False, "لم يتم تحديد طابعة الفواتير. اخترها من الإعدادات."))

    def test_settings_are_local_not_shop_data(self):
        settings = PrinterSettings()
        self.assertIsInstance(settings._store, QSettings)                      # Windows user settings, not MySQL
        self.assertEqual((BILL_KEY, LABEL_KEY), ("printing/bill_printer", "printing/label_printer"))


if __name__ == "__main__":
    unittest.main()
