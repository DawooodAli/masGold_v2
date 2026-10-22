@echo off
rem masGold - تشغيل برنامج الـ production (قاعدة masgold_prod). التشغيل العادي (التجريبي) يفضل من main.py كما هو.
cd /d "%~dp0"
set MASGOLD_ENV_FILE=.env.production
".venv\Scripts\python.exe" main.py
