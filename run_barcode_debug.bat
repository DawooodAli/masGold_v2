@echo off
rem masGold - تشغيل البرنامج من الكود مع تسجيل ضغطات حقول الباركود (للتشخيص فقط).
rem افتح فاتورة جديدة، اجعل الكيبورد عربي، امسح باركود واحد واضغط Enter، ثم ابعت الملف:
rem %LOCALAPPDATA%\masGold v2\barcode_debug.log
cd /d "%~dp0"
set MASGOLD_BARCODE_DEBUG=1
".venv\Scripts\python.exe" main.py
