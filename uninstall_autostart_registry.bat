@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "RUN_NAME=XENEON Dashboard Local"
set "STARTUP_VBS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\XENEON Dashboard Local.vbs"

echo Eliminando autoarranque de XENEON Dashboard...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'XENEON Dashboard Local' -ErrorAction SilentlyContinue"
schtasks /Delete /TN "%RUN_NAME%" /F >nul 2>&1
if exist "%STARTUP_VBS%" del /f /q "%STARTUP_VBS%" >nul 2>&1

echo Autoarranque eliminado.
pause
