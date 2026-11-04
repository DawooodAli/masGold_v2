@echo off
rem masGold - بناء نسخة exe (فولدر) في dist\masGold. ملف الإعدادات .env.production يُنسخ بجانب البرنامج يدويًا ولا يدخل في البناء.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --distpath dist --workpath build\work tools\build\masgold.spec
echo.
echo Done: dist\masGold\masGold.exe  (copy .env.production next to it)
