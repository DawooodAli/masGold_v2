"""Fetch-only command line entry point; credentials resolve through configuration."""
import json
import argparse
import getpass
import warnings
from config.gold_price_config import GoldTimoConfig
from services.gold_price_diagnostic import DiagnosticProvider, diagnose
from services.secret_store import SecretStoreError
from services.gold_price_errors import ConnectionTestError

def prompt_key(window=False):
    if not window:
        # Fail closed when no secure terminal is available; never echo a key.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass("GoldTimo key (hidden, never saved): ")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit, QLabel
    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Cairo", 11))
    app.setLayoutDirection(Qt.RightToLeft)
    dialog = QInputDialog()
    dialog.setWindowTitle("مجوهرات ماس — اختبار اتصال فقط")
    dialog.setLabelText("أدخل مفتاح GoldTimo الذي نجح في PowerShell.\nلن يُحفظ المفتاح ولن تُعتمد الأسعار.")
    dialog.setTextEchoMode(QLineEdit.Password)
    dialog.setOkButtonText("اختبار الاتصال")
    dialog.setCancelButtonText("إلغاء")
    dialog.setStyleSheet("QDialog { background: #151719; color: #eee; } "
        "QLabel { color: #eee; } QLineEdit { background: #202326; color: #eee; "
        "border: 1px solid #8d7440; border-radius: 5px; padding: 7px; } "
        "QPushButton { background: #30291d; color: #e4c578; border: 1px solid #8d7440; "
        "border-radius: 5px; padding: 7px 14px; text-align: right; }")
    for label in dialog.findChildren(QLabel):
        label.setAlignment(Qt.AlignRight)
    edit = dialog.findChild(QLineEdit)
    edit.setAlignment(Qt.AlignRight)
    edit.setInputMethodHints(Qt.ImhSensitiveData | Qt.ImhNoPredictiveText)
    if not dialog.exec():
        return None
    key = dialog.textValue()
    dialog.setTextValue("")
    return key


def compare_entered(entered, *, trace=False):
    """21K first; run the strict three-karat fetch only after transient success."""
    from services.secret_store import SecretStore
    from config.gold_price_config import credential_diagnostics
    stored, stored_known = None, False
    try:
        stored = SecretStore().get_secret("goldtimo_api_key")
        stored_known = True
    except SecretStoreError:
        pass  # A broken store must not prevent the independent direct-key path.
    metadata = credential_diagnostics(entered, stored, "ENTERED", entered,
                                       stored_known=stored_known)
    if trace:
        print(json.dumps(metadata), flush=True)
    # Both calls use fetch(entered_key=...) / probe_21(entered_key=...), so this
    # input cannot be replaced by keyring or developer environment resolution.
    direct = diagnose(GoldTimoConfig(), entered_key=entered, probe_21=True)
    try:
        stored_result = (diagnose(GoldTimoConfig(api_key=stored,
            credential_trace=credential_diagnostics(None, stored, "KEYRING", stored)), probe_21=True)
            if stored else None)
    except ValueError:
        stored_result = {"validation": "INVALID_CONFIGURATION", "error_code": "CONFIGURATION",
                         "activation_performed": False}
    complete = (diagnose(GoldTimoConfig(), entered_key=entered)
                if direct["validation"] == "FETCHED_21_ONLY" else None)
    result = {"entered_21": direct, "stored_21": stored_result, "entered_all_karats": complete,
              "activation_performed": False}
    if not trace:
        for item in (direct, stored_result, complete):
            if item:
                item.pop("diagnostics", None)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    prompt = parser.add_mutually_exclusive_group()
    prompt.add_argument("--prompt-key", action="store_true", help="Read an unsaved key from a hidden terminal prompt")
    prompt.add_argument("--prompt-key-window", action="store_true", help="Read an unsaved key in a local password dialog")
    parser.add_argument("--compare-stored", action="store_true", help="Compare entered/stored 21K; then fetch 18/21/24 with the entered key")
    parser.add_argument("--probe-21", action="store_true", help="Only request the exact 21K endpoint")
    parser.add_argument("--trace", action="store_true", help="Include only allowlisted credential/request metadata")
    args = parser.parse_args(argv)
    if args.compare_stored and not (args.prompt_key or args.prompt_key_window):
        parser.error("--compare-stored requires a secure key prompt")
    try:
        entered = prompt_key(args.prompt_key_window) if args.prompt_key or args.prompt_key_window else None
        if (args.prompt_key or args.prompt_key_window) and not entered:
            return 2
        if args.compare_stored:
            result = compare_entered(entered, trace=args.trace)
        else:
            config = GoldTimoConfig() if entered is not None else GoldTimoConfig.from_env()
            result = diagnose(config, entered_key=entered, probe_21=args.probe_21)
            if not args.trace:
                result.pop("diagnostics", None)
    except (SecretStoreError, ValueError) as exc:
        safe = ConnectionTestError("CREDENTIAL_STORE" if isinstance(exc, SecretStoreError) else "CONFIGURATION")
        result = {"validation": "INVALID_CONFIGURATION", "activation_performed": False,
                  "error_code": safe.code, "error": str(safe)}
    except (EOFError, getpass.GetPassWarning):
        print("A secure local password prompt is required; no key was read.")
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if args.compare_stored:
        return 0 if (result.get("entered_all_karats") or {}).get("validation") == "PASS" else 1
    return 0 if result["validation"] in ("PASS", "FETCHED_21_ONLY") else 1


if __name__ == "__main__":
    raise SystemExit(main())
