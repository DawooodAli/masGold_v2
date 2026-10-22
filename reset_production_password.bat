@echo off
rem masGold - emergency recovery of the OWNER password in the production database (masgold_prod). Close the program first.
rem The tool prints the database name before changing anything: it must be masgold_prod. You type the new password (hidden) twice, then a confirmation.
cd /d "%~dp0"
set MASGOLD_ENV_FILE=.env.production
".venv\Scripts\python.exe" tools\reset_owner_password.py
pause
