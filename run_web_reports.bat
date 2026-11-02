@echo off
rem masGold - تقارير الأيباد (عرض فقط) على قاعدة masgold_prod. يعمل على 127.0.0.1:8765؛ للوصول من الأيباد: tailscale serve --bg 8765
cd /d "%~dp0"
set MASGOLD_ENV_FILE=.env.production
".venv\Scripts\python.exe" -m webapp %*
