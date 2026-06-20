@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "PYTHONW=%ROOT%\.venv\Scripts\pythonw.exe"
set "RUNNER=%ROOT%\run_dashboard.pyw"
set "DATA=%ROOT%\data"

if not exist "%DATA%" mkdir "%DATA%" >nul 2>&1

echo Arrancando XENEON Dashboard sin consola...
echo Carpeta: %ROOT%

if not exist "%PYTHONW%" (
  echo ERROR: No existe %PYTHONW%
  pause
  exit /b 1
)
if not exist "%RUNNER%" (
  echo ERROR: No existe %RUNNER%
  pause
  exit /b 1
)

REM El proceso se lanza separado. Esta ventana se puede cerrar sin matar el servidor.
start "" /D "%ROOT%" "%PYTHONW%" "%RUNNER%"

echo Esperando a que abra el puerto 5050...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; for($i=0;$i -lt 20;$i++){ try { $c=New-Object Net.Sockets.TcpClient; $iar=$c.BeginConnect('127.0.0.1',5050,$null,$null); if($iar.AsyncWaitHandle.WaitOne(500)){ $c.EndConnect($iar); $c.Close(); $ok=$true; break } $c.Close() } catch {}; Start-Sleep -Milliseconds 500 }; if($ok){ Write-Host 'OK: puerto 5050 abierto' } else { Write-Host 'ERROR: puerto 5050 no responde'; exit 1 }"

echo.
echo Prueba en el navegador:
echo http://127.0.0.1:5050/dashboard.html
echo.
echo Logs:
echo %DATA%\autostart_registry.log
echo %DATA%\dashboard_stdout.log
echo %DATA%\dashboard_stderr.log
echo.
echo Puedes cerrar esta ventana. El servidor deberia seguir vivo.
pause
