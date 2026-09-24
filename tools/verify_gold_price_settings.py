"""Interactive Settings save/restart verification, with price writes disabled.

Run once with --stage save, then in a fresh process with --stage restart.
The operator logs in normally and enters/saves the key in the real Settings UI.
Only sanitized service results are written to the review directory.
"""
import argparse
import json
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from main import ApplicationController


class VerificationPrices:
    def __init__(self, service):
        self.service = service

    def snapshot(self):
        return self.service.snapshot()

    def refresh(self, **kwargs):
        return self.snapshot()

    def preview_manual(self, *args):
        return self.service.preview_manual(*args)

    def activate_manual(self, *args, **kwargs):
        raise PermissionError("التحقق من الإعدادات لا يعتمد أسعاراً.")

    def return_to_auto(self, *args, **kwargs):
        raise PermissionError("التحديث التلقائي غير مسموح أثناء هذا التحقق.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("save", "restart"), required=True)
    args = parser.parse_args()
    QQuickStyle.setStyle("Material")
    app = QGuiApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Cairo", 11))
    app.setLayoutDirection(Qt.RightToLeft)
    controller = ApplicationController(app)
    controller.gold_prices._service = VerificationPrices(controller.gold_prices._service)
    state = {"opened": False, "saved": False, "tested": False, "complete": False}
    original = controller.gold_settings._complete

    def completed(worker, result, error):
        original(worker, result, error)
        if not controller.session.is_current(worker.actor):
            return
        if worker.operation == "save" and not error:
            state["saved"] = True
        if worker.operation == "test" and not error and result.get("credential_source") == "KEYRING":
            root = controller.operational_engine.rootObjects()[0]
            key = root.findChild(QObject, "providerApiKey")
            report = {"stage": args.stage, "qml_key_empty": key.property("text") == "",
                      "engine_mode": controller.gold_prices.mode, "result": result}
            folder = Path("reports/gold_price_v1_review")
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"live-{args.stage}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, ensure_ascii=True), flush=True)
            state["complete"] = True
            QTimer.singleShot(1800, app.quit)

    controller.gold_settings._complete = completed

    def poll():
        if not controller.session.authenticated or not controller.operational_engine:
            return
        roots = controller.operational_engine.rootObjects()
        if not roots:
            return
        if not state["opened"]:
            roots[0].setProperty("currentPage", 6)
            state["opened"] = True
        vm = controller.gold_settings
        if (not state["tested"] and not vm.busy and vm.data.get("stored")
                and (args.stage == "restart" or state["saved"])):
            state["tested"] = True
            vm.test(vm.data["base_url"], vm.data["timeout"], "")

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(100)
    app.exec()
    return 0 if state["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
