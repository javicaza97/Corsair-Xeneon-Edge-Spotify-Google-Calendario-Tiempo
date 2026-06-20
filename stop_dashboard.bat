@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "PIDFILE=%ROOT%\data\dashboard.pid"

echo Parando XENEON Dashboard...
if exist "%PIDFILE%" (
  for /f %%P in (%PIDFILE%) do (
    taskkill /PID %%P /F >nul 2>&1
  )
  del /f /q "%PIDFILE%" >nul 2>&1
)

REM Fallback: matar el proceso que tenga abierto el puerto 5050.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=(Get-NetTCPConnection -LocalPort 5050 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess); if($p){ Stop-Process -Id $p -Force; Write-Host 'Proceso detenido:' $p } else { Write-Host 'No hay proceso escuchando en 5050' }"

echo Listo.
pause
