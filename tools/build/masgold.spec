# PyInstaller spec: the masGold program as a FOLDER build (dist\masGold\masGold.exe), owner request 2026-11-04.
# Build with build_exe.bat. The settings file (.env.production) is NOT bundled: it sits next to the exe (config/database_config.py).
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent.parent
datas = [(str(ROOT / "qml"), "qml"), (str(ROOT / "webapp" / "static"), "webapp/static")]
hidden = ["PySide6.QtWebEngineQuick", "PySide6.QtWebEngineCore", "argon2", "keyring.backends.Windows", "win32timezone", "win32print", "win32api", "win32ui", "win32con"]

a = Analysis([str(ROOT / "main.py")], pathex=[str(ROOT)], datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "unittest", "pytest", "IPython", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtCharts",
                       "PySide6.QtDataVisualization", "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtQuick3D"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="masGold", console=False, icon=str(ROOT / "tools" / "build" / "masgold.ico"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="masGold")
