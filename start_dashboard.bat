@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo No existe .venv. Ejecuta install.bat primero.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python app.py
