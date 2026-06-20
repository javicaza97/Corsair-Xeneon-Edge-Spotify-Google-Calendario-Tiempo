@echo off
setlocal
cd /d "%~dp0"
echo ========================================
echo  Instalando XENEON Dashboard Local
echo ========================================
where py >nul 2>nul
if errorlevel 1 (
  echo No se ha encontrado Python Launcher ^(py^). Instala Python 3 desde python.org y marca "Add Python to PATH".
  pause
  exit /b 1
)
py -3 -m venv .venv
if errorlevel 1 (
  echo Error creando entorno virtual.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if not exist .env (
  copy .env.example .env >nul
  echo.
  echo Se ha creado .env. Editalo y rellena tus credenciales de Spotify y Google.
)
echo.
echo Instalacion terminada.
echo Edita el archivo .env antes de conectar Spotify/Google.
pause
