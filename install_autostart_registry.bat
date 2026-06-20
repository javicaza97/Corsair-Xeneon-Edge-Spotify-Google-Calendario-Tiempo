@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "PYTHONW=%ROOT%\.venv\Scripts\pythonw.exe"
set "RUNNER=%ROOT%\run_dashboard.pyw"
set "RUN_NAME=XENEON Dashboard Local"
set "STARTUP_VBS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\XENEON Dashboard Local.vbs"

echo Instalando autoarranque por Registro para XENEON Dashboard...
echo Carpeta del proyecto: %ROOT%

if not exist "%RUNNER%" (
  echo ERROR: No existe run_dashboard.pyw en la carpeta del proyecto.
  pause
  exit /b 1
)

if not exist "%PYTHONW%" (
  echo ERROR: No existe el Python del entorno virtual:
  echo %PYTHONW%
  echo Ejecuta primero install.bat para crear .venv e instalar dependencias.
  pause
  exit /b 1
)

REM Limpiar metodos antiguos para evitar conflictos.
schtasks /Delete /TN "%RUN_NAME%" /F >nul 2>&1
if exist "%STARTUP_VBS%" del /f /q "%STARTUP_VBS%" >nul 2>&1

powershell -NoProfile -ExecutionPolicy Bypass -Command "$name='XENEON Dashboard Local'; $pythonw='%PYTHONW%'; $runner='%RUNNER%'; $value='\"' + $pythonw + '\" \"' + $runner + '\"'; New-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Force | Out-Null; Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name $name -Value $value; Write-Host 'Valor instalado:' $value"
if errorlevel 1 (
  echo ERROR: No se pudo escribir en el Registro.
  pause
  exit /b 1
)

echo.
echo Autoarranque instalado en:
echo HKCU\Software\Microsoft\Windows\CurrentVersion\Run
echo Nombre: %RUN_NAME%
echo.
call "%ROOT%\start_registry_now.bat"
