"""Settings → الطباعة: the Bill printer and the Label printer of THIS workstation.

Both lists come from the printers Windows reports (never typed). Each choice is stored
locally (config.printer_settings / QSettings) and independently of the other. The two
test prints use only their own printer and never touch the database.
"""
from PySide6.QtCore import QObject, QThread, Property, Signal, Slot

from config.printer_settings import BILL_FORMAT_TEXT, LABEL_FORMAT_TEXT, PrinterSettings, installed_printers


class _PrintTask(QThread):
    """Driver/spooler work off the GUI thread: selecting the printer, reading back its page
    setup and spooling can block for seconds on Windows."""
    done = Signal(object, str)

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self._work = work

    def run(self):
        try:
            self.done.emit(self._work(), "")
        except Exception as exc:                      # reported, never raised into Qt
            self.done.emit(None, str(exc) or type(exc).__name__)
        finally:
            self._work = None


def _thread_runner(owner, work, finished):
    task = _PrintTask(work, owner)
    task.done.connect(finished)
    task.finished.connect(task.deleteLater)
    owner._tasks.add(task)
    task.finished.connect(lambda t=task: owner._tasks.discard(t))
    task.start()


class PrinterSettingsViewModel(QObject):
    changed = Signal()

    def __init__(self, session=None, settings=None, printers=None, bill_printing=None, label_printing=None,
                 parent=None, runner=None):
        super().__init__(parent)
        # runner(owner, work, finished(result, error)): a background thread by default; tests may
        # pass a synchronous one.
        self._runner = runner or _thread_runner
        self._tasks, self._bill_busy = set(), False
        self._session = session
        self._settings = settings or PrinterSettings()
        self._list_printers = printers or installed_printers
        self._bill_printing = bill_printing        # InvoicePrintService-like (print_test_page)
        self._label_printing = label_printing      # PrototypeZplPrinter-like (print_test_label)
        self._printers = []
        self._message, self._error = "", False
        self.refreshPrinters()

    # ------------------------------------------------------------ printers
    @Slot()
    def refreshPrinters(self):
        try:
            self._printers = list(self._list_printers())
        except Exception:
            self._printers = []
        self.changed.emit()

    @Property("QVariantList", notify=changed)
    def printers(self):
        return self._printers

    def _choice(self, name):
        return {"name": name, "available": bool(name) and name in self._printers,
                "status": "" if not name else ("متاحة" if name in self._printers else "غير متاحة حالياً")}

    @Property("QVariantMap", notify=changed)
    def billPrinter(self):
        return self._choice(self._settings.bill_printer())

    @Property("QVariantMap", notify=changed)
    def labelPrinter(self):
        return self._choice(self._settings.label_printer())

    @Property(str, constant=True)
    def billFormat(self):
        return BILL_FORMAT_TEXT

    @Property(str, constant=True)
    def labelFormat(self):
        return LABEL_FORMAT_TEXT

    @Slot(str)
    def setBillPrinter(self, name):
        """Only a printer Windows reports right now can be chosen; the Label printer is untouched."""
        if name not in self._printers:
            self._say("اختر طابعة من القائمة.", True)
            return
        self._settings.set_bill_printer(name)
        self._say(f"طابعة الفواتير: {name}", False)

    @Slot(str)
    def setLabelPrinter(self, name):
        if name not in self._printers:
            self._say("اختر طابعة من القائمة.", True)
            return
        self._settings.set_label_printer(name)
        self._say(f"طابعة الباركود / الليبل: {name}", False)

    # ------------------------------------------------------------ test prints (no database)
    def _bill(self):
        if self._bill_printing is None:
            from services.invoice_print_service import InvoicePrintService
            self._bill_printing = InvoicePrintService(self._session, settings=self._settings)
        return self._bill_printing

    def _label(self):
        if self._label_printing is None:
            from services.barcode_print_service import PrototypeZplPrinter
            self._label_printing = PrototypeZplPrinter(settings=self._settings)
        return self._label_printing

    @Slot()
    def testBillPrint(self):
        """The calibration bill on the Bill printer, in the background: the UI never freezes."""
        if self._bill_busy:
            return
        choice = self.billPrinter
        if not choice["available"]:
            self._say("لم يتم تحديد طابعة الفواتير. اخترها من القائمة." if not choice["name"]
                      else f"طابعة الفواتير '{choice['name']}' غير متاحة حالياً.", True)
            return
        self._bill_busy = True
        self._say("جاري إرسال الطباعة...", False)
        self._runner(self, self._bill().print_test_page, self._bill_test_done)

    def _bill_test_done(self, outcome, error):
        self._bill_busy = False
        if error or outcome is None:
            self._say(f"تعذرت الطباعة: {error}", True)
        else:
            self._say("تم إرسال فاتورة تجريبية إلى طابعة الفواتير." if outcome.printed else outcome.message,
                      not outcome.printed)

    @Property(bool, notify=changed)
    def billTestBusy(self):
        return self._bill_busy

    @Slot()
    def testLabelPrint(self):
        choice = self.labelPrinter
        if not choice["available"]:
            self._say("لم يتم تحديد طابعة الباركود / الليبل. اخترها من القائمة." if not choice["name"]
                      else f"طابعة الليبل '{choice['name']}' غير متاحة حالياً.", True)
            return
        try:
            self._label().print_test_label()
            self._say("تم إرسال ليبل تجريبي إلى طابعة الباركود / الليبل.", False)
        except Exception as exc:
            self._say(f"تعذرت طباعة الليبل: {exc}", True)

    # ------------------------------------------------------------ messages
    def _say(self, message, error):
        self._message, self._error = message, error
        self.changed.emit()

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(bool, notify=changed)
    def messageIsError(self):
        return self._error
